"""NetSim automation toolkit — a small Python SDK + CLI for driving a running
NetSim instance the way a network-automation engineer drives real gear.

It talks to the NetSim REST API (the same API the web UI uses), so you can:

* build a topology declaratively (labs-as-code),
* render Cisco configs from Jinja2 templates + per-device variables,
* push config to devices (netmiko ``send_config_set`` style),
* assert the resulting network state (Batfish/pyATS style), and
* detect config drift against a saved baseline.

See ``automation.client.NetSimClient`` for the SDK and ``automation.netdevops``
for the high-level "render → push → validate" pipeline used by the CLI, the
examples, and the NetDevOps CI workflow.
"""
from .client import NetSimClient, NetSimError

__all__ = ["NetSimClient", "NetSimError"]
