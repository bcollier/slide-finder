"""Match what I said to the slides of a session, by text, for sessions with no video alignment.

    python -m slidefinder.align_text 45-851 --dry-run --only "2023 Fall (AB2)" 6   # inspect one
    python -m slidefinder.align_text 45-851                                       # load all

For each session with a deck and a de-identified timed transcript:
  1. keep only cues labeled `instructor`, and cut them into ~45-second windows;
  2. embed the windows and the session's slide cards;
  3. assign windows to slides in order (a dynamic program that prefers staying on a slide or
     moving to the next one, and charges for jumps and for going back);
  4. store each slide's windows as an occurrence (`method: text`, `confidence: low`) on the
     slide's canonical copy, plus searchable `said` chunks.
Lower confidence than video matching: a window lands on the slide whose text it most resembles,
which is usually but not always the slide that was on screen.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

from . import config, db, embed
from .archive import ARCHIVE_COURSES, BUILD, term_key, term_slug
from .load_archive import COURSE_NAMES, group_slides
from .text import card_text, said_chunks
from .describe import load_cache as load_descriptions

WINDOW_SECONDS, MIN_WINDOW_WORDS = 45, 25
MIN_INSTRUCTOR_WORDS = 500
FORWARD_STEP, BACK_PENALTY, MAX_FORWARD = 0.015, 0.08, 0.15
MIN_SIM = 0.06  # centered similarity; a window below this on its assigned slide is dropped (chatter, admin, Q&A)
TIME_PRIOR = 0.25  # cost of being a full deck-length away from where the clock says we should be
GUEST = {("45-851", "2023 Fall (AB2)", 10), ("45-851", "2024 Fall (AB2)", 13)}  # transcripts never indexed


def pick_transcript(session_dir: Path) -> tuple[Path | None, list[dict]]:
    """The timed transcript with the most instructor words, or none."""
    best, best_cues, best_words = None, [], 0
    for f in sorted(glob.glob(str(session_dir / "transcript*.timed.jsonl"))):
        cues = [json.loads(l) for l in open(f) if l.strip()]
        inst = [c for c in cues if c.get("speaker") == "instructor" and c.get("text", "").strip()]
        words = sum(len(c["text"].split()) for c in inst)
        if words > best_words:
            best, best_cues, best_words = Path(f), inst, words
    return (best, best_cues) if best_words >= MIN_INSTRUCTOR_WORDS else (None, [])


def windows_of(cues: list[dict]) -> list[dict]:
    out, cur, start, n = [], [], None, 0
    for c in sorted(cues, key=lambda c: c["start_seconds"]):
        if cur and (c["end_seconds"] - start > WINDOW_SECONDS and n >= MIN_WINDOW_WORDS):
            out.append({"start": start, "end": cur[-1]["end_seconds"], "text": " ".join(x["text"].strip() for x in cur)})
            cur, start, n = [], None, 0
        if start is None:
            start = c["start_seconds"]
        cur.append(c)
        n += len(c["text"].split())
    if cur:
        out.append({"start": start, "end": cur[-1]["end_seconds"], "text": " ".join(x["text"].strip() for x in cur)})
    return out


def assign(sim: np.ndarray) -> list[int]:
    """sim[w, s]: window w vs slide s. Returns the slide index for each window."""
    W, S = sim.shape
    dp = np.full((W, S), -1e9)
    back = np.zeros((W, S), dtype=int)
    dp[0] = sim[0]
    jump = np.arange(S)
    for w in range(1, W):
        for s in range(S):
            d = s - jump  # positive: moving forward by d; negative: going back
            pen = np.where(d >= 0, np.minimum(np.maximum(d - 1, 0) * FORWARD_STEP, MAX_FORWARD), BACK_PENALTY)
            cand = dp[w - 1] - pen
            k = int(np.argmax(cand))
            dp[w, s] = cand[k] + sim[w, s]
            back[w, s] = k
    path = [int(np.argmax(dp[-1]))]
    for w in range(W - 1, 0, -1):
        path.append(int(back[w, path[-1]]))
    return path[::-1]


def card_embeddings(course: str, rows: list[dict]) -> np.ndarray:
    cache = BUILD / f"{course}.cards.npy"
    if cache.exists():
        E = np.load(cache)
        if len(E) == len(rows):
            return E
    name = COURSE_NAMES[course]
    cards = [card_text(name, r["session_title"], r["slide_number"], r["title"], r["text"], r["notes"]) for r in rows]
    E = np.array(embed.embed_documents(cards, progress=lambda d, n: print(f"  cards {d}/{n}", file=sys.stderr)), dtype=np.float32)
    np.save(cache, E)
    return E


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("course", choices=ARCHIVE_COURSES)
    p.add_argument("--only", nargs=2, metavar=("TERM", "SESSION"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    course = args.course

    rows = [json.loads(l) for l in (BUILD / f"{course}.jsonl").open()]
    E = card_embeddings(course, rows)
    En = E / np.linalg.norm(E, axis=1, keepdims=True)
    canon = {}
    for g in group_slides(rows, E):
        # Must match load_archive's choice: newest term first, then session, then page.
        g.sort(key=lambda i: (term_key(rows[i]["term"]), rows[i]["session"], rows[i]["slide_number"]), reverse=True)
        for i in g:
            canon[rows[i]["slide_id"]] = rows[i]["slide_id"] if i == g[0] else rows[g[0]]["slide_id"]
    live = {r["slide_id"] for r in db.select_all("sf_slides", {"course": f"eq.{course}", "select": "slide_id"})}
    missing = {v for v in canon.values() if v not in live}
    if missing:
        sys.exit(f"{len(missing)} canonical slides are not in the database; reload the course first")

    # Slide text for matching: the card plus the image description (picture-only slides have no
    # text of their own, and lectures spend minutes on them).
    descs = load_descriptions()
    name = COURSE_NAMES[course]

    sessions = {}
    for i, r in enumerate(rows):
        sessions.setdefault((r["term"], r["session"]), []).append(i)
    root = config.LECTURE_ARCHIVE / ARCHIVE_COURSES[course]
    total_occ = total_said = 0
    for (term, snum), idx in sorted(sessions.items()):
        if args.only and (term != args.only[0] or snum != int(args.only[1])):
            continue
        if (course, term, snum) in GUEST:
            continue
        idx.sort(key=lambda i: rows[i]["slide_number"])
        sdir = next((d for d in (root / term).iterdir() if d.is_dir() and re.match(rf"^{snum:02d} ", d.name)), None)
        if not sdir:
            continue
        tfile, cues = pick_transcript(sdir)
        if not tfile:
            continue
        wins = windows_of(cues)
        if not wins:
            continue
        Wv = np.array(embed.embed_documents([w["text"] for w in wins]), dtype=np.float32)
        Wn = Wv / np.linalg.norm(Wv, axis=1, keepdims=True)
        stexts = [card_text(name, rows[i]["session_title"], rows[i]["slide_number"], rows[i]["title"], rows[i]["text"],
                            rows[i]["notes"], descs.get(canon[rows[i]["slide_id"]], "")) for i in idx]
        Sv = np.array(embed.embed_documents(stexts), dtype=np.float32)
        Sn = Sv / np.linalg.norm(Sv, axis=1, keepdims=True)
        raw = Wn @ Sn.T
        # Double-center: a slide that is similar to everything (generic text) should not win every
        # window, and a window similar to every slide (chatter) should not count much anywhere.
        sim = raw - raw.mean(axis=1, keepdims=True) - raw.mean(axis=0, keepdims=True) + raw.mean()
        t = np.array([(w["start"] + w["end"]) / 2 for w in wins])
        t = (t - t.min()) / max(t.max() - t.min(), 1)
        pos = np.arange(len(idx)) / max(len(idx) - 1, 1)
        sim = sim - TIME_PRIOR * np.abs(t[:, None] - pos[None, :])
        path = assign(sim)
        per_slide: dict[int, list[int]] = {}
        for w, s in enumerate(path):
            if sim[w, s] >= MIN_SIM:
                per_slide.setdefault(s, []).append(w)
        kept = sum(len(v) for v in per_slide.values())
        print(f"{term} s{snum:02d} {rows[idx[0]]['session_title'][:40]}: {len(wins)} windows, {kept} kept on "
              f"{len(per_slide)} of {len(idx)} slides ({tfile.name})")
        if args.dry_run:
            for s in sorted(per_slide):
                ws = per_slide[s]
                r = rows[idx[s]]
                print(f"   p{r['slide_number']:>3} {r['title'][:38]!r:<40} {len(ws)} win  {wins[ws[0]]['start']/60:5.1f}-{wins[ws[-1]]['end']/60:5.1f} min  "
                      f"sim {np.mean([sim[w, s] for w in ws]):.2f}  \"{wins[ws[0]]['text'][:70]}\"")
            continue

        occs, items = {}, []
        for s, ws in per_slide.items():
            r = rows[idx[s]]
            cid = canon[r["slide_id"]]
            oid = f"{cid}@{term_slug(term)}"
            o = occs.setdefault(oid, {"id": oid, "slide_id": cid, "term": term, "session": snum, "windows": [],
                                      "method": "text", "confidence": "low", "transcript": ""})
            o["windows"] += [[round(wins[w]["start"], 1), round(wins[w]["end"], 1)] for w in ws]
            o["transcript"] = (o["transcript"] + " " + " ".join(wins[w]["text"] for w in ws)).strip()
        for o in occs.values():
            title = next((rows[i]["title"] for i in idx if canon[rows[i]["slide_id"]] == o["slide_id"]), "")
            for k, chunk in enumerate(said_chunks(title, o["transcript"])):
                items.append({"id": f"{o['slide_id']}-said-{term_slug(term)}-{k}", "slide_id": o["slide_id"], "kind": "said",
                              "term": term, "occurrence_id": o["id"], "text": chunk})
        if items:
            vecs = embed.embed_documents([i["text"] for i in items])
            for i, v in zip(items, vecs):
                i["embedding"] = v
        db.upsert("sf_occurrences", list(occs.values()))
        if items:
            db.upsert("sf_items", items, batch=100)
        total_occ += len(occs)
        total_said += len(items)
    if not args.dry_run:
        print(f"stored {total_occ} occurrences and {total_said} said chunks")


if __name__ == "__main__":
    main()
