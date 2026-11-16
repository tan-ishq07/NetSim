#!/usr/bin/env python3
"""End-to-end NetDevOps demo against a running NetSim server.

It reads a declarative network definition, renders each device's Cisco config
from Jinja2 templates, pushes the config, then asserts the intended state
(interfaces up, routes present, hosts reachable). Exits non-zero if anything
fails — so it doubles as a CI gate.

    # start the server first:
    uvicorn app.main:app --port 8000 &
    python examples/automation/provision_and_verify.py --url http://localhost:8000
"""
from __future__ import annotations

import argparse
import os
import sys

# allow running from the repo root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from automation import NetSimClient  # noqa: E402
from automation.netdevops import deploy_and_verify, format_report, load_network_def  # noqa: E402

DEFAULT_DEF = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "automation", "labs", "two_router_ospf.yaml")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:8000", help="NetSim base URL")
    ap.add_argument("--def", dest="netdef", default=DEFAULT_DEF, help="network definition YAML")
    args = ap.parse_args()

    client = NetSimClient(args.url)
    net_def = load_network_def(args.netdef)
    report, ok = deploy_and_verify(client, net_def)
    print(format_report(report))

    # demonstrate drift detection: mutate one device, then diff vs baseline
    client.push_config("R1", ["interface GigabitEthernet0/1", "shutdown", "exit"])
    drift = client.drift()
    if not drift["in_sync"]:
        print("\n[drift] detected config change vs baseline:")
        for device, change in drift["drift"].items():
            for line in change.get("removed", []):
                print(f"        - {device}: {line}")
            for line in change.get("added", []):
                print(f"        + {device}: {line}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
