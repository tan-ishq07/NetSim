"""Tests for the automation toolkit: template rendering, topology translation,
and the full NetDevOps deploy/verify pipeline against a live server."""
import os
import socket
import threading
import time

import pytest
import uvicorn

from automation import NetSimClient
from automation.netdevops import (deploy_and_verify, format_report,
                                   load_network_def, render_configs,
                                   topology_spec)

LAB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "automation", "labs", "two_router_ospf.yaml")


# --- pure functions (no server) ------------------------------------------- #
def test_render_configs_router_and_host():
    net_def = load_network_def(LAB)
    rendered = render_configs(net_def)
    r1 = [line.strip() for line in rendered["R1"]]
    assert "hostname R1" in r1
    assert "ip address 192.168.1.1 255.255.255.0" in r1
    assert "router ospf 1" in r1
    assert "network 10.0.0.0 0.0.0.3 area 0" in r1
    assert "ip default-gateway 192.168.1.1" in [line.strip() for line in rendered["PC1"]]
    # blank lines are stripped
    assert all(line.strip() for line in rendered["R1"])


def test_topology_spec_maps_links():
    spec = topology_spec(load_network_def(LAB))
    assert len(spec["devices"]) == 4
    assert {"dev_a", "if_a", "dev_b", "if_b"} <= set(spec["links"][0])
    names = {d["name"] for d in spec["devices"]}
    assert names == {"PC1", "R1", "R2", "PC2"}


def test_format_report_renders_pass_fail():
    report = {"name": "x", "ok": True, "steps": [
        {"step": "apply_topology", "devices": 2, "links": 1, "errors": []},
        {"step": "validate", "compliant": True, "passed": 2, "total": 2,
         "score": 100, "results": []},
    ]}
    text = format_report(report)
    assert "result: PASS" in text
    assert "2/2 passed" in text


# --- end-to-end pipeline against a live server ----------------------------- #
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server_url():
    from app.main import app
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_deploy_and_verify_full_pipeline(server_url):
    client = NetSimClient(server_url)
    net_def = load_network_def(LAB)
    report, ok = deploy_and_verify(client, net_def)
    assert ok, format_report(report)
    validate_step = next(s for s in report["steps"] if s["step"] == "validate")
    assert validate_step["compliant"]
    assert validate_step["passed"] == validate_step["total"] == 8


def test_drift_detection(server_url):
    client = NetSimClient(server_url, workspace="drift-test")
    deploy_and_verify(client, load_network_def(LAB))
    client.snapshot()
    assert client.drift()["in_sync"] is True
    client.push_config("R1", ["interface GigabitEthernet0/1", "shutdown", "exit"])
    drift = client.drift()
    assert drift["in_sync"] is False
    assert "R1" in drift["drift"]


def test_structured_intent_roundtrip(server_url):
    client = NetSimClient(server_url, workspace="intent-test")
    deploy_and_verify(client, load_network_def(LAB))
    intent = client.get_intent("R1")
    assert intent["hostname"] == "R1"
    assert any(i.get("ipv4", {}).get("address") == "192.168.1.1"
               for i in intent["interfaces"])
