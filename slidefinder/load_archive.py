"""Phase 2: group near-duplicate slides across terms, then load one copy of each into Supabase.

    python -m slidefinder.load_archive 45-851
    python -m slidefinder.load_archive 45-851 --skip-images

Reads build/<course>.jsonl from archive.py. Two pages are the same slide when any of these holds:
  - identical text (at least five words), whatever the template looks like
  - card embeddings with cosine similarity >= SIM and image hashes within HAMMING bits
  - near-identical images (within 4 bits), for picture-only pages
The copy from the newest term is indexed; every term that used the slide is kept in `used_in`.
This near-duplicate grouping is a second use of the vector embeddings.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, db, embed
from .archive import ARCHIVE_COURSES, BUILD, term_key
from .load import upload_images
from .text import card_text

SIM, HAMMING = 0.95, 10
COURSE_NAMES = {k: v.split(" ", 1)[1] for k, v in ARCHIVE_COURSES.items()}


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def dhash16(path: str) -> np.ndarray:
    """A 256-bit difference hash of the thumbnail, for picture-only pages (the 64-bit one is too coarse)."""
    g = np.asarray(Image.open(path).convert("L").resize((17, 16), Image.Resampling.LANCZOS), dtype=np.int16)
    return (g[:, 1:] > g[:, :-1]).reshape(-1)


def group_slides(rows: list[dict], E: np.ndarray) -> list[list[int]]:
    """Union pages that are the same slide. Rules, in words:
    A. identical text (5+ words) and a similar image, or identical text of 25+ words (template may differ)
    B. text-bearing pages whose cards embed within SIM and whose images are within HAMMING bits
    C. picture-only pages (under 5 words) whose 256-bit hashes differ in at most 6 bits
    """
    n = len(rows)
    uf = UnionFind(n)
    words = np.array([len(r["text"].split()) for r in rows])
    H = np.array([r["dhash"] for r in rows], dtype=np.uint64)
    bits = np.unpackbits(H.view(np.uint8).reshape(n, 8), axis=1)
    ham = (bits[:, None, :] != bits[None, :, :]).sum(axis=2)

    by_text = defaultdict(list)
    for i, r in enumerate(rows):
        if words[i] >= 5:
            by_text[r["text_hash"]].append(i)
    for idx in by_text.values():
        for j in idx[1:]:
            if ham[idx[0], j] <= 12 or words[j] >= 25:
                uf.union(idx[0], j)

    En = E / np.linalg.norm(E, axis=1, keepdims=True)
    sims = En @ En.T
    textual = words >= 5
    for i, j in np.argwhere(np.triu((sims >= SIM) & (ham <= HAMMING), k=1)):
        if textual[i] and textual[j]:
            uf.union(int(i), int(j))

    pic = np.where(~textual)[0]
    if len(pic) > 1:
        P = np.stack([dhash16(rows[i]["local_thumb"]) for i in pic])
        d16 = (P[:, None, :] != P[None, :, :]).sum(axis=2)
        for a, b in np.argwhere(np.triu(d16 <= 6, k=1)):
            uf.union(int(pic[a]), int(pic[b]))

    groups = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    return list(groups.values())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("course", choices=ARCHIVE_COURSES)
    p.add_argument("--skip-images", action="store_true")
    args = p.parse_args()
    course, name = args.course, COURSE_NAMES[args.course]

    rows = [json.loads(l) for l in (BUILD / f"{course}.jsonl").open()]
    print(f"{course}: {len(rows)} pages from {len({r['term'] for r in rows})} terms")
    cards = [card_text(name, r["session_title"], r["slide_number"], r["title"], r["text"], r["notes"]) for r in rows]
    t = time.time()
    E = np.array(embed.embed_documents(cards, progress=lambda d, n: print(f"  embedded {d}/{n}", file=sys.stderr)), dtype=np.float32)
    print(f"embedded {len(cards)} cards in {time.time() - t:.0f}s")

    t = time.time()
    groups = group_slides(rows, E)
    print(f"grouped into {len(groups)} unique slides in {time.time() - t:.0f}s "
          f"({len(rows) - len(groups)} repeats across terms)")

    slides, items, images = [], [], []
    for g in groups:
        g.sort(key=lambda i: (term_key(rows[i]["term"]), rows[i]["session"], rows[i]["slide_number"]), reverse=True)
        c = rows[g[0]]
        used = []
        for i in g:
            tag = f"{rows[i]['term']} · session {rows[i]['session']}"
            if tag not in used:
                used.append(tag)
        slides.append({
            "slide_id": c["slide_id"], "course": course, "course_name": name, "term": c["term"],
            "session": c["session"], "session_title": c["session_title"], "session_date": c["session_date"],
            "slide_number": c["slide_number"], "title": c["title"], "text": c["text"], "notes": c["notes"],
            "image_path": c["image_path"], "thumb_path": c["thumb_path"], "used_in": used,
        })
        items.append({"id": f"{c['slide_id']}-card-0", "slide_id": c["slide_id"], "kind": "card", "term": c["term"],
                      "occurrence_id": None, "text": cards[g[0]], "embedding": E[g[0]].tolist()})
        images.append((c["image_path"], Path(c["local_image"])))
        images.append((c["thumb_path"], Path(c["local_thumb"])))

    t = time.time()
    db.delete("sf_slides", {"course": f"eq.{course}"})  # a clean reload; items cascade
    db.upsert("sf_slides", slides, on_conflict="slide_id")
    db.upsert("sf_items", items, batch=100)
    print(f"upserted {len(slides)} slides + cards in {time.time() - t:.0f}s; totals: slides {db.count('sf_slides')}, items {db.count('sf_items')}")
    if not args.skip_images:
        t = time.time()
        n = upload_images(images)
        print(f"uploaded {n} images in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
