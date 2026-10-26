"""Load phase 1 data (Faculty Twin's processed build) into Supabase.

    python -m slidefinder.load --limit 50        # time a small run first
    python -m slidefinder.load                   # everything
    python -m slidefinder.load --skip-images     # text only

Reads, per course and session, under $LECTURE_ARCHIVE/_build:
    slides/<course>/sNN/slides.json   slide text, notes, image paths
    slides/<course>/sNN/deck.json     session title and date
    align/<course>/sNN.json           what I said while each slide was on screen (de-identified)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, db, embed
from .text import card_text, said_chunks


def read_build(limit: int | None = None) -> tuple[list[dict], list[dict], list[dict], list[tuple[str, Path]]]:
    slides, items, occurrences, images = [], [], [], []
    for key, meta in config.COURSES.items():
        for deck_path in sorted((config.FT_BUILD / "slides" / key).glob("s*/deck.json")):
            deck = json.load(deck_path.open())
            session = int(deck["session"])
            if (key, session) in config.SKIP_SESSIONS:
                continue
            rows = json.load((deck_path.parent / "slides.json").open())
            align_path = config.FT_BUILD / "align" / key / f"s{session:02d}.json"
            align = {a["slide_id"]: a for a in json.load(align_path.open())} if align_path.exists() else {}

            for r in rows:
                sid = r["slide_id"]
                n = int(r["slide_number"])
                title = (r.get("title") or "").strip()
                text, notes = r.get("text") or "", r.get("notes") or ""
                img = f"slides/{key}/{sid}.webp"
                thumb = f"slides/{key}/{sid}-thumb.webp"
                slides.append({
                    "slide_id": sid, "course": meta["course"], "course_name": meta["name"],
                    "term": meta["term"], "session": session, "session_title": deck["session_title"],
                    "session_date": deck.get("date"), "slide_number": n, "title": title,
                    "text": text, "notes": notes, "image_path": img, "thumb_path": thumb,
                    "used_in": [meta["term"]],
                })
                images.append((img, config.FT_BUILD / r["image"]))
                images.append((thumb, config.FT_BUILD / r["thumb"]))
                items.append({
                    "id": f"{sid}-card-0", "slide_id": sid, "kind": "card", "term": meta["term"],
                    "occurrence_id": None,
                    "text": card_text(meta["name"], deck["session_title"], n, title, text, notes),
                })
                a = align.get(sid)
                if a and a.get("transcript", "").strip():
                    occ_id = f"{sid}@{meta['term']}"
                    methods = a.get("methods") or []
                    occurrences.append({
                        "id": occ_id, "slide_id": sid, "term": meta["term"], "session": session,
                        "windows": a.get("windows") or [], "transcript": a["transcript"].strip(),
                        "method": max(set(methods), key=methods.count) if methods else None,
                        "confidence": "high" if methods and methods.count("frame") == len(methods) else "medium",
                    })
                    for i, chunk in enumerate(said_chunks(title, a["transcript"])):
                        items.append({
                            "id": f"{sid}-said-{i}", "slide_id": sid, "kind": "said",
                            "term": meta["term"], "occurrence_id": occ_id, "text": chunk,
                        })
                if limit and len(slides) >= limit:
                    return slides, items, occurrences, images
    return slides, items, occurrences, images


def upload_images(images: list[tuple[str, Path]], workers: int = 4) -> int:
    def one(pair):
        path, src = pair
        if src.exists():
            db.upload(path, src.read_bytes())
            return 1
        return 0
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for i, ok in enumerate(ex.map(one, images), 1):
            done += ok
            if i % 200 == 0:
                print(f"  images {i}/{len(images)}", file=sys.stderr)
    return done


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="only the first N slides (for timing)")
    p.add_argument("--skip-images", action="store_true")
    args = p.parse_args()

    t0 = time.time()
    slides, items, occurrences, images = read_build(args.limit)
    print(f"read: {len(slides)} slides, {len(items)} items "
          f"({sum(i['kind'] == 'said' for i in items)} said), {len(occurrences)} occurrences")
    longest = max(len(i["text"].split()) for i in items)
    print(f"longest item: {longest} words")

    t = time.time()
    vecs = embed.embed_documents([i["text"] for i in items],
                                 progress=lambda d, n: print(f"  embedded {d}/{n}", file=sys.stderr))
    for i, v in zip(items, vecs):
        i["embedding"] = v
    print(f"embed: {len(items)} items in {time.time() - t:.1f}s")

    t = time.time()
    db.upsert("sf_slides", slides, on_conflict="slide_id")
    db.upsert("sf_occurrences", occurrences)
    db.upsert("sf_items", items, batch=100)
    print(f"upsert: {time.time() - t:.1f}s; table counts: slides {db.count('sf_slides')}, "
          f"items {db.count('sf_items')}, occurrences {db.count('sf_occurrences')}")

    if not args.skip_images:
        t = time.time()
        n = upload_images(images)
        print(f"images: {n} uploaded in {time.time() - t:.1f}s")
    print(f"total {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
