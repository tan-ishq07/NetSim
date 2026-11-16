"""FastAPI application: REST API for the simulator plus the static SPA."""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engine import automation, dissect, labs, pcap, templates, tutor, wan, wireless
from .workspace import WorkspaceStore

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="NetSim", version="0.1.0")
store = WorkspaceStore()


def ws(workspace: str | None) -> "object":
    return store.get(workspace or "default")


class DeviceIn(BaseModel):
    kind: str
    name: str
    ports: int | None = None
    x: float = 0
    y: float = 0


class LinkIn(BaseModel):
    dev_a: str
    if_a: str
    dev_b: str
    if_b: str


class CliIn(BaseModel):
    device: str
    command: str


class PingIn(BaseModel):
    src: str
    dst: str
    count: int = 4


class PositionIn(BaseModel):
    name: str
    x: float
    y: float


class TutorIn(BaseModel):
    question: str = ""


class LabSpecIn(BaseModel):
    spec: dict


class GradeIn(BaseModel):
    checks: list = []


class TemplateIn(BaseModel):
    id: str


class PushIn(BaseModel):
    device: str
    config: list = []


class IntentIn(BaseModel):
    device: str
    intent: dict = {}


class RenderIn(BaseModel):
    template: str = ""
    variables: dict = {}
    device: str | None = None
    push: bool = False


class ValidateIn(BaseModel):
    assertions: list = []


class WifiDeviceIn(BaseModel):
    name: str
    kind: str  # ap | wifi-client | wlc
    x: float = 0.0
    y: float = 0.0


class SsidIn(BaseModel):
    device: str  # AP or WLC name
    ssid: str
    auth: str = "open"  # open | wpa2-psk | wpa3-sae
    psk: str | None = None
    vlan: int = 1
    wmm: bool = True
    hidden: bool = False


class WifiProfileIn(BaseModel):
    client: str
    ssid: str
    psk: str = ""


class CapwapIn(BaseModel):
    ap: str
    wlc: str


class WifiActionIn(BaseModel):
    client: str
    ssid: str | None = None
    x: float | None = None
    y: float | None = None


class PppIn(BaseModel):
    auth: str = "none"
    client: dict | None = None
    server_users: dict | None = None
    pppoe: bool = False


class QosClassifyIn(BaseModel):
    packet: dict = {}
    classes: list = []


class QosPoliceIn(BaseModel):
    packets: list = []  # [[time, size_bytes], ...]
    cir_bps: int = 1_000_000
    bc_bytes: int = 1500


class QosScheduleIn(BaseModel):
    queues: list = []
    rounds: int = 1


class SdwanIn(BaseModel):
    paths: list = []  # [{system_ip,color,encap?,loss,latency,jitter,up,preference}]
    sla: dict = {}
    preferred_colors: list | None = None


class VxlanIn(BaseModel):
    vteps: list = []        # [{name, ip, vnis:[...]}]
    advertise: list = []    # [{vtep, vni, mac}]
    forward: dict | None = None  # {src_vtep, vni, dst_mac}


class MplsIn(BaseModel):
    path: list = []
    fec: str = ""
    php: bool = True


class SrIn(BaseModel):
    sid_list: list = []
    node_sids: dict = {}


@app.get("/api/topology")
def get_topology(x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return w.topology()


@app.post("/api/devices")
def add_device(body: DeviceIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        try:
            if body.kind == "host":
                w.net.add_host(body.name)
            elif body.kind == "switch":
                w.net.add_switch(body.name, body.ports or 4)
            elif body.kind == "router":
                w.net.add_router(body.name, body.ports or 2)
            else:
                raise HTTPException(400, f"unknown kind {body.kind}")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        w.positions[body.name] = {"x": body.x, "y": body.y}
        return w.topology()


@app.delete("/api/devices/{name}")
def delete_device(name: str, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        dev = w.net.devices.pop(name, None)
        if dev is None:
            raise HTTPException(404, "no such device")
        w.net.links = [
            ln for ln in w.net.links
            if ln.a.device.name != name and ln.b.device.name != name
        ]
        for d in w.net.devices.values():
            for i in d.interfaces.values():
                if i.link and (i.link.a.device.name == name or i.link.b.device.name == name):
                    i.link = None
        w.positions.pop(name, None)
        w.sessions.pop(name, None)
        return w.topology()


@app.post("/api/links")
def add_link(body: LinkIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        try:
            w.net.connect(body.dev_a, body.if_a, body.dev_b, body.if_b)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return w.topology()


@app.post("/api/links/delete")
def delete_link(body: LinkIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        keep = []
        for ln in w.net.links:
            match = (
                ln.a.device.name == body.dev_a and ln.a.name == body.if_a
                and ln.b.device.name == body.dev_b and ln.b.name == body.if_b
            )
            if match:
                ln.a.link = None
                ln.b.link = None
            else:
                keep.append(ln)
        w.net.links = keep
        return w.topology()


@app.patch("/api/positions")
def set_position(body: PositionIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        if body.name in w.net.devices:
            w.positions[body.name] = {"x": body.x, "y": body.y}
        return {"ok": True}


@app.post("/api/cli")
def run_cli(body: CliIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        if body.device not in w.net.devices:
            raise HTTPException(404, "no such device")
        sess = w.session(body.device)
        output = sess.execute(body.command)
        return {"output": output, "prompt": sess.prompt}


@app.get("/api/cli/{device}/prompt")
def cli_prompt(device: str, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        if device not in w.net.devices:
            raise HTTPException(404, "no such device")
        return {"prompt": w.session(device).prompt}


@app.post("/api/ping")
def run_ping(body: PingIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return w.net.ping(body.src, body.dst, body.count)


@app.post("/api/traceroute")
def run_traceroute(body: PingIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return w.net.traceroute(body.src, body.dst)


@app.post("/api/reset")
def reset(x_workspace: str | None = Header(default=None)):
    w = store.reset(x_workspace or "default")
    with w.lock:
        return w.topology()


@app.post("/api/sample")
def sample(x_workspace: str | None = Header(default=None)):
    w = store.reset(x_workspace or "default")
    with w.lock:
        w.load_sample()
        return w.topology()


@app.get("/api/capture/stream")
async def stream_capture(workspace: str | None = None, gap: int = 110):
    """Server-Sent Events stream of the most recent capture, one frame per tick.

    EventSource cannot set request headers, so the workspace rides as a query
    param. Each frame is emitted as a dissected JSON object; a final ``end``
    event closes the stream. This is the "live" Packet-Tracer-style feed: the
    frames were computed by the engine, then paced out over the wire.
    """
    w = ws(workspace)
    with w.lock:
        frames = dissect.capture_list(list(w.net.last_capture))
    delay = max(0.0, min(gap, 2000) / 1000.0)

    async def gen():
        for f in frames:
            yield f"data: {json.dumps(f)}\n\n"
            await asyncio.sleep(delay)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/capture.pcap")
def download_pcap(x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        data = pcap.to_pcap(w.net.last_capture)
    return Response(
        content=data,
        media_type="application/vnd.tcpdump.pcap",
        headers={"Content-Disposition": "attachment; filename=netsim-capture.pcap"},
    )


@app.post("/api/tutor")
def ask_tutor(body: TutorIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return tutor.explain(w.net, body.question)


@app.post("/api/lab/apply")
def apply_lab(body: LabSpecIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return w.apply_lab(body.spec)


@app.get("/api/templates")
def get_templates():
    return {"templates": templates.list_templates()}


@app.post("/api/templates/apply")
def apply_template(body: TemplateIn, x_workspace: str | None = Header(default=None)):
    tpl = templates.get_template(body.id)
    if tpl is None:
        raise HTTPException(404, "no such template")
    w = store.reset(x_workspace or "default")
    with w.lock:
        result = w.apply_lab(tpl["spec"])
    return {
        "id": tpl["id"],
        "name": tpl["name"],
        "objective": tpl["objective"],
        "checks": tpl["spec"].get("checks", []),
        "topology": result["topology"],
        "errors": result["errors"],
    }


@app.get("/api/lab/export")
def export_lab(x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return w.export_lab()


@app.post("/api/lab/grade")
def grade_lab(body: GradeIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return labs.grade_lab(w.net, body.checks)


def _run_config(w, device: str, lines: list) -> dict:
    """netmiko-style: enter config mode, run lines, leave. Returns per-line output."""
    if device not in w.net.devices:
        raise HTTPException(404, "no such device")
    sess = w.session(device)
    outputs = []
    errors = []
    for cmd in ["enable", "configure terminal", *lines, "end"]:
        out = sess.execute(cmd)
        if cmd not in ("enable", "configure terminal", "end"):
            outputs.append({"command": cmd, "output": out})
            if out.startswith("%"):
                errors.append(f"'{cmd}' -> {out}")
    return {"applied": outputs, "errors": errors, "prompt": sess.prompt}


@app.post("/api/automation/push")
def automation_push(body: PushIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return _run_config(w, body.device, list(body.config))


@app.get("/api/automation/intent/{device}")
def automation_get_intent(device: str, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        dev = w.net.devices.get(device)
        if dev is None:
            raise HTTPException(404, "no such device")
        return automation.device_intent(dev)


@app.post("/api/automation/intent")
def automation_apply_intent(body: IntentIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        lines = automation.intent_to_cli(body.intent)
        result = _run_config(w, body.device, lines)
        result["generated_cli"] = lines
        result["intent"] = automation.device_intent(w.net.devices[body.device])
        return result


@app.post("/api/automation/render")
def automation_render(body: RenderIn, x_workspace: str | None = Header(default=None)):
    rendered = automation.render_template(body.template, body.variables)
    lines = [ln for ln in (line.rstrip() for line in rendered.splitlines()) if ln]
    out = {"rendered": rendered, "lines": lines}
    if body.push and body.device:
        w = ws(x_workspace)
        with w.lock:
            out["result"] = _run_config(w, body.device, lines)
    return out


@app.post("/api/automation/snapshot")
def automation_snapshot(x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        w.baseline = automation.snapshot(w.net)
        return {"baseline": w.baseline}


@app.get("/api/automation/drift")
def automation_drift(x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        if w.baseline is None:
            raise HTTPException(409, "no baseline snapshot; POST /api/automation/snapshot first")
        current = automation.snapshot(w.net)
        drift = automation.diff_snapshots(w.baseline, current)
        return {"drift": drift, "in_sync": not drift}


@app.post("/api/automation/validate")
def automation_validate(body: ValidateIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return automation.validate(w.net, body.assertions)


@app.post("/api/wireless/device")
def wireless_add_device(body: WifiDeviceIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        try:
            if body.kind == "ap":
                w.net.add_ap(body.name, body.x, body.y)
            elif body.kind == "wifi-client":
                w.net.add_wifi_client(body.name, body.x, body.y)
            elif body.kind == "wlc":
                w.net.add_wlc(body.name)
            else:
                raise HTTPException(400, f"unknown wireless kind {body.kind}")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        w.positions[body.name] = {"x": body.x, "y": body.y}
        return {"ok": True, "topology": w.topology()}


@app.post("/api/wireless/ssid")
def wireless_add_ssid(body: SsidIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        dev = w.net.devices.get(body.device)
        cfg = {"auth": body.auth, "psk": body.psk, "vlan": body.vlan,
               "wmm": body.wmm, "hidden": body.hidden}
        if isinstance(dev, wireless.AccessPoint):
            dev.ssids[body.ssid] = cfg
        elif isinstance(dev, wireless.WirelessLANController):
            dev.wlans[body.ssid] = cfg
        else:
            raise HTTPException(400, "device is not an AP or WLC")
        return {"ok": True, "ssid": body.ssid}


@app.post("/api/wireless/profile")
def wireless_add_profile(body: WifiProfileIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        dev = w.net.devices.get(body.client)
        if not isinstance(dev, wireless.WirelessClient):
            raise HTTPException(400, "not a wireless client")
        dev.profiles[body.ssid] = body.psk
        return {"ok": True}


@app.post("/api/wireless/capwap")
def wireless_capwap(body: CapwapIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        res = wireless.capwap_join(w.net, body.ap, body.wlc)
        if not res["ok"]:
            raise HTTPException(400, res["error"])
        return res


@app.post("/api/wireless/scan")
def wireless_scan(body: WifiActionIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        dev = w.net.devices.get(body.client)
        if not isinstance(dev, wireless.WirelessClient):
            raise HTTPException(400, "not a wireless client")
        return {"results": wireless.scan(w.net, dev)}


@app.post("/api/wireless/associate")
def wireless_associate(body: WifiActionIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return wireless.associate(w.net, body.client, body.ssid)


@app.post("/api/wireless/move")
def wireless_move(body: WifiActionIn, x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        if body.x is None or body.y is None:
            raise HTTPException(400, "x and y are required")
        res = wireless.move(w.net, body.client, body.x, body.y)
        w.positions[body.client] = {"x": body.x, "y": body.y}
        return res


@app.get("/api/wireless/status")
def wireless_status(x_workspace: str | None = Header(default=None)):
    w = ws(x_workspace)
    with w.lock:
        return wireless.status(w.net)


@app.post("/api/wan/ppp")
def wan_ppp(body: PppIn):
    if body.pppoe:
        return wan.pppoe_session(body.auth, body.client, body.server_users)
    return wan.ppp_negotiate(body.auth, body.client, body.server_users)


@app.post("/api/wan/qos/classify")
def wan_qos_classify(body: QosClassifyIn):
    return {"class": wan.classify(body.packet, body.classes)}


@app.post("/api/wan/qos/police")
def wan_qos_police(body: QosPoliceIn):
    pkts = [(float(t), int(s)) for t, s in body.packets]
    return wan.police(pkts, body.cir_bps, body.bc_bytes)


@app.post("/api/wan/qos/schedule")
def wan_qos_schedule(body: QosScheduleIn):
    return {"order": wan.llq_schedule(body.queues, body.rounds)}


def _to_path(d: dict) -> wan.SdwanPath:
    tloc = wan.Tloc(d["system_ip"], d["color"], d.get("encap", "ipsec"))
    return wan.SdwanPath(tloc, loss=d.get("loss", 0.0), latency=d.get("latency", 0.0),
                         jitter=d.get("jitter", 0.0), up=d.get("up", True),
                         preference=d.get("preference", 0))


def _path_out(p: wan.SdwanPath | None) -> dict | None:
    if p is None:
        return None
    return {"system_ip": p.tloc.system_ip, "color": p.tloc.color,
            "encap": p.tloc.encap, "loss": p.loss, "latency": p.latency,
            "jitter": p.jitter, "preference": p.preference, "bfd": wan.bfd_state(p)}


@app.post("/api/wan/sdwan/route")
def wan_sdwan_route(body: SdwanIn):
    paths = [_to_path(d) for d in body.paths]
    res = wan.app_aware_route(paths, body.sla, body.preferred_colors)
    best = wan.omp_best_path(paths)
    return {"chosen": _path_out(res["chosen"]), "omp_best": _path_out(best),
            "sla_met": res["sla_met"], "fallback": res["fallback"],
            "eligible_colors": res["eligible_colors"]}


@app.post("/api/wan/vxlan")
def wan_vxlan(body: VxlanIn):
    fabric = wan.VxlanFabric()
    for v in body.vteps:
        fabric.add_vtep(v["name"], v["ip"], v.get("vnis", []))
    routes = [fabric.evpn_advertise(a["vtep"], a["vni"], a["mac"]) for a in body.advertise]
    out = {"evpn_routes": routes,
           "mac_table": [{"vni": k[0], "mac": k[1], "vtep": v}
                         for k, v in fabric.mac_table.items()]}
    if body.forward:
        out["forward"] = fabric.forward(body.forward["src_vtep"],
                                        body.forward["vni"], body.forward["dst_mac"])
    return out


@app.post("/api/wan/mpls")
def wan_mpls(body: MplsIn):
    return wan.ldp_lsp(body.path, body.fec, php=body.php)


@app.post("/api/wan/sr")
def wan_sr(body: SrIn):
    try:
        return wan.sr_resolve(body.sid_list, body.node_sids)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
