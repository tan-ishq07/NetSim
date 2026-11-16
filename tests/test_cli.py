import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import Network  # noqa: E402
from app.engine.cli import CLISession  # noqa: E402


def test_configure_host_and_ping_via_cli():
    net = Network()
    net.add_host("PC1")
    net.add_host("PC2")
    net.add_switch("SW1", ports=4)
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW1", "FastEthernet0/1")

    for name, ip in (("PC1", "10.0.0.1"), ("PC2", "10.0.0.2")):
        cli = CLISession(net.devices[name], net)
        cli.execute("enable")
        cli.execute("configure terminal")
        cli.execute("interface eth0")
        cli.execute(f"ip address {ip} 255.255.255.0")
        cli.execute("no shutdown")

    cli = CLISession(net.devices["PC1"], net)
    out = cli.execute("ping 10.0.0.2")
    assert "!" in out
    assert "Success rate is 100 percent" in out


def test_prompt_changes_with_mode():
    net = Network()
    net.add_switch("SW1")
    cli = CLISession(net.devices["SW1"], net)
    assert cli.prompt == "SW1>"
    cli.execute("enable")
    assert cli.prompt == "SW1#"
    cli.execute("configure terminal")
    assert cli.prompt == "SW1(config)#"
    cli.execute("interface FastEthernet0/0")
    assert cli.prompt == "SW1(config-if)#"
    cli.execute("exit")
    assert cli.prompt == "SW1(config)#"


def test_show_ip_interface_brief():
    net = Network()
    r = net.add_router("R1")
    r.interfaces["GigabitEthernet0/0"].set_ip("10.0.0.254", "24")
    r.interfaces["GigabitEthernet0/0"].enabled = True
    cli = CLISession(r, net)
    out = cli.execute("show ip interface brief")
    assert "GigabitEthernet0/0" in out
    assert "10.0.0.254" in out


def test_abbreviated_commands():
    net = Network()
    net.add_router("R1")
    cli = CLISession(net.devices["R1"], net)
    cli.execute("en")
    assert cli.prompt == "R1#"
    cli.execute("conf t")
    assert cli.prompt == "R1(config)#"
    cli.execute("int g0/0")
    assert cli.prompt == "R1(config-if)#"
