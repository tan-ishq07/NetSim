"""A thin, typed Python SDK over the NetSim REST API.

The client keeps an ``X-Workspace`` header so every call lands in the same
isolated topology (mirroring how the browser scopes its state). It deliberately
exposes one method per automation primitive so scripts read like a runbook:

    nc = NetSimClient("http://localhost:8000")
    nc.apply_lab(spec)                  # labs-as-code
    nc.push_config("R1", ["hostname R1"])  # netmiko-style config push
    nc.validate([{ "type": "reachable", "src": "PC1", "dst": "192.168.2.10" }])
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional


class NetSimError(RuntimeError):
    """Raised when the NetSim API returns a non-2xx response."""


class NetSimClient:
    """Minimal REST client for a running NetSim server (stdlib-only).

    Args:
        base_url: e.g. ``http://localhost:8000`` or the live demo URL.
        workspace: workspace id; a random one is generated if omitted so
            parallel runs don't clobber each other's topology.
        timeout: per-request timeout in seconds.
    """

    def __init__(self, base_url: str = "http://localhost:8000",
                 workspace: Optional[str] = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace or f"auto-{uuid.uuid4().hex[:8]}"
        self.timeout = timeout

    # -- transport ---------------------------------------------------------- #
    def _request(self, method: str, path: str,
                 body: Optional[dict] = None) -> Any:
        url = f"{self.base_url}/api{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Workspace", self.workspace)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass
            raise NetSimError(f"{method} {path} -> {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise NetSimError(f"cannot reach NetSim at {self.base_url}: {exc.reason}") from None
        return json.loads(raw) if raw else None

    # -- topology / labs-as-code ------------------------------------------- #
    def topology(self) -> dict:
        """Current topology (devices + links)."""
        return self._request("GET", "/topology")

    def load_sample(self) -> dict:
        """Load the built-in PC1–SW1–PC2 sample topology."""
        return self._request("POST", "/sample")

    def apply_lab(self, spec: dict) -> dict:
        """Build the whole topology from a declarative spec (devices/links/config)."""
        return self._request("POST", "/lab/apply", {"spec": spec})

    def export_lab(self) -> dict:
        """Serialize the live topology + running-config back to a spec."""
        return self._request("GET", "/lab/export")

    def grade(self, checks: List[dict]) -> dict:
        """Grade the topology against lab checks (0–100 score)."""
        return self._request("POST", "/lab/grade", {"checks": checks})

    # -- config push ------------------------------------------------------- #
    def push_config(self, device: str, config: List[str]) -> dict:
        """Push config lines to a device (enter config mode, apply, exit)."""
        return self._request("POST", "/automation/push",
                             {"device": device, "config": list(config)})

    def render(self, template: str, variables: dict,
               device: Optional[str] = None, push: bool = False) -> dict:
        """Render a server-side template; optionally push it to ``device``."""
        return self._request("POST", "/automation/render", {
            "template": template, "variables": variables,
            "device": device, "push": push,
        })

    # -- structured intent (RESTCONF/NETCONF-ish) -------------------------- #
    def get_intent(self, device: str) -> dict:
        """Structured (YANG-ish) view of a device's config."""
        return self._request("GET", f"/automation/intent/{device}")

    def apply_intent(self, device: str, intent: dict) -> dict:
        """Translate a structured intent to CLI and apply it."""
        return self._request("POST", "/automation/intent",
                             {"device": device, "intent": intent})

    # -- snapshot / drift -------------------------------------------------- #
    def snapshot(self) -> dict:
        """Save a running-config baseline for the whole topology."""
        return self._request("POST", "/automation/snapshot")

    def drift(self) -> dict:
        """Diff the live running-config against the saved baseline."""
        return self._request("GET", "/automation/drift")

    # -- validation (Batfish/pyATS style) ---------------------------------- #
    def validate(self, assertions: List[dict]) -> dict:
        """Run compliance assertions; returns per-assertion pass/fail + score."""
        return self._request("POST", "/automation/validate",
                             {"assertions": assertions})
