"""Score retrieval on labeled questions.

    python -m eval.run                 # all modes
    python -m eval.run --mode vector

Reads FT_EVAL_PATH (private; never copied into this repo): one JSON object per line with
`question`, `type` and, for answerable ones, `expected_slides` (slide ids that are good answers).
Off-topic questions should come back flagged weak.

Metrics per mode: hit@1, hit@5 (a good slide in the top 5), recall@10, MRR, and the share of
off-topic questions correctly flagged. Prints one row per mode; only these aggregates go in the README.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from slidefinder.search import search

MODES = ("hybrid", "vector", "keyword")


def load() -> list[dict]:
    path = Path(os.environ.get("FT_EVAL_PATH", "")).expanduser()
    rows = [json.loads(l) for l in path.open() if l.strip()]
    for r in rows:
        e = r.get("expected_slides") or []
        r["expected"] = set([e] if isinstance(e, str) else e)
    return rows


def score(rows: list[dict], mode: str, kinds: set | None = None) -> dict:
    from slidefinder import search as S
    hit1 = hit5 = rec10 = mrr = 0.0
    n_ans = 0
    off_ok = off_n = 0
    misses = []
    for r in rows:
        # The labels name slides in the question's own course; other courses now hold the same
        # material (Data Mining has its own clustering deck), so score within the course.
        course = (r.get("course") or "").split(" ")[0] or None
        d = search(r["question"], course, mode, n=10, kinds=kinds)
        ids = [x["slide_id"] for x in d["results"]]
        if r["expected"]:
            n_ans += 1
            ranks = [i for i, s in enumerate(ids) if s in r["expected"]]
            if ranks:
                hit1 += ranks[0] == 0
                hit5 += ranks[0] < 5
                mrr += 1 / (ranks[0] + 1)
            else:
                misses.append((r.get("qid_hint"), r["question"][:60]))
            rec10 += len(set(ids) & r["expected"]) / len(r["expected"])
        elif r.get("type") == "off_topic":
            off_n += 1
            off_ok += d["weak"]
    return {"mode": mode + (" (no desc)" if kinds else ""), "n": n_ans, "hit@1": hit1 / n_ans, "hit@5": hit5 / n_ans,
            "recall@10": rec10 / n_ans, "MRR": mrr / n_ans, "off_topic_flagged": f"{off_ok}/{off_n}", "misses": misses}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=MODES)
    args = p.parse_args()
    rows = load()
    print(f"{sum(bool(r['expected']) for r in rows)} answerable questions, "
          f"{sum(r.get('type') == 'off_topic' for r in rows)} off-topic\n")
    print(f"{'mode':<20} {'hit@1':>6} {'hit@5':>6} {'rec@10':>7} {'MRR':>6}  off-topic flagged")
    runs = [(m, None) for m in ([args.mode] if args.mode else MODES)] + ([("hybrid", {"card", "said", "note"})] if not args.mode else [])
    for mode, kinds in runs:
        s = score(rows, mode, kinds)
        print(f"{s['mode']:<20} {s['hit@1']:>6.2f} {s['hit@5']:>6.2f} {s['recall@10']:>7.2f} {s['MRR']:>6.2f}  {s['off_topic_flagged']}")
        if s["misses"]:
            print("   missed (not in top 10):", "; ".join(f"{q}" for _, q in s["misses"]))


if __name__ == "__main__":
    main()
