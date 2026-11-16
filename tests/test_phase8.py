"""Phase 8: BGP, FHRP (HSRP/VRRP), EtherChannel/LACP and RSTP."""

from app.engine import Network
from app.engine.cli import CLISession


def _sess(net, dev):
    return CLISession(net.devices[dev], net)


def _run(net, dev, *cmds):
    s = _sess(net, dev)
    out = [s.execute(c) for c in cmds]
    return s, out


# --------------------------------------------------------------------- BGP
def _bgp_triangle():
    """PC_A -- R1(AS65001) == R2(AS65002) == R3(AS65003) -- PC_B.

    R1 originates 10.1.1.0/24, R3 originates 10.3.3.0/24; R2 is transit."""
    net = Network()
    for r in ("R1", "R2", "R3"):
        net.add_router(r)
    pa = net.add_host("PC_A")
    pb = net.add_host("PC_B")

    # transit links
    net.connect("R1", "g0/1", "R2", "g0/0")  # 10.0.12.0/24
    net.connect("R2", "g0/1", "R3", "g0/0")  # 10.0.23.0/24
    # edge subnets
    net.connect("PC_A", "eth0", "R1", "g0/0")  # 10.1.1.0/24
    net.connect("PC_B", "eth0", "R3", "g0/1")  # 10.3.3.0/24

    pa.primary.set_ip("10.1.1.10", "24")
    pa.primary.enabled = True
    pa.gateway = "10.1.1.1"
    pb.primary.set_ip("10.3.3.10", "24")
    pb.primary.enabled = True
    pb.gateway = "10.3.3.1"

    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "ip address 10.1.1.1 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.12.1 255.255.255.0", "no shutdown", "exit",
         "router bgp 65001",
         "neighbor 10.0.12.2 remote-as 65002",
         "network 10.1.1.0 mask 255.255.255.0", "end")
    _run(net, "R2", "enable", "configure terminal",
         "interface g0/0", "ip address 10.0.12.2 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.23.1 255.255.255.0", "no shutdown", "exit",
         "router bgp 65002",
         "neighbor 10.0.12.1 remote-as 65001",
         "neighbor 10.0.23.2 remote-as 65003", "end")
    _run(net, "R3", "enable", "configure terminal",
         "interface g0/0", "ip address 10.0.23.2 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.3.3.1 255.255.255.0", "no shutdown", "exit",
         "router bgp 65003",
         "neighbor 10.0.23.1 remote-as 65002",
         "network 10.3.3.0 mask 255.255.255.0", "end")
    return net


def test_bgp_multihop_propagation_and_ping():
    net = _bgp_triangle()
    # end-to-end reachability across three ASes (also drives BGP convergence)
    assert net.ping("PC_A", "10.3.3.10", 4)["received"] == 4
    r3 = net.devices["R3"]
    # R3 learns R1's prefix with AS-path [65002, 65001]
    learned = {f"{n}/{p}": ap for n, p, _nh, ap in r3.bgp_routes}
    assert "10.1.1.0/24" in learned
    assert learned["10.1.1.0/24"] == [65002, 65001]


def test_bgp_summary_established():
    net = _bgp_triangle()
    out = _sess(net, "R2").execute("show ip bgp summary")
    assert "65001" in out and "65003" in out
    # both neighbours established => prefix counts (digits), not "Idle"
    assert "Idle" not in out


def test_bgp_broken_session_no_route():
    """Drop R2->R3 peering: R3 must no longer learn R1's prefix."""
    net = _bgp_triangle()
    r2 = net.devices["R2"]
    r2.bgp_neighbors = [n for n in r2.bgp_neighbors if n["ip"] != "10.0.23.2"]
    assert net.ping("PC_A", "10.3.3.10", 4)["received"] == 0


# -------------------------------------------------------------- HSRP/VRRP
def _hsrp_pair(priority1=110, priority2=100):
    """PC -- SW -- {R1, R2} all on 10.0.0.0/24 with VIP 10.0.0.254."""
    net = Network()
    pc = net.add_host("PC")
    net.add_switch("SW")
    net.add_router("R1")
    net.add_router("R2")
    net.connect("PC", "eth0", "SW", "FastEthernet0/0")
    net.connect("R1", "g0/0", "SW", "FastEthernet0/1")
    net.connect("R2", "g0/0", "SW", "FastEthernet0/2")
    pc.primary.set_ip("10.0.0.10", "24")
    pc.primary.enabled = True
    pc.gateway = "10.0.0.254"
    _run(net, "R1", "enable", "configure terminal", "interface g0/0",
         "ip address 10.0.0.1 255.255.255.0",
         f"standby 1 priority {priority1}", "standby 1 ip 10.0.0.254",
         "standby 1 preempt", "no shutdown", "end")
    _run(net, "R2", "enable", "configure terminal", "interface g0/0",
         "ip address 10.0.0.2 255.255.255.0",
         f"standby 1 priority {priority2}", "standby 1 ip 10.0.0.254",
         "no shutdown", "end")
    return net


def test_hsrp_active_answers_virtual_ip():
    net = _hsrp_pair()
    assert net.ping("PC", "10.0.0.254", 4)["received"] == 4
    r1 = net.devices["R1"]
    r2 = net.devices["R2"]
    assert net.standby_state(r1)[0]["state"] == "Active"
    assert net.standby_state(r2)[0]["state"] == "Standby"


def test_hsrp_failover_to_standby():
    net = _hsrp_pair()
    assert net.ping("PC", "10.0.0.254", 1)["received"] == 1
    # the Active router's LAN interface fails
    _run(net, "R1", "enable", "configure terminal", "interface g0/0", "shutdown", "end")
    assert net.standby_state(net.devices["R2"])[0]["state"] == "Active"
    # the virtual IP stays reachable via the new Active (constant vMAC)
    assert net.ping("PC", "10.0.0.254", 4)["received"] == 4


def test_vrrp_show_standby():
    net = Network()
    net.add_router("R1")
    s, _ = _run(net, "R1", "enable", "configure terminal", "interface g0/0",
                "ip address 10.0.0.1 255.255.255.0",
                "vrrp 5 ip 10.0.0.254", "no shutdown", "end")
    out = s.execute("show standby")
    assert "Group 5 (VRRP)" in out
    assert "10.0.0.254" in out


# ------------------------------------------------------------ EtherChannel
def _dual_link_switches():
    """SW1 == SW2 over two parallel links; PC1/PC2 hang off each switch."""
    net = Network()
    net.add_switch("SW1")
    net.add_switch("SW2")
    pc1 = net.add_host("PC1")
    pc2 = net.add_host("PC2")
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW2", "FastEthernet0/0")
    net.connect("SW1", "FastEthernet0/1", "SW2", "FastEthernet0/1")
    net.connect("SW1", "FastEthernet0/2", "SW2", "FastEthernet0/2")
    pc1.primary.set_ip("10.0.0.10", "24")
    pc1.primary.enabled = True
    pc2.primary.set_ip("10.0.0.20", "24")
    pc2.primary.enabled = True
    return net


def _inter_switch_blocking(net):
    count = 0
    for sw in ("SW1", "SW2"):
        for i in net.devices[sw].interfaces.values():
            peer = i.peer()
            if i.is_up and peer and peer.device.kind == "switch" and i.stp_state == "blocking":
                count += 1
    return count


def test_stp_blocks_one_of_parallel_links():
    net = _dual_link_switches()
    net.compute_spanning_tree()
    assert _inter_switch_blocking(net) == 1
    assert net.ping("PC1", "10.0.0.20", 2)["received"] == 2


def test_etherchannel_bundles_parallel_links():
    net = _dual_link_switches()
    for sw in ("SW1", "SW2"):
        _run(net, sw, "enable", "configure terminal",
             "interface FastEthernet0/1", "channel-group 1 mode active", "exit",
             "interface FastEthernet0/2", "channel-group 1 mode active", "end")
    net.compute_spanning_tree()
    # bundled => no member is blocked
    assert _inter_switch_blocking(net) == 0
    assert net.ping("PC1", "10.0.0.20", 2)["received"] == 2
    out = _sess(net, "SW1").execute("show etherchannel summary")
    assert "LACP" in out
    assert "FastEthernet0/1" in out and "FastEthernet0/2" in out


# -------------------------------------------------------------------- RSTP
def test_rapid_pvst_mode_reported():
    net = Network()
    net.add_switch("SW1")
    net.add_switch("SW2")
    net.connect("SW1", "FastEthernet0/1", "SW2", "FastEthernet0/1")
    s, _ = _run(net, "SW1", "enable", "configure terminal",
                "spanning-tree mode rapid-pvst", "end")
    out = s.execute("show spanning-tree")
    assert "protocol rstp" in out


# ----------------------------------------------------------- round-trip
def test_bgp_round_trip_config():
    net = _bgp_triangle()
    cmds = _sess(net, "R1")._running_config_lines()
    joined = "\n".join(cmds)
    assert "router bgp 65001" in joined
    assert "neighbor 10.0.12.2 remote-as 65002" in joined
    assert "network 10.1.1.0 mask 255.255.255.0" in joined


def test_hsrp_round_trip_config():
    net = _hsrp_pair()
    cmds = _sess(net, "R1")._running_config_lines()
    joined = "\n".join(cmds)
    assert "standby 1 ip 10.0.0.254" in joined
    assert "standby 1 priority 110" in joined
    assert "standby 1 preempt" in joined
