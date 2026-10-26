"""Give untitled slides a title.

    python -m slidefinder.title --dry-run --limit 10
    python -m slidefinder.title --yes

A slide has no title when its heading is part of a picture or its first text line is a page
number or a link. A text-only call to Claude Haiku 4.5 reads the slide's extracted text and
its image description (which usually quotes the heading) and returns the heading shown on
the slide, or a short descriptive title when there is none. The title is written to the slide,
and the slide's card and description items are rebuilt and re-embedded so search sees it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import base64
import io
import re

import anthropic
from PIL import Image

from . import config, db, embed
from .archive import clean_title
from .describe import MODEL, desc_text
from .text import card_text

PROMPT = """This lecture slide has no extracted title. Give it one.
If the description names a heading shown on the slide, return that heading exactly.
Otherwise write a 3 to 7 word title that says what the slide shows (no quotes, no trailing period).
Return the title only.

Course: {course_name}
Session: {session_title}
Slide text (may be empty or junk): {text}
Image description: {description}"""


VISION_PROMPT = """Return the heading printed on this lecture slide, exactly as written (no quotes, no trailing period).
If the slide has no heading, write a 3 to 7 word title that says what it shows.
Return the title only."""


def title_one(client: anthropic.Anthropic, s: dict, vision: bool = False) -> str:
    if vision:
        im = Image.open(io.BytesIO(db.download(s["image_path"]))).convert("RGB")
        if im.width > 1024:
            im = im.resize((1024, round(im.height * 1024 / im.width)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.b64encode(buf.getvalue()).decode()}},
                   {"type": "text", "text": VISION_PROMPT}]
    else:
        content = PROMPT.format(course_name=s["course_name"], session_title=s["session_title"],
                                text=(s["text"] or "")[:400], description=(s["description"] or "")[:900])
    for attempt in range(3):
        try:
            r = client.messages.create(model=MODEL, max_tokens=40, messages=[{"role": "user", "content": content}])
            break
        except Exception:
            if attempt == 2:
                return s.get("title") or ""
            time.sleep(3 * (attempt + 1))
    t = " ".join(r.content[0].text.strip().strip('"').split())
    return t.rstrip(".")[:140]


def untitled_by_text(s: dict) -> bool:
    """The slides the loader could not title: no usable first text line."""
    lines = [l for l in (s["text"] or "").splitlines() if l.strip()]
    return not lines or clean_title(lines[0]) == ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--vision", action="store_true", help="read the heading off the slide image (redo slides titled from text)")
    args = p.parse_args()
    if args.vision:
        rows = [r for r in db.select_all("sf_slides", {"select": "*"}) if untitled_by_text(r)]
    else:
        rows = db.select_all("sf_slides", {"title": "eq.", "select": "*"})
    rows = [r for r in rows if r.get("description")]
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} untitled slides, about ${len(rows) * (0.0025 if args.vision else 0.0006):.2f}")
    if not args.dry_run and not args.yes:
        sys.exit("add --yes to run")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    with ThreadPoolExecutor(args.workers) as ex:
        titles = list(ex.map(lambda s: title_one(client, s, args.vision), rows))
    for s, t in zip(rows[:40] if args.dry_run else rows[:10], titles):
        print(f"  {s['slide_id']:<34} {t!r}   <- text {(s['text'] or '')[:30]!r}")
    if args.dry_run:
        return
    items = []
    for s, t in zip(rows, titles):
        s["title"] = t
        items.append({"id": f"{s['slide_id']}-card-0", "slide_id": s["slide_id"], "kind": "card", "term": s["term"],
                      "occurrence_id": None, "text": card_text(s["course_name"], s["session_title"], s["slide_number"], t, s["text"], s["notes"])})
        items.append({"id": f"{s['slide_id']}-desc-0", "slide_id": s["slide_id"], "kind": "desc", "term": s["term"],
                      "occurrence_id": None, "text": desc_text(s, s["description"])})
    vecs = embed.embed_documents([i["text"] for i in items], progress=lambda d, n: print(f"  embedded {d}/{n}", file=sys.stderr))
    for i, v in zip(items, vecs):
        i["embedding"] = v
    for s, t in zip(rows, titles):
        db.patch("sf_slides", {"slide_id": f"eq.{s['slide_id']}"}, {"title": t})
    db.upsert("sf_items", items, batch=100)
    print(f"titled {len(rows)} slides; rebuilt {len(items)} items")


if __name__ == "__main__":
    main()
