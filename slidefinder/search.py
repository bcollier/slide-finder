"""Turn a question into ranked slides.

sf_search (Postgres) returns the top items by meaning and by keywords, fused by reciprocal
rank. Several items belong to one slide (its card, chunks of what I said, my notes), so this
module merges them per slide, decides whether anything fits, and attaches the slide rows.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor

from . import db, embed

# Cosine distance of the best vector hit above which the page says "I don't think I taught this".
# Placeholder until Step 5 looks at real on-topic and off-topic numbers.
WEAK_DISTANCE = 0.55
TOP_SLIDES = 30
MAX_SLIDES = 100
# Score boost when more than one kind of item matched the same slide. Started at 0.15; eval/run.py on
# 28 labeled questions (Oct 26) showed it hurt (hit@1 0.57 vs 0.68, MRR 0.72 vs 0.78), so it is off.
KIND_BONUS = 0.0
KEYWORD_WEIGHT = 0.5  # a keyword rank counts half a meaning rank: keywords alone rarely find the right slide (see eval)


def search(question: str, course: str | None = None, mode: str = "hybrid", n: int = TOP_SLIDES,
           kinds: set[str] | None = None) -> dict:
    """kinds: restrict to these item kinds (used by the eval to measure what each kind adds)."""
    n = max(1, min(int(n), MAX_SLIDES))
    qvec = embed.embed_query(question)
    hits = db.rpc("sf_search", {
        "query_embedding": qvec, "query_text": question, "match_count": max(60, n * 4), "course_filter": course,
    })
    if kinds:
        hits = [h for h in hits if h["kind"] in kinds]
    if mode == "vector":
        hits = [h for h in hits if h["vec_rank"] is not None]
        hits.sort(key=lambda h: h["vec_rank"])
    elif mode == "keyword":
        hits = [h for h in hits if h["fts_rank"] is not None]
        hits.sort(key=lambda h: h["fts_rank"])

    # Merge per slide: best item's score, a bonus for agreement across kinds, remember why.
    by_slide: dict[str, dict] = {}
    for rank, h in enumerate(hits):
        s = by_slide.setdefault(h["slide_id"], {"slide_id": h["slide_id"], "score": 0.0, "kinds": set(),
                                                 "why": None, "distance": None, "vec_rank": None, "fts_rank": None,
                                                 "items": 0})
        s["items"] += 1
        s.setdefault("hits", []).append({"id": h["id"], "kind": h["kind"], "term": h["term"], "distance": h["distance"],
                                         "vec_rank": h["vec_rank"], "fts_rank": h["fts_rank"],
                                         "text": re.sub(r"^\[[^\]]*\]\s*", "", h["text"] or "")})
        for key in ("vec_rank", "fts_rank"):
            if h[key] is not None and (s[key] is None or h[key] < s[key]):
                s[key] = h[key]
        if mode == "hybrid":
            score = ((1.0 / (60 + h["vec_rank"]) if h["vec_rank"] is not None else 0.0)
                     + KEYWORD_WEIGHT * (1.0 / (60 + h["fts_rank"]) if h["fts_rank"] is not None else 0.0))
        else:
            score = 1.0 / (60 + rank)
        if score > s["score"]:
            s["score"] = score
            s["why"] = {"kind": h["kind"], "text": h["text"], "term": h["term"]}
        s["kinds"].add(h["kind"])
        if h["distance"] is not None and (s["distance"] is None or h["distance"] < s["distance"]):
            s["distance"] = h["distance"]
    for s in by_slide.values():
        s["raw_score"] = s["score"]
        if len(s["kinds"]) > 1:
            s["score"] *= 1 + KIND_BONUS
        s["kinds"] = sorted(s["kinds"])
    # Ties (same ranks) go to the slide whose item is closer in meaning.
    ranked = sorted(by_slide.values(), key=lambda s: (-s["score"], s["distance"] if s["distance"] is not None else 9))[:n]

    for s in ranked:
        s["hits"].sort(key=lambda h: ((h["vec_rank"] if h["vec_rank"] is not None else 10**6),
                                      (h["fts_rank"] if h["fts_rank"] is not None else 10**6)))
    attach_evidence(ranked, qvec)

    # Attach slide rows and signed thumbnails.
    ids = [s["slide_id"] for s in ranked]
    rows = {r["slide_id"]: r for r in db.select("sf_slides", {"slide_id": f"in.({','.join(ids)})"})} if ids else {}
    thumbs = db.signed_urls([rows[i]["thumb_path"] for i in ids if i in rows and rows[i].get("thumb_path")])
    results = []
    for s in ranked:
        r = rows.get(s["slide_id"])
        if not r:
            continue
        s["evidence"] = {"items": s.pop("hits", []), "misses": s.pop("misses", {})}
        results.append({**s, "slide": {k: r[k] for k in (
            "slide_id", "course", "course_name", "term", "session", "session_title", "slide_number",
            "title", "text", "pic_hash", "used_in")}, "thumb": thumbs.get(r.get("thumb_path") or "")})

    results = stack_versions(results)

    best = min((s["distance"] for s in ranked if s["distance"] is not None), default=None)
    return {
        "question": question, "mode": mode, "n": n, "kind_bonus": KIND_BONUS,
        "weak": best is None or best > WEAK_DISTANCE, "best_distance": best, "weak_distance": WEAK_DISTANCE,
        "results": results,
    }


EVIDENCE_SLIDES = 40  # sentence closeness is computed for this many top results
MAX_SENTENCES = 10  # per best item, for the sentence-level closeness shown on the card


def sentences_of(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if len(p.split()) >= 3][:MAX_SENTENCES]


def attach_evidence(ranked: list[dict], qvec: list[float]) -> None:
    """For each ranked slide: which of its items did not match at all, and how close each
    sentence of its best item is to the question (one embedding batch for the whole page)."""
    ids = [s["slide_id"] for s in ranked]
    if not ids:
        return
    all_items = db.select_all("sf_items", {"slide_id": f"in.({','.join(ids)})", "select": "id,slide_id,kind"})
    hit_ids = {h["id"] for s in ranked for h in s["hits"]}
    for s in ranked:
        s["misses"] = {}
    by_id = {s["slide_id"]: s for s in ranked}
    for it in all_items:
        if it["id"] not in hit_ids:
            m = by_id[it["slide_id"]]["misses"]
            m[it["kind"]] = m.get(it["kind"], 0) + 1
    sents, owners = [], []
    for s in ranked[:EVIDENCE_SLIDES]:
        if s["hits"]:
            for t in sentences_of(s["hits"][0]["text"]):
                sents.append(t)
                owners.append(s["hits"][0])
    if not sents:
        return
    try:
        batches = [sents[k:k + 100] for k in range(0, len(sents), 100)]
        with ThreadPoolExecutor(max_workers=min(6, len(batches))) as ex:
            vecs = [v for part in ex.map(embed.embed_documents, batches) for v in part]
    except Exception:
        return
    qn = math.sqrt(sum(x * x for x in qvec)) or 1.0
    for owner, text, v in zip(owners, sents, vecs):
        vn = math.sqrt(sum(x * x for x in v)) or 1.0
        sim = sum(a * b for a, b in zip(qvec, v)) / (qn * vn)
        owner.setdefault("sentences", []).append({"text": text, "dist": round(1 - sim, 4)})


PIC_BITS = 24  # same threshold as the loader: pictures this close are the same picture


def family_key(text: str) -> str | None:
    """Slides with the same text (5+ words) are versions of one slide, whatever course or term."""
    words = (text or "").lower().split()
    return " ".join(words) if len(words) >= 5 else None


def picture_only(text: str) -> bool:
    return len((text or "").split()) < 5


def same_slide(a: dict, b: dict) -> bool:
    """Two result slides are versions of one slide: identical text, or (for picture-only
    slides) the same embedded picture."""
    if (a["course"], a["term"]) == (b["course"], b["term"]):
        return False  # same deck family: build-up steps and captions, not versions
    ka, kb = family_key(a.get("text")), family_key(b.get("text"))
    if ka and kb:
        return ka == kb
    if picture_only(a.get("text")) and picture_only(b.get("text")) and a.get("pic_hash") and b.get("pic_hash"):
        return bin(int(a["pic_hash"], 16) ^ int(b["pic_hash"], 16)).count("1") <= PIC_BITS
    return False


def term_order(term: str) -> tuple:
    """"2025 Fall (M2)" sorts after "2025 Spring (A3)": newest term first when reversed."""
    parts = (term or "").split()
    year = int(parts[0]) if parts and parts[0].isdigit() else 0
    season = {"spring": 1, "summer": 2, "fall": 3}.get(parts[1].lower(), 0) if len(parts) > 1 else 0
    return (year, season, term)


def stack_versions(results: list[dict]) -> list[dict]:
    """Group versions of the same slide from different courses or terms into one result, in the
    slot of the best-ranked version. Versions share their text, or, for picture-only slides,
    their picture. Two copies from the same course and term are kept apart."""
    stacks: list[dict] = []
    for r in results:
        home = next((st for st in stacks if all(same_slide(r["slide"], v["slide"]) for v in st["versions"])), None)
        if home:
            home["versions"].append(r)
        else:
            stacks.append({"versions": [r]})
    out = []
    for st in stacks:
        vs = sorted(st["versions"], key=lambda v: term_order(v["slide"]["term"]), reverse=True)
        top = st["versions"][0]  # best ranked keeps the slot and the scores shown first
        out.append({**top, "versions": vs})
    return out


def slide_detail(slide_id: str) -> dict | None:
    rows = db.select("sf_slides", {"slide_id": f"eq.{slide_id}"})
    if not rows:
        return None
    r = rows[0]
    occ = db.select("sf_occurrences", {"slide_id": f"eq.{slide_id}", "order": "term.desc"})
    items = db.select("sf_items", {"slide_id": f"eq.{slide_id}", "select": "id,kind,term,occurrence_id,text,created_at",
                                   "order": "id"})
    order = {"card": 0, "desc": 1, "said": 2, "note": 3}
    items.sort(key=lambda i: (order.get(i["kind"], 9), i["id"]))
    notes = [i for i in items if i["kind"] == "note"]
    urls = db.signed_urls([p for p in (r.get("image_path"), r.get("thumb_path")) if p])
    return {"slide": r, "image": urls.get(r.get("image_path") or ""), "occurrences": occ, "notes": notes,
            "items": items, "versions": slide_versions(r)}


def slide_versions(r: dict) -> list[dict]:
    """The same slide in other courses or terms (same text, or the same picture for a
    picture-only slide), each with its own narration."""
    sel = "slide_id,course,course_name,term,session,session_title,slide_number,title,used_in,thumb_path"
    if family_key(r.get("text")):
        others = db.select("sf_slides", {"text": f"eq.{r['text']}", "select": sel})
    elif r.get("pic_hash"):
        others = db.select("sf_slides", {"pic_hash": f"eq.{r['pic_hash']}", "select": sel})
    else:
        return []
    others = [o for o in others if o["slide_id"] != r["slide_id"] and (o["course"], o["term"]) != (r["course"], r["term"])]
    if not others:
        return []
    ids = [o["slide_id"] for o in others]
    occ = db.select("sf_occurrences", {"slide_id": f"in.({','.join(ids)})", "order": "term.desc"})
    thumbs = db.signed_urls([o["thumb_path"] for o in others if o.get("thumb_path")])
    out = []
    for o in sorted(others, key=lambda o: term_order(o["term"]), reverse=True):
        out.append({"slide": o, "thumb": thumbs.get(o.get("thumb_path") or ""),
                    "occurrences": [x for x in occ if x["slide_id"] == o["slide_id"]]})
    return out
