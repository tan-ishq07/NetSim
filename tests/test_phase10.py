"""Phase 10: security suite — port-security, DHCP snooping, DAI, IP source
guard, 802.1X, zone-based firewall, IPsec VPN visibility and IPS signatures."""

from app.engine import Network
from app.engine.cli import CLISession
from app.engine.models import DhcpPool


def _sess(net, dev):
    return CLISession(net.devices[dev], net)


def _run(net, dev, *cmds):
    s = _sess(net, dev)
    return [s.execute(c) for c in cmds]


def _pc_sw_pc():
    """PC1 -- SW -- PC2, same subnet 10.0.0.0/24 (PC1 on Fa0/0, PC2 on Fa0/1)."""
    net = Network()
    pc1, pc2 = net.add_host("PC1"), net.add_host("PC2")
    net.add_switch("SW", ports=4)
    net.connect("PC1", "eth0", "SW", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW", "FastEthernet0/1")
    pc1.primary.set_ip("10.0.0.1", "24")
    pc1.primary.enabled = True
    pc2.primary.set_ip("10.0.0.2", "24")
    pc2.primary.enabled = True
    return net


def _pc_router_pc():
    """PC1 -- R1 -- PC2 across two subnets (10.0.0/24 and 10.0.1/24)."""
    net = Network()
    pc1, pc2 = net.add_host("PC1"), net.add_host("PC2")
    net.add_router("R1")
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    net.connect("PC2", "eth0", "R1", "GigabitEthernet0/1")
    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "ip address 10.0.0.254 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.1.254 255.255.255.0", "no shutdown", "end")
    pc1.primary.set_ip("10.0.0.1", "24")
    pc1.primary.enabled = True
    pc1.gateway = "10.0.0.254"
    pc2.primary.set_ip("10.0.1.1", "24")
    pc2.primary.enabled = True
    pc2.gateway = "10.0.1.254"
    return net


# ------------------------------------------------------------ port security
def test_port_security_allows_within_limit():
    net = _pc_sw_pc()
    _run(net, "SW", "enable", "configure terminal", "interface FastEthernet0/0",
         "switchport port-security", "switchport port-security maximum 1", "end")
    assert net.ping("PC1", "10.0.0.2")["received"] == 1


def test_port_security_violation_err_disables_port():
    net = _pc_sw_pc()
    # allow only a bogus MAC and a max of 1 -> PC1's real MAC violates -> shutdown
    _run(net, "SW", "enable", "configure terminal", "interface FastEthernet0/0",
         "switchport port-security", "switchport port-security maximum 1",
         "switchport port-security mac-address AAAA.BBBB.CCCC", "end")
    assert net.ping("PC1", "10.0.0.2")["received"] == 0
    sw = net.devices["SW"]
    assert sw.interfaces["FastEthernet0/0"].err_disabled is True
    assert any(e["kind"] == "port-security" for e in sw.security_log)
    out = _sess(net, "SW").execute("show port-security")
    assert "err-disabled" in out


# ------------------------------------------------------------ DHCP snooping
def _pc_sw_dhcp():
    net = Network()
    pc1 = net.add_host("PC1")
    net.add_switch("SW", ports=4)
    r1 = net.add_router("R1")
    r1.interfaces["GigabitEthernet0/0"].set_ip("10.0.0.254", "24")
    r1.interfaces["GigabitEthernet0/0"].enabled = True
    r1.dhcp_pools["LAN"] = DhcpPool(name="LAN", network="10.0.0.0", prefix=24,
                                    gateway="10.0.0.254")
    net.connect("PC1", "eth0", "SW", "FastEthernet0/0")
    net.connect("SW", "FastEthernet0/3", "R1", "GigabitEthernet0/0")
    pc1.primary.enabled = True
    return net


def test_dhcp_snooping_drops_reply_on_untrusted_port():
    net = _pc_sw_dhcp()
    # snooping on, but the server-facing port is left UNTRUSTED -> offer dropped
    _run(net, "SW", "enable", "configure terminal", "ip dhcp snooping",
         "ip dhcp snooping vlan 1", "end")
    assert net.dhcp_request("PC1") is None


def test_dhcp_snooping_trusted_port_builds_binding():
    net = _pc_sw_dhcp()
    _run(net, "SW", "enable", "configure terminal", "ip dhcp snooping",
         "ip dhcp snooping vlan 1", "interface FastEthernet0/3",
         "ip dhcp snooping trust", "end")
    lease = net.dhcp_request("PC1")
    assert lease is not None and lease["ip"] == "10.0.0.1"
    sw = net.devices["SW"]
    binding = next((b for b in sw.dhcp_snoop_bindings if b["ip"] == "10.0.0.1"), None)
    assert binding is not None
    assert binding["mac"] == net.devices["PC1"].primary.mac


# ----------------------------------------------------- dynamic ARP inspection
def test_dai_drops_arp_without_binding():
    net = _pc_sw_pc()
    _run(net, "SW", "enable", "configure terminal",
         "ip arp inspection vlan 1", "end")
    # no snooping bindings exist, ports untrusted -> ARP dropped -> ping fails
    assert net.ping("PC1", "10.0.0.2")["received"] == 0
    assert any(e["kind"] == "dai" for e in net.devices["SW"].security_log)


def test_dai_permits_arp_matching_binding():
    net = _pc_sw_pc()
    sw = net.devices["SW"]
    _run(net, "SW", "enable", "configure terminal",
         "ip arp inspection vlan 1", "end")
    for pc, port in (("PC1", "FastEthernet0/0"), ("PC2", "FastEthernet0/1")):
        dev = net.devices[pc]
        sw.dhcp_snoop_bindings.append({"mac": dev.primary.mac, "ip": dev.primary.ip,
                                       "vlan": 1, "interface": port})
    assert net.ping("PC1", "10.0.0.2")["received"] == 1


# --------------------------------------------------------- IP source guard
def test_ip_source_guard_blocks_unbound_source():
    net = _pc_sw_pc()
    _run(net, "SW", "enable", "configure terminal", "interface FastEthernet0/0",
         "ip verify source", "end")
    # ARP still resolves (no DAI), but the IP frame from PC1 has no binding
    assert net.ping("PC1", "10.0.0.2")["received"] == 0
    assert any(e["kind"] == "ipsg" for e in net.devices["SW"].security_log)


def test_ip_source_guard_permits_bound_source():
    net = _pc_sw_pc()
    sw = net.devices["SW"]
    pc1 = net.devices["PC1"]
    sw.dhcp_snoop_bindings.append({"mac": pc1.primary.mac, "ip": "10.0.0.1",
                                   "vlan": 1, "interface": "FastEthernet0/0"})
    _run(net, "SW", "enable", "configure terminal", "interface FastEthernet0/0",
         "ip verify source", "end")
    assert net.ping("PC1", "10.0.0.2")["received"] == 1


# ------------------------------------------------------------------- 802.1X
def test_dot1x_unauthorized_port_blocks_traffic():
    net = _pc_sw_pc()
    _run(net, "SW", "enable", "configure terminal", "dot1x system-auth-control",
         "username alice password s3cret", "interface FastEthernet0/0",
         "authentication port-control auto", "end")
    assert net.ping("PC1", "10.0.0.2")["received"] == 0


def test_dot1x_authentication_opens_port():
    net = _pc_sw_pc()
    _run(net, "SW", "enable", "configure terminal", "dot1x system-auth-control",
         "username alice password s3cret", "interface FastEthernet0/0",
         "authentication port-control auto", "end")
    assert net.dot1x_authenticate("SW", "FastEthernet0/0", "alice", "wrong") is False
    assert net.ping("PC1", "10.0.0.2")["received"] == 0
    assert net.dot1x_authenticate("SW", "FastEthernet0/0", "alice", "s3cret") is True
    assert net.ping("PC1", "10.0.0.2")["received"] == 1


# ------------------------------------------------------ zone-based firewall
def _zoned(net, action=None):
    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "zone-member security INSIDE", "exit",
         "interface g0/1", "zone-member security OUTSIDE", "end")
    if action:
        _run(net, "R1", "enable", "configure terminal",
             f"zone-pair security INSIDE OUTSIDE {action}", "end")


def test_zone_firewall_denies_without_policy():
    net = _pc_router_pc()
    _zoned(net)  # zones assigned but no permit/inspect pair -> default deny
    assert net.ping("PC1", "10.0.1.1")["received"] == 0


def test_zone_firewall_inspect_allows_and_is_stateful():
    net = _pc_router_pc()
    _zoned(net, action="inspect")
    # inside->outside is inspected; the return traffic is allowed by the session
    assert net.ping("PC1", "10.0.1.1")["received"] == 1


def test_zone_firewall_blocks_outside_initiated():
    # On a fresh topology (no inside-opened session), an outside-initiated flow
    # to the inside zone is denied (no OUTSIDE->INSIDE zone-pair).
    net = _pc_router_pc()
    _zoned(net, action="inspect")
    assert net.ping("PC2", "10.0.0.1")["received"] == 0


# ------------------------------------------------------------------- IPsec
def _ipsec_pair(match=True):
    net = Network()
    net.add_router("R1")
    net.add_router("R2")
    net.connect("R1", "g0/1", "R2", "g0/1")
    _run(net, "R1", "enable", "configure terminal",
         "interface g0/0", "ip address 10.1.1.1 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.12.1 255.255.255.0", "no shutdown", "exit",
         "crypto tunnel 10.0.12.2 10.1.1.0/24 10.2.2.0/24", "end")
    remote = "10.2.2.0/24 10.1.1.0/24" if match else "10.9.9.0/24 10.1.1.0/24"
    _run(net, "R2", "enable", "configure terminal",
         "interface g0/0", "ip address 10.2.2.1 255.255.255.0", "no shutdown", "exit",
         "interface g0/1", "ip address 10.0.12.2 255.255.255.0", "no shutdown", "exit",
         f"crypto tunnel 10.0.12.1 {remote}", "end")
    return net


def test_ipsec_session_up_with_reciprocal_tunnel():
    net = _ipsec_pair(match=True)
    sessions = net.crypto_sessions(net.devices["R1"])
    assert sessions and sessions[0]["status"] == "UP-ACTIVE"
    out = _sess(net, "R1").execute("show crypto session")
    assert "UP-ACTIVE" in out


def test_ipsec_session_down_when_proposals_mismatch():
    net = _ipsec_pair(match=False)
    sessions = net.crypto_sessions(net.devices["R1"])
    assert sessions and sessions[0]["status"] == "DOWN"


# ------------------------------------------------------------------- IPS/IDS
def test_ips_drop_signature_blocks_and_alerts():
    net = _pc_router_pc()
    _run(net, "R1", "enable", "configure terminal",
         "ip ips signature 2004 icmp type 8 drop name ICMP-ECHO", "end")
    assert net.ping("PC1", "10.0.1.1")["received"] == 0
    r1 = net.devices["R1"]
    assert any(a["action"] == "drop" for a in r1.ips_alerts)


def test_ips_alert_signature_logs_but_permits():
    net = _pc_router_pc()
    _run(net, "R1", "enable", "configure terminal",
         "ip ips signature 2004 icmp type 8 alert name ICMP-ECHO", "end")
    assert net.ping("PC1", "10.0.1.1")["received"] == 1
    assert net.devices["R1"].ips_alerts  # alert recorded even though permitted


# --------------------------------------------------------- config roundtrip
def test_security_config_roundtrips():
    net = _pc_sw_pc()
    _run(net, "SW", "enable", "configure terminal", "ip dhcp snooping",
         "ip dhcp snooping vlan 1", "ip arp inspection vlan 1",
         "interface FastEthernet0/0", "switchport port-security",
         "switchport port-security maximum 2", "ip verify source", "end")
    cfg = _sess(net, "SW").execute("show running-config")
    assert "ip dhcp snooping" in cfg
    assert "ip arp inspection vlan 1" in cfg
    assert "switchport port-security maximum 2" in cfg
    assert "ip verify source" in cfg

    # replay the configured switch's running-config onto a fresh topology
    cmds = _sess(net, "SW")._running_config_lines()
    net2 = _pc_sw_pc()
    s2 = _sess(net2, "SW")
    for c in cmds:
        s2.execute(c)
    sw2 = net2.devices["SW"]
    assert sw2.dhcp_snooping is True
    assert 1 in sw2.dai_vlans
    fa0 = sw2.interfaces["FastEthernet0/0"]
    assert fa0.port_security and fa0.port_security_max == 2
    assert fa0.ip_source_guard is True
