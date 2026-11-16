# NetSim automation examples

These scripts drive a running NetSim instance through its REST API using the
[`automation`](../../automation) Python SDK — the same way you'd automate real
gear with netmiko/NAPALM, but against a zero-install simulator.

## Prerequisites

```bash
pip install -r requirements.txt          # the app
pip install -r automation/requirements.txt   # the SDK + CLI
uvicorn app.main:app --port 8000              # start NetSim
```

## `provision_and_verify.py`

End-to-end **NetDevOps** flow against the [`two_router_ospf.yaml`](../../automation/labs/two_router_ospf.yaml)
network definition:

1. **Build** the topology declaratively (labs-as-code).
2. **Render** each device's Cisco config from a Jinja2 template + per-device vars.
3. **Push** the config (netmiko `send_config_set` style).
4. **Snapshot** a running-config baseline.
5. **Validate** intended state — interfaces up, OSPF routes present, hosts
   reachable across the backbone (Batfish/pyATS style). Non-zero exit on failure.
6. **Detect drift** by mutating a device and diffing against the baseline.

```bash
python examples/automation/provision_and_verify.py --url http://localhost:8000
```

```
=== NetDevOps deploy: two-router-ospf ===
[OK] topology: 4 devices, 3 links
[OK] push R1: 13 lines
[OK] push R2: 13 lines
[OK] push PC1: 5 lines
[OK] push PC2: 5 lines
[OK] saved running-config baseline
[OK] validation: 8/8 passed (score 100/100)
=== result: PASS ===
```

## CLI

The same pipeline is available as a CLI:

```bash
python -m automation.cli render   automation/labs/two_router_ospf.yaml
python -m automation.cli deploy   automation/labs/two_router_ospf.yaml --url http://localhost:8000
python -m automation.cli validate automation/labs/two_router_ospf.yaml --url http://localhost:8000
python -m automation.cli drift    --url http://localhost:8000
python -m automation.cli intent R1 --url http://localhost:8000
```
