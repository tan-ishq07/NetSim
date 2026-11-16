"""Assemble the full tutor SFT dataset and write train/val JSONL.

Combines all three pillars (engine-grounded diagnosis, CLI command Q&A, broad
networking knowledge), de-duplicates, shuffles deterministically, and writes a
train/validation split plus the syllabus and a stats summary. The output is the
seed corpus the Kaggle notebook trains on (and expands via teacher distillation
across ``syllabus.json``).

Usage:
    python -m app.tutor_ai.generate --out ai/data
"""
from __future__ import annotations

import argparse
import json
import os
import random
from typing import Dict, List

from .cli_qa import build_cli_qa
from .dataset import build_dataset
from .knowledge import SYLLABUS, build_knowledge_qa, syllabus_topics


def _dedupe(examples: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for e in examples:
        user = e["messages"][1]["content"]
        asst = e["messages"][2]["content"]
        key = (user, asst)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def build_all(seed: int = 1337, subnet_count: int = 250,
              count_per_gen: int = 12) -> List[Dict]:
    examples: List[Dict] = []
    examples.extend(build_dataset(seed=seed, count_per_gen=count_per_gen))
    examples.extend(build_cli_qa())
    examples.extend(build_knowledge_qa(subnet_count=subnet_count, seed=seed))
    examples = _dedupe(examples)
    random.Random(seed).shuffle(examples)
    return examples


def _write_jsonl(path: str, rows: List[Dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ai/data")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--val-frac", type=float, default=0.04)
    ap.add_argument("--subnet-count", type=int, default=250)
    ap.add_argument("--count-per-gen", type=int, default=12)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = build_all(seed=args.seed, subnet_count=args.subnet_count,
                     count_per_gen=args.count_per_gen)
    n_val = max(1, int(len(rows) * args.val_frac))
    val, train = rows[:n_val], rows[n_val:]

    _write_jsonl(os.path.join(args.out, "tutor_sft.train.jsonl"), train)
    _write_jsonl(os.path.join(args.out, "tutor_sft.val.jsonl"), val)
    with open(os.path.join(args.out, "syllabus.json"), "w", encoding="utf-8") as f:
        json.dump(SYLLABUS, f, indent=2)

    by_pillar: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for r in rows:
        by_pillar[r["meta"]["pillar"]] = by_pillar.get(r["meta"]["pillar"], 0) + 1
        by_kind[r["meta"]["kind"]] = by_kind.get(r["meta"]["kind"], 0) + 1
    stats = {
        "total": len(rows), "train": len(train), "val": len(val),
        "by_pillar": by_pillar, "by_kind": dict(sorted(by_kind.items())),
        "syllabus_topics": len(syllabus_topics()),
    }
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
