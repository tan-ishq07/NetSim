import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import Network  # noqa: E402


def _two_hosts_one_switch():
    net = Network()
    h1 = net.add_host("PC1")
    h2 = net.add_host("PC2")
    net.add_switch("SW1", ports=4)
    h1.primary.set_ip("10.0.0.1", "24")
    h1.primary.enabled = True
    h2.primary.set_ip("10.0.0.2", "24")
    h2.primary.enabled = True
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW1", "FastEthernet0/1")
    return net


def test_same_subnet_ping_succeeds():
    net = _two_hosts_one_switch()
    res = net.ping("PC1", "10.0.0.2")
    assert res["ok"] is True
    assert res["received"] == 1
    assert res["loss_pct"] == 0.0


def test_switch_learns_macs():
    net = _two_hosts_one_switch()
    net.ping("PC1", "10.0.0.2")
    sw = net.devices["SW1"]
    learned_macs = {mac for (_vlan, mac) in sw.mac_table.keys()}
    assert net.devices["PC1"].primary.mac in learned_macs
    assert net.devices["PC2"].primary.mac in learned_macs


def test_capture_contains_arp_and_icmp():
    net = _two_hosts_one_switch()
    res = net.ping("PC1", "10.0.0.2")
    protos = [c["protocol"] for c in res["capture"]]
    assert "ARP" in protos
    assert "ICMP" in protos


def test_ping_unknown_host_fails():
    net = _two_hosts_one_switch()
    res = net.ping("PC1", "10.0.0.99")
    assert res["ok"] is True
    assert res["received"] == 0
    assert res["loss_pct"] == 100.0


def test_vlan_isolation_blocks_ping():
    net = _two_hosts_one_switch()
    sw = net.devices["SW1"]
    sw.interfaces["FastEthernet0/1"].vlan = 20  # move PC2 to another VLAN
    res = net.ping("PC1", "10.0.0.2")
    assert res["received"] == 0


def _routed_topology():
    net = Network()
    h1 = net.add_host("PC1")
    h2 = net.add_host("PC2")
    r = net.add_router("R1", ports=2)
    h1.primary.set_ip("10.0.0.1", "24")
    h1.primary.enabled = True
    h1.gateway = "10.0.0.254"
    h2.primary.set_ip("10.0.1.1", "24")
    h2.primary.enabled = True
    h2.gateway = "10.0.1.254"
    g0 = r.interfaces["GigabitEthernet0/0"]
    g1 = r.interfaces["GigabitEthernet0/1"]
    g0.set_ip("10.0.0.254", "24")
    g0.enabled = True
    g1.set_ip("10.0.1.254", "24")
    g1.enabled = True
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    net.connect("PC2", "eth0", "R1", "GigabitEthernet0/1")
    return net


def test_inter_subnet_ping_via_router():
    net = _routed_topology()
    res = net.ping("PC1", "10.0.1.1")
    assert res["received"] == 1


def test_router_down_interface_breaks_ping():
    net = _routed_topology()
    net.devices["R1"].interfaces["GigabitEthernet0/1"].enabled = False
    res = net.ping("PC1", "10.0.1.1")
    assert res["received"] == 0
