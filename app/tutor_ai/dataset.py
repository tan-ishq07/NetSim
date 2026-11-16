"""Build the engine-grounded diagnosis dataset (pillar 1).

For every base lab (built-in templates + procedurally generated topologies) we
emit:
  * one "healthy" example (everything forwards, so the answer is a sanity-check
    walkthrough), and
  * one example per injected single fault, where the answer is the oracle's
    diagnosis + exact IOS fix, corroborated by the live symptoms.

Examples are chat-formatted (system/user/assistant) with a ``meta`` block for
filtering and held-out evaluation. Question/answer phrasings are varied so the
student model does not overfit a single template sentence."""
from __future__ import annotations

import random
from typing import Dict, Iterator, List

from app.engine import templates as templates_mod
from app.workspace import Workspace

from . import mutators
from .symptoms import collect_symptoms, render_observation

SYSTEM = (
    "You are NetSim Tutor, an expert Cisco networking instructor embedded in a "
    "network simulator. Diagnose problems precisely from the symptoms, explain "
    "the root cause in plain English, and give the exact Cisco IOS commands to "
    "fix it. Be accurate and concise."
)

_FAULT_QUESTIONS = [
    "Why isn't this working?",
    "My pings are failing - what's wrong and how do I fix it?",
    "Can you find the misconfiguration and tell me the fix?",
    "Troubleshoot this topology for me.",
    "What's broken here and what commands fix it?",
    "Help, connectivity is down. What did I get wrong?",
]

_OK_QUESTIONS = [
    "Does this look correctly configured?",
    "Everything pings - can you confirm the config is sound?",
    "Is there anything wrong with this topology?",
    "Review my configuration please.",
]

_FIX_INTRO = ["Fix", "To fix it, run", "Apply this on", "Corrective configuration on"]


def _answer_for_fault(fault: Dict, sym: Dict) -> str:
    intro = random.choice(_FIX_INTRO)
    fix_lines = "\n".join(fault["fix"])
    body = f"{fault['what']}.\n\n{fault['why']}\n\n{intro} {fault['device']}:\n{fix_lines}"
    # Tie the fix to a concrete verification step.
    body += "\n\nThen re-test with 'ping' (and 'show ip interface brief' / the relevant 'show' command) to confirm it recovers."
    return body


def _answer_healthy(sym: Dict) -> str:
    return (
        "No misconfiguration detected - the symptoms show end-to-end reachability. "
        "The essentials are in place: interfaces are 'no shutdown' (up/up), the IP "
        "addresses and masks match on each link, hosts have a default gateway, and "
        "routing covers the destination networks. Keep verifying with 'ping', "
        "'show ip interface brief', and 'show ip route'."
    )


def _example(question: str, observation: str, answer: str, meta: Dict) -> Dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{observation}\n\nQuestion: {question}"},
            {"role": "assistant", "content": answer},
        ],
        "meta": meta,
    }


def example_from_fault(base_spec: Dict, mutated_spec: Dict, fault: Dict,
                       source: str) -> Dict | None:
    """Apply a faulted spec, observe symptoms, and build a grounded example."""
    w = Workspace()
    res = w.apply_lab(mutated_spec)
    # Ignore config-apply errors caused intentionally by the fault; we still
    # want the observed symptoms. But a totally un-appliable spec is useless.
    sym = collect_symptoms(w.net)
    observation = render_observation(sym)
    answer = _answer_for_fault(fault, sym)
    meta = {"pillar": "diagnosis", "kind": fault["kind"], "device": fault["device"],
            "source": source, "apply_errors": len(res.get("errors", []))}
    return _example(random.choice(_FAULT_QUESTIONS), observation, answer, meta)


def examples_for_base(base_spec: Dict, source: str,
                      healthy: bool = True) -> Iterator[Dict]:
    if healthy:
        w = Workspace()
        w.apply_lab(base_spec)
        sym = collect_symptoms(w.net)
        if not sym["any_loss"]:
            yield _example(random.choice(_OK_QUESTIONS), render_observation(sym),
                           _answer_healthy(sym),
                           {"pillar": "diagnosis", "kind": "healthy", "source": source})
    for mutated_spec, fault in mutators.mutations(base_spec):
        ex = example_from_fault(base_spec, mutated_spec, fault, source)
        if ex is not None:
            yield ex


# --------------------------------------------------------------------------- #
# Procedural base topologies (more volume + addressing diversity)
# --------------------------------------------------------------------------- #

def _cfg(*lines: str) -> List[str]:
    return ["enable", "configure terminal", *lines, "end"]


def _host_cfg(ip: str, gw: str) -> List[str]:
    return _cfg("interface eth0", f"ip address {ip} 255.255.255.0", "no shutdown",
                "exit", f"ip default-gateway {gw}")


def gen_router_lan(seed: int) -> Dict:
    """One router gateway with two hosts on a LAN (gateway + addressing faults)."""
    rng = random.Random(seed)
    net = f"10.{rng.randint(1, 254)}.{rng.randint(0, 254)}"
    gw, h1, h2 = f"{net}.1", f"{net}.10", f"{net}.20"
    return {
        "devices": [
            {"kind": "router", "name": "R1", "ports": 2, "x": 300, "y": 120},
            {"kind": "host", "name": "PCA", "x": 120, "y": 60},
            {"kind": "host", "name": "PCB", "x": 120, "y": 200},
        ],
        "links": [
            {"dev_a": "PCA", "if_a": "eth0", "dev_b": "R1", "if_b": "GigabitEthernet0/0"},
            {"dev_a": "PCB", "if_a": "eth0", "dev_b": "R1", "if_b": "GigabitEthernet0/1"},
        ],
        "config": {
            "R1": _cfg("interface GigabitEthernet0/0", f"ip address {gw} 255.255.255.0",
                      "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {net}.129 255.255.255.0",
                      "no shutdown"),
            "PCA": _host_cfg(h1, gw),
            "PCB": _host_cfg(f"{net}.130", f"{net}.129"),
        },
        "checks": [{"type": "ping", "name": "A->B", "src": "PCA", "dst": h2, "expect": "success"}],
    }


def gen_two_router_static(seed: int) -> Dict:
    """Two routers, a /30 WAN link, static routes to each far LAN."""
    rng = random.Random(seed * 7 + 1)
    a, b = rng.randint(1, 200), rng.randint(1, 200)
    lanA, lanB = f"192.168.{a}", f"192.168.{b if b != a else a + 1}"
    wan = f"172.16.{rng.randint(0, 254)}"
    return {
        "devices": [
            {"kind": "router", "name": "R1", "ports": 2, "x": 280, "y": 140},
            {"kind": "router", "name": "R2", "ports": 2, "x": 560, "y": 140},
            {"kind": "host", "name": "H1", "x": 100, "y": 140},
            {"kind": "host", "name": "H2", "x": 740, "y": 140},
        ],
        "links": [
            {"dev_a": "H1", "if_a": "eth0", "dev_b": "R1", "if_b": "GigabitEthernet0/0"},
            {"dev_a": "R1", "if_a": "GigabitEthernet0/1", "dev_b": "R2", "if_b": "GigabitEthernet0/1"},
            {"dev_a": "H2", "if_a": "eth0", "dev_b": "R2", "if_b": "GigabitEthernet0/0"},
        ],
        "config": {
            "R1": _cfg("interface GigabitEthernet0/0", f"ip address {lanA}.1 255.255.255.0", "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {wan}.1 255.255.255.252", "no shutdown", "exit",
                      f"ip route {lanB}.0 255.255.255.0 {wan}.2"),
            "R2": _cfg("interface GigabitEthernet0/0", f"ip address {lanB}.1 255.255.255.0", "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {wan}.2 255.255.255.252", "no shutdown", "exit",
                      f"ip route {lanA}.0 255.255.255.0 {wan}.1"),
            "H1": _host_cfg(f"{lanA}.10", f"{lanA}.1"),
            "H2": _host_cfg(f"{lanB}.10", f"{lanB}.1"),
        },
        "checks": [{"type": "ping", "name": "H1->H2", "src": "H1", "dst": f"{lanB}.10", "expect": "success"}],
    }


def gen_ospf_pair(seed: int) -> Dict:
    rng = random.Random(seed * 13 + 5)
    a, b = rng.randint(1, 200), rng.randint(1, 200)
    lanA, lanB = f"10.{a}.0", f"10.{b if b != a else a + 1}.0"
    wan = f"10.0.{rng.randint(1, 254)}"
    return {
        "devices": [
            {"kind": "router", "name": "R1", "ports": 2, "x": 280, "y": 140},
            {"kind": "router", "name": "R2", "ports": 2, "x": 560, "y": 140},
            {"kind": "host", "name": "H1", "x": 100, "y": 140},
            {"kind": "host", "name": "H2", "x": 740, "y": 140},
        ],
        "links": [
            {"dev_a": "H1", "if_a": "eth0", "dev_b": "R1", "if_b": "GigabitEthernet0/0"},
            {"dev_a": "R1", "if_a": "GigabitEthernet0/1", "dev_b": "R2", "if_b": "GigabitEthernet0/1"},
            {"dev_a": "H2", "if_a": "eth0", "dev_b": "R2", "if_b": "GigabitEthernet0/0"},
        ],
        "config": {
            "R1": _cfg("interface GigabitEthernet0/0", f"ip address {lanA}.1 255.255.255.0", "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {wan}.1 255.255.255.252", "no shutdown", "exit",
                      "router ospf 1", f"network {lanA}.0 0.0.0.255 area 0", f"network {wan}.0 0.0.0.3 area 0"),
            "R2": _cfg("interface GigabitEthernet0/0", f"ip address {lanB}.1 255.255.255.0", "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {wan}.2 255.255.255.252", "no shutdown", "exit",
                      "router ospf 1", f"network {lanB}.0 0.0.0.255 area 0", f"network {wan}.0 0.0.0.3 area 0"),
            "H1": _host_cfg(f"{lanA}.10", f"{lanA}.1"),
            "H2": _host_cfg(f"{lanB}.10", f"{lanB}.1"),
        },
        "checks": [{"type": "ping", "name": "H1->H2", "src": "H1", "dst": f"{lanB}.10", "expect": "success"}],
    }


def gen_ebgp_pair(seed: int) -> Dict:
    rng = random.Random(seed * 17 + 3)
    asn1, asn2 = rng.randint(64512, 65000), rng.randint(64512, 65000)
    if asn1 == asn2:
        asn2 += 1
    lanA, lanB = f"203.0.{rng.randint(1, 200)}", f"198.51.{rng.randint(1, 200)}"
    wan = f"10.{rng.randint(1, 254)}.0"
    return {
        "devices": [
            {"kind": "router", "name": "R1", "ports": 2, "x": 280, "y": 140},
            {"kind": "router", "name": "R2", "ports": 2, "x": 560, "y": 140},
            {"kind": "host", "name": "H1", "x": 100, "y": 140},
            {"kind": "host", "name": "H2", "x": 740, "y": 140},
        ],
        "links": [
            {"dev_a": "H1", "if_a": "eth0", "dev_b": "R1", "if_b": "GigabitEthernet0/0"},
            {"dev_a": "R1", "if_a": "GigabitEthernet0/1", "dev_b": "R2", "if_b": "GigabitEthernet0/1"},
            {"dev_a": "H2", "if_a": "eth0", "dev_b": "R2", "if_b": "GigabitEthernet0/0"},
        ],
        "config": {
            "R1": _cfg("interface GigabitEthernet0/0", f"ip address {lanA}.1 255.255.255.0", "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {wan}.1 255.255.255.252", "no shutdown", "exit",
                      f"router bgp {asn1}", f"neighbor {wan}.2 remote-as {asn2}",
                      f"network {lanA}.0 mask 255.255.255.0"),
            "R2": _cfg("interface GigabitEthernet0/0", f"ip address {lanB}.1 255.255.255.0", "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {wan}.2 255.255.255.252", "no shutdown", "exit",
                      f"router bgp {asn2}", f"neighbor {wan}.1 remote-as {asn1}",
                      f"network {lanB}.0 mask 255.255.255.0"),
            "H1": _host_cfg(f"{lanA}.10", f"{lanA}.1"),
            "H2": _host_cfg(f"{lanB}.10", f"{lanB}.1"),
        },
        "checks": [{"type": "ping", "name": "H1->H2", "src": "H1", "dst": f"{lanB}.10", "expect": "success"}],
    }


def gen_acl_lan(seed: int) -> Dict:
    rng = random.Random(seed * 19 + 7)
    net = f"172.20.{rng.randint(1, 254)}"
    return {
        "devices": [
            {"kind": "router", "name": "R1", "ports": 2, "x": 300, "y": 120},
            {"kind": "host", "name": "PCA", "x": 120, "y": 60},
            {"kind": "host", "name": "PCB", "x": 120, "y": 200},
        ],
        "links": [
            {"dev_a": "PCA", "if_a": "eth0", "dev_b": "R1", "if_b": "GigabitEthernet0/0"},
            {"dev_a": "PCB", "if_a": "eth0", "dev_b": "R1", "if_b": "GigabitEthernet0/1"},
        ],
        "config": {
            "R1": _cfg("interface GigabitEthernet0/0", f"ip address {net}.1 255.255.255.0", "no shutdown", "exit",
                      "interface GigabitEthernet0/1", f"ip address {net}.129 255.255.255.128", "no shutdown", "exit",
                      f"access-list 100 permit ip {net}.0 0.0.0.127 any",
                      "interface GigabitEthernet0/0", "ip access-group 100 in"),
            "PCA": _host_cfg(f"{net}.10", f"{net}.1"),
            "PCB": _host_cfg(f"{net}.130", f"{net}.129"),
        },
        "checks": [{"type": "ping", "name": "A->B", "src": "PCA", "dst": f"{net}.130", "expect": "success"}],
    }


_GENERATORS = [gen_router_lan, gen_two_router_static, gen_ospf_pair,
               gen_ebgp_pair, gen_acl_lan]


def procedural_bases(count_per_gen: int = 12) -> Iterator[tuple]:
    for gen in _GENERATORS:
        for s in range(count_per_gen):
            yield gen(s), f"proc:{gen.__name__}:{s}"


def build_dataset(seed: int = 1337, count_per_gen: int = 12) -> List[Dict]:
    """Build the full pillar-1 diagnosis dataset."""
    random.seed(seed)
    out: List[Dict] = []
    for tpl in templates_mod.TEMPLATES:
        out.extend(examples_for_base(tpl["spec"], f"template:{tpl['id']}"))
    for spec, source in procedural_bases(count_per_gen):
        out.extend(examples_for_base(spec, source))
    return out
