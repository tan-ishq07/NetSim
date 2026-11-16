"""Phase 7: ACLs, NAT/PAT, traceroute and CDP/LLDP neighbour discovery."""

from app.engine import Network
from app.engine.cli import CLISession
from app.workspace import Workspace


def _sess(net, dev):
    return CLISession(net.devices[dev], net)


def _run(net, dev, *cmds):
    s = _sess(net, dev)
    out = [s.execute(c) for c in cmds]
    return s, out


def _routed_pair():
    """PC1 -- R1 -- PC2 across two /24 subnets, R1 configured and up."""
    net = Network()
    pc1 = net.add_host("PC1")
    pc2 = net.add_host("PC2")
    net.add_router("R1")
    pc1.primary.set_ip("10.0.0.10", "24")
    pc1.primary.enabled = True
    pc1.gateway = "10.0.0.1"
    pc2.primary.set_ip("10.0.1.10", "24")
    pc2.primary.enabled = True
    pc2.gateway = "10.0.1.1"
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    net.connect("PC2", "eth0", "R1", "GigabitEthernet0/1")
    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "ip address 10.0.0.1 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.1.1 255.255.255.0", "no shutdown", "end")
    return net


# --------------------------------------------------------------------- ACLs
def test_extended_acl_denies_icmp_then_permits():
    net = _routed_pair()
    assert net.ping("PC1", "10.0.1.10", 4)["received"] == 4

    _run(net, "R1", "enable", "configure terminal",
         "access-list 100 deny icmp host 10.0.0.10 host 10.0.1.10",
         "access-list 100 permit ip any any",
         "interface g0/0", "ip access-group 100 in", "end")
    assert net.ping("PC1", "10.0.1.10", 4)["received"] == 0


def test_standard_named_acl_blocks_source_outbound():
    net = _routed_pair()
    _run(net, "R1", "enable", "configure terminal",
         "ip access-list standard BLOCK10",
         "deny 10.0.0.10 0.0.0.0",
         "permit any", "exit",
         "interface g0/1", "ip access-group BLOCK10 out", "end")
    assert net.ping("PC1", "10.0.1.10", 4)["received"] == 0


def test_acl_implicit_deny():
    net = _routed_pair()
    # An ACL that only permits a different host => implicit deny drops PC1.
    _run(net, "R1", "enable", "configure terminal",
         "access-list 1 permit host 10.0.0.99",
         "interface g0/0", "ip access-group 1 in", "end")
    assert net.ping("PC1", "10.0.1.10", 4)["received"] == 0


def test_show_ip_access_lists():
    net = _routed_pair()
    s, _ = _run(net, "R1", "enable", "configure terminal",
                "access-list 10 permit 192.168.1.0 0.0.0.255", "end")
    out = s.execute("show ip access-lists")
    assert "Standard IP access list 10" in out
    assert "permit 192.168.1.0 0.0.0.255" in out


# ---------------------------------------------------------------------- NAT
def _inside_outside():
    net = Network()
    pc1 = net.add_host("PC1")
    srv = net.add_host("SRV")
    net.add_router("R1")
    pc1.primary.set_ip("10.0.0.10", "24")
    pc1.primary.enabled = True
    pc1.gateway = "10.0.0.1"
    srv.primary.set_ip("200.0.0.2", "24")
    srv.primary.enabled = True
    srv.gateway = "200.0.0.1"
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    net.connect("SRV", "eth0", "R1", "GigabitEthernet0/1")
    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "ip address 10.0.0.1 255.255.255.0", "ip nat inside", "no shutdown", "exit",
         "interface g0/1", "ip address 200.0.0.1 255.255.255.0", "ip nat outside", "no shutdown", "end")
    return net


def test_pat_overload_translates_and_returns():
    net = _inside_outside()
    _run(net, "R1", "enable", "configure terminal",
         "access-list 1 permit 10.0.0.0 0.0.0.255",
         "ip nat inside source list 1 interface g0/1 overload", "end")
    res = net.ping("PC1", "200.0.0.2", 4)
    assert res["received"] == 4
    table = net.devices["R1"].nat_table
    assert any(t["inside_local"] == "10.0.0.10" and t["inside_global"] == "200.0.0.1" for t in table)


def test_static_nat():
    net = _inside_outside()
    _run(net, "R1", "enable", "configure terminal",
         "ip nat inside source static 10.0.0.10 200.0.0.9", "end")
    # SRV must be able to route back to the global address (same subnet here)
    res = net.ping("PC1", "200.0.0.2", 4)
    assert res["received"] == 4
    assert any(t["inside_global"] == "200.0.0.9" for t in net.devices["R1"].nat_table)


def test_dynamic_pool_nat():
    net = _inside_outside()
    _run(net, "R1", "enable", "configure terminal",
         "access-list 1 permit 10.0.0.0 0.0.0.255",
         "ip nat pool POOL 200.0.0.50 200.0.0.60 prefix-length 24",
         "ip nat inside source list 1 pool POOL", "end")
    res = net.ping("PC1", "200.0.0.2", 4)
    assert res["received"] == 4
    assert net.devices["R1"].nat_pool_map.get("10.0.0.10") == "200.0.0.50"


# --------------------------------------------------------------- traceroute
def test_traceroute_lists_hops():
    net = _routed_pair()
    res = net.traceroute("PC1", "10.0.1.10")
    assert res["reached"] is True
    hops = res["hops"]
    assert hops[0]["hop"] == "10.0.0.1"   # R1 ingress replies Time Exceeded
    assert hops[-1]["hop"] == "10.0.1.10"  # destination replies


def test_traceroute_same_subnet_single_hop():
    net = Network()
    pc1 = net.add_host("PC1")
    pc2 = net.add_host("PC2")
    net.add_switch("SW1")
    pc1.primary.set_ip("192.168.1.10", "24")
    pc1.primary.enabled = True
    pc2.primary.set_ip("192.168.1.20", "24")
    pc2.primary.enabled = True
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW1", "FastEthernet0/1")
    res = net.traceroute("PC1", "192.168.1.20")
    assert res["reached"] is True
    assert res["hops"][0]["hop"] == "192.168.1.20"


# --------------------------------------------------------------- CDP / LLDP
def test_cdp_neighbors():
    net = _routed_pair()
    s = _sess(net, "R1")
    out = s.execute("show cdp neighbors")
    assert "PC1" in out and "PC2" in out
    assert "GigabitEthernet0/0" in out


def test_lldp_neighbors():
    net = _routed_pair()
    s = _sess(net, "R1")
    out = s.execute("show lldp neighbors")
    assert "PC1" in out and "eth0" in out


# --------------------------------------------------- config round-trip (lab)
def test_acl_nat_round_trip_via_lab():
    net = _inside_outside()
    _run(net, "R1", "enable", "configure terminal",
         "access-list 1 permit 10.0.0.0 0.0.0.255",
         "ip nat inside source list 1 interface g0/1 overload",
         "access-list 100 permit ip any any",
         "interface g0/0", "ip access-group 100 in", "end")

    ws = Workspace()
    ws.net = net
    spec = ws.export_lab()
    ws2 = Workspace()
    ws2.apply_lab(spec)
    r1 = ws2.net.devices["R1"]
    assert r1.acls["1"]["entries"][0].text() == "permit 10.0.0.0 0.0.0.255"
    assert r1.acls["100"]["kind"] == "extended"
    assert r1.nat_dynamic and r1.nat_dynamic[0]["overload"] is True
    assert r1.get_interface("GigabitEthernet0/0").acl_in == "100"
    assert r1.get_interface("GigabitEthernet0/0").nat_role == "inside"
    assert r1.get_interface("GigabitEthernet0/1").nat_role == "outside"
    # behaviour survives the round-trip
    assert ws2.net.ping("PC1", "200.0.0.2", 4)["received"] == 4
