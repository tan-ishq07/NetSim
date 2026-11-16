"""In-memory workspace store. Each workspace is an isolated topology plus the
per-device CLI sessions and node positions for the canvas.

State lives in the process heap, so the web server must run as a single worker
(see Procfile `--workers 1`); multiple workers would each hold a divergent copy
and requests load-balanced between them would see inconsistent state. To scale
horizontally this store must move to a shared backend (e.g. Redis/Postgres)
keyed by workspace id."""

from __future__ import annotations

import threading
from typing import Dict

from .engine import Network, wireless
from .engine.cli import CLISession


class Workspace:
    def __init__(self) -> None:
        self.net = Network()
        self.sessions: Dict[str, CLISession] = {}
        self.positions: Dict[str, dict] = {}
        self.baseline: Dict[str, list] | None = None  # automation config snapshot
        self.lock = threading.RLock()

    def session(self, device_name: str) -> CLISession:
        dev = self.net.devices[device_name]
        s = self.sessions.get(device_name)
        if s is None or s.device is not dev:
            s = CLISession(dev, self.net)
            self.sessions[device_name] = s
        return s

    def topology(self) -> dict:
        devices = []
        for d in self.net.devices.values():
            data = d.to_dict()
            data["position"] = self.positions.get(d.name, {"x": 0, "y": 0})
            devices.append(data)
        links = [
            {
                "a": {"device": ln.a.device.name, "interface": ln.a.name},
                "b": {"device": ln.b.device.name, "interface": ln.b.name},
                "up": ln.is_up,
            }
            for ln in self.net.links
        ]
        return {"devices": devices, "links": links}

    def apply_lab(self, spec: dict) -> dict:
        """Rebuild this workspace from a lab spec (devices, links, config)."""
        self.net = Network()
        self.sessions = {}
        self.positions = {}
        self.baseline = None
        errors = []
        for d in spec.get("devices", []):
            kind = d["kind"]
            if kind == "host":
                self.net.add_host(d["name"])
            elif kind == "switch":
                self.net.add_switch(d["name"], d.get("ports", 4))
            elif kind == "router":
                self.net.add_router(d["name"], d.get("ports", 2))
            elif kind == "ap":
                self.net.add_ap(d["name"], d.get("x", 0), d.get("y", 0))
            elif kind == "wifi-client":
                self.net.add_wifi_client(d["name"], d.get("x", 0), d.get("y", 0))
            elif kind == "wlc":
                self.net.add_wlc(d["name"])
            else:
                errors.append(f"unknown device kind {kind}")
                continue
            self.positions[d["name"]] = {"x": d.get("x", 0), "y": d.get("y", 0)}
        for ln in spec.get("links", []):
            try:
                self.net.connect(ln["dev_a"], ln["if_a"], ln["dev_b"], ln["if_b"])
            except (ValueError, KeyError) as exc:
                errors.append(f"link error: {exc}")
        for device, lines in spec.get("config", {}).items():
            if device not in self.net.devices:
                errors.append(f"config for unknown device {device}")
                continue
            sess = self.session(device)
            for line in lines:
                out = sess.execute(line)
                if out.startswith("%"):
                    errors.append(f"{device}: '{line}' -> {out}")
        errors.extend(self._apply_wireless(spec.get("wireless", {})))
        return {"topology": self.topology(), "errors": errors}

    def _apply_wireless(self, wl: dict) -> list:
        """Apply a wireless config block: SSIDs, client profiles, CAPWAP joins."""
        wifi = wireless
        errors: list = []
        for s in wl.get("ssids", []):
            dev = self.net.devices.get(s["device"])
            cfg = {"auth": s.get("auth", "open"), "psk": s.get("psk"),
                   "vlan": s.get("vlan", 1), "wmm": s.get("wmm", True),
                   "hidden": s.get("hidden", False)}
            if isinstance(dev, wifi.AccessPoint):
                dev.ssids[s["ssid"]] = cfg
            elif isinstance(dev, wifi.WirelessLANController):
                dev.wlans[s["ssid"]] = cfg
            else:
                errors.append(f"ssid target {s['device']} is not an AP/WLC")
        for c in wl.get("capwap", []):
            res = wifi.capwap_join(self.net, c["ap"], c["wlc"])
            if not res.get("ok"):
                errors.append(f"capwap {c['ap']}->{c['wlc']}: {res.get('error')}")
        for p in wl.get("profiles", []):
            dev = self.net.devices.get(p["client"])
            if isinstance(dev, wifi.WirelessClient):
                dev.profiles[p["ssid"]] = p.get("psk", "")
            else:
                errors.append(f"profile target {p['client']} is not a client")
        return errors

    def export_lab(self) -> dict:
        """Serialize the current topology + per-device running config to a spec."""
        devices = [
            {
                "kind": d.kind,
                "name": d.name,
                "ports": len(d.interfaces),
                **self.positions.get(d.name, {"x": 0, "y": 0}),
            }
            for d in self.net.devices.values()
        ]
        links = [
            {"dev_a": ln.a.device.name, "if_a": ln.a.name,
             "dev_b": ln.b.device.name, "if_b": ln.b.name}
            for ln in self.net.links
        ]
        config = {}
        for d in self.net.devices.values():
            lines = CLISession(d, self.net)._running_config_lines()
            if lines:
                config[d.name] = lines
        return {"devices": devices, "links": links, "config": config, "checks": []}

    def load_sample(self) -> None:
        net = self.net
        pc1 = net.add_host("PC1")
        pc2 = net.add_host("PC2")
        net.add_switch("SW1", ports=4)
        pc1.primary.set_ip("192.168.1.10", "24")
        pc1.primary.enabled = True
        pc2.primary.set_ip("192.168.1.20", "24")
        pc2.primary.enabled = True
        net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
        net.connect("PC2", "eth0", "SW1", "FastEthernet0/1")
        self.positions = {
            "PC1": {"x": 120, "y": 120},
            "SW1": {"x": 360, "y": 260},
            "PC2": {"x": 600, "y": 120},
        }


class WorkspaceStore:
    def __init__(self) -> None:
        self._spaces: Dict[str, Workspace] = {}
        self._lock = threading.Lock()

    def get(self, wid: str) -> Workspace:
        with self._lock:
            if wid not in self._spaces:
                self._spaces[wid] = Workspace()
            return self._spaces[wid]

    def reset(self, wid: str) -> Workspace:
        with self._lock:
            self._spaces[wid] = Workspace()
            return self._spaces[wid]
