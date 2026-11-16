"""Tests for the Cisco-style context-sensitive CLI help."""

from app.engine.cli import CLISession
from app.engine.simulator import Network


def _host_if_session():
    net = Network()
    net.add_host("PC1")
    s = CLISession(net.devices["PC1"], net)
    s.execute("enable")
    s.execute("configure terminal")
    s.execute("interface eth0")
    return s


def _router_session():
    net = Network()
    net.add_router("R1")
    s = CLISession(net.devices["R1"], net)
    s.execute("enable")
    s.execute("configure terminal")
    return s


def _switch_session():
    net = Network()
    net.add_switch("SW1")
    s = CLISession(net.devices["SW1"], net)
    s.execute("enable")
    s.execute("configure terminal")
    s.execute("interface FastEthernet0/1")
    return s


def test_bare_help_lists_mode_commands():
    s = _host_if_session()
    out = s.execute("?")
    assert "ip " in out
    assert "shutdown" in out
    assert "exit" in out


def test_ip_address_help_shows_mask_argument():
    s = _host_if_session()
    assert "address" in s.execute("ip ?")
    addr = s.execute("ip address ?")
    assert "A.B.C.D" in addr  # expects an IP address
    mask = s.execute("ip address 192.168.1.10 ?")
    assert "A.B.C.D" in mask  # then a subnet mask


def test_partial_word_help_completes_prefix():
    s = _host_if_session()
    out = s.execute("ip add?")
    assert "address" in out


def test_host_does_not_offer_switchport():
    s = _host_if_session()
    out = s.execute("?")
    assert "switchport" not in out


def test_switch_offers_switchport_not_routing():
    s = _switch_session()
    out = s.execute("?")
    assert "switchport" in out
    # routing-only helper-address must not appear on a switch port
    assert "helper-address" not in s.execute("ip ?")


def test_router_config_offers_router_process():
    s = _router_session()
    out = s.execute("?")
    assert "router" in out
    assert "ospf" in s.execute("router ?")


def test_help_does_not_execute_or_change_mode():
    s = _host_if_session()
    before = s.mode
    s.execute("ip address ?")
    assert s.mode == before
    # the interface still has no IP — help must not configure anything
    assert s.device.interfaces["eth0"].ip is None


def test_show_help_lists_subcommands():
    s = _router_session()
    s.execute("end")  # priv mode
    out = s.execute("show ?")
    assert "running-config" in out
    assert "ip" in out


def test_unknown_command_points_to_help():
    s = _host_if_session()
    out = s.execute("frobnicate")
    assert "?" in out


def test_wrong_mode_command_lists_valid_commands():
    # 'ip' is only valid in config mode; at the priv prompt the help should
    # explain that and list what IS valid here.
    s = _router_session()
    s.execute("end")  # priv mode
    out = s.execute("ip ?")
    assert "not valid here" in out
    assert "configure" in out  # lists the commands available in priv mode


def test_ping_available_on_router():
    net = Network()
    net.add_router("R1")
    s = CLISession(net.devices["R1"], net)
    assert "ping" in s.execute("?")


def test_nat_drills_down_to_arguments():
    s = _router_session()
    out = s.execute("ip nat inside source ?")
    assert "static" in out and "list" in out
    overload = s.execute("ip nat inside source list MYACL ?")
    assert "overload" in overload and "pool" in overload


def test_security_commands_fully_specified():
    s = _router_session()
    assert "tunnel" in s.execute("crypto ?")
    zp = s.execute("zone-pair security A B ?")
    assert "inspect" in zp and "permit" in zp and "deny" in zp
    assert "password" in s.execute("username bob ?")


def test_named_acl_permit_offers_match_grammar():
    s = _router_session()
    s.execute("ip access-list extended BLOCK")
    out = s.execute("permit ?")
    assert "tcp" in out and "host" in out and "any" in out
