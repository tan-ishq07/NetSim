"""Scale + complex-topology regression tests.

These build larger, CCIE-style topologies (spine/leaf OSPF fabric, multi-area
OSPF, an eBGP chain across many ASes, and a looped L2 core) and assert real
end-to-end reachability + protocol route install. They also guard the OSPF
convergence path that was optimised from O(R^2 * I^2) to near-linear adjacency
building, so a regression there would show up as a timeout on the larger fabric.
"""
import itertools

from app.engine.simulator import Network

_ctr = itertools.count(0)


def _psub():
    k = next(_ctr)
    return f"10.{(k >> 8) & 255}.{k & 255}"


def _gi(n):
    return f"GigabitEthernet0/{n}"


def _up(iface, ip):
    iface.set_ip(ip, "24")
    iface.enabled = True


def build_clos(spines, leaves):
    """2-tier Clos OSPF fabric (single area 0) with one host per leaf."""
    net = Network()
    for s in range(spines):
        net.add_router(f"SP{s}", ports=leaves).ospf_pid = 1
    for leaf in range(leaves):
        net.add_router(f"LF{leaf}", ports=spines + 1).ospf_pid = 1
        net.add_host(f"H{leaf}")
    for leaf in range(leaves):
        for s in range(spines):
            sub = _psub()
            _up(net.devices[f"LF{leaf}"].interfaces[_gi(s)], f"{sub}.1")
            _up(net.devices[f"SP{s}"].interfaces[_gi(leaf)], f"{sub}.2")
            net.connect(f"LF{leaf}", _gi(s), f"SP{s}", _gi(leaf))
            net.devices[f"LF{leaf}"].ospf_networks.append((f"{sub}.0", 24, 0))
            net.devices[f"SP{s}"].ospf_networks.append((f"{sub}.0", 24, 0))
    for leaf in range(leaves):
        sub = f"172.16.{leaf}"
        hp = _gi(spines)
        _up(net.devices[f"LF{leaf}"].interfaces[hp], f"{sub}.254")
        net.connect(f"LF{leaf}", hp, f"H{leaf}", "eth0")
        host = net.devices[f"H{leaf}"]
        _up(host.primary, f"{sub}.1")
        host.gateway = f"{sub}.254"
        net.devices[f"LF{leaf}"].ospf_networks.append((f"{sub}.0", 24, 0))
    return net


def test_clos_fabric_full_reachability():
    net = build_clos(4, 8)            # 12 routers + 8 hosts, 32 fabric links
    net.compute_ospf()
    for dst in (1, 4, 7):
        assert net.ping("H0", f"172.16.{dst}.1", 1)["received"] == 1
    sp0 = net.devices["SP0"]
    learned = {(n, p) for (n, p, _nh, _c) in sp0.ospf_routes}
    assert ("172.16.7.0", 24) in learned


def test_large_clos_converges_and_pings():
    # 6 spines x 24 leaves = 30 routers, 144 fabric links; would be slow with
    # the old O(R^2*I^2) adjacency build, must stay fast / correct now.
    net = build_clos(6, 24)
    net.compute_ospf()
    assert net.ping("H0", "172.16.23.1", 1)["received"] == 1


def test_multi_area_ospf_cross_area_reachability():
    net = Network()
    core = net.add_router("CORE", ports=3)
    core.ospf_pid = 1
    endpoints = []
    for a in range(1, 4):
        abr = net.add_router(f"ABR{a}", ports=2)
        abr.ospf_pid = 1
        s0 = _psub()
        _up(core.interfaces[_gi(a - 1)], f"{s0}.1")
        _up(abr.interfaces[_gi(0)], f"{s0}.2")
        net.connect("CORE", _gi(a - 1), f"ABR{a}", _gi(0))
        core.ospf_networks.append((f"{s0}.0", 24, 0))
        abr.ospf_networks.append((f"{s0}.0", 24, 0))   # backbone area 0
        ir = net.add_router(f"A{a}R", ports=2)
        ir.ospf_pid = 1
        s1 = _psub()
        _up(abr.interfaces[_gi(1)], f"{s1}.1")
        _up(ir.interfaces[_gi(0)], f"{s1}.2")
        net.connect(f"ABR{a}", _gi(1), f"A{a}R", _gi(0))
        abr.ospf_networks.append((f"{s1}.0", 24, a))
        ir.ospf_networks.append((f"{s1}.0", 24, a))
        hs = f"172.20.{a}"
        host = net.add_host(f"AH{a}")
        _up(ir.interfaces[_gi(1)], f"{hs}.254")
        net.connect(f"A{a}R", _gi(1), host.name, "eth0")
        _up(host.primary, f"{hs}.1")
        host.gateway = f"{hs}.254"
        ir.ospf_networks.append((f"{hs}.0", 24, a))
        endpoints.append((host.name, f"{hs}.1"))
    net.compute_ospf()
    # host in area 1 reaches host in area 3 (area1 -> area0 -> area3)
    assert net.ping(endpoints[0][0], endpoints[2][1], 1)["received"] == 1


def test_ebgp_chain_propagates_across_many_as():
    num_as = 8
    net = Network()
    rs = []
    for a in range(num_as):
        r = net.add_router(f"AS{a}", ports=3)
        r.bgp_asn = 65000 + a
        rs.append(r)
    for a in range(num_as - 1):
        sub = _psub()
        _up(rs[a].interfaces[_gi(1)], f"{sub}.1")
        _up(rs[a + 1].interfaces[_gi(0)], f"{sub}.2")
        net.connect(f"AS{a}", _gi(1), f"AS{a+1}", _gi(0))
        rs[a].bgp_neighbors.append({"ip": f"{sub}.2", "remote_as": 65000 + a + 1})
        rs[a + 1].bgp_neighbors.append({"ip": f"{sub}.1", "remote_as": 65000 + a})

    def host(a, idx, third):
        sub = f"172.30.{third}"
        h = net.add_host(f"BH{idx}")
        _up(rs[a].interfaces[_gi(2)], f"{sub}.254")
        net.connect(f"AS{a}", _gi(2), h.name, "eth0")
        _up(h.primary, f"{sub}.1")
        h.gateway = f"{sub}.254"
        rs[a].bgp_networks.append((f"{sub}.0", 24))
        return h.name, f"{sub}.1"

    h0 = host(0, 0, 0)
    hN = host(num_as - 1, 1, 1)
    assert net.ping(h0[0], hN[1], 1)["received"] == 1
    learned = {(n, p): path for (n, p, _nh, path) in rs[0].bgp_routes}
    assert len(learned[("172.30.1.0", 24)]) == num_as - 1   # AS-path grows per hop


def test_looped_l2_core_stp_breaks_loops_and_forwards():
    n = 12
    net = Network()
    for s in range(n):
        net.add_switch(f"SW{s}", ports=8)
    for s in range(n):
        net.connect(f"SW{s}", "FastEthernet0/2", f"SW{(s+1)%n}", "FastEthernet0/3")
    for s in range(0, n, 3):
        net.connect(f"SW{s}", "FastEthernet0/4", f"SW{(s+2)%n}", "FastEthernet0/5")
    h1 = net.add_host("LH1")
    h2 = net.add_host("LH2")
    net.connect("SW0", "FastEthernet0/0", "LH1", "eth0")
    net.connect(f"SW{n//2}", "FastEthernet0/0", "LH2", "eth0")
    _up(h1.primary, "192.168.0.1")
    _up(h2.primary, "192.168.0.2")
    net.compute_spanning_tree()
    blocked = sum(
        1 for d in net.devices.values() for i in d.interfaces.values()
        if getattr(i, "stp_state", None) in ("BLK", "Altn", "blocking")
    )
    assert blocked > 0                                   # loops were broken
    assert net.ping("LH1", "192.168.0.2", 1)["received"] == 1
