"""High-level NetDevOps pipeline: declarative network definition -> rendered
config -> pushed to devices -> validated.

A *network definition* is a single YAML file describing intent:

    name: two-router-ospf
    topology:
      devices: [{kind: router, name: R1, ports: 2}, ...]
      links:   [{dev_a: R1, if_a: GigabitEthernet0/0, dev_b: R2, if_b: GigabitEthernet0/0}, ...]
    templates: {R1: router.j2, R2: router.j2, PC1: host.j2, PC2: host.j2}
    vars:      {R1: {...}, R2: {...}, PC1: {...}, PC2: {...}}
    validate:  [{type: reachable, src: PC1, dst: 192.168.2.10}, ...]

``deploy_and_verify`` runs the whole flow against a live NetSim server and
returns a structured report plus an overall ``ok`` flag, so the same code backs
the CLI, the examples, and the CI gate.
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .client import NetSimClient

DEFAULT_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def load_network_def(path: str) -> dict:
    """Load and minimally validate a network-definition YAML file."""
    with open(path, "r", encoding="utf-8") as fh:
        net_def = yaml.safe_load(fh)
    if not isinstance(net_def, dict) or "topology" not in net_def:
        raise ValueError(f"{path}: not a valid network definition (missing 'topology')")
    return net_def


def render_configs(net_def: dict, templates_dir: str = DEFAULT_TEMPLATES_DIR) -> Dict[str, List[str]]:
    """Render every device's config from its Jinja2 template + vars.

    Returns ``{device: [config_line, ...]}`` with blank lines stripped.
    """
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    templates = net_def.get("templates", {})
    variables = net_def.get("vars", {})
    rendered: Dict[str, List[str]] = {}
    for device, template_name in templates.items():
        tpl = env.get_template(template_name)
        text = tpl.render(**(variables.get(device, {})))
        rendered[device] = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return rendered


def topology_spec(net_def: dict) -> dict:
    """Translate the definition's ``topology`` block into a NetSim lab spec."""
    topo = net_def["topology"]
    links = [
        {"dev_a": ln["dev_a"], "if_a": ln["if_a"],
         "dev_b": ln["dev_b"], "if_b": ln["if_b"]}
        for ln in topo.get("links", [])
    ]
    return {"devices": topo.get("devices", []), "links": links, "config": {}, "checks": []}


def deploy_and_verify(client: NetSimClient, net_def: dict,
                      templates_dir: str = DEFAULT_TEMPLATES_DIR) -> Tuple[dict, bool]:
    """Run the full pipeline against ``client``.

    Steps: apply topology -> render configs -> push per device -> snapshot
    baseline -> validate. Returns ``(report, ok)`` where ``ok`` is True only if
    the topology built without errors, every push was clean, and every
    validation assertion passed.
    """
    report: dict = {"name": net_def.get("name", "network"), "steps": []}
    ok = True

    # 1. topology (labs-as-code)
    spec = topology_spec(net_def)
    applied = client.apply_lab(spec)
    topo_errors = applied.get("errors", [])
    ok = ok and not topo_errors
    report["steps"].append({"step": "apply_topology",
                            "devices": len(spec["devices"]),
                            "links": len(spec["links"]),
                            "errors": topo_errors})

    # 2. render + push config per device
    rendered = render_configs(net_def, templates_dir)
    push_report = []
    for device, lines in rendered.items():
        res = client.push_config(device, lines)
        errs = res.get("errors", [])
        ok = ok and not errs
        push_report.append({"device": device, "lines": len(lines), "errors": errs})
    report["steps"].append({"step": "push_config", "devices": push_report})

    # 3. baseline snapshot (for later drift detection)
    client.snapshot()
    report["steps"].append({"step": "snapshot_baseline", "ok": True})

    # 4. validate intended state
    assertions = net_def.get("validate", [])
    validation = client.validate(assertions) if assertions else {
        "compliant": True, "passed": 0, "total": 0, "score": 100, "results": []}
    ok = ok and validation.get("compliant", False) if assertions else ok
    report["steps"].append({"step": "validate", **validation})

    report["ok"] = ok
    return report, ok


def format_report(report: dict) -> str:
    """Render a deploy report as a readable, CI-log-friendly string."""
    lines = [f"=== NetDevOps deploy: {report['name']} ==="]
    for step in report["steps"]:
        name = step["step"]
        if name == "apply_topology":
            status = "OK" if not step["errors"] else "ERRORS"
            lines.append(f"[{status}] topology: {step['devices']} devices, "
                        f"{step['links']} links")
            lines += [f"        ! {e}" for e in step["errors"]]
        elif name == "push_config":
            for d in step["devices"]:
                status = "OK" if not d["errors"] else "ERRORS"
                lines.append(f"[{status}] push {d['device']}: {d['lines']} lines")
                lines += [f"        ! {e}" for e in d["errors"]]
        elif name == "snapshot_baseline":
            lines.append("[OK] saved running-config baseline")
        elif name == "validate":
            if step["total"]:
                lines.append(f"[{'OK' if step['compliant'] else 'FAIL'}] "
                            f"validation: {step['passed']}/{step['total']} "
                            f"passed (score {step['score']}/100)")
                for r in step["results"]:
                    mark = "PASS" if r["passed"] else "FAIL"
                    a = r["assertion"]
                    lines.append(f"        {mark}  {a.get('type')} {a} — {r['detail']}")
            else:
                lines.append("[OK] no validation assertions defined")
    lines.append(f"=== result: {'PASS' if report['ok'] else 'FAIL'} ===")
    return "\n".join(lines)
