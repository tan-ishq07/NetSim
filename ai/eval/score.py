"""Score tutor predictions against the held-out gold answers.

Pillar-aware, deterministic, no GPU/network required so it runs in CI and
locally. Two ways to use it:

  * As a library: ``score_records(records)`` where each record is
    ``{"question", "gold", "pred", "pillar"}``.
  * As a CLI: ``python ai/eval/score.py --pred preds.jsonl`` where each line is
    such a record.

The headline metric is **key-token recall**: we extract the load-bearing tokens
from the gold answer (IOS commands, IP/mask/network literals, numbers, protocol
keywords) and measure the fraction that appear in the prediction. This rewards
getting the *fix/fact* right rather than matching prose. We also report exact
(normalised) match and a per-pillar breakdown.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from typing import Dict, Iterable, List

# Networking terms that carry meaning in an answer; presence/absence matters.
_KEYWORDS = {
    "shutdown", "no", "ip", "address", "default-gateway", "network", "area",
    "remote-as", "neighbor", "permit", "deny", "access-list", "ospf", "bgp",
    "eigrp", "rip", "vlan", "trunk", "access", "switchport", "nat", "inside",
    "outside", "overload", "standby", "vrrp", "hsrp", "acl", "subnet", "mask",
    "broadcast", "gateway", "route", "static", "dhcp", "dns", "tcp", "udp",
    "icmp", "arp", "mtu", "ttl", "duplex", "speed", "encapsulation", "dot1q",
}

_IPISH = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")
_NUM = re.compile(r"\b\d+\b")
_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]+")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def key_tokens(gold: str) -> List[str]:
    g = gold or ""
    toks = set()
    toks.update(m.group(0) for m in _IPISH.finditer(g))
    toks.update(m.group(0) for m in _NUM.finditer(g))
    for m in _WORD.finditer(g.lower()):
        w = m.group(0)
        if w in _KEYWORDS:
            toks.add(w)
    return sorted(toks)


def recall(gold: str, pred: str) -> float:
    toks = key_tokens(gold)
    if not toks:
        # nothing load-bearing extracted; fall back to normalised containment
        return 1.0 if _norm(gold) and _norm(gold) in _norm(pred) else 0.0
    p = _norm(pred)
    hit = sum(1 for t in toks if t.lower() in p)
    return hit / len(toks)


def score_records(records: Iterable[dict]) -> dict:
    by_pillar: Dict[str, List[float]] = defaultdict(list)
    exact_by_pillar: Dict[str, List[float]] = defaultdict(list)
    all_recall: List[float] = []
    all_exact: List[float] = []
    for r in records:
        gold, pred = r.get("gold", ""), r.get("pred", "")
        pillar = r.get("pillar", "unknown")
        rec = recall(gold, pred)
        ex = 1.0 if _norm(gold) == _norm(pred) else 0.0
        by_pillar[pillar].append(rec)
        exact_by_pillar[pillar].append(ex)
        all_recall.append(rec)
        all_exact.append(ex)

    def mean(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    return {
        "n": len(all_recall),
        "key_token_recall": mean(all_recall),
        "exact_match": mean(all_exact),
        "by_pillar": {
            p: {"n": len(v), "key_token_recall": mean(v),
                "exact_match": mean(exact_by_pillar[p])}
            for p, v in sorted(by_pillar.items())
        },
    }


def _load(path: str) -> List[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, help="jsonl of {question,gold,pred,pillar}")
    ap.add_argument("--out", default=None, help="optional path to write metrics json")
    args = ap.parse_args()
    metrics = score_records(_load(args.pred))
    text = json.dumps(metrics, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)


if __name__ == "__main__":
    main()
