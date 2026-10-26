"""Store each slide's picture hash (`sf_slides.pic_hash`): the 256-bit dHash of the largest picture
embedded in the page, read from the image file, so the same picture-only slide is recognised
across courses and templates (search results stack it; the slide view lists its other courses).

    python -m slidefinder.family
"""

from __future__ import annotations

import json
import re
import sys

import fitz

from . import config, db
from .archive import ARCHIVE_COURSES, BUILD, iter_sessions, page_pics, term_slug
from .load_archive import usable_pics

PHASE1_TERM = "2026 Fall"


def main() -> None:
    slides = db.select_all("sf_slides", {"select": "slide_id,course,term,session,slide_number"})
    hashes: dict[str, str | None] = {}
    # Phase 2 courses: hashes are in the build files.
    for course in ("45-851", "45-885", "46-887"):
        f = BUILD / f"{course}.jsonl"
        if f.exists():
            for l in f.open():
                r = json.loads(l)
                p = usable_pics(r.get("pics") or [])
                hashes[r["slide_id"]] = p[0] if p else None
    # Phase 1 courses: same archive, read the page again.
    for course in ("70-445", "45-884"):
        want = {(s["session"], s["slide_number"]): s["slide_id"] for s in slides if s["course"] == course}
        for sess in iter_sessions(course):
            if sess["term"] != PHASE1_TERM:
                continue
            pdf = sess["pdf"] or BUILD / "converted" / course / term_slug(sess["term"]) / "slides.pdf"
            if not pdf.exists():
                continue
            doc = fitz.open(pdf)
            for i, page in enumerate(doc):
                sid = want.get((sess["session"], i + 1))
                if sid:
                    p = usable_pics(page_pics(doc, page))
                    hashes[sid] = p[0] if p else None
    rows = [(s["slide_id"], hashes.get(s["slide_id"])) for s in slides]
    have = sum(1 for _, h in rows if h)
    print(f"{len(rows)} slides, {have} with a picture hash", file=sys.stderr)
    for k in range(0, len(rows), 400):
        chunk = rows[k:k + 400]
        values = ",".join(f"('{sid}', {('NULL' if h is None else repr(h))})" for sid, h in chunk)
        db.exec_sql(f"update sf_slides s set pic_hash = v.h from (values {values}) as v(sid, h) where s.slide_id = v.sid;")
    print("stored")


if __name__ == "__main__":
    main()
