import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct  # noqa: E402

from app.engine import Network  # noqa: E402
from app.engine import labs, pcap, tutor  # noqa: E402
from app.engine.cli import CLISession  # noqa: E402
from app.engine.models import DhcpPool  # noqa: E402


# --------------------------------------------------------------- VLAN trunking
def _vlan_trunk_topology():
    net = Network()
    pc1, pc2, pc3 = net.add_host("PC1"), net.add_host("PC2"), net.add_host("PC3")
    sw1, sw2 = net.add_switch("SW1", ports=4), net.add_switch("SW2", ports=4)
    for pc, ip in ((pc1, "10.0.0.1"), (pc2, "10.0.0.2"), (pc3, "10.0.0.3")):
        pc.primary.set_ip(ip, "24")
        pc.primary.enabled = True
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW2", "FastEthernet0/0")
    net.connect("PC3", "eth0", "SW2", "FastEthernet0/1")
    net.connect("SW1", "FastEthernet0/3", "SW2", "FastEthernet0/3")
    sw1.interfaces["FastEthernet0/0"].vlan = 10
    sw2.interfaces["FastEthernet0/0"].vlan = 10
    sw2.interfaces["FastEthernet0/1"].vlan = 20
    sw1.interfaces["FastEthernet0/3"].mode = "trunk"
    sw2.interfaces["FastEthernet0/3"].mode = "trunk"
    return net


def test_vlan_trunk_same_vlan_pings_across_switches():
    net = _vlan_trunk_topology()
    assert net.ping("PC1", "10.0.0.2")["received"] == 1


def test_vlan_isolates_different_vlan_across_trunk():
    net = _vlan_trunk_topology()
    # PC3 is in VLAN 20, so even on the same subnet it must not be reachable.
    assert net.ping("PC1", "10.0.0.3")["received"] == 0


# ------------------------------------------------------------------------- STP
def test_stp_blocks_one_port_on_redundant_links():
    net = Network()
    net.add_switch("SW1", ports=4)
    net.add_switch("SW2", ports=4)
    net.connect("SW1", "FastEthernet0/2", "SW2", "FastEthernet0/2")
    net.connect("SW1", "FastEthernet0/3", "SW2", "FastEthernet0/3")
    net.compute_spanning_tree()
    blocking = [
        i.name
        for sw in ("SW1", "SW2")
        for i in net.devices[sw].interfaces.values()
        if i.stp_state == "blocking"
    ]
    # Exactly one of the four endpoints must block to break the loop.
    assert len(blocking) == 1


def test_stp_keeps_connectivity_on_ring():
    net = Network()
    for n in ("SW1", "SW2", "SW3"):
        net.add_switch(n, ports=4)
    pc1, pc2 = net.add_host("PC1"), net.add_host("PC2")
    pc1.primary.set_ip("10.0.0.1", "24")
    pc1.primary.enabled = True
    pc2.primary.set_ip("10.0.0.2", "24")
    pc2.primary.enabled = True
    net.connect("SW1", "FastEthernet0/2", "SW2", "FastEthernet0/2")
    net.connect("SW2", "FastEthernet0/3", "SW3", "FastEthernet0/3")
    net.connect("SW3", "FastEthernet0/2", "SW1", "FastEthernet0/3")
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW3", "FastEthernet0/0")
    net.compute_spanning_tree()
    blocking = sum(
        1
        for sw in ("SW1", "SW2", "SW3")
        for i in net.devices[sw].interfaces.values()
        if i.stp_state == "blocking"
    )
    assert blocking == 1  # ring of 3 has exactly one blocked port
    assert net.ping("PC1", "10.0.0.2")["received"] == 1


# ------------------------------------------------------------------------ DHCP
def test_dhcp_assigns_address_and_gateway():
    net = Network()
    pc1 = net.add_host("PC1")
    r1 = net.add_router("R1")
    r1.interfaces["GigabitEthernet0/0"].set_ip("10.0.0.254", "24")
    r1.interfaces["GigabitEthernet0/0"].enabled = True
    pool = DhcpPool(name="LAN", network="10.0.0.0", prefix=24, gateway="10.0.0.254")
    r1.dhcp_pools["LAN"] = pool
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    pc1.primary.enabled = True

    lease = net.dhcp_request("PC1")
    assert lease is not None
    assert lease["ip"] == "10.0.0.1"
    assert lease["gateway"] == "10.0.0.254"
    assert pc1.primary.ip == "10.0.0.1"
    # the host can now reach its gateway
    assert net.ping("PC1", "10.0.0.254")["received"] == 1


def test_dhcp_dora_visible_in_capture():
    net = Network()
    pc1 = net.add_host("PC1")
    r1 = net.add_router("R1")
    r1.interfaces["GigabitEthernet0/0"].set_ip("10.0.0.254", "24")
    r1.interfaces["GigabitEthernet0/0"].enabled = True
    r1.dhcp_pools["LAN"] = DhcpPool(name="LAN", network="10.0.0.0", prefix=24, gateway="10.0.0.254")
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    pc1.primary.enabled = True
    net.dhcp_request("PC1")
    summaries = [c.frame.summary() for c in net.last_capture]
    joined = " ".join(summaries)
    assert "DHCP Discover" in joined
    assert "DHCP Offer" in joined
    assert "DHCP Request" in joined
    assert "DHCP ACK" in joined


# ------------------------------------------------------------------------ OSPF
def _ospf_topology(enable_ospf=True):
    net = Network()
    pc1, pc2 = net.add_host("PC1"), net.add_host("PC2")
    r1, r2 = net.add_router("R1"), net.add_router("R2")
    r1.interfaces["GigabitEthernet0/0"].set_ip("10.0.0.254", "24")
    r1.interfaces["GigabitEthernet0/0"].enabled = True
    r1.interfaces["GigabitEthernet0/1"].set_ip("10.0.12.1", "24")
    r1.interfaces["GigabitEthernet0/1"].enabled = True
    r2.interfaces["GigabitEthernet0/0"].set_ip("10.0.12.2", "24")
    r2.interfaces["GigabitEthernet0/0"].enabled = True
    r2.interfaces["GigabitEthernet0/1"].set_ip("10.0.1.254", "24")
    r2.interfaces["GigabitEthernet0/1"].enabled = True
    pc1.primary.set_ip("10.0.0.1", "24")
    pc1.primary.enabled = True
    pc1.gateway = "10.0.0.254"
    pc2.primary.set_ip("10.0.1.1", "24")
    pc2.primary.enabled = True
    pc2.gateway = "10.0.1.254"
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    net.connect("R1", "GigabitEthernet0/1", "R2", "GigabitEthernet0/0")
    net.connect("PC2", "eth0", "R2", "GigabitEthernet0/1")
    if enable_ospf:
        r1.ospf_pid = 1
        r1.ospf_networks = [("10.0.0.0", 24, 0), ("10.0.12.0", 24, 0)]
        r2.ospf_pid = 1
        r2.ospf_networks = [("10.0.1.0", 24, 0), ("10.0.12.0", 24, 0)]
    return net


def test_ospf_installs_routes_and_pings():
    net = _ospf_topology(enable_ospf=True)
    net.compute_ospf()
    r1 = net.devices["R1"]
    nets = {(n, p) for (n, p, _nh, _c) in r1.ospf_routes}
    assert ("10.0.1.0", 24) in nets
    assert net.ping("PC1", "10.0.1.1")["received"] == 1


def test_without_ospf_no_inter_router_route():
    net = _ospf_topology(enable_ospf=False)
    assert net.ping("PC1", "10.0.1.1")["received"] == 0


# ------------------------------------------------------------------------- CLI
def test_cli_dhcp_and_ospf_config():
    net = _ospf_topology(enable_ospf=False)
    cli = CLISession(net.devices["R1"], net)
    for cmd in ("enable", "configure terminal", "router ospf 1",
                "network 10.0.0.0 0.0.0.255 area 0", "network 10.0.12.0 0.0.0.255 area 0", "end"):
        cli.execute(cmd)
    assert net.devices["R1"].ospf_pid == 1
    assert ("10.0.0.0", 24, 0) in net.devices["R1"].ospf_networks
    out = CLISession(net.devices["R1"], net).execute("show ip route")
    assert "O - OSPF" in out


# ------------------------------------------------------------------- pcap export
def test_pcap_has_valid_header_and_records():
    net = Network()
    pc1, pc2 = net.add_host("PC1"), net.add_host("PC2")
    net.add_switch("SW1", ports=4)
    for pc, ip in ((pc1, "10.0.0.1"), (pc2, "10.0.0.2")):
        pc.primary.set_ip(ip, "24")
        pc.primary.enabled = True
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW1", "FastEthernet0/1")
    net.ping("PC1", "10.0.0.2")
    blob = pcap.to_pcap(net.last_capture)
    # global header: magic + version + ... + linktype Ethernet(1)
    magic, vmaj, vmin = struct.unpack("!IHH", blob[:8])
    assert magic == 0xA1B2C3D4
    assert (vmaj, vmin) == (2, 4)
    linktype = struct.unpack("!I", blob[20:24])[0]
    assert linktype == 1
    # at least one packet record present and lengths consistent
    incl_len = struct.unpack("!I", blob[24 + 8:24 + 12])[0]
    assert incl_len > 14  # Ethernet header minimum


# ------------------------------------------------------------------------ tutor
def test_tutor_detects_interface_down():
    net = Network()
    pc1 = net.add_host("PC1")
    net.add_switch("SW1", ports=4)
    pc1.primary.set_ip("10.0.0.1", "24")  # IP set but interface left shut
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    findings = tutor.analyze(net)
    assert any("administratively down" in f for f in findings)
    result = tutor.explain(net, "why can't I ping?")
    assert result["source"] == "rules"
    assert result["findings"]


def test_tutor_detects_ospf_area_mismatch():
    net = _ospf_topology(enable_ospf=True)
    net.devices["R2"].ospf_networks = [("10.0.1.0", 24, 0), ("10.0.12.0", 24, 9)]
    findings = tutor.analyze(net)
    assert any("area" in f.lower() for f in findings)


# ------------------------------------------------------------------------- labs
def test_lab_grade_ping_and_route():
    net = _ospf_topology(enable_ospf=True)
    report = labs.grade_lab(net, [
        {"type": "ping", "src": "PC1", "dst": "10.0.1.1", "expect": "success", "name": "PC1 reaches PC2"},
        {"type": "route", "device": "R1", "network": "10.0.1.0/24", "expect_present": True, "name": "R1 has OSPF route"},
        {"type": "interface_up", "device": "R1", "interface": "GigabitEthernet0/1", "name": "R1 uplink up"},
    ])
    assert report["score"] == 100
    assert report["passed"] == 3


def test_lab_apply_builds_working_topology():
    from app.workspace import Workspace
    spec = {
        "devices": [
            {"kind": "host", "name": "PC1"},
            {"kind": "host", "name": "PC2"},
            {"kind": "switch", "name": "SW1", "ports": 4},
        ],
        "links": [
            {"dev_a": "PC1", "if_a": "eth0", "dev_b": "SW1", "if_b": "FastEthernet0/0"},
            {"dev_a": "PC2", "if_a": "eth0", "dev_b": "SW1", "if_b": "FastEthernet0/1"},
        ],
        "config": {
            "PC1": ["enable", "configure terminal", "interface eth0",
                    "ip address 10.0.0.1 255.255.255.0", "no shutdown", "end"],
            "PC2": ["enable", "configure terminal", "interface eth0",
                    "ip address 10.0.0.2 255.255.255.0", "no shutdown", "end"],
        },
    }
    w = Workspace()
    result = w.apply_lab(spec)
    assert result["errors"] == []
    assert w.net.ping("PC1", "10.0.0.2")["received"] == 1


def test_lab_export_roundtrip():
    from app.workspace import Workspace
    w = Workspace()
    w.load_sample()
    spec = w.export_lab()
    assert any(d["name"] == "PC1" for d in spec["devices"])
    w2 = Workspace()
    result = w2.apply_lab(spec)
    assert result["errors"] == []
    assert w2.net.ping("PC1", "192.168.1.20")["received"] == 1


def test_lab_export_preserves_positions_for_saved_topologies():
    # The browser "saved topologies" library stores export_lab() output and
    # reloads it via apply_lab(); canvas positions must survive the round-trip.
    from app.workspace import Workspace
    w = Workspace()
    w.load_sample()
    spec = w.export_lab()
    pc1 = next(d for d in spec["devices"] if d["name"] == "PC1")
    assert (pc1["x"], pc1["y"]) == (120, 120)
    w2 = Workspace()
    w2.apply_lab(spec)
    assert w2.positions["PC1"] == {"x": 120, "y": 120}
    assert w2.topology()["devices"][0]["position"]["x"] in (120, 360, 600)
