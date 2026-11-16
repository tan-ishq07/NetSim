"""Single-fault injectors over a lab spec.

Each mutator is an *oracle*: it knows exactly what it broke and the exact IOS
commands that fix it. It yields one or more ``(mutated_spec, fault)`` variants,
where ``fault`` carries the kind, a plain-English explanation, and the precise
fix. Mutators only fire when the relevant configuration is present, so the
faults they create are always grounded in the engine's behaviour and the
deterministic analyzer's findings."""
from __future__ import annotations

import copy
import re
from typing import Dict, Iterator, List, Tuple

Fault = Dict[str, object]
Spec = Dict[str, object]


def _wrap(lines: List[str]) -> List[str]:
    return ["enable", "configure terminal", *lines, "end"]


def _iter_config(spec: Spec) -> Iterator[Tuple[str, List[str]]]:
    for dev, lines in spec.get("config", {}).items():
        yield dev, lines


def _kind_of(spec: Spec, device: str) -> str:
    for d in spec.get("devices", []):
        if d["name"] == device:
            return d["kind"]
    return "device"


def _current_iface(lines: List[str], idx: int) -> str | None:
    for j in range(idx, -1, -1):
        m = re.match(r"\s*interface\s+(\S+)", lines[j], re.I)
        if m:
            return m.group(1)
    return None


# --------------------------------------------------------------------------- #
# Mutators
# --------------------------------------------------------------------------- #

def m_shutdown_iface(spec: Spec) -> Iterator[Tuple[Spec, Fault]]:
    for dev, lines in _iter_config(spec):
        for idx, ln in enumerate(lines):
            if ln.strip().lower() == "no shutdown":
                iface = _current_iface(lines, idx)
                if not iface:
                    continue
                new = copy.deepcopy(spec)
                new["config"][dev][idx] = "shutdown"
                yield new, {
                    "kind": "interface_shutdown",
                    "device": dev,
                    "what": f"{dev} interface {iface} is administratively shut down",
                    "why": (f"{iface} on {dev} is in the 'shutdown' state, so the link is "
                            f"down (up/down) and no traffic can pass through it."),
                    "fix": _wrap([f"interface {iface}", "no shutdown"]),
                }
                break  # one fault per device keeps it a single-fault example


def m_remove_gateway(spec: Spec) -> Iterator[Tuple[Spec, Fault]]:
    for dev, lines in _iter_config(spec):
        if _kind_of(spec, dev) != "host":
            continue
        for idx, ln in enumerate(lines):
            m = re.match(r"\s*ip default-gateway\s+(\S+)", ln, re.I)
            if m:
                gw = m.group(1)
                new = copy.deepcopy(spec)
                del new["config"][dev][idx]
                yield new, {
                    "kind": "missing_default_gateway",
                    "device": dev,
                    "what": f"{dev} has no default gateway configured",
                    "why": (f"Without a default gateway {dev} can reach hosts on its own "
                            f"subnet but cannot send packets to any other subnet - there is "
                            f"no next hop for off-link destinations."),
                    "fix": _wrap([f"ip default-gateway {gw}"]),
                }
                break


def m_wrong_host_ip(spec: Spec) -> Iterator[Tuple[Spec, Fault]]:
    for dev, lines in _iter_config(spec):
        if _kind_of(spec, dev) != "host":
            continue
        for idx, ln in enumerate(lines):
            m = re.match(r"\s*ip address\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)", ln, re.I)
            if m:
                ip, mask = m.group(1), m.group(2)
                octets = ip.split(".")
                bad = ".".join(octets[:2] + [str((int(octets[2]) + 50) % 256), octets[3]])
                new = copy.deepcopy(spec)
                new["config"][dev][idx] = f"ip address {bad} {mask}"
                yield new, {
                    "kind": "wrong_ip_subnet",
                    "device": dev,
                    "what": f"{dev} has IP {bad} which is in the wrong subnet",
                    "why": (f"{dev}'s address {bad} is not in the same subnet as its default "
                            f"gateway/neighbours, so ARP for the gateway fails and it cannot "
                            f"reach anything off-link. The correct address is {ip}."),
                    "fix": _wrap(["interface eth0", f"ip address {ip} {mask}"]),
                }
                break


def m_duplicate_ip(spec: Spec) -> Iterator[Tuple[Spec, Fault]]:
    host_ips: List[Tuple[str, int, str]] = []  # (device, line idx, ip)
    for dev, lines in _iter_config(spec):
        if _kind_of(spec, dev) != "host":
            continue
        for idx, ln in enumerate(lines):
            m = re.match(r"\s*ip address\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)", ln, re.I)
            if m:
                host_ips.append((dev, idx, m.group(1)))
                break
    if len(host_ips) >= 2:
        (d1, _i1, ip1) = host_ips[0]
        (d2, i2, ip2) = host_ips[1]
        if ip1 != ip2:
            new = copy.deepcopy(spec)
            mask = re.match(r"\s*ip address\s+\S+\s+(\S+)", new["config"][d2][i2], re.I).group(1)
            new["config"][d2][i2] = f"ip address {ip1} {mask}"
            yield new, {
                "kind": "duplicate_ip",
                "device": d2,
                "what": f"{d2} is configured with {ip1}, a duplicate of {d1}",
                "why": (f"Two devices ({d1} and {d2}) now share the IP {ip1}. Duplicate "
                        f"addresses cause ARP conflicts and intermittent/zero reachability. "
                        f"{d2} should use a unique address such as {ip2}."),
                "fix": _wrap(["interface eth0", f"ip address {ip2} {mask}"]),
            }


def m_ospf_area(spec: Spec) -> Iterator[Tuple[Spec, Fault]]:
    for dev, lines in _iter_config(spec):
        for idx, ln in enumerate(lines):
            m = re.match(r"\s*network\s+(\S+)\s+(\S+)\s+area\s+(\d+)", ln, re.I)
            if m:
                netw, wild, area = m.group(1), m.group(2), int(m.group(3))
                bad = area + 1 if area == 0 else 0
                new = copy.deepcopy(spec)
                new["config"][dev][idx] = f"network {netw} {wild} area {bad}"
                pid = _ospf_pid(lines)
                yield new, {
                    "kind": "ospf_area_mismatch",
                    "device": dev,
                    "what": f"{dev} put {netw} in OSPF area {bad} instead of area {area}",
                    "why": (f"OSPF neighbours must agree on the area for a shared link. {dev} "
                            f"advertises {netw} in area {bad} while the neighbour uses area "
                            f"{area}, so the adjacency never reaches FULL and routes aren't "
                            f"exchanged."),
                    "fix": _wrap([f"router ospf {pid}",
                                  f"no network {netw} {wild} area {bad}",
                                  f"network {netw} {wild} area {area}"]),
                }
                break


def _ospf_pid(lines: List[str]) -> str:
    for ln in lines:
        m = re.match(r"\s*router ospf\s+(\d+)", ln, re.I)
        if m:
            return m.group(1)
    return "1"


def m_bgp_remote_as(spec: Spec) -> Iterator[Tuple[Spec, Fault]]:
    for dev, lines in _iter_config(spec):
        for idx, ln in enumerate(lines):
            m = re.match(r"\s*neighbor\s+(\S+)\s+remote-as\s+(\d+)", ln, re.I)
            if m:
                peer, asn = m.group(1), m.group(2)
                new = copy.deepcopy(spec)
                new["config"][dev][idx] = f"neighbor {peer} remote-as 64999"
                lpid = _bgp_asn(lines)
                yield new, {
                    "kind": "bgp_remote_as_mismatch",
                    "device": dev,
                    "what": f"{dev} has the wrong remote-as (64999) for neighbor {peer}",
                    "why": (f"The configured remote-as 64999 does not match the peer's actual "
                            f"AS ({asn}), so the BGP session stays in Idle/Active and never "
                            f"reaches Established; no prefixes are exchanged."),
                    "fix": _wrap([f"router bgp {lpid}",
                                  f"no neighbor {peer} remote-as 64999",
                                  f"neighbor {peer} remote-as {asn}"]),
                }
                break


def _bgp_asn(lines: List[str]) -> str:
    for ln in lines:
        m = re.match(r"\s*router bgp\s+(\d+)", ln, re.I)
        if m:
            return m.group(1)
    return "65000"


def m_acl_remove_permit(spec: Spec) -> Iterator[Tuple[Spec, Fault]]:
    for dev, lines in _iter_config(spec):
        permit_idx = [i for i, ln in enumerate(lines)
                      if re.search(r"\bpermit\b", ln, re.I) and "access-list" in ln.lower()]
        if permit_idx:
            idx = permit_idx[0]
            original = lines[idx]
            new = copy.deepcopy(spec)
            del new["config"][dev][idx]
            yield new, {
                "kind": "acl_missing_permit",
                "device": dev,
                "what": f"{dev} is missing the ACL permit entry: '{original.strip()}'",
                "why": ("An access-list ends with an implicit 'deny any'. With the permit "
                        "entry removed, the ACL matches nothing and silently drops all the "
                        "traffic it is applied to."),
                "fix": _wrap([original.strip()]),
            }


ALL_MUTATORS = [
    m_shutdown_iface,
    m_remove_gateway,
    m_wrong_host_ip,
    m_duplicate_ip,
    m_ospf_area,
    m_bgp_remote_as,
    m_acl_remove_permit,
]


def mutations(spec: Spec, limit_per_kind: int = 2) -> Iterator[Tuple[Spec, Fault]]:
    """Yield single-fault variants of ``spec`` across all applicable mutators."""
    for mut in ALL_MUTATORS:
        count = 0
        for variant in mut(spec):
            yield variant
            count += 1
            if count >= limit_per_kind:
                break
