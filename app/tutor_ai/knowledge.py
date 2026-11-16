"""Broad networking knowledge Q&A (pillar 3).

Three sources, all original / computed (no copyrighted book or courseware text):

1. ``SYLLABUS`` - a structured map of the topics every covered engineering role
   must know. It doubles as the prompt plan for the Kaggle teacher model that
   expands open-ended coverage (see ai/notebooks).
2. ``AUTHORED_QA`` - concise, originally-written concept/protocol/design/
   troubleshooting answers across the whole syllabus.
3. Procedural generators (subnetting, well-known ports, admin distances) that
   emit unlimited, exactly-correct Q&A computed from first principles.
"""
from __future__ import annotations

import ipaddress
import random
from typing import Dict, Iterator, List, Tuple

SYSTEM = (
    "You are NetSim Tutor, an expert networking instructor covering CCNA through "
    "CCIE and real-world network/security/wireless/automation engineering. "
    "Answer any networking question accurately and clearly, at the right depth "
    "for the question. If a question is ambiguous, state your assumption."
)

# --------------------------------------------------------------------------- #
# Syllabus (also the teacher-model expansion plan)
# --------------------------------------------------------------------------- #

SYLLABUS: Dict[str, List[str]] = {
    "Fundamentals": [
        "OSI model layers and PDUs", "TCP/IP model", "encapsulation/decapsulation",
        "unicast/multicast/broadcast/anycast", "bandwidth vs throughput vs latency",
        "collision vs broadcast domains", "MTU and fragmentation",
    ],
    "Ethernet & Switching": [
        "MAC addressing and the CAM table", "ARP", "VLANs and 802.1Q tagging",
        "access vs trunk ports", "native VLAN", "VTP", "DTP",
        "STP (802.1D)", "RSTP (802.1w)", "PVST+/Rapid-PVST+", "MST",
        "root bridge election", "port roles/states", "PortFast/BPDU Guard",
        "EtherChannel (PAgP/LACP)", "inter-VLAN routing / router-on-a-stick / SVIs",
        "storm control", "voice VLAN",
    ],
    "IP Addressing": [
        "IPv4 classes and CIDR", "subnetting and VLSM", "supernetting/summarization",
        "private RFC1918 ranges", "broadcast/network/usable ranges", "wildcard masks",
    ],
    "IPv6": [
        "IPv6 address format and abbreviation", "address types (GUA/LLA/ULA/multicast)",
        "EUI-64", "SLAAC", "DHCPv6", "NDP/ND", "dual-stack", "IPv6 static/OSPFv3",
    ],
    "Routing": [
        "routing table and longest-prefix match", "administrative distance", "metrics",
        "static and default routes", "floating static routes",
        "RIPv2", "EIGRP (DUAL, feasible successor)", "OSPF (LSAs, areas, DR/BDR, cost)",
        "BGP (eBGP/iBGP, path attributes, best-path)", "route redistribution",
        "route maps and prefix lists", "PBR", "FHRP (HSRP/VRRP/GLBP)",
    ],
    "Services": [
        "DHCP (DORA) and relay", "DNS records and resolution", "NAT/PAT", "NTP",
        "SNMP v2c/v3", "Syslog severities", "CDP/LLDP", "TFTP/FTP/SCP for IOS",
    ],
    "Security": [
        "AAA (RADIUS/TACACS+)", "802.1X", "port security", "DHCP snooping",
        "Dynamic ARP Inspection", "IP Source Guard", "ACLs (standard/extended/named)",
        "zone-based firewall", "stateful inspection", "IPsec (IKE/ESP/AH)",
        "site-to-site vs remote-access VPN", "SSL/TLS VPN", "IPS/IDS",
        "control-plane policing", "the CIA triad", "common attacks (spoofing, MITM, DoS)",
    ],
    "Wireless": [
        "RF fundamentals (channels, RSSI, SNR)", "802.11 standards", "SSID/BSSID/ESS",
        "association and authentication", "WPA2-PSK/Enterprise", "WPA3-SAE",
        "4-way handshake", "autonomous vs lightweight APs", "WLC and CAPWAP",
        "roaming", "WMM/QoS", "coverage vs capacity design",
    ],
    "WAN & SD-WAN": [
        "leased lines", "PPP (LCP/NCP, PAP/CHAP)", "PPPoE", "MPLS L3VPN concepts",
        "label switching (push/swap/pop)", "Segment Routing", "DMVPN",
        "SD-WAN (OMP, TLOC, BFD, app-aware routing)", "QoS (classification, marking, "
        "policing vs shaping, LLQ/CBWFQ, DSCP)",
    ],
    "Datacenter & Cloud": [
        "spine-leaf/Clos fabric", "VXLAN/EVPN", "overlay vs underlay", "VRF",
        "cloud connectivity (VPC, peering, transit gateway, direct connect)",
        "load balancing", "containers/k8s networking basics",
    ],
    "Automation": [
        "REST APIs", "RESTCONF/NETCONF", "YANG models", "JSON/XML/YAML",
        "Python for networking (netmiko/napalm)", "Ansible playbooks",
        "Jinja2 templating", "idempotency", "config drift and intent",
        "CI/CD for network config", "telemetry/streaming",
    ],
    "Troubleshooting & Design": [
        "structured troubleshooting (OSI top-down/bottom-up)", "ping/traceroute analysis",
        "show/debug methodology", "high availability and redundancy design",
        "hierarchical (core/distribution/access) design", "capacity planning",
        "naming/IPAM/documentation", "change management",
    ],
}


def syllabus_topics() -> List[Tuple[str, str]]:
    return [(domain, t) for domain, items in SYLLABUS.items() for t in items]


# --------------------------------------------------------------------------- #
# Authored Q&A (original, concise)
# --------------------------------------------------------------------------- #

AUTHORED_QA: List[Tuple[str, str]] = [
    ("What are the seven layers of the OSI model, top to bottom?",
     "Application, Presentation, Session, Transport, Network, Data Link, Physical "
     "(layers 7 down to 1). A common mnemonic is 'All People Seem To Need Data "
     "Processing'. Routers operate at L3, switches at L2, hubs at L1."),
    ("What is the difference between a collision domain and a broadcast domain?",
     "A collision domain is a segment where frames can collide; each switch port is "
     "its own collision domain. A broadcast domain is the set of devices that receive "
     "a Layer-2 broadcast; by default a VLAN/switch is one broadcast domain, and "
     "routers (or separate VLANs) break it up."),
    ("How does ARP work?",
     "To reach an IP on the local subnet a host needs the destination MAC. It "
     "broadcasts an ARP request 'who has 10.0.0.5?'; the owner replies with its MAC, "
     "which the sender caches in its ARP table. For off-subnet destinations the host "
     "ARPs for its default gateway instead."),
    ("What is the difference between an access port and a trunk port?",
     "An access port carries a single VLAN untagged and connects end devices. A trunk "
     "port carries many VLANs between switches/routers using 802.1Q tags; the native "
     "VLAN is sent untagged. Configure with 'switchport mode access/trunk'."),
    ("What problem does Spanning Tree Protocol solve and how?",
     "STP prevents Layer-2 loops (and the broadcast storms/MAC flapping they cause) in "
     "topologies with redundant links. It elects a root bridge (lowest bridge ID), then "
     "blocks redundant paths, leaving one loop-free active topology. RSTP (802.1w) "
     "converges much faster than the original 802.1D."),
    ("How is the STP root bridge elected?",
     "By lowest Bridge ID = priority (default 32768, in increments of 4096) followed by "
     "the lowest MAC address. Lower priority wins; set 'spanning-tree vlan X priority' or "
     "use 'root primary' to influence it."),
    ("What is the difference between LACP and PAgP?",
     "Both bundle physical links into one logical EtherChannel. LACP (802.3ad) is the "
     "open standard with modes active/passive; PAgP is Cisco-proprietary with modes "
     "desirable/auto. At least one side must actively negotiate (active or desirable)."),
    ("What is administrative distance and list common values?",
     "AD is the trustworthiness of a routing source when multiple protocols offer the "
     "same prefix; lower wins. Connected 0, static 1, eBGP 20, EIGRP 90, OSPF 110, "
     "RIP 120, external EIGRP 170, iBGP 200, unusable 255."),
    ("How does the router choose between two routes to the same destination?",
     "First longest-prefix match (most specific). If prefixes tie, lowest administrative "
     "distance wins. If AD also ties (same protocol), the lowest metric wins; equal-metric "
     "paths can load-balance."),
    ("Explain OSPF DR/BDR election.",
     "On multi-access segments OSPF elects a Designated Router and Backup DR to reduce "
     "adjacencies; routers fully adjacent only with DR/BDR. Highest OSPF priority wins "
     "(default 1; 0 = never DR), tie-broken by highest Router-ID. It is non-preemptive."),
    ("What are the OSPF area types?",
     "Backbone (area 0) to which all areas connect; standard areas; stub (blocks "
     "external LSAs, injects a default); totally stubby (also blocks inter-area); NSSA "
     "(stub that still allows local redistribution via type-7 LSAs)."),
    ("What is the difference between eBGP and iBGP?",
     "eBGP is between different autonomous systems (AD 20, TTL 1 by default, changes "
     "next-hop/AS-path). iBGP is within one AS (AD 200) and requires a full mesh or "
     "route reflectors/confederations because iBGP-learned routes aren't re-advertised "
     "to other iBGP peers (loop prevention)."),
    ("List the BGP best-path attributes in order.",
     "Weight (highest, Cisco-local), Local Preference (highest), locally originated, "
     "shortest AS-Path, lowest Origin (IGP<EGP<incomplete), lowest MED, eBGP over iBGP, "
     "lowest IGP metric to next-hop, then oldest/lowest Router-ID/neighbor IP."),
    ("What is HSRP and how does failover work?",
     "HSRP is a Cisco first-hop redundancy protocol: two+ routers share a virtual IP/MAC "
     "as the gateway. The highest priority becomes Active; the Standby takes over if the "
     "Active fails. Enable preempt so a recovered higher-priority router reclaims Active. "
     "VRRP is the open-standard equivalent."),
    ("What is the difference between NAT and PAT?",
     "Static/dynamic NAT maps inside addresses to outside addresses one-to-one. PAT "
     "(NAT overload) maps many inside addresses to one outside address by also "
     "translating source ports, which is how a whole LAN shares one public IP."),
    ("What is the difference between a standard and an extended ACL?",
     "Standard ACLs (1-99) match source IP only and should be placed near the "
     "destination. Extended ACLs (100-199) match source+destination, protocol, and "
     "ports, and are placed near the source. Every ACL ends with an implicit 'deny any'."),
    ("How does DHCP assign an address (DORA)?",
     "Discover (client broadcast), Offer (server proposes a lease), Request (client "
     "requests the offered address), Acknowledge (server confirms). Across subnets an "
     "'ip helper-address' relay forwards the broadcasts to the server as unicast."),
    ("What does DHCP snooping protect against and how?",
     "It stops rogue DHCP servers and starvation attacks by trusting only configured "
     "uplink ports for DHCP server replies and building a binding table (MAC/IP/VLAN/"
     "port) from leases on untrusted ports. That table also feeds DAI and IP Source Guard."),
    ("What is Dynamic ARP Inspection?",
     "DAI validates ARP packets on untrusted ports against the DHCP snooping binding "
     "table and drops spoofed ARP, preventing ARP poisoning / man-in-the-middle attacks. "
     "It requires DHCP snooping to be enabled."),
    ("Describe how IPsec protects a site-to-site VPN.",
     "IKE (Phase 1) authenticates peers and builds a secure management channel; Phase 2 "
     "negotiates IPsec SAs. ESP then encrypts and authenticates the tunneled traffic "
     "(AH authenticates only). Interesting traffic defined by a crypto ACL triggers the "
     "tunnel."),
    ("What is the WPA2 4-way handshake for?",
     "After association it derives and installs the per-session encryption keys (PTK/GTK) "
     "from the PMK without sending the passphrase over the air, and confirms both sides "
     "share the key. WPA3 replaces the PSK exchange with SAE (Dragonfly) for forward "
     "secrecy and offline-attack resistance."),
    ("What is the difference between an autonomous AP and a lightweight AP?",
     "An autonomous AP is self-contained with its own config. A lightweight AP joins a "
     "Wireless LAN Controller over CAPWAP, which centralizes configuration, RF "
     "management, and roaming; the AP forwards/encrypts per the WLC's policy."),
    ("Explain policing versus shaping in QoS.",
     "Both limit rate. Policing drops or re-marks traffic that exceeds the rate "
     "immediately (no buffering) and is typically applied inbound. Shaping buffers "
     "excess traffic and sends it later to smooth bursts, applied outbound; it adds "
     "delay but avoids drops."),
    ("What do PPP's LCP and NCP phases do, and PAP vs CHAP?",
     "LCP establishes/maintains the link and negotiates options (authentication, MRU, "
     "magic number); NCP (e.g. IPCP) negotiates Layer-3 parameters. PAP sends the "
     "password in cleartext in a two-way exchange; CHAP uses a three-way challenge/hash "
     "and never sends the password, so CHAP is preferred."),
    ("What is MPLS label switching (push/swap/pop)?",
     "Ingress LER pushes a label onto the packet; core LSRs swap labels hop-by-hop using "
     "the label (not the IP lookup) for fast forwarding; the egress (or penultimate, via "
     "PHP/implicit-null) pops the label. Labels are distributed by LDP."),
    ("What are TLOC, OMP and BFD in SD-WAN?",
     "TLOC (Transport Locator) identifies a WAN transport attachment point. OMP is the "
     "overlay control protocol that advertises routes/TLOCs/service info between edges "
     "and the controller. BFD runs per tunnel to measure loss/latency/jitter and detect "
     "failures, enabling application-aware path selection."),
    ("What is the difference between RESTCONF and NETCONF?",
     "Both configure devices using YANG data models. NETCONF uses RPCs over SSH with XML "
     "and datastores (running/candidate) and transactions; RESTCONF exposes the same YANG "
     "models over HTTP(S) using REST verbs with JSON or XML, which is simpler for web "
     "tooling."),
    ("What does idempotency mean in network automation?",
     "An operation is idempotent if running it repeatedly yields the same end state as "
     "running it once. Ansible modules and intent-based tools aim for this so re-running "
     "a playbook only changes what's drifted, rather than re-applying or duplicating "
     "config."),
    ("How would you troubleshoot 'PC1 can't reach the internet' methodically?",
     "Work the layers: confirm PC1 has a correct IP/mask/gateway/DNS (ipconfig); ping the "
     "gateway (L2/local), then a public IP (routing/NAT), then a hostname (DNS). Check the "
     "switch port/VLAN, the router's default route and 'ip nat' config, and any ACL/"
     "firewall in the path. Use traceroute to localize where it stops."),
    ("What is VLSM and why use it?",
     "Variable Length Subnet Masking lets you use different mask lengths within the same "
     "major network, so a point-to-point link can be a /30 while a user LAN is a /24. It "
     "conserves address space versus fixed-length subnetting."),
    ("What is the difference between TCP and UDP?",
     "TCP is connection-oriented and reliable: handshake, sequencing, acknowledgements, "
     "retransmission, flow and congestion control (used by HTTP, SSH). UDP is "
     "connectionless and best-effort with low overhead (used by DNS, DHCP, VoIP, "
     "streaming) where speed matters more than guaranteed delivery."),
    ("What is a VRF?",
     "Virtual Routing and Forwarding keeps multiple independent routing tables on one "
     "router, isolating customers/tenants so overlapping address space and separate "
     "reachability are possible on shared hardware. It's the basis of MPLS L3VPN."),
    ("What is VXLAN/EVPN used for?",
     "VXLAN tunnels Layer-2 over a Layer-3 (IP) underlay using a 24-bit VNI (16M "
     "segments), enabling stretched L2 and multitenancy across a spine-leaf fabric. EVPN "
     "(a BGP address family) is the control plane that distributes MAC/IP reachability, "
     "replacing flood-and-learn."),
    ("What is the purpose of a default gateway?",
     "It is the next-hop router a host sends packets to when the destination is on a "
     "different subnet. Without it a host can only reach its own subnet. On Cisco hosts "
     "in this simulator set it with 'ip default-gateway'."),
]


# --------------------------------------------------------------------------- #
# Procedural generators (unlimited, computed answers)
# --------------------------------------------------------------------------- #

def _subnet_steps(ip: ipaddress.IPv4Address, prefix: int) -> str:
    """Worked, first-principles solution for one IPv4 subnetting problem.

    Teaching the *procedure* (mask -> block size -> boundary -> broadcast ->
    host count) lets a small model reproduce the arithmetic instead of trying
    to memorise every answer, which is where it otherwise gets numbers wrong.
    """
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    mask = net.netmask
    mask_octets = [int(o) for o in str(mask).split(".")]
    ip_octets = [int(o) for o in str(ip).split(".")]
    maskbin = ".".join(format(o, "08b") for o in mask_octets)
    full = prefix // 8
    rem = prefix % 8
    hostbits = 32 - prefix
    hosts = net.num_addresses - 2
    lines = [
        f"Step 1 - Mask: /{prefix} sets the first {prefix} bits, so the mask is "
        f"{maskbin} = {mask}.",
    ]
    if rem == 0:
        lines.append(
            f"Step 2 - /{prefix} ends on an octet boundary, so the first {full} "
            f"octet(s) are the network and the rest are host bits; zero the host "
            f"octet(s) to get the network address {net.network_address}.")
    else:
        block = 2 ** (8 - rem)
        oi = full  # 0-based index of the partial ('interesting') octet
        nb = (ip_octets[oi] // block) * block
        lines.append(
            f"Step 2 - The interesting octet is octet {oi + 1} (mask value "
            f"{mask_octets[oi]}); block size = 256 - {mask_octets[oi]} = {block}.")
        lines.append(
            f"Step 3 - The largest multiple of {block} at or below {ip_octets[oi]} "
            f"is {nb}, so the network address is {net.network_address}/{prefix}.")
    lines.append(
        f"Step {len(lines) + 1} - The broadcast sets every host bit to 1: "
        f"{net.broadcast_address}.")
    lines.append(
        f"Step {len(lines) + 1} - Usable hosts = 2^(32-{prefix}) - 2 = "
        f"2^{hostbits} - 2 = {hosts}, range {net.network_address + 1} - "
        f"{net.broadcast_address - 1}.")
    lines.append(
        f"Answer - Network: {net.network_address}/{prefix}. Mask: {mask}. "
        f"Broadcast: {net.broadcast_address}. Usable hosts: {hosts}. "
        f"Usable host range: {net.network_address + 1} - "
        f"{net.broadcast_address - 1}.")
    return "\n".join(lines)


def subnetting_qa(n: int, seed: int = 0) -> Iterator[Dict]:
    rng = random.Random(seed)
    for _ in range(n):
        prefix = rng.randint(8, 30)
        ip = ipaddress.IPv4Address(rng.randint(0x0A000000, 0xDFFFFFFF))
        q = (f"For the address {ip}/{prefix}, what are the network address, subnet "
             f"mask, broadcast address, and number of usable hosts? Show your work.")
        a = _subnet_steps(ip, prefix)
        yield {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
            {"role": "assistant", "content": a},
        ], "meta": {"pillar": "knowledge", "kind": "subnetting"}}


def hosts_per_prefix_qa() -> Iterator[Dict]:
    """Deterministic 'how many hosts/subnets for /N' drills (exact by formula)."""
    for prefix in range(8, 31):
        hostbits = 32 - prefix
        hosts = (1 << hostbits) - 2
        mask = str(ipaddress.ip_network(f"0.0.0.0/{prefix}").netmask)
        wildcard = str(ipaddress.IPv4Address((1 << hostbits) - 1))
        q = (f"How many usable hosts does a /{prefix} subnet have, and what are "
             f"its subnet mask and wildcard mask?")
        a = (f"A /{prefix} leaves {hostbits} host bits, so usable hosts = "
             f"2^{hostbits} - 2 = {hosts}. Subnet mask {mask}, wildcard mask "
             f"{wildcard}.")
        yield {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
            {"role": "assistant", "content": a},
        ], "meta": {"pillar": "knowledge", "kind": "prefix"}}


_WELL_KNOWN_PORTS = {
    20: "FTP data", 21: "FTP control", 22: "SSH", 23: "Telnet", 25: "SMTP",
    53: "DNS", 67: "DHCP server", 68: "DHCP client", 69: "TFTP", 80: "HTTP",
    110: "POP3", 123: "NTP", 143: "IMAP", 161: "SNMP", 162: "SNMP trap",
    179: "BGP", 443: "HTTPS", 514: "Syslog", 636: "LDAPS", 830: "NETCONF over SSH",
}


def port_qa() -> Iterator[Dict]:
    for port, svc in _WELL_KNOWN_PORTS.items():
        proto = "UDP" if port in (53, 67, 68, 69, 123, 161, 162, 514) else "TCP"
        if port == 53:
            proto = "TCP/UDP"
        q = f"What service uses port {port}, and is it TCP or UDP?"
        a = f"Port {port} is {svc} ({proto})."
        yield {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
            {"role": "assistant", "content": a},
        ], "meta": {"pillar": "knowledge", "kind": "ports"}}


# --------------------------------------------------------------------------- #
# Configuration command tasks (real Cisco IOS / IOS XE syntax).
# Each entry is (task, exact command(s), mode). Verified against Cisco IOS XE
# command references; phrased as "how do I ..." so the model learns the exact
# command line for common real-world configuration tasks.
# --------------------------------------------------------------------------- #

COMMAND_TASKS: List[Tuple[str, str, str]] = [
    ("set a router interface IP address",
     "interface gigabitEthernet0/0\n ip address 192.168.1.1 255.255.255.0\n no shutdown",
     "interface configuration mode"),
    ("configure a default route to next-hop 203.0.113.1",
     "ip route 0.0.0.0 0.0.0.0 203.0.113.1", "global configuration mode"),
    ("create a static route to 10.2.0.0/24 via 10.1.1.2",
     "ip route 10.2.0.0 255.255.255.0 10.1.1.2", "global configuration mode"),
    ("set the default gateway on a switch or PC-style host",
     "ip default-gateway 192.168.1.1", "global configuration mode"),
    ("enable OSPF process 1 and advertise 10.0.0.0/24 in area 0",
     "router ospf 1\n network 10.0.0.0 0.0.0.255 area 0", "global configuration mode"),
    ("enable EIGRP AS 100 for network 10.0.0.0",
     "router eigrp 100\n network 10.0.0.0", "global configuration mode"),
    ("create VLAN 10 and name it SALES",
     "vlan 10\n name SALES", "global configuration mode"),
    ("make an interface an access port in VLAN 10",
     "interface gigabitEthernet0/1\n switchport mode access\n switchport access vlan 10",
     "interface configuration mode"),
    ("configure a trunk port using 802.1Q",
     "interface gigabitEthernet0/1\n switchport mode trunk\n switchport trunk encapsulation dot1q",
     "interface configuration mode"),
    ("enable port security with a maximum of 2 MACs and sticky learning",
     "interface gigabitEthernet0/1\n switchport port-security\n switchport port-security "
     "maximum 2\n switchport port-security mac-address sticky",
     "interface configuration mode"),
    ("enable DHCP snooping globally and on VLAN 10",
     "ip dhcp snooping\n ip dhcp snooping vlan 10", "global configuration mode"),
    ("enable Dynamic ARP Inspection on VLAN 10",
     "ip arp inspection vlan 10", "global configuration mode"),
    ("create a DHCP pool for 192.168.1.0/24",
     "ip dhcp pool LAN\n network 192.168.1.0 255.255.255.0\n default-router 192.168.1.1\n "
     "dns-server 8.8.8.8", "global configuration mode"),
    ("forward DHCP requests to a server at 10.0.0.10 (relay)",
     "interface gigabitEthernet0/0\n ip helper-address 10.0.0.10",
     "interface configuration mode"),
    ("configure PAT (NAT overload) out GigabitEthernet0/0",
     "interface gigabitEthernet0/0\n ip nat outside\n!\nip nat inside source list 1 "
     "interface gigabitEthernet0/0 overload", "global configuration mode"),
    ("configure HSRP group 1 with virtual IP 192.168.1.1 and priority 110",
     "interface gigabitEthernet0/0\n standby 1 ip 192.168.1.1\n standby 1 priority 110\n "
     "standby 1 preempt", "interface configuration mode"),
    ("create a standard named ACL permitting 10.0.0.0/8",
     "ip access-list standard MGMT\n permit 10.0.0.0 0.255.255.255",
     "global configuration mode"),
    ("save the running configuration",
     "copy running-config startup-config", "privileged EXEC mode"),
    ("set an enable secret password",
     "enable secret cisco123", "global configuration mode"),
    ("configure SSH access (require SSH on the VTY lines)",
     "ip domain-name lab.local\n crypto key generate rsa modulus 2048\n line vty 0 4\n "
     "transport input ssh\n login local", "global configuration mode"),
    ("set the device hostname to R1",
     "hostname R1", "global configuration mode"),
    ("configure a loopback interface with IP 1.1.1.1/32",
     "interface loopback0\n ip address 1.1.1.1 255.255.255.255", "global configuration mode"),
    ("add a description to an interface",
     "interface gigabitEthernet0/0\n description LINK_TO_CORE", "interface configuration mode"),
    ("set the OSPF router-id to 1.1.1.1",
     "router ospf 1\n router-id 1.1.1.1", "global configuration mode"),
    ("make an interface passive in OSPF process 1",
     "router ospf 1\n passive-interface gigabitEthernet0/0", "global configuration mode"),
    ("enable RIP version 2 for network 10.0.0.0",
     "router rip\n version 2\n network 10.0.0.0", "global configuration mode"),
    ("configure a BGP neighbor 203.0.113.2 in AS 65002 (local AS 65001)",
     "router bgp 65001\n neighbor 203.0.113.2 remote-as 65002", "global configuration mode"),
    ("configure a static NAT mapping from 10.0.0.5 to 203.0.113.5",
     "ip nat inside source static 10.0.0.5 203.0.113.5", "global configuration mode"),
    ("create an extended ACL permitting HTTP to host 10.0.0.5",
     "ip access-list extended WEB\n permit tcp any host 10.0.0.5 eq 80", "global configuration mode"),
    ("apply ACL MGMT inbound on an interface",
     "interface gigabitEthernet0/0\n ip access-group MGMT in", "interface configuration mode"),
    ("enable PortFast on an access port",
     "interface gigabitEthernet0/1\n spanning-tree portfast", "interface configuration mode"),
    ("make a switch the spanning-tree root for VLAN 10",
     "spanning-tree vlan 10 root primary", "global configuration mode"),
    ("bundle two interfaces into an LACP EtherChannel",
     "interface range gigabitEthernet0/1 - 2\n channel-group 1 mode active", "interface configuration mode"),
    ("configure router-on-a-stick for VLAN 10",
     "interface gigabitEthernet0/0.10\n encapsulation dot1Q 10\n ip address 192.168.10.1 255.255.255.0",
     "subinterface configuration mode"),
    ("exclude an address range from a DHCP pool",
     "ip dhcp excluded-address 192.168.1.1 192.168.1.10", "global configuration mode"),
    ("enable IPv6 routing and assign an IPv6 address to an interface",
     "ipv6 unicast-routing\n!\ninterface gigabitEthernet0/0\n ipv6 address 2001:DB8::1/64",
     "global configuration mode"),
    ("configure an IPv6 default route via 2001:DB8::2",
     "ipv6 route ::/0 2001:DB8::2", "global configuration mode"),
    ("set an NTP server at 10.0.0.1",
     "ntp server 10.0.0.1", "global configuration mode"),
    ("configure a console line password",
     "line console 0\n password cisco\n login", "global configuration mode"),
]


def command_task_qa() -> Iterator[Dict]:
    for task, cmd, mode in COMMAND_TASKS:
        a = f"In {mode}:\n{cmd}"
        for q in (
            f"What is the exact Cisco IOS command to {task}?",
            f"Which Cisco IOS commands do I use to {task}?",
        ):
            yield {"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": q},
                {"role": "assistant", "content": a},
            ], "meta": {"pillar": "knowledge", "kind": "command"}}


def _authored() -> Iterator[Dict]:
    for q, a in AUTHORED_QA:
        yield {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
            {"role": "assistant", "content": a},
        ], "meta": {"pillar": "knowledge", "kind": "concept"}}


def build_knowledge_qa(subnet_count: int = 200, seed: int = 2024) -> List[Dict]:
    out: List[Dict] = []
    out.extend(_authored())
    out.extend(port_qa())
    out.extend(command_task_qa())
    out.extend(hosts_per_prefix_qa())
    out.extend(subnetting_qa(subnet_count, seed=seed))
    return out


if __name__ == "__main__":
    qa = build_knowledge_qa()
    print(f"{len(qa)} knowledge Q&A examples")
    print(f"syllabus topics: {len(syllabus_topics())}")
