"""Turn a question into ranked slides, and explain each one.

The SQL function `sf_search` returns the top items by meaning (exact cosine scan) and by
keywords (full-text search), each with its rank on that side. Everything after that happens
here:

    1. score every item from its two ranks (reciprocal rank fusion, keywords at half weight);
    2. merge items per slide: a slide takes its best item, and remembers every hit;
    3. rank the slides and keep the first n;
    4. gather evidence for the slide view: items that did not match, and how close each
       sentence of the best item is to the question;
    5. attach the slide rows and signed thumbnails;
    6. stack versions of the same slide from other courses or terms into one result.

The slide view (`slide_detail`) adds the narration per term, every indexed item, notes, and the
same slide in other courses (`slide_versions`).
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor

from . import db, embed

# Cosine distance of the best hit above which the page says "I don't think I taught this".
# On-topic answers land at 0.35 to 0.50 across the index; off-topic questions at 0.58 and up.
WEAK_DISTANCE = 0.55
TOP_SLIDES = 30
MAX_SLIDES = 100
RRF_K = 60  # reciprocal rank fusion constant: an item ranked r on a side contributes 1 / (RRF_K + r)
# A keyword rank counts half a meaning rank. With equal weight, a slide whose image description
# happened to contain every word of the question outranked the right slide; halving it changed
# nothing in the evaluation. (A bonus for slides matched by several kinds of item was also
# tried and measured harmful: hit@1 0.57 against 0.68.)
KEYWORD_WEIGHT = 0.5
EVIDENCE_SLIDES = 40  # sentence closeness is computed for this many top results
MAX_SENTENCES = 10  # per best item
PIC_BITS = 24  # of 256: two pictures this close are the same picture (same threshold as the loader)

SLIDE_FIELDS = ("slide_id", "course", "course_name", "term", "session", "session_title", "slide_number",
                "title", "text", "pic_hash", "used_in")
_TITLE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")  # narration chunks start with "[slide title] "


# ---- search ------------------------------------------------------------------

def search(question: str, course: str | None = None, mode: str = "hybrid", n: int = TOP_SLIDES,
           kinds: set[str] | None = None, keyword_weight: float = KEYWORD_WEIGHT) -> dict:
    """Ranked slides for a question.

    mode: "hybrid" fuses meaning and keywords; "vector" and "keyword" use one side only.
    kinds: restrict to these item kinds (the evaluation uses it to measure what each kind adds).
    keyword_weight: how much a keyword rank counts next to a meaning rank in hybrid mode
    (0 ignores keywords, 1 is equal weight); the page lets the user move it.
    """
    n = max(1, min(int(n), MAX_SLIDES))
    qvec = embed.embed_query(question)
    hits = fetch_hits(qvec, question, course, n, mode, kinds)
    ranked = rank_slides(hits, mode, keyword_weight)[:n]
    attach_evidence(ranked, qvec)
    results = stack_versions(attach_slides(ranked))
    best = min((s["distance"] for s in ranked if s["distance"] is not None), default=None)
    return {
        "question": question, "mode": mode, "n": n,
        "weak": best is None or best > WEAK_DISTANCE, "best_distance": best, "weak_distance": WEAK_DISTANCE,
        "keyword_weight": keyword_weight, "results": results,
    }


def fetch_hits(qvec: list[float], question: str, course: str | None, n: int, mode: str,
               kinds: set[str] | None) -> list[dict]:
    """Candidate items from the database, filtered and ordered for the requested mode."""
    hits = db.rpc("sf_search", {
        "query_embedding": qvec, "query_text": question, "match_count": max(60, n * 4), "course_filter": course,
    })
    if kinds:
        hits = [h for h in hits if h["kind"] in kinds]
    if mode == "vector":
        hits = sorted((h for h in hits if h["vec_rank"] is not None), key=lambda h: h["vec_rank"])
    elif mode == "keyword":
        hits = sorted((h for h in hits if h["fts_rank"] is not None), key=lambda h: h["fts_rank"])
    return hits


def item_score(hit: dict, position: int, mode: str, keyword_weight: float = KEYWORD_WEIGHT) -> float:
    """Reciprocal rank fusion of the item's two ranks; a single-side mode scores by list position."""
    if mode != "hybrid":
        return 1.0 / (RRF_K + position)
    meaning = 1.0 / (RRF_K + hit["vec_rank"]) if hit["vec_rank"] is not None else 0.0
    keyword = 1.0 / (RRF_K + hit["fts_rank"]) if hit["fts_rank"] is not None else 0.0
    return meaning + keyword_weight * keyword


def rank_slides(hits: list[dict], mode: str, keyword_weight: float = KEYWORD_WEIGHT) -> list[dict]:
    """Merge items per slide and sort. A slide takes its best item's score, the lowest distance
    and the best rank on each side, and keeps every hit for the evidence views."""
    by_slide: dict[str, dict] = {}
    for position, h in enumerate(hits):
        s = by_slide.setdefault(h["slide_id"], {
            "slide_id": h["slide_id"], "score": 0.0, "kinds": set(), "why": None, "distance": None,
            "vec_rank": None, "fts_rank": None, "items": 0, "hits": [],
        })
        s["items"] += 1
        s["hits"].append({"id": h["id"], "kind": h["kind"], "term": h["term"], "distance": h["distance"],
                          "vec_rank": h["vec_rank"], "fts_rank": h["fts_rank"],
                          "text": _TITLE_PREFIX.sub("", h["text"] or "")})
        s["kinds"].add(h["kind"])
        for side in ("vec_rank", "fts_rank"):
            if h[side] is not None and (s[side] is None or h[side] < s[side]):
                s[side] = h[side]
        if h["distance"] is not None and (s["distance"] is None or h["distance"] < s["distance"]):
            s["distance"] = h["distance"]
        score = item_score(h, position, mode, keyword_weight)
        if score > s["score"]:
            s["score"] = score
            s["why"] = {"kind": h["kind"], "text": h["text"], "term": h["term"]}
    for s in by_slide.values():
        s["kinds"] = sorted(s["kinds"])
        s["hits"].sort(key=lambda h: (h["vec_rank"] if h["vec_rank"] is not None else 10**6,
                                      h["fts_rank"] if h["fts_rank"] is not None else 10**6))
    # Ties (same ranks) go to the slide whose item is closer in meaning.
    return sorted(by_slide.values(), key=lambda s: (-s["score"], s["distance"] if s["distance"] is not None else 9))


def attach_slides(ranked: list[dict]) -> list[dict]:
    """Add each slide's row and a signed thumbnail URL; move the hits into an `evidence` block."""
    ids = [s["slide_id"] for s in ranked]
    rows = {r["slide_id"]: r for r in db.select("sf_slides", {"slide_id": f"in.({','.join(ids)})"})} if ids else {}
    thumbs = db.signed_urls([rows[i]["thumb_path"] for i in ids if i in rows and rows[i].get("thumb_path")])
    results = []
    for s in ranked:
        r = rows.get(s["slide_id"])
        if not r:
            continue
        s["evidence"] = {"items": s.pop("hits", []), "misses": s.pop("misses", {})}
        results.append({**s, "slide": {k: r[k] for k in SLIDE_FIELDS}, "thumb": thumbs.get(r.get("thumb_path") or "")})
    return results


# ---- evidence ------------------------------------------------------------------

def sentences_of(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if len(p.split()) >= 3][:MAX_SENTENCES]


def cosine_distance(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return 1 - sum(x * y for x, y in zip(a, b)) / (na * nb)


def attach_evidence(ranked: list[dict], qvec: list[float]) -> None:
    """For each ranked slide: which of its items did not match at all (`misses`, counts per
    kind), and how close each sentence of its best item is to the question (`sentences` on
    that hit). All sentences of the page go to the embedding API in one parallel batch."""
    for s in ranked:
        s["misses"] = {}
    ids = [s["slide_id"] for s in ranked]
    if not ids:
        return
    hit_ids = {h["id"] for s in ranked for h in s["hits"]}
    by_id = {s["slide_id"]: s for s in ranked}
    for it in db.select_all("sf_items", {"slide_id": f"in.({','.join(ids)})", "select": "id,slide_id,kind"}):
        if it["id"] not in hit_ids:
            misses = by_id[it["slide_id"]]["misses"]
            misses[it["kind"]] = misses.get(it["kind"], 0) + 1

    sentences, owners = [], []
    for s in ranked[:EVIDENCE_SLIDES]:
        if s["hits"]:
            for text in sentences_of(s["hits"][0]["text"]):
                sentences.append(text)
                owners.append(s["hits"][0])
    if not sentences:
        return
    try:
        batches = [sentences[k:k + 100] for k in range(0, len(sentences), 100)]
        with ThreadPoolExecutor(max_workers=min(6, len(batches))) as ex:
            vectors = [v for batch in ex.map(embed.embed_documents, batches) for v in batch]
    except Exception:
        return  # the evidence is optional; the search still answers
    for owner, text, v in zip(owners, sentences, vectors):
        owner.setdefault("sentences", []).append({"text": text, "dist": round(cosine_distance(qvec, v), 4)})


# ---- versions of one slide ---------------------------------------------------------

def family_key(text: str) -> str | None:
    """Slides with the same text (5+ words) are versions of one slide, whatever course or term."""
    words = (text or "").lower().split()
    return " ".join(words) if len(words) >= 5 else None


def picture_only(text: str) -> bool:
    return len((text or "").split()) < 5


def same_picture(a: str | None, b: str | None) -> bool:
    return bool(a and b) and bin(int(a, 16) ^ int(b, 16)).count("1") <= PIC_BITS


def same_slide(a: dict, b: dict) -> bool:
    """Two slides are versions of one slide: identical text, or (both picture-only) the same
    embedded picture. Never within one course and term: those are build-up steps and captions."""
    if (a["course"], a["term"]) == (b["course"], b["term"]):
        return False
    ka, kb = family_key(a.get("text")), family_key(b.get("text"))
    if ka and kb:
        return ka == kb
    if picture_only(a.get("text")) and picture_only(b.get("text")):
        return same_picture(a.get("pic_hash"), b.get("pic_hash"))
    return False


def term_order(term: str) -> tuple:
    """"2025 Fall (M2)" sorts after "2025 Spring (A3)": newest term first when reversed."""
    parts = (term or "").split()
    year = int(parts[0]) if parts and parts[0].isdigit() else 0
    season = {"spring": 1, "summer": 2, "fall": 3}.get(parts[1].lower(), 0) if len(parts) > 1 else 0
    return (year, season, term)


def stack_versions(results: list[dict]) -> list[dict]:
    """Group versions of one slide into one result, in the slot of the best-ranked version,
    with `versions` ordered newest term first."""
    stacks: list[list[dict]] = []
    for r in results:
        home = next((st for st in stacks if all(same_slide(r["slide"], v["slide"]) for v in st)), None)
        if home is None:
            stacks.append([r])
        else:
            home.append(r)
    out = []
    for st in stacks:
        versions = sorted(st, key=lambda v: term_order(v["slide"]["term"]), reverse=True)
        out.append({**st[0], "versions": versions})
    return out


# ---- slide view ------------------------------------------------------------------

def slide_detail(slide_id: str) -> dict | None:
    """Everything the slide view shows: the row, a signed image URL, narration per term,
    every indexed item, notes, and the same slide in other courses."""
    rows = db.select("sf_slides", {"slide_id": f"eq.{slide_id}"})
    if not rows:
        return None
    r = rows[0]
    occurrences = db.select("sf_occurrences", {"slide_id": f"eq.{slide_id}", "order": "term.desc"})
    items = db.select("sf_items", {"slide_id": f"eq.{slide_id}", "select": "id,kind,term,occurrence_id,text,created_at",
                                   "order": "id"})
    order = {"card": 0, "desc": 1, "said": 2, "note": 3}
    items.sort(key=lambda i: (order.get(i["kind"], 9), i["id"]))
    urls = db.signed_urls([p for p in (r.get("image_path"), r.get("thumb_path")) if p])
    return {"slide": r, "image": urls.get(r.get("image_path") or ""), "occurrences": occurrences,
            "notes": [i for i in items if i["kind"] == "note"], "items": items, "versions": slide_versions(r)}


def slide_versions(r: dict) -> list[dict]:
    """The same slide in other courses or terms (same text, or the same picture for a
    picture-only slide), each with its own narration, newest term first."""
    fields = "slide_id,course,course_name,term,session,session_title,slide_number,title,used_in,thumb_path"
    if family_key(r.get("text")):
        others = db.select("sf_slides", {"text": f"eq.{r['text']}", "select": fields})
    elif r.get("pic_hash"):
        others = db.select("sf_slides", {"pic_hash": f"eq.{r['pic_hash']}", "select": fields})
    else:
        return []
    others = [o for o in others if o["slide_id"] != r["slide_id"] and (o["course"], o["term"]) != (r["course"], r["term"])]
    if not others:
        return []
    ids = [o["slide_id"] for o in others]
    occurrences = db.select("sf_occurrences", {"slide_id": f"in.({','.join(ids)})", "order": "term.desc"})
    thumbs = db.signed_urls([o["thumb_path"] for o in others if o.get("thumb_path")])
    return [{"slide": o, "thumb": thumbs.get(o.get("thumb_path") or ""),
             "occurrences": [x for x in occurrences if x["slide_id"] == o["slide_id"]]}
            for o in sorted(others, key=lambda o: term_order(o["term"]), reverse=True)]
