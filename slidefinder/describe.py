"""Write a short description of what each slide shows, with a vision model, and fold it into the card.

    python -m slidefinder.describe --limit 20     # try a few, print them
    python -m slidefinder.describe --course 45-851
    python -m slidefinder.describe                # every slide without a description

Picture-only slides have almost no text to embed; this is what makes them findable. Descriptions
are cached in cache/descriptions.jsonl by slide id, so a re-run costs nothing. The card item is
re-embedded with the description appended. Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import base64
import re
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from . import config, db, embed
from .text import card_text

MODEL = "claude-haiku-4-5-20251001"
CACHE = config.ROOT / "cache" / "descriptions.jsonl"
COST_PER_SLIDE = 0.0025  # rough: ~900 image tokens + ~300 text in, ~120 out
COST_CAP = 15.0
PROMPT = """You are indexing a professor's lecture slide so it can be found later by meaning.
Course: {course_name}. Session: {session_title}. Slide {n}{title}.
Slide text (may be empty):
{text}
Speaker notes (may be empty):
{notes}

Look at the image. In 60 to 90 words, plain prose, no preamble: say what the slide shows (a chart
and what it plots, a diagram and its parts, a code snippet and what it does, a photo, a table,
a quote), the concept it is teaching, and any names of methods, tools, companies or examples
visible. Do not repeat the slide text verbatim; add what the picture says."""


def load_cache() -> dict[str, str]:
    if not CACHE.exists():
        return {}
    return {d["slide_id"]: d["description"] for d in map(json.loads, CACHE.open()) if d.get("description")}


def describe_one(client, slide: dict) -> str:
    im = Image.open(io.BytesIO(db.download(slide["image_path"]))).convert("RGB")
    if im.width > 1024:
        im = im.resize((1024, round(im.height * 1024 / im.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    prompt = PROMPT.format(course_name=slide["course_name"], session_title=slide["session_title"],
                           n=slide["slide_number"], title=f": {slide['title']}" if slide["title"] else "",
                           text=slide["text"][:1500] or "(none)", notes=slide["notes"][:800] or "(none)")
    for attempt in range(3):
        try:
            r = client.messages.create(
                model=MODEL, max_tokens=220,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(buf.getvalue()).decode()}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            text = r.content[0].text
            text = re.sub(r"^\s*(#+\s*[^\n]*|slide (analysis|summary|description):?)\s*\n", "", text, flags=re.I)
            return " ".join(text.split())
        except Exception as e:  # rate limit or a blip; back off and retry
            if attempt == 2:
                print(f"  failed {slide['slide_id']}: {e}", file=sys.stderr)
                return ""
            time.sleep(3 * (attempt + 1))
    return ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--course")
    p.add_argument("--limit", type=int)
    p.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    params = {"select": "slide_id,course,course_name,session_title,slide_number,title,text,notes,image_path,description",
              "order": "slide_id"}
    if args.course:
        params["course"] = f"eq.{args.course}"
    slides = db.select(params and "sf_slides", params)
    cache = load_cache()
    todo = [s for s in slides if not s["description"] and s["slide_id"] not in cache]
    if args.limit:
        todo = todo[:args.limit]
    cached = [s for s in slides if not s["description"] and s["slide_id"] in cache]
    est = len(todo) * COST_PER_SLIDE
    print(f"{len(slides)} slides; {len(cached)} cached, {len(todo)} to describe, about ${est:.2f}")
    if est > COST_CAP and not args.yes:
        sys.exit(f"over the ${COST_CAP:.0f} cap; rerun with --yes or --course/--limit")

    t = time.time()
    done = 0
    CACHE.parent.mkdir(exist_ok=True)
    with CACHE.open("a") as f, ThreadPoolExecutor(args.workers) as ex:
        for s, desc in zip(todo, ex.map(lambda s: describe_one(client, s), todo)):
            if desc:
                f.write(json.dumps({"slide_id": s["slide_id"], "description": desc}) + "\n")
                f.flush()
                cache[s["slide_id"]] = desc
            done += 1
            if args.limit:
                print(f"\n{s['slide_id']} {s['title'][:50]!r}\n  {desc}")
            elif done % 100 == 0:
                print(f"  {done}/{len(todo)} {time.time() - t:.0f}s", file=sys.stderr)
    print(f"described {done} in {time.time() - t:.0f}s")

    # Fold descriptions into the slide rows and re-embed the cards.
    pending = [s for s in slides if not s["description"] and s["slide_id"] in cache]
    if not pending:
        return
    cards = [card_text(s["course_name"], s["session_title"], s["slide_number"], s["title"], s["text"], s["notes"],
                       cache[s["slide_id"]]) for s in pending]
    vecs = embed.embed_documents(cards, progress=lambda d, n: print(f"  embedded {d}/{n}", file=sys.stderr))
    for i in range(0, len(pending), 200):
        chunk = pending[i:i + 200]
        db.upsert("sf_slides", [{"slide_id": s["slide_id"], "description": cache[s["slide_id"]]} for s in chunk], on_conflict="slide_id") if False else None
        for s in chunk:  # PostgREST upsert needs full rows; patch one column instead
            db._check(db._client.patch("/rest/v1/sf_slides", params={"slide_id": f"eq.{s['slide_id']}"},
                                       json={"description": cache[s["slide_id"]]}, headers={"Prefer": "return=minimal"}))
    items = [{"id": f"{s['slide_id']}-card-0", "slide_id": s["slide_id"], "kind": "card", "term": None,
              "occurrence_id": None, "text": c, "embedding": v} for s, c, v in zip(pending, cards, vecs)]
    terms = {r["slide_id"]: r["term"] for r in db.select("sf_slides", {"select": "slide_id,term",
             "slide_id": f"in.({','.join(s['slide_id'] for s in pending)})"})} if len(pending) < 300 else None
    for it in items:
        it["term"] = (terms or {}).get(it["slide_id"]) or next(
            (r["term"] for r in db.select("sf_slides", {"select": "term", "slide_id": f"eq.{it['slide_id']}"})), "")
    db.upsert("sf_items", items, batch=100)
    print(f"updated {len(pending)} slides and re-embedded their cards")


if __name__ == "__main__":
    main()
