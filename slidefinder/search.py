"""Turn a question into ranked slides.

sf_search (Postgres) returns the top items by meaning and by keywords, fused by reciprocal
rank. Several items belong to one slide (its card, chunks of what I said, my notes), so this
module merges them per slide, decides whether anything fits, and attaches the slide rows.
"""

from __future__ import annotations

from . import db, embed

# Cosine distance of the best vector hit above which the page says "I don't think I taught this".
# Placeholder until Step 5 looks at real on-topic and off-topic numbers.
WEAK_DISTANCE = 0.55
TOP_SLIDES = 30
MAX_SLIDES = 100
# Score boost when more than one kind of item matched the same slide. Started at 0.15; eval/run.py on
# 28 labeled questions (Oct 26) showed it hurt (hit@1 0.57 vs 0.68, MRR 0.72 vs 0.78), so it is off.
KIND_BONUS = 0.0


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
        for key in ("vec_rank", "fts_rank"):
            if h[key] is not None and (s[key] is None or h[key] < s[key]):
                s[key] = h[key]
        score = h["score"] if mode == "hybrid" else 1.0 / (60 + rank)
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
    ranked = sorted(by_slide.values(), key=lambda s: -s["score"])[:n]

    # Attach slide rows and signed thumbnails.
    ids = [s["slide_id"] for s in ranked]
    rows = {r["slide_id"]: r for r in db.select("sf_slides", {"slide_id": f"in.({','.join(ids)})"})} if ids else {}
    thumbs = db.signed_urls([rows[i]["thumb_path"] for i in ids if i in rows and rows[i].get("thumb_path")])
    results = []
    for s in ranked:
        r = rows.get(s["slide_id"])
        if not r:
            continue
        results.append({**s, "slide": {k: r[k] for k in (
            "slide_id", "course", "course_name", "term", "session", "session_title", "slide_number",
            "title", "text", "used_in")}, "thumb": thumbs.get(r.get("thumb_path") or "")})

    results = stack_versions(results)

    best = min((s["distance"] for s in ranked if s["distance"] is not None), default=None)
    return {
        "question": question, "mode": mode, "n": n, "kind_bonus": KIND_BONUS,
        "weak": best is None or best > WEAK_DISTANCE, "best_distance": best,
        "results": results,
    }


def family_key(text: str) -> str | None:
    """Slides with the same text (5+ words) are versions of one slide, whatever course or term."""
    words = (text or "").lower().split()
    return " ".join(words) if len(words) >= 5 else None


def term_order(term: str) -> tuple:
    """"2025 Fall (M2)" sorts after "2025 Spring (A3)": newest term first when reversed."""
    parts = (term or "").split()
    year = int(parts[0]) if parts and parts[0].isdigit() else 0
    season = {"spring": 1, "summer": 2, "fall": 3}.get(parts[1].lower(), 0) if len(parts) > 1 else 0
    return (year, season, term)


def stack_versions(results: list[dict]) -> list[dict]:
    """Group versions of the same slide from different courses or terms into one result, in the
    slot of the best-ranked version. Two copies from the same course and term are kept apart:
    those are different slides that happen to share a caption (build-up steps, for example)."""
    stacks: list[dict] = []
    for r in results:
        key = family_key(r["slide"].get("text"))
        home = None
        if key:
            for st in stacks:
                if st["key"] == key and all((v["slide"]["course"], v["slide"]["term"]) !=
                                            (r["slide"]["course"], r["slide"]["term"]) for v in st["versions"]):
                    home = st
                    break
        if home:
            home["versions"].append(r)
        else:
            stacks.append({"key": key, "versions": [r]})
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
    items = db.select("sf_items", {"slide_id": f"eq.{slide_id}", "select": "id,kind,term,text,created_at",
                                   "order": "id"})
    order = {"card": 0, "desc": 1, "said": 2, "note": 3}
    items.sort(key=lambda i: (order.get(i["kind"], 9), i["id"]))
    notes = [i for i in items if i["kind"] == "note"]
    urls = db.signed_urls([p for p in (r.get("image_path"), r.get("thumb_path")) if p])
    return {"slide": r, "image": urls.get(r.get("image_path") or ""), "occurrences": occ, "notes": notes,
            "items": items}
