"""Phase 9: IPv6 dual-stack, EIGRP, RIP, redistribution and route-maps subset."""

from app.engine import Network
from app.engine.cli import CLISession
from app.engine.labs import grade_lab
from app.engine.tutor import analyze


def _sess(net, dev):
    return CLISession(net.devices[dev], net)


def _run(net, dev, *cmds):
    s = _sess(net, dev)
    out = [s.execute(c) for c in cmds]
    return s, out


def _three_router_line(proto):
    """PC_A -- R1 == R2 == R3 -- PC_B, all routers running `proto`.

    Edge subnets 10.1.1.0/24 (R1) and 10.3.3.0/24 (R3); transit 10.0.12/24,
    10.0.23/24.  Returns the Network."""
    net = Network()
    for r in ("R1", "R2", "R3"):
        net.add_router(r)
    pa = net.add_host("PC_A")
    pb = net.add_host("PC_B")
    net.connect("R1", "g0/1", "R2", "g0/0")
    net.connect("R2", "g0/1", "R3", "g0/0")
    net.connect("PC_A", "eth0", "R1", "g0/0")
    net.connect("PC_B", "eth0", "R3", "g0/1")

    pa.primary.set_ip("10.1.1.10", "24")
    pa.primary.enabled = True
    pa.gateway = "10.1.1.1"
    pb.primary.set_ip("10.3.3.10", "24")
    pb.primary.enabled = True
    pb.gateway = "10.3.3.1"

    if proto == "eigrp":
        head = ["router eigrp 100"]

        def nets(*cidrs):
            return [f"network {c} 0.0.0.255" for c in cidrs]
    else:
        head = ["router rip", "version 2"]

        def nets(*cidrs):
            return [f"network {c}" for c in cidrs]

    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "ip address 10.1.1.1 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.12.1 255.255.255.0", "no shutdown", "exit",
         *head, *nets("10.1.1.0", "10.0.12.0"), "end")
    _run(net, "R2", "enable", "configure terminal",
         "interface g0/0", "ip address 10.0.12.2 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.23.1 255.255.255.0", "no shutdown", "exit",
         *head, *nets("10.0.12.0", "10.0.23.0"), "end")
    _run(net, "R3", "enable", "configure terminal",
         "interface g0/0", "ip address 10.0.23.2 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.3.3.1 255.255.255.0", "no shutdown", "exit",
         *head, *nets("10.0.23.0", "10.3.3.0"), "end")
    return net


# --------------------------------------------------------------------- EIGRP
def test_eigrp_learns_remote_subnet_and_pings():
    net = _three_router_line("eigrp")
    res = net.ping("PC_A", "10.3.3.10", 4)
    assert res["received"] == 4, res
    r1 = net.devices["R1"]
    learned = {(n, p) for n, p, _nh, _m in r1.eigrp_routes}
    assert ("10.3.3.0", 24) in learned
    assert ("10.0.23.0", 24) in learned


def test_eigrp_admin_distance_in_route_table():
    net = _three_router_line("eigrp")
    net.ping("PC_A", "10.3.3.10", 1)
    out = _sess(net, "R1").execute("show ip route")
    assert "D    10.3.3.0/24 [90/" in out


def test_eigrp_neighbors_form_on_shared_subnet():
    net = _three_router_line("eigrp")
    nbrs = net.eigrp_neighbors(net.devices["R2"])
    addrs = {n["address"] for n in nbrs}
    assert "10.0.12.1" in addrs  # R1
    assert "10.0.23.2" in addrs  # R3


# ----------------------------------------------------------------------- RIP
def test_rip_learns_remote_subnet_and_pings():
    net = _three_router_line("rip")
    res = net.ping("PC_A", "10.3.3.10", 4)
    assert res["received"] == 4, res
    r1 = net.devices["R1"]
    learned = {(n, p): hops for n, p, _nh, hops in r1.rip_routes}
    assert (("10.3.3.0", 24)) in learned
    # R1 -> R2 -> R3 edge is two hops away
    assert learned[("10.3.3.0", 24)] == 2


def test_rip_admin_distance_120_loses_to_eigrp():
    # Same prefix learned via both: EIGRP (AD 90) must win over RIP (AD 120).
    net = _three_router_line("eigrp")
    # additionally enable RIP everywhere for the same nets
    for r, edge in (("R1", "10.1.1.0"), ("R3", "10.3.3.0")):
        _run(net, r, "enable", "configure terminal", "router rip", "version 2",
             "network 10.0.12.0", "network 10.0.23.0", f"network {edge}", "end")
    _run(net, "R2", "enable", "configure terminal", "router rip", "version 2",
         "network 10.0.12.0", "network 10.0.23.0", "end")
    net.ping("PC_A", "10.3.3.10", 1)
    egress, _nh = net._route_lookup(net.devices["R1"], "10.3.3.10")
    # EIGRP route is preferred; both protocols point out g0/1, but ensure a
    # route exists and AD selection is exercised via the table render.
    out = _sess(net, "R1").execute("show ip route")
    assert "D    10.3.3.0/24" in out
    assert egress is not None


# -------------------------------------------------------------- redistribution
def test_redistribute_static_into_eigrp():
    net = _three_router_line("eigrp")
    # R3 gets a static route to an external prefix and redistributes it.
    _run(net, "R3", "enable", "configure terminal",
         "ip route 172.16.9.0 255.255.255.0 10.0.23.1",
         "router eigrp 100", "redistribute static", "end")
    net.ping("PC_A", "10.3.3.10", 1)  # trigger compute
    r1 = net.devices["R1"]
    learned = {(n, p) for n, p, _nh, _m in r1.eigrp_routes}
    assert ("172.16.9.0", 24) in learned


def test_redistribute_connected_into_rip():
    net = _three_router_line("rip")
    # R3 has an extra LAN not in any RIP network statement; redistribute it.
    _run(net, "R3", "enable", "configure terminal",
         "interface g0/1", "ip address 10.3.3.1 255.255.255.0", "exit",
         "router rip", "redistribute connected", "end")
    # add a loopback-like extra interface address on R3 via a 3rd port
    r3 = net.devices["R3"]
    r3.add_interface("GigabitEthernet0/2")
    _run(net, "R3", "enable", "configure terminal",
         "interface g0/2", "ip address 192.168.50.1 255.255.255.0", "no shutdown", "exit", "end")
    # g0/2 has no link/peer so is_up is False -> not redistributed; bring a peer up
    net.add_host("PC_C")
    net.connect("PC_C", "eth0", "R3", "g0/2")
    netc = net.devices["PC_C"]
    netc.primary.set_ip("192.168.50.10", "24")
    netc.primary.enabled = True
    net.ping("PC_A", "10.3.3.10", 1)  # trigger compute
    r1 = net.devices["R1"]
    learned = {(n, p) for n, p, _nh, _m in r1.rip_routes}
    assert ("192.168.50.0", 24) in learned


# ----------------------------------------------------------------------- IPv6
def _dual_stack_pair():
    """PC6_A -- R1 == R2 -- PC6_B with IPv6 + static v6 routes."""
    net = Network()
    net.add_router("R1")
    net.add_router("R2")
    net.add_host("PC6_A")
    net.add_host("PC6_B")
    net.connect("R1", "g0/1", "R2", "g0/0")
    net.connect("PC6_A", "eth0", "R1", "g0/0")
    net.connect("PC6_B", "eth0", "R2", "g0/1")

    _run(net, "R1", "enable", "configure terminal", "ipv6 unicast-routing",
         "interface g0/0", "ipv6 address 2001:db8:1::1/64", "no shutdown", "exit",
         "interface g0/1", "ipv6 address 2001:db8:12::1/64", "no shutdown", "exit",
         "ipv6 route 2001:db8:2::/64 2001:db8:12::2", "end")
    _run(net, "R2", "enable", "configure terminal", "ipv6 unicast-routing",
         "interface g0/0", "ipv6 address 2001:db8:12::2/64", "no shutdown", "exit",
         "interface g0/1", "ipv6 address 2001:db8:2::1/64", "no shutdown", "exit",
         "ipv6 route 2001:db8:1::/64 2001:db8:12::1", "end")
    _run(net, "PC6_A", "enable", "configure terminal",
         "interface eth0", "ipv6 address 2001:db8:1::10/64", "no shutdown", "exit",
         "ipv6 default-gateway 2001:db8:1::1", "end")
    _run(net, "PC6_B", "enable", "configure terminal",
         "interface eth0", "ipv6 address 2001:db8:2::10/64", "no shutdown", "exit",
         "ipv6 default-gateway 2001:db8:2::1", "end")
    return net


def test_ipv6_static_routing_reachable():
    net = _dual_stack_pair()
    res = net.ping6("PC6_A", "2001:db8:2::10", 4)
    assert res["received"] == 4, res


def test_ipv6_unreachable_when_route_missing():
    net = _dual_stack_pair()
    # remove R1's static v6 route to PC6_B's subnet
    net.devices["R1"].static_routes6 = []
    res = net.ping6("PC6_A", "2001:db8:2::10", 4)
    assert res["received"] == 0, res


def test_ipv6_link_down_breaks_reachability():
    net = _dual_stack_pair()
    # shut R2's far interface -> PC6_B unreachable
    _run(net, "R2", "enable", "configure terminal", "interface g0/1", "shutdown", "end")
    res = net.ping6("PC6_A", "2001:db8:2::10", 4)
    assert res["received"] == 0, res


def test_ipv6_show_route_and_brief():
    net = _dual_stack_pair()
    rt = _sess(net, "R1").execute("show ipv6 route")
    assert "2001:db8:1::/64" in rt  # connected
    assert "S   2001:db8:2::/64" in rt  # static
    brief = _sess(net, "R1").execute("show ipv6 interface brief")
    assert "2001:db8:1::1/64" in brief


# ------------------------------------------------------------- config roundtrip
def test_phase9_config_roundtrip():
    net = _three_router_line("eigrp")
    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "ipv6 address 2001:db8:aa::1/64", "exit",
         "ipv6 route 2001:db8:bb::/64 2001:db8:aa::2",
         "router eigrp 100", "redistribute connected", "end")
    r1 = net.devices["R1"]
    cmds = _sess(net, "R1")._running_config_lines()

    # replay onto a fresh, identically-cabled topology
    net2 = _three_router_line("eigrp")
    # wipe R1 config we will rebuild (interfaces remain cabled)
    r1b = net2.devices["R1"]
    r1b.eigrp_networks = []
    r1b.static_routes6 = []
    r1b.redistribute = {}
    for i in r1b.interfaces.values():
        i.ipv6 = None
        i.ipv6_prefix = None
    s = _sess(net2, "R1")
    for c in cmds:
        s.execute(c)
    assert r1b.eigrp_asn == r1.eigrp_asn
    assert r1b.interfaces["GigabitEthernet0/0"].ipv6 == "2001:db8:aa::1"
    assert ("2001:db8:bb::", 64, "2001:db8:aa::2") in r1b.static_routes6
    assert "connected" in r1b.redistribute.get("eigrp", set())


# ----------------------------------------------------------------- tutor checks
def test_tutor_flags_eigrp_as_mismatch():
    net = _three_router_line("eigrp")
    # break R3's AS so it differs from R2 on the shared 10.0.23.0/24 link
    net.devices["R3"].eigrp_asn = 200
    msgs = analyze(net)
    assert any("different ASes" in m and "EIGRP" in m for m in msgs), msgs


def test_tutor_flags_ipv6_missing_gateway():
    net = _dual_stack_pair()
    net.devices["PC6_A"].gateway6 = None
    msgs = analyze(net)
    assert any("ipv6 default-gateway" in m for m in msgs), msgs


# -------------------------------------------------------------- lab autograding
def test_lab_grade_eigrp_route_and_ping6():
    net = _three_router_line("eigrp")
    # add a dual-stack island so ping6 has something to verify
    checks = [
        {"type": "ping", "name": "v4 end-to-end", "src": "PC_A", "dst": "10.3.3.10"},
        {"type": "route", "name": "EIGRP route on R1", "device": "R1",
         "network": "10.3.3.0/24", "expect_present": True},
    ]
    report = grade_lab(net, checks)
    assert report["score"] == 100, report

    net6 = _dual_stack_pair()
    report6 = grade_lab(net6, [
        {"type": "ping6", "name": "v6 end-to-end", "src": "PC6_A", "dst": "2001:db8:2::10"},
    ])
    assert report6["score"] == 100, report6
