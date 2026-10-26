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
TOP_SLIDES = 12
KIND_BONUS = 0.15  # score boost when more than one kind of item matched the same slide


def search(question: str, course: str | None = None, mode: str = "hybrid") -> dict:
    qvec = embed.embed_query(question)
    hits = db.rpc("sf_search", {
        "query_embedding": qvec, "query_text": question, "match_count": 50, "course_filter": course,
    })
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
                                                 "why": None, "distance": None})
        score = h["score"] if mode == "hybrid" else 1.0 / (60 + rank)
        if score > s["score"]:
            s["score"] = score
            s["why"] = {"kind": h["kind"], "text": h["text"], "term": h["term"]}
        s["kinds"].add(h["kind"])
        if h["distance"] is not None and (s["distance"] is None or h["distance"] < s["distance"]):
            s["distance"] = h["distance"]
    for s in by_slide.values():
        if len(s["kinds"]) > 1:
            s["score"] *= 1 + KIND_BONUS
        s["kinds"] = sorted(s["kinds"])
    ranked = sorted(by_slide.values(), key=lambda s: -s["score"])[:TOP_SLIDES]

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
            "title", "used_in")}, "thumb": thumbs.get(r.get("thumb_path") or "")})

    best = min((s["distance"] for s in ranked if s["distance"] is not None), default=None)
    return {
        "question": question, "mode": mode,
        "weak": best is None or best > WEAK_DISTANCE, "best_distance": best,
        "results": results,
    }


def slide_detail(slide_id: str) -> dict | None:
    rows = db.select("sf_slides", {"slide_id": f"eq.{slide_id}"})
    if not rows:
        return None
    r = rows[0]
    occ = db.select("sf_occurrences", {"slide_id": f"eq.{slide_id}", "order": "term.desc"})
    notes = db.select("sf_items", {"slide_id": f"eq.{slide_id}", "kind": "eq.note", "select": "id,text,created_at",
                                   "order": "created_at.desc"})
    urls = db.signed_urls([p for p in (r.get("image_path"), r.get("thumb_path")) if p])
    return {"slide": r, "image": urls.get(r.get("image_path") or ""), "occurrences": occ, "notes": notes}
