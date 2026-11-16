"""Phase 11: automation surface (intent <-> CLI, templating, drift, validate)."""
from app.engine import automation
from app.engine.cli import CLISession
from app.engine.simulator import Network


def _two_pc_router():
    """PC1 -- R1 -- PC2 across two /24s, fully configured and reachable."""
    net = Network()
    pc1 = net.add_host("PC1")
    pc2 = net.add_host("PC2")
    net.add_router("R1", ports=2)
    net.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    net.connect("PC2", "eth0", "R1", "GigabitEthernet0/1")

    def run(dev, lines):
        s = CLISession(net.devices[dev], net)
        for c in ["enable", "configure terminal", *lines, "end"]:
            s.execute(c)

    run("R1", ["interface g0/0", "ip address 10.0.0.254 255.255.255.0", "no shutdown",
               "exit", "interface g0/1", "ip address 10.0.1.254 255.255.255.0", "no shutdown"])
    run("PC1", ["interface eth0", "ip address 10.0.0.1 255.255.255.0", "no shutdown",
                "exit", "ip default-gateway 10.0.0.254"])
    run("PC2", ["interface eth0", "ip address 10.0.1.1 255.255.255.0", "no shutdown",
                "exit", "ip default-gateway 10.0.1.254"])
    pc1.gateway = "10.0.0.254"
    pc2.gateway = "10.0.1.254"
    return net


# --------------------------------------------------------------------------- #
# intent  <->  CLI
# --------------------------------------------------------------------------- #
def test_device_intent_reflects_config():
    net = _two_pc_router()
    intent = automation.device_intent(net.devices["R1"])
    assert intent["kind"] == "router"
    g0 = next(i for i in intent["interfaces"] if i["name"] == "GigabitEthernet0/0")
    assert g0["ipv4"]["address"] == "10.0.0.254"
    assert g0["ipv4"]["mask"] == "255.255.255.0"
    assert g0["enabled"] is True


def test_intent_to_cli_generates_config():
    intent = {
        "interfaces": [
            {"name": "GigabitEthernet0/0", "ipv4": {"address": "192.0.2.1", "prefix": 24},
             "enabled": True},
        ],
        "static_routes": [{"network": "0.0.0.0", "prefix": 0, "next_hop": "192.0.2.254"}],
        "ospf": {"process_id": 1, "networks": [{"network": "192.0.2.0", "prefix": 24, "area": 0}]},
    }
    lines = automation.intent_to_cli(intent)
    assert "interface GigabitEthernet0/0" in lines
    assert " ip address 192.0.2.1 255.255.255.0" in lines
    assert " no shutdown" in lines
    assert "ip route 0.0.0.0 0.0.0.0 192.0.2.254" in lines
    assert "router ospf 1" in lines
    assert " network 192.0.2.0 0.0.0.255 area 0" in lines


def test_intent_roundtrip_applies_to_fresh_device():
    """Capture R1 intent, replay it onto a brand-new router, ping must work."""
    net = _two_pc_router()
    intent = automation.device_intent(net.devices["R1"])

    # rebuild an identical topology but configure R1 only from the captured intent
    net2 = Network()
    net2.add_host("PC1")
    net2.add_host("PC2")
    net2.add_router("R1", ports=2)
    net2.connect("PC1", "eth0", "R1", "GigabitEthernet0/0")
    net2.connect("PC2", "eth0", "R1", "GigabitEthernet0/1")
    s = CLISession(net2.devices["R1"], net2)
    for c in ["enable", "configure terminal", *automation.intent_to_cli(intent), "end"]:
        s.execute(c)

    r1 = net2.devices["R1"]
    assert r1.get_interface("GigabitEthernet0/0").ip == "10.0.0.254"
    assert r1.get_interface("GigabitEthernet0/1").ip == "10.0.1.254"


def test_intent_to_cli_disabled_interface_emits_shutdown():
    lines = automation.intent_to_cli(
        {"interfaces": [{"name": "Gig0/0", "enabled": False}]})
    assert " shutdown" in lines and " no shutdown" not in lines


# --------------------------------------------------------------------------- #
# template rendering
# --------------------------------------------------------------------------- #
def test_render_simple_variable():
    out = automation.render_template("hostname {{ name }}", {"name": "EDGE1"})
    assert out == "hostname EDGE1"


def test_render_for_loop_over_dicts():
    tmpl = (
        "{% for i in ifaces %}interface {{ i.name }}\n"
        " ip address {{ i.ip }} {{ i.mask }}\n{% endfor %}"
    )
    out = automation.render_template(tmpl, {"ifaces": [
        {"name": "Gig0/0", "ip": "10.0.0.1", "mask": "255.255.255.0"},
        {"name": "Gig0/1", "ip": "10.0.1.1", "mask": "255.255.255.0"},
    ]})
    assert "interface Gig0/0" in out
    assert " ip address 10.0.1.1 255.255.255.0" in out
    assert out.count("interface ") == 2


def test_render_if_else_branches():
    tmpl = "{% if trunk %}switchport mode trunk{% else %}switchport mode access{% endif %}"
    assert automation.render_template(tmpl, {"trunk": True}) == "switchport mode trunk"
    assert automation.render_template(tmpl, {"trunk": False}) == "switchport mode access"


def test_render_if_equality():
    tmpl = "{% if role == 'core' %}high{% endif %}"
    assert automation.render_template(tmpl, {"role": "core"}) == "high"
    assert automation.render_template(tmpl, {"role": "edge"}) == ""


def test_render_nested_for_in_if():
    tmpl = "{% if enable %}{% for v in vlans %}vlan {{ v }}\n{% endfor %}{% endif %}"
    out = automation.render_template(tmpl, {"enable": True, "vlans": [10, 20]})
    assert out == "vlan 10\nvlan 20\n"
    assert automation.render_template(tmpl, {"enable": False, "vlans": [10, 20]}) == ""


# --------------------------------------------------------------------------- #
# snapshot + drift
# --------------------------------------------------------------------------- #
def test_drift_detects_added_and_removed_lines():
    net = _two_pc_router()
    base = automation.snapshot(net)
    # change the topology: add a static route on R1
    s = CLISession(net.devices["R1"], net)
    for c in ["enable", "configure terminal", "ip route 8.8.8.0 255.255.255.0 10.0.1.1", "end"]:
        s.execute(c)
    cur = automation.snapshot(net)
    drift = automation.diff_snapshots(base, cur)
    assert "R1" in drift
    assert any("8.8.8.0" in ln for ln in drift["R1"]["added"])
    assert drift["R1"]["removed"] == []


def test_no_drift_when_unchanged():
    net = _two_pc_router()
    base = automation.snapshot(net)
    assert automation.diff_snapshots(base, automation.snapshot(net)) == {}


# --------------------------------------------------------------------------- #
# Batfish-style validation
# --------------------------------------------------------------------------- #
def test_validate_passes_on_compliant_network():
    net = _two_pc_router()
    report = automation.validate(net, [
        {"type": "interface_ip", "device": "R1", "interface": "GigabitEthernet0/0",
         "ip": "10.0.0.254"},
        {"type": "interface_up", "device": "R1", "interface": "GigabitEthernet0/0"},
        {"type": "route_present", "device": "R1", "network": "10.0.1.0/24"},
        {"type": "no_duplicate_ip"},
        {"type": "reachable", "src": "PC1", "dst": "10.0.1.1"},
    ])
    assert report["compliant"] is True
    assert report["score"] == 100


def test_validate_flags_wrong_ip_and_duplicates():
    net = _two_pc_router()
    # introduce a duplicate IP
    net.devices["PC2"].primary.set_ip("10.0.0.1", "24")
    report = automation.validate(net, [
        {"type": "interface_ip", "device": "R1", "interface": "GigabitEthernet0/0",
         "ip": "10.9.9.9"},
        {"type": "no_duplicate_ip"},
    ])
    assert report["compliant"] is False
    assert report["passed"] == 0


def test_validate_route_absent_expectation():
    net = _two_pc_router()
    report = automation.validate(net, [
        {"type": "route_present", "device": "R1", "network": "203.0.113.0/24",
         "expect_present": False},
    ])
    assert report["compliant"] is True
