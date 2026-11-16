"""Observe what a student would see on a (possibly broken) topology.

Given a live :class:`Network`, produce a structured, human-readable symptom
report: device/interface state, a host-to-host reachability matrix, and the
deterministic analyzer findings. This is the "Observed" half of a training
example - the part a learner can see without already knowing the answer."""
from __future__ import annotations

from itertools import permutations
from typing import List

from app.engine import Host, Network, Router
from app.engine import tutor as tutor_mod


def topology_summary(net: Network) -> str:
    """One line per device describing kind, interfaces and L3 addressing."""
    lines: List[str] = []
    for dev in net.devices.values():
        parts = []
        for i in dev.interfaces.values():
            if i.ip:
                state = "up" if i.is_up else ("down" if i.enabled is False else "no-link")
                parts.append(f"{i.name}={i.ip}/{i.prefix}({state})")
            elif getattr(i, "ipv6", None):
                parts.append(f"{i.name}={i.ipv6}/{i.ipv6_prefix}")
        extra = []
        if isinstance(dev, Router):
            if dev.ospf_pid is not None:
                extra.append(f"ospf {dev.ospf_pid}")
            if dev.bgp_asn is not None:
                extra.append(f"bgp {dev.bgp_asn}")
            if dev.eigrp_asn is not None:
                extra.append(f"eigrp {dev.eigrp_asn}")
            if dev.static_routes:
                extra.append(f"{len(dev.static_routes)} static route(s)")
        gw = getattr(dev, "gateway", None)
        if gw:
            extra.append(f"gw {gw}")
        suffix = (" [" + ", ".join(extra) + "]") if extra else ""
        lines.append(f"{dev.kind} {dev.name}: {', '.join(parts) or 'no L3 addresses'}{suffix}")
    return "\n".join(lines)


def _hosts_with_ip(net: Network):
    return [d for d in net.devices.values() if isinstance(d, Host) and d.primary.ip]


def reachability(net: Network, max_pairs: int = 6) -> List[dict]:
    """Ping a representative set of host pairs; return per-pair results."""
    hosts = _hosts_with_ip(net)
    out: List[dict] = []
    pairs = list(permutations(hosts, 2))
    # Deterministic, bounded sample so generation stays fast on large topologies.
    for src, dst in pairs[:max_pairs]:
        res = net.ping(src.name, dst.primary.ip, count=4)
        if not res.get("ok"):
            out.append({"src": src.name, "dst": dst.primary.ip, "ok": False,
                        "error": res.get("error", "ping failed")})
        else:
            out.append({"src": src.name, "dst": dst.primary.ip, "ok": True,
                        "received": res["received"], "sent": res["sent"]})
    return out


def interface_problems(net: Network) -> List[str]:
    """Notable down/disconnected interfaces (the kind shown by 'show ip int brief')."""
    out: List[str] = []
    for dev in net.devices.values():
        for i in dev.interfaces.values():
            if i.ip and not i.enabled:
                out.append(f"{dev.name} {i.name} is administratively down")
            elif i.ip and i.link is None:
                out.append(f"{dev.name} {i.name} has no cable connected")
    return out


def collect_symptoms(net: Network, max_pairs: int = 6) -> dict:
    """Full structured observation of the current topology."""
    findings = tutor_mod.analyze(net)
    reach = reachability(net, max_pairs=max_pairs)
    return {
        "topology": topology_summary(net),
        "interfaces": interface_problems(net),
        "reachability": reach,
        "findings": findings,
        "any_loss": any((not r["ok"]) or r.get("received", 0) < r.get("sent", 1) for r in reach),
    }


def render_observation(sym: dict) -> str:
    """Render symptoms as the 'Observed' text block a student would report."""
    lines = [sym["topology"], "", "Observed symptoms:"]
    had = False
    for r in sym["reachability"]:
        had = True
        if not r["ok"]:
            lines.append(f"- ping from {r['src']} to {r['dst']}: cannot start ({r['error']})")
        elif r["received"] == 0:
            lines.append(f"- ping from {r['src']} to {r['dst']}: 0/{r['sent']} (100% loss)")
        elif r["received"] < r["sent"]:
            lines.append(f"- ping from {r['src']} to {r['dst']}: {r['received']}/{r['sent']} (partial)")
        else:
            lines.append(f"- ping from {r['src']} to {r['dst']}: {r['received']}/{r['sent']} OK")
    for ip in sym["interfaces"]:
        had = True
        lines.append(f"- {ip}")
    if not had:
        lines.append("- (everything appears to be forwarding)")
    return "\n".join(lines)
