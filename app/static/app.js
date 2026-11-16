const WID = (() => {
  let id = localStorage.getItem("netsim-wid");
  if (!id) { id = "w-" + Math.random().toString(36).slice(2, 10); localStorage.setItem("netsim-wid", id); }
  return id;
})();

async function api(method, path, body) {
  const res = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Workspace": WID },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "request failed");
  }
  return res.json();
}

// hand-drawn inline-SVG device glyphs (stroke inherits the device's color)
const GLYPH = {
  host: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
  switch: '<rect x="2.5" y="8" width="19" height="8" rx="1.5"/><path d="M6 8V5l3 2M18 8V5l-3 2M6 16v3l3-2M18 16v3l-3-2"/>',
  router: '<circle cx="12" cy="12" r="8"/><path d="M12 8V5l-2 1.5M12 8l2-1.5M12 16v3l2-1.5M12 16l-2 1.5M8 12H5l1.5-2M8 12l-1.5 2M16 12h3l-1.5-2M16 12l1.5 2"/>',
  ap: '<rect x="6" y="13" width="12" height="6" rx="1.5"/><circle cx="12" cy="16" r="1"/><path d="M8.5 9.5a5 5 0 0 1 7 0M6.5 7a8 8 0 0 1 11 0"/>',
  "wifi-client": '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/><path d="M10 8.5a3 3 0 0 1 4 0M8.5 6.5a6 6 0 0 1 7 0"/>',
  wlc: '<rect x="3" y="9" width="18" height="6" rx="1.5"/><path d="M6.5 12h.01M9 12h.01"/><path d="M14 11.5a3 3 0 0 1 4 0M12.5 9.8a6 6 0 0 1 7 0"/>',
};
const ROLE = { host: "host", switch: "switch", router: "router", ap: "access point", "wifi-client": "client", wlc: "controller" };
const PREFIX = { host: "PC", switch: "SW", router: "R" };

function glyph(kind) {
  const body = GLYPH[kind] || GLYPH.host;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
}
function shortIf(name) { return (name || "").replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa").replace("Ethernet", "e"); }

let topo = { devices: [], links: [] };
let mode = "none";          // none | link | delete
let linkFirst = null;       // device name awaiting second endpoint
let selected = null;        // device opened in CLI
let lastCapture = [];
let cliHistory = {};        // device -> printed text
let cmdHist = {};           // device -> [previous command lines]
let histPos = {};           // device -> current index while browsing history

const nodesEl = document.getElementById("nodes");
const linksEl = document.getElementById("links");

function nextName(kind) {
  const p = PREFIX[kind];
  let n = 1;
  const names = new Set(topo.devices.map((d) => d.name));
  while (names.has(p + n)) n++;
  return p + n;
}

function deviceByName(name) { return topo.devices.find((d) => d.name === name); }

function firstFreeInterface(name) {
  const d = deviceByName(name);
  if (!d) return null;
  const free = d.interfaces.find((i) => !i.connected);
  return free ? free.name : null;
}

const SVGNS = "http://www.w3.org/2000/svg";

function render() {
  // which devices have at least one live link -> LED lit
  const upDevices = new Set();
  for (const ln of topo.links) if (ln.up) { upDevices.add(ln.a.device); upDevices.add(ln.b.device); }

  // links
  linksEl.innerHTML = "";
  for (const ln of topo.links) {
    const a = deviceByName(ln.a.device), b = deviceByName(ln.b.device);
    if (!a || !b) continue;
    const line = document.createElementNS(SVGNS, "line");
    line.setAttribute("x1", a.position.x); line.setAttribute("y1", a.position.y);
    line.setAttribute("x2", b.position.x); line.setAttribute("y2", b.position.y);
    line.classList.add(ln.up ? "up" : "down");
    line.dataset.key = `${ln.a.device}|${ln.a.interface}|${ln.b.device}|${ln.b.interface}`;
    linksEl.appendChild(line);
    // interface labels near each endpoint, like a real wiring diagram
    addIfLabel(a.position, b.position, ln.a.interface);
    addIfLabel(b.position, a.position, ln.b.interface);
  }

  // nodes
  nodesEl.innerHTML = "";
  for (const d of topo.devices) {
    const el = document.createElement("div");
    el.className = `node ${d.kind}`;
    el.dataset.name = d.name;
    if (selected === d.name) el.classList.add("selected");
    if (linkFirst === d.name) el.classList.add("linking");
    if (upDevices.has(d.name)) el.classList.add("lit");
    el.style.left = d.position.x + "px";
    el.style.top = d.position.y + "px";
    const ip = (d.interfaces.find((i) => i.ip) || {}).ip || "";
    el.innerHTML =
      `<div class="tile">${glyph(d.kind)}<span class="led"></span></div>` +
      `<div class="label">${d.hostname}</div>` +
      `<div class="role">${ROLE[d.kind] || d.kind}</div>` +
      (ip ? `<div class="ip">${ip}</div>` : "");
    el.title = `${d.hostname} — ${ROLE[d.kind] || d.kind}`;
    el.addEventListener("mousedown", (e) => startDrag(e, d));
    el.addEventListener("click", (e) => { e.stopPropagation(); onNodeClick(d); });
    nodesEl.appendChild(el);
  }
  refreshPingSrc();
  updateChrome();
}

function addIfLabel(from, to, ifName) {
  const dx = to.x - from.x, dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const off = 46; // sit on the cable, clear of the node tile
  const t = document.createElementNS(SVGNS, "text");
  t.setAttribute("x", from.x + (dx / len) * off);
  t.setAttribute("y", from.y + (dy / len) * off);
  t.setAttribute("text-anchor", "middle");
  t.setAttribute("class", "if-label");
  t.textContent = shortIf(ifName);
  linksEl.appendChild(t);
}

function updateChrome() {
  const empty = document.getElementById("empty");
  if (empty) empty.classList.toggle("hidden", topo.devices.length > 0);
  const hint = document.getElementById("hint");
  if (hint) hint.classList.toggle("hidden", topo.devices.length === 0);
  const sd = document.getElementById("statDevices");
  const sl = document.getElementById("statLinks");
  if (sd) sd.textContent = `${topo.devices.length} device${topo.devices.length === 1 ? "" : "s"}`;
  if (sl) sl.textContent = `${topo.links.length} link${topo.links.length === 1 ? "" : "s"}`;
}

// ---- drag -----------------------------------------------------------------
let drag = null;
function startDrag(e, d) {
  if (mode !== "none") return;
  drag = { name: d.name, moved: false };
  e.preventDefault();
}
window.addEventListener("mousemove", (e) => {
  if (!drag) return;
  const rect = document.getElementById("canvasWrap").getBoundingClientRect();
  const d = deviceByName(drag.name);
  d.position.x = Math.max(40, e.clientX - rect.left);
  d.position.y = Math.max(40, e.clientY - rect.top);
  drag.moved = true;
  render();
});
window.addEventListener("mouseup", async () => {
  if (drag && drag.moved) {
    const d = deviceByName(drag.name);
    await api("PATCH", "/positions", { name: d.name, x: d.position.x, y: d.position.y });
  }
  drag = null;
});

// ---- interactions ---------------------------------------------------------
async function onNodeClick(d) {
  if (mode === "delete") {
    topo = await api("DELETE", "/devices/" + encodeURIComponent(d.name));
    if (selected === d.name) closeCli();
    render();
    return;
  }
  if (mode === "link") {
    if (!linkFirst) { linkFirst = d.name; render(); return; }
    if (linkFirst === d.name) { linkFirst = null; render(); return; }
    const ifA = firstFreeInterface(linkFirst), ifB = firstFreeInterface(d.name);
    if (!ifA || !ifB) { alert("No free interface on one of the devices."); linkFirst = null; render(); return; }
    try {
      topo = await api("POST", "/links", { dev_a: linkFirst, if_a: ifA, dev_b: d.name, if_b: ifB });
    } catch (e) { alert(e.message); }
    linkFirst = null; setMode("none"); render();
    return;
  }
  openCli(d.name);
}

async function openCli(name) {
  selected = name;
  document.querySelector('[data-tab="cli"]').click();
  const tgt = document.getElementById("cliTarget");
  tgt.classList.add("live");
  tgt.innerHTML = `<span class="dot"></span>${name} console`;
  const out = document.getElementById("cliOut");
  out.textContent = cliHistory[name] ||
    `Connected to ${name}. Type ? anywhere for context help (e.g. 'ip address ?'), ` +
    `Tab to complete, ↑/↓ for history.\n`;
  const { prompt } = await api("GET", `/cli/${encodeURIComponent(name)}/prompt`);
  document.getElementById("cliPrompt").textContent = prompt;
  const input = document.getElementById("cliCmd");
  input.value = "";
  histPos[name] = undefined;
  input.disabled = false; input.focus();
  render();
}
function closeCli() {
  selected = null;
  document.getElementById("cliCmd").disabled = true;
  document.getElementById("cliPrompt").textContent = "";
  const tgt = document.getElementById("cliTarget");
  tgt.classList.remove("live");
  tgt.innerHTML = `<span class="dot"></span>No device selected`;
  document.getElementById("cliOut").textContent = "";
}

// Fetch Cisco-style context help for the current buffer (sends "<buffer> ?").
async function cliHelp(query) {
  const res = await api("POST", "/cli", { device: selected, command: query });
  return res.output || "";
}

// Parse a help block into the literal tokens it offers (for Tab completion).
function helpTokens(block) {
  return block
    .split("\n")
    .map((l) => l.trim().split(/\s+/)[0])
    .filter((t) => t && !t.startsWith("<") && t !== "%");
}

// Echo "<prompt> <buf>?" and print the context-help listing for the buffer,
// without consuming what the user has typed (Cisco IOS behaviour).
async function showContextHelp(buf) {
  if (!selected) return;
  const promptEl = document.getElementById("cliPrompt");
  const out = document.getElementById("cliOut");
  out.textContent += `${promptEl.textContent} ${buf}?\n`;
  const help = await cliHelp(buf + (buf.endsWith(" ") || buf === "" ? "?" : " ?"));
  if (help) out.textContent += help + "\n";
  cliHistory[selected] = out.textContent;
  out.scrollTop = out.scrollHeight;
  document.getElementById("cliCmd").focus();
}

// A trailing '?' (however it was entered) triggers context help and is stripped
// from the buffer, so the user keeps typing right where they left off. This is
// more robust than a keydown handler (works for every keyboard/IME/layout).
document.getElementById("cliCmd").addEventListener("input", (e) => {
  if (!selected) return;
  const v = e.target.value;
  if (v.endsWith("?")) {
    const buf = v.slice(0, -1);
    e.target.value = buf;
    showContextHelp(buf);
  }
});

document.getElementById("cliCmd").addEventListener("keydown", async (e) => {
  if (!selected) return;
  const input = e.target;
  const promptEl = document.getElementById("cliPrompt");
  const out = document.getElementById("cliOut");

  // ----- context help: '?' shows options without consuming the buffer -------
  if (e.key === "?") {
    e.preventDefault();
    showContextHelp(input.value);
    return;
  }

  // ----- tab completion -----------------------------------------------------
  if (e.key === "Tab") {
    e.preventDefault();
    const buf = input.value;
    if (!buf.trim()) return;
    const parts = buf.split(/\s+/);
    const last = parts[parts.length - 1];
    const block = await cliHelp(buf + "?");
    const opts = helpTokens(block).filter((t) => buf.endsWith(" ") || t.startsWith(last));
    if (opts.length === 1) {
      if (buf.endsWith(" ")) input.value = buf + opts[0] + " ";
      else { parts[parts.length - 1] = opts[0]; input.value = parts.join(" ") + " "; }
    } else if (opts.length > 1) {
      out.textContent += `${promptEl.textContent} ${buf}\n` + block + "\n";
      out.scrollTop = out.scrollHeight;
    }
    return;
  }

  // ----- command history ----------------------------------------------------
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    const h = cmdHist[selected] || [];
    if (!h.length) return;
    e.preventDefault();
    let pos = histPos[selected];
    if (pos === undefined) pos = h.length;
    pos += e.key === "ArrowUp" ? -1 : 1;
    pos = Math.max(0, Math.min(h.length, pos));
    histPos[selected] = pos;
    input.value = pos === h.length ? "" : h[pos];
    return;
  }

  if (e.key !== "Enter") return;
  const cmd = input.value;
  input.value = "";
  if (cmd.trim()) {
    cmdHist[selected] = cmdHist[selected] || [];
    cmdHist[selected].push(cmd);
  }
  histPos[selected] = undefined;
  out.textContent += `${promptEl.textContent} ${cmd}\n`;
  const res = await api("POST", "/cli", { device: selected, command: cmd });
  if (res.output) out.textContent += res.output + "\n";
  promptEl.textContent = res.prompt;
  cliHistory[selected] = out.textContent;
  out.scrollTop = out.scrollHeight;
  // device config may have changed (ip, hostname) -> refresh canvas
  topo = await api("GET", "/topology");
  render();
});

// ---- toolbar --------------------------------------------------------------
function setMode(m) {
  mode = m; linkFirst = null;
  document.getElementById("linkBtn").classList.toggle("active", m === "link");
  document.getElementById("deleteBtn").classList.toggle("active", m === "delete");
}

document.querySelectorAll("[data-add]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const kind = btn.dataset.add;
    const name = nextName(kind);
    const x = 120 + Math.random() * 360, y = 100 + Math.random() * 280;
    topo = await api("POST", "/devices", { kind, name, x, y });
    setMode("none"); render();
  })
);
document.getElementById("linkBtn").addEventListener("click", () => setMode(mode === "link" ? "none" : "link"));
document.getElementById("deleteBtn").addEventListener("click", () => setMode(mode === "delete" ? "none" : "delete"));
document.getElementById("resetBtn").addEventListener("click", async () => {
  if (!confirm("Clear the whole topology?")) return;
  topo = await api("POST", "/reset"); cliHistory = {}; closeCli(); render();
});
async function loadSample() {
  topo = await api("POST", "/sample"); cliHistory = {}; closeCli(); render();
}
document.getElementById("sampleBtn").addEventListener("click", loadSample);
document.getElementById("emptySampleBtn").addEventListener("click", loadSample);
document.getElementById("emptyLabBtn").addEventListener("click", () =>
  document.querySelector('[data-tab="lab"]').click()
);

// ---- tabs -----------------------------------------------------------------
document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    document.querySelectorAll(".tab-body").forEach((b) => b.classList.add("hidden"));
    document.getElementById("tab-" + tab.dataset.tab).classList.remove("hidden");
    if (tab.dataset.tab === "capture") openAnalyzer();
    else closeAnalyzer();
  })
);

// ---- ping -----------------------------------------------------------------
function refreshPingSrc() {
  const sel = document.getElementById("pingSrc");
  const cur = sel.value;
  sel.innerHTML = "";
  topo.devices.filter((d) => d.kind === "host").forEach((d) => {
    const o = document.createElement("option");
    o.value = d.name; o.textContent = `${d.hostname} (${(d.interfaces[0] || {}).ip || "no ip"})`;
    sel.appendChild(o);
  });
  if (cur) sel.value = cur;
}
document.getElementById("pingRun").addEventListener("click", async () => {
  const src = document.getElementById("pingSrc").value;
  const dst = document.getElementById("pingDst").value.trim();
  const out = document.getElementById("pingOut");
  if (!src || !dst) { out.textContent = "Pick a source host and a destination IP."; return; }
  out.textContent = "Pinging…";
  const res = await api("POST", "/ping", { src, dst, count: 4 });
  if (!res.ok) { out.textContent = "% " + res.error; return; }
  const dots = res.results.map((r) => (r.reply ? "Reply" : "Request timed out")).join("\n");
  out.textContent = `Pinging ${dst} from ${src}:\n${dots}\n\n` +
    `Packets: Sent = ${res.sent}, Received = ${res.received}, Lost = ${res.sent - res.received} (${res.loss_pct}% loss)`;
  lastCapture = res.capture;
  ingestCapture(res.capture, true);
});

// ===========================================================================
// Packet analyzer — Wireshark-style three panes + Packet-Tracer transport.
// ===========================================================================
let capLog = [];        // rolling capture across all events (renumbered)
let capRows = [];       // filtered subset currently displayed
let capSel = -1;        // selected index into capLog
let capExpr = "";       // active display filter
let playing = false, playTimer = null;
let liveES = null;      // active SSE stream (if any)

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function protoClass(c) {
  const p = (c.protocol || "").toLowerCase();
  if (p.includes("arp")) return "r-arp";
  if (p.includes("dhcp")) return "r-dhcp";
  if (p.includes("icmp")) return "r-icmp";
  if (p === "ip" || p === "ipv4" || p === "ipv6") return "r-ip";
  return "r-ip";
}
function endpointIP(c, side) {
  // pull the L3 address from the dissection if present, else fall back to device
  for (const L of c.layers || []) {
    if (L.name && L.name.startsWith("Internet Protocol")) {
      for (const [k, v] of L.fields) {
        if (side === "src" && k === "Source Address") return v;
        if (side === "dst" && k === "Destination Address") return v;
      }
    }
    if (L.name && L.name.startsWith("Address Resolution")) {
      for (const [k, v] of L.fields) {
        if (side === "src" && k === "Sender IP address") return v;
        if (side === "dst" && k === "Target IP address") return v;
      }
    }
  }
  return side === "src" ? c.from.device : c.to.device;
}
function frameInfo(c) {
  // Wireshark-ish one-line summary derived from the dissection
  const top = (c.layers || [])[ (c.layers || []).length - 1 ];
  const get = (name, key) => {
    const L = (c.layers || []).find((x) => x.name.startsWith(name));
    if (!L) return null;
    const f = L.fields.find((p) => p[0] === key);
    return f ? f[1] : null;
  };
  const p = (c.protocol || "").toUpperCase();
  if (p === "ARP") {
    const op = get("Address Resolution", "Opcode") || "";
    return op.startsWith("request")
      ? `Who has ${get("Address Resolution", "Target IP address")}? Tell ${get("Address Resolution", "Sender IP address")}`
      : `${get("Address Resolution", "Sender IP address")} is at ${get("Address Resolution", "Sender MAC address")}`;
  }
  if (p === "ICMP") {
    const t = (get("Internet Control Message", "Type") || "");
    const id = get("Internet Control Message", "Identifier");
    const seq = get("Internet Control Message", "Sequence Number");
    const kind = t.includes("reply") ? "Echo (ping) reply" : t.includes("request") ? "Echo (ping) request" : t;
    return `${kind}   id=${id}, seq=${seq}, ttl=${get("Internet Protocol", "Time to Live")}`;
  }
  if (p === "DHCP") {
    const L = (c.layers || []).find((x) => x.name.startsWith("Dynamic Host"));
    return L ? L.fields[0][1] : c.summary;
  }
  return c.summary || (top ? top.name : "");
}

// ---- ingest + rolling buffer ---------------------------------------------
function ingestCapture(frames, live) {
  if (!frames || !frames.length) return;
  if (liveES) { liveES.close(); liveES = null; }
  const analyzerOpen = !$("analyzer").classList.contains("hidden");
  if (live && analyzerOpen) {
    streamFrames(frames);          // append one-by-one (SSE, else paced)
  } else {
    frames.forEach(appendFrame);
    refreshCapView(true);
  }
}
function appendFrame(c) {
  const f = Object.assign({}, c);
  f.no = capLog.length + 1;        // renumber across the rolling buffer
  capLog.push(f);
}
function clearCapture() {
  if (liveES) { liveES.close(); liveES = null; }
  stopPlay();
  capLog = []; capRows = []; capSel = -1;
  refreshCapView(true);
  renderDetail(null); renderHex(null);
}

// ---- live streaming (server SSE, with paced client fallback) -------------
function streamFrames(frames) {
  const gap = 110 * playSpeed();
  let i = 0;
  const fallback = () => {
    const tick = () => {
      if (i >= frames.length) return;
      appendFrame(frames[i++]); refreshCapView(true);
      autoSelectLast();
      setTimeout(tick, gap);
    };
    tick();
  };
  // try real server-push first; query param carries the workspace (EventSource
  // can't set headers). Fall back to client pacing on any error.
  try {
    const es = new EventSource(`/api/capture/stream?workspace=${encodeURIComponent(WID)}&gap=${Math.round(gap)}`);
    liveES = es;
    let got = 0;
    es.onmessage = (e) => {
      got++;
      appendFrame(JSON.parse(e.data)); refreshCapView(true); autoSelectLast();
    };
    es.addEventListener("end", () => { es.close(); liveES = null; });
    es.onerror = () => {
      es.close(); liveES = null;
      if (got === 0) fallback();        // SSE unavailable → client pacing
    };
  } catch (_) { fallback(); }
}
function autoSelectLast() {
  if (capRows.length) selectCap(capRows[capRows.length - 1].no, false);
  $("anCount").textContent = `${capLog.length} frame${capLog.length === 1 ? "" : "s"}`;
}

// ---- display filter -------------------------------------------------------
function matchFilter(c, expr) {
  if (!expr) return true;
  // split on " and " / "&&"; every clause must match
  const clauses = expr.toLowerCase().split(/\s+(?:and|&&)\s+/).filter(Boolean);
  return clauses.every((cl) => matchClause(c, cl.trim()));
}
function matchClause(c, cl) {
  const proto = (c.protocol || "").toLowerCase();
  const protos = (c.protocols || []).map((p) => p.toLowerCase());
  let m;
  if ((m = cl.match(/^ip\.addr\s*==\s*(.+)$/))) {
    const ip = m[1].trim();
    return endpointIP(c, "src") === ip || endpointIP(c, "dst") === ip;
  }
  if ((m = cl.match(/^ip\.src\s*==\s*(.+)$/))) return endpointIP(c, "src") === m[1].trim();
  if ((m = cl.match(/^ip\.dst\s*==\s*(.+)$/))) return endpointIP(c, "dst") === m[1].trim();
  if ((m = cl.match(/^vlan\s*==\s*(\d+)$/))) return String(c.vlan || "") === m[1];
  if (cl === "vlan") return !!c.vlan;
  if (["arp", "icmp", "dhcp", "ip", "udp", "eth", "ipv4", "ipv6"].includes(cl))
    return proto === cl || protos.includes(cl) || (cl === "ip" && protos.includes("ip"));
  // bare token: match device name or any address substring
  return (c.from.device + " " + c.to.device + " " + (c.summary || "")).toLowerCase().includes(cl);
}
function refreshCapView(keepSel) {
  capRows = capLog.filter((c) => matchFilter(c, capExpr));
  renderRows();
  $("anCount").textContent = `${capLog.length} frame${capLog.length === 1 ? "" : "s"}`
    + (capExpr ? ` · ${capRows.length} shown` : "");
  const sum = $("capSummary");
  if (sum) sum.innerHTML = capLog.length
    ? `<span class="dot" style="background:var(--st-up)"></span>${capLog.length} frames captured.`
    : `<span class="dot"></span>No frames captured yet.`;
  if (!keepSel && capSel >= 0) selectCap(capSel, false);
  if (!$("capFlowView").classList.contains("hidden")) renderFlow();
}

// ---- packet list ----------------------------------------------------------
function renderRows() {
  const tb = $("capRows");
  tb.innerHTML = "";
  $("capListView").querySelector(".an-listpane").classList.toggle("empty", capRows.length === 0);
  capRows.forEach((c) => {
    const tr = document.createElement("tr");
    tr.className = protoClass(c);
    tr.dataset.no = c.no;
    if (c.no === capSel) tr.classList.add("sel");
    tr.innerHTML =
      `<td class="c-no">${c.no}</td>` +
      `<td class="c-time">${c.time.toFixed(6)}</td>` +
      `<td class="c-src">${esc(endpointIP(c, "src"))}</td>` +
      `<td class="c-dst">${esc(endpointIP(c, "dst"))}</td>` +
      `<td class="c-proto"><span class="pf">${esc(c.protocol)}</span></td>` +
      `<td class="c-len">${c.length}</td>` +
      `<td class="c-info">${esc(frameInfo(c))}</td>`;
    tr.addEventListener("click", () => selectCap(c.no, true));
    tb.appendChild(tr);
  });
}

// ---- selection + detail + hex + cross-highlight ---------------------------
function frameByNo(no) { return capLog.find((c) => c.no === no); }
function selectCap(no, scroll) {
  capSel = no;
  const c = frameByNo(no);
  $("capRows").querySelectorAll("tr").forEach((tr) =>
    tr.classList.toggle("sel", Number(tr.dataset.no) === no));
  if (scroll) {
    const row = $("capRows").querySelector(`tr[data-no="${no}"]`);
    if (row) row.scrollIntoView({ block: "nearest" });
  }
  renderDetail(c); renderHex(c);
  crossHighlight(c);
  if (!$("capFlowView").classList.contains("hidden")) highlightFlow(no);
}
function renderDetail(c) {
  const box = $("capDetail");
  if (!c) { box.innerHTML = `<div class="placeholder">Select a packet to inspect its layers.</div>`; return; }
  box.innerHTML = "";
  (c.layers || []).forEach((L, li) => {
    const node = document.createElement("div");
    node.className = "tl" + (li === 0 ? " collapsed" : "");
    const fields = L.fields.map(([k, v]) => {
      const gen = /^\[.*\]$/.test(k) || /^\[.*\]$/.test(String(v));
      const cls = k.includes("Source") || k.includes("Sender") ? " hl-src"
        : k.includes("Destination") || k.includes("Target") ? " hl-dst" : "";
      return `<div class="tf${gen ? " gen" : ""}${cls}"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`;
    }).join("");
    node.innerHTML =
      `<div class="tl-head"><span class="tl-tw">▾</span><span>${esc(L.name)}</span></div>` +
      `<div class="tl-fields">${fields}</div>`;
    const head = node.querySelector(".tl-head");
    head.addEventListener("click", () => {
      node.classList.toggle("collapsed");
      node.querySelector(".tl-tw").textContent = node.classList.contains("collapsed") ? "▸" : "▾";
      highlightBytes(L.range);
    });
    head.addEventListener("mouseenter", () => highlightBytes(L.range));
    node.querySelector(".tl-tw").textContent = li === 0 ? "▸" : "▾";
    box.appendChild(node);
  });
}
function renderHex(c) {
  const pre = $("capHex");
  if (!c || !c.bytes) { pre.textContent = ""; return; }
  const b = c.bytes;
  let html = "";
  for (let off = 0; off < b.length; off += 16) {
    const hexCells = [];
    let asc = "";
    for (let i = 0; i < 16; i++) {
      if (off + i < b.length) {
        const v = b[off + i];
        hexCells.push(`<span class="hb" data-i="${off + i}">${v.toString(16).padStart(2, "0")}</span>`);
        asc += `<span class="hb" data-i="${off + i}">${v >= 32 && v < 127 ? esc(String.fromCharCode(v)) : "."}</span>`;
      } else { hexCells.push("  "); asc += " "; }
    }
    const left = hexCells.slice(0, 8).join(" ");
    const right = hexCells.slice(8).join(" ");
    html += `<span class="off">${off.toString(16).padStart(4, "0")}</span>  ${left}  ${right}   <span class="asc">${asc}</span>\n`;
  }
  pre.innerHTML = html;
}
function highlightBytes(range) {
  const pre = $("capHex");
  pre.querySelectorAll(".hl").forEach((e) => e.classList.remove("hl"));
  if (!range) return;
  const [lo, hi] = range;
  pre.querySelectorAll(".hb").forEach((e) => {
    const i = Number(e.dataset.i);
    if (i >= lo && i < hi) e.classList.add("hl");
  });
}
function crossHighlight(c) {
  linksEl.querySelectorAll("line.flash").forEach((l) => l.classList.remove("flash"));
  nodesEl.querySelectorAll(".node.xhl").forEach((n) => n.classList.remove("xhl"));
  if (!c) return;
  const key = findLinkKey(c.from.device, c.to.device);
  const line = key && linksEl.querySelector(`line[data-key="${key}"]`);
  if (line) line.classList.add("flash");
  [c.from.device, c.to.device].forEach((d) => {
    const el = nodesEl.querySelector(`.node[data-name="${d}"]`);
    if (el) el.classList.add("xhl");
  });
}

// ---- transport ------------------------------------------------------------
function playSpeed() { return parseFloat($("trSpeed").value) || 1; }
function curIdx() { return capRows.findIndex((c) => c.no === capSel); }
function gotoIdx(i) {
  if (!capRows.length) return;
  i = Math.max(0, Math.min(capRows.length - 1, i));
  const c = capRows[i];
  selectCap(c.no, true);
  // advance the on-canvas dot for this hop
  const a = deviceByName(c.from.device), b = deviceByName(c.to.device);
  if (a && b) flyPacket(a.position, b.position, protoClass(c).slice(2));
}
function stepFwd() { stopPlay(); gotoIdx(curIdx() < 0 ? 0 : curIdx() + 1); }
function stepBack() { stopPlay(); gotoIdx(curIdx() <= 0 ? 0 : curIdx() - 1); }
function startPlay() {
  if (!capRows.length) return;
  playing = true; $("trPlay").innerHTML = "❚❚ Pause"; $("trPlay").classList.add("active");
  let i = curIdx(); if (i < 0 || i >= capRows.length - 1) i = -1;
  const tick = () => {
    if (!playing) return;
    i++;
    if (i >= capRows.length) { stopPlay(); return; }
    gotoIdx(i);
    playTimer = setTimeout(tick, 520 * playSpeed());
  };
  tick();
}
function stopPlay() {
  playing = false; clearTimeout(playTimer); playTimer = null;
  const b = $("trPlay"); if (b) { b.innerHTML = "▶ Play"; b.classList.remove("active"); }
}
function togglePlay() { playing ? stopPlay() : startPlay(); }

// ---- flow / ladder diagram ------------------------------------------------
function renderFlow() {
  const svg = $("capFlow");
  const NS = "http://www.w3.org/2000/svg";
  svg.innerHTML = "";
  const lanes = [];
  capRows.forEach((c) => { [c.from.device, c.to.device].forEach((d) => { if (!lanes.includes(d)) lanes.push(d); }); });
  if (!lanes.length || !capRows.length) {
    svg.setAttribute("height", "120"); svg.setAttribute("width", "100%");
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", 16); t.setAttribute("y", 40); t.setAttribute("class", "flbl");
    t.textContent = "No packets to chart.";
    svg.appendChild(t); return;
  }
  const padL = 24, laneGap = Math.max(150, 760 / Math.max(1, lanes.length - 1));
  const top = 46, rowH = 30;
  const W = padL * 2 + (lanes.length - 1) * laneGap + 40;
  const H = top + capRows.length * rowH + 24;
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const laneX = {}; lanes.forEach((d, i) => (laneX[d] = padL + 20 + i * laneGap));

  svg.innerHTML =
    `<defs><marker id="flowArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="context-stroke"/></marker></defs>`;
  lanes.forEach((d) => {
    const x = laneX[d];
    const ln = document.createElementNS(NS, "line");
    ln.setAttribute("x1", x); ln.setAttribute("y1", top - 10);
    ln.setAttribute("x2", x); ln.setAttribute("y2", H - 8); ln.setAttribute("class", "life");
    svg.appendChild(ln);
    const lbl = document.createElementNS(NS, "text");
    lbl.setAttribute("x", x); lbl.setAttribute("y", 24); lbl.setAttribute("text-anchor", "middle");
    lbl.setAttribute("class", "host-lbl"); lbl.textContent = d;
    svg.appendChild(lbl);
  });
  capRows.forEach((c, i) => {
    const y = top + i * rowH;
    const x1 = laneX[c.from.device], x2 = laneX[c.to.device];
    const arrow = document.createElementNS(NS, "line");
    arrow.setAttribute("x1", x1); arrow.setAttribute("y1", y);
    arrow.setAttribute("x2", x2); arrow.setAttribute("y2", y);
    arrow.setAttribute("class", `arrow ${protoClass(c)}` + (c.no === capSel ? " sel" : ""));
    arrow.dataset.no = c.no;
    svg.appendChild(arrow);
    const mid = (x1 + x2) / 2;
    const lbl = document.createElementNS(NS, "text");
    lbl.setAttribute("x", mid); lbl.setAttribute("y", y - 5); lbl.setAttribute("text-anchor", "middle");
    lbl.setAttribute("class", "flbl"); lbl.textContent = `${c.protocol} ${frameInfo(c).slice(0, 38)}`;
    svg.appendChild(lbl);
    const tm = document.createElementNS(NS, "text");
    tm.setAttribute("x", 6); tm.setAttribute("y", y + 3); tm.setAttribute("class", "ftime");
    tm.textContent = c.time.toFixed(4);
    svg.appendChild(tm);
    const hit = document.createElementNS(NS, "rect");
    hit.setAttribute("x", 0); hit.setAttribute("y", y - rowH / 2);
    hit.setAttribute("width", W); hit.setAttribute("height", rowH); hit.setAttribute("class", "hitrow");
    hit.addEventListener("click", () => selectCap(c.no, true));
    svg.appendChild(hit);
  });
}
function highlightFlow(no) {
  $("capFlow").querySelectorAll(".arrow").forEach((a) =>
    a.classList.toggle("sel", Number(a.dataset.no) === no));
}

// ---- analyzer open/close + wiring ----------------------------------------
function openAnalyzer() {
  $("analyzer").classList.remove("hidden");
  document.body.classList.add("analyzer-open");
  refreshCapView(true);
}
function closeAnalyzer() {
  stopPlay();
  $("analyzer").classList.add("hidden");
  document.body.classList.remove("analyzer-open");
}
$("openAnalyzerBtn").addEventListener("click", openAnalyzer);
$("anClose").addEventListener("click", closeAnalyzer);
$("clearCapBtn").addEventListener("click", clearCapture);
$("trPlay").addEventListener("click", togglePlay);
$("trFwd").addEventListener("click", stepFwd);
$("trBack").addEventListener("click", stepBack);
$("trFirst").addEventListener("click", () => { stopPlay(); gotoIdx(0); });
$("trLast").addEventListener("click", () => { stopPlay(); gotoIdx(capRows.length - 1); });
function applyFilter() {
  const inp = $("capFilter");
  capExpr = inp.value.trim();
  refreshCapView(true);
  inp.classList.toggle("bad", capExpr && capRows.length === 0 && capLog.length > 0);
}
$("capFilterApply").addEventListener("click", applyFilter);
$("capFilter").addEventListener("keydown", (e) => { if (e.key === "Enter") applyFilter(); });
$("capFilterClear").addEventListener("click", () => { $("capFilter").value = ""; capExpr = ""; refreshCapView(true); $("capFilter").classList.remove("bad"); });
$("viewListBtn").addEventListener("click", () => {
  $("viewListBtn").classList.add("active"); $("viewFlowBtn").classList.remove("active");
  $("capListView").classList.remove("hidden"); $("capFlowView").classList.add("hidden");
});
$("viewFlowBtn").addEventListener("click", () => {
  $("viewFlowBtn").classList.add("active"); $("viewListBtn").classList.remove("active");
  $("capFlowView").classList.remove("hidden"); $("capListView").classList.add("hidden");
  renderFlow();
});

$("pcapBtn").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = "/api/capture.pcap?_=" + Date.now();
  a.download = "netsim-capture.pcap";
  // workspace header can't ride on a plain link; fetch as blob with the header instead
  fetch("/api/capture.pcap", { headers: { "X-Workspace": WID } })
    .then((r) => r.blob())
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      a.href = url; a.click(); URL.revokeObjectURL(url);
    });
});

// ---- tutor ----------------------------------------------------------------
const TUTOR_SYS =
  "You are NetSim Tutor, an expert Cisco networking instructor embedded in a " +
  "network simulator. Answer networking questions accurately and concisely. " +
  "When given topology facts, diagnose the root cause and give the exact IOS fix.";

const tutorAIStatus = document.getElementById("tutorAIStatus");
const tutorUseAI = document.getElementById("tutorUseAI");

function setAIStatus(txt) { if (tutorAIStatus) tutorAIStatus.textContent = txt; }

document.getElementById("tutorLoadBtn").addEventListener("click", async () => {
  if (!window.NetSimTutorAI) { setAIStatus("unavailable"); return; }
  if (window.NetSimTutorAI.state() === "ready") { setAIStatus("ready"); return; }
  setAIStatus("loading… (first time downloads the model, then it's cached)");
  try {
    await window.NetSimTutorAI.load((p) => {
      if (p && p.status === "progress" && p.total) {
        const pct = Math.round((p.loaded / p.total) * 100);
        setAIStatus(`downloading ${p.file || "model"}: ${pct}%`);
      } else if (p && p.status) {
        setAIStatus(p.status + (p.file ? ` ${p.file}` : ""));
      }
    });
    tutorUseAI.checked = true;
    setAIStatus(`ready · ${window.NetSimTutorAI.backend}`);
  } catch (e) {
    setAIStatus("failed to load — using rules engine");
  }
});

function groundingFromTutor(res) {
  // Turn the server's deterministic scan into grounding facts for the model.
  if (!res || !res.findings || !res.findings.length) {
    return "No specific misconfiguration was detected by the live topology scan.";
  }
  return "Live topology scan found:\n" + res.findings.map((f) => "- " + f).join("\n");
}

document.getElementById("tutorBtn").addEventListener("click", async () => {
  const q = document.getElementById("tutorQ").value.trim();
  const out = document.getElementById("tutorOut");
  if (!q) { out.textContent = "% ask a question first"; return; }
  out.textContent = "Analysing…";

  // Tier 3 (always available): the deterministic rules engine. We fetch it
  // first so we have grounding facts even when the model answers.
  let res = null;
  try { res = await api("POST", "/tutor", { question: q }); }
  catch (e) { /* server scan optional for general questions */ }

  const useAI = tutorUseAI && tutorUseAI.checked && window.NetSimTutorAI &&
    window.NetSimTutorAI.state() === "ready";

  if (useAI) {
    try {
      const grounding = groundingFromTutor(res);
      const messages = [
        { role: "system", content: TUTOR_SYS },
        { role: "user", content: `${grounding}\n\nQuestion: ${q}` },
      ];
      out.textContent = "Thinking (on-device)…";
      const ans = await window.NetSimTutorAI.answer(messages);
      if (ans) {
        out.textContent = ans +
          `\n\n— source: on-device model (${window.NetSimTutorAI.backend}), grounded by live scan`;
        return;
      }
    } catch (e) {
      // Tier fallthrough to rules engine below.
      setAIStatus("model error — used rules engine");
    }
  }

  if (res) {
    out.textContent = res.answer + `\n\n— source: ${res.source}`;
  } else {
    out.textContent = "% tutor unavailable";
  }
});

// ---- template labs --------------------------------------------------------
async function loadTemplateMenu() {
  const sel = document.getElementById("templateSelect");
  if (!sel) return;
  try {
    const { templates } = await api("GET", "/templates");
    sel.innerHTML = templates
      .map((t) => `<option value="${t.id}">[${t.category}] ${t.name} (${t.devices} dev)</option>`)
      .join("");
  } catch (e) { /* menu stays empty if offline */ }
}
document.getElementById("templateLoadBtn").addEventListener("click", async () => {
  const out = document.getElementById("labOut");
  const id = document.getElementById("templateSelect").value;
  if (!id) { out.textContent = "% no template selected"; return; }
  try {
    const res = await api("POST", "/templates/apply", { id });
    topo = res.topology; cliHistory = {}; closeCli(); render();
    document.getElementById("templateObjective").textContent = "Objective: " + res.objective;
    document.getElementById("labJson").value = JSON.stringify({ checks: res.checks }, null, 2);
    out.textContent = res.errors.length
      ? "Loaded with issues:\n" + res.errors.map((e) => "- " + e).join("\n")
      : `Loaded "${res.name}". Click Grade ✓ to check the objectives.`;
  } catch (e) { out.textContent = "% " + e.message; }
});
loadTemplateMenu();

// ---- lab automation -------------------------------------------------------
document.getElementById("labExportBtn").addEventListener("click", async () => {
  const spec = await api("GET", "/lab/export");
  document.getElementById("labJson").value = JSON.stringify(spec, null, 2);
  const blob = new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "netsim-lab.json"; a.click(); URL.revokeObjectURL(url);
});
document.getElementById("labApplyBtn").addEventListener("click", async () => {
  const out = document.getElementById("labOut");
  let spec;
  try { spec = JSON.parse(document.getElementById("labJson").value); }
  catch (e) { out.textContent = "% invalid JSON: " + e.message; return; }
  try {
    const res = await api("POST", "/lab/apply", { spec });
    topo = res.topology; cliHistory = {}; closeCli(); render();
    out.textContent = res.errors.length
      ? "Applied with issues:\n" + res.errors.map((e) => "- " + e).join("\n")
      : "Lab applied cleanly.";
  } catch (e) { out.textContent = "% " + e.message; }
});
document.getElementById("labGradeBtn").addEventListener("click", async () => {
  const out = document.getElementById("labOut");
  let parsed;
  try { parsed = JSON.parse(document.getElementById("labJson").value); }
  catch (e) { out.textContent = "% invalid JSON: " + e.message; return; }
  const checks = Array.isArray(parsed) ? parsed : (parsed.checks || []);
  if (!checks.length) { out.textContent = "% provide a checks array or a spec with a 'checks' field."; return; }
  const res = await api("POST", "/lab/grade", { checks });
  const lines = res.results.map((r) => `${r.passed ? "PASS" : "FAIL"}  ${r.name} — ${r.detail}`);
  out.textContent = `Score: ${res.score}/100 (${res.passed}/${res.total})\n\n` + lines.join("\n");
});
document.getElementById("replayBtn").addEventListener("click", async () => {
  for (const c of lastCapture) {
    const key = findLinkKey(c.from.device, c.to.device);
    const line = key && linksEl.querySelector(`line[data-key="${key}"]`);
    const a = deviceByName(c.from.device), b = deviceByName(c.to.device);
    if (line && a && b) {
      line.classList.add("flash");
      await flyPacket(a.position, b.position, (c.protocol || "ip").toLowerCase());
      line.classList.remove("flash");
    } else { await sleep(120); }
  }
});

// animate a packet dot travelling along a cable, colored by protocol
function flyPacket(from, to, proto) {
  return new Promise((resolve) => {
    const dot = document.createElementNS(SVGNS, "circle");
    dot.setAttribute("r", "4.5");
    dot.setAttribute("class", `packet ${proto}`);
    linksEl.appendChild(dot);
    const t0 = performance.now(), dur = 420;
    function step(now) {
      const k = Math.min(1, (now - t0) / dur);
      dot.setAttribute("cx", from.x + (to.x - from.x) * k);
      dot.setAttribute("cy", from.y + (to.y - from.y) * k);
      if (k < 1) requestAnimationFrame(step);
      else { dot.remove(); resolve(); }
    }
    requestAnimationFrame(step);
  });
}
function findLinkKey(d1, d2) {
  for (const ln of topo.links) {
    if ((ln.a.device === d1 && ln.b.device === d2) || (ln.a.device === d2 && ln.b.device === d1))
      return `${ln.a.device}|${ln.a.interface}|${ln.b.device}|${ln.b.interface}`;
  }
  return null;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- saved topologies (browser-local library) -----------------------------
// Topologies are stored in localStorage so they survive server restarts /
// redeploys (the in-memory workspace store does not). Each entry is the full
// lab spec (devices, links, positions, per-device running-config, wireless)
// produced by /api/lab/export, so a load restores the topology exactly.
const TOPO_KEY = "netsim-topologies";

function topoStore() {
  try { return JSON.parse(localStorage.getItem(TOPO_KEY)) || {}; }
  catch { return {}; }
}
function topoStoreSave(store) { localStorage.setItem(TOPO_KEY, JSON.stringify(store)); }
function topoSorted() {
  return Object.values(topoStore()).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
}
function topoMsg(text, isErr) {
  const el = document.getElementById("topoMsg");
  el.textContent = text || "";
  el.style.color = isErr ? "var(--bad, #e0584b)" : "";
}
function validSpec(spec) {
  return spec && typeof spec === "object" && Array.isArray(spec.devices) && Array.isArray(spec.links);
}

async function applySpec(spec) {
  const res = await api("POST", "/lab/apply", { spec });
  topo = res.topology; cliHistory = {}; closeCli(); render();
  return res;
}

function renderTopoList() {
  const ul = document.getElementById("topoList");
  const items = topoSorted();
  ul.innerHTML = "";
  if (!items.length) {
    ul.innerHTML = '<li class="topo-empty">No saved topologies yet.</li>';
    return;
  }
  for (const it of items) {
    const li = document.createElement("li");
    li.className = "topo-item";
    const when = it.savedAt ? new Date(it.savedAt).toLocaleString() : "";
    const count = (it.spec.devices || []).length;
    li.innerHTML =
      `<div class="topo-meta"><span class="topo-name"></span>` +
      `<span class="topo-sub">${count} device${count === 1 ? "" : "s"} · ${when}</span></div>` +
      `<div class="topo-actions">` +
      `<button data-act="load">Load</button>` +
      `<button data-act="rename">Rename</button>` +
      `<button data-act="download">Download</button>` +
      `<button data-act="delete" class="danger">Delete</button>` +
      `</div>`;
    li.querySelector(".topo-name").textContent = it.name;
    li.querySelector('[data-act="load"]').addEventListener("click", async () => {
      try {
        const res = await applySpec(it.spec);
        topoMsg(res.errors && res.errors.length
          ? `Loaded "${it.name}" with ${res.errors.length} issue(s).`
          : `Loaded "${it.name}".`, res.errors && res.errors.length);
      } catch (e) { topoMsg("% " + e.message, true); }
    });
    li.querySelector('[data-act="rename"]').addEventListener("click", () => {
      const name = prompt("Rename topology:", it.name);
      if (!name || !name.trim()) return;
      const store = topoStore();
      store[it.id].name = name.trim();
      topoStoreSave(store); renderTopoList(); topoMsg(`Renamed to "${name.trim()}".`);
    });
    li.querySelector('[data-act="download"]').addEventListener("click", () => {
      downloadSpec(it.name, it.spec);
    });
    li.querySelector('[data-act="delete"]').addEventListener("click", () => {
      if (!confirm(`Delete "${it.name}"?`)) return;
      const store = topoStore();
      delete store[it.id];
      topoStoreSave(store); renderTopoList(); topoMsg(`Deleted "${it.name}".`);
    });
    ul.appendChild(li);
  }
}

function downloadSpec(name, spec) {
  const safe = (name || "topology").replace(/[^\w.-]+/g, "_");
  const blob = new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = safe + ".json"; a.click(); URL.revokeObjectURL(url);
}

document.getElementById("topoSaveBtn").addEventListener("click", async () => {
  const input = document.getElementById("topoName");
  let name = input.value.trim();
  if (!name) { topoMsg("Enter a name to save this topology.", true); input.focus(); return; }
  let spec;
  try { spec = await api("GET", "/lab/export"); }
  catch (e) { topoMsg("% " + e.message, true); return; }
  const store = topoStore();
  const existing = Object.values(store).find((t) => t.name.toLowerCase() === name.toLowerCase());
  if (existing && !confirm(`A topology named "${existing.name}" exists. Overwrite it?`)) return;
  const id = existing ? existing.id : "t-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  store[id] = { id, name, savedAt: Date.now(), spec };
  topoStoreSave(store); input.value = ""; renderTopoList();
  topoMsg(`Saved "${name}" (${(spec.devices || []).length} devices).`);
});

document.getElementById("topoImportBtn").addEventListener("click", () => {
  document.getElementById("topoImportFile").click();
});
document.getElementById("topoImportFile").addEventListener("change", (ev) => {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    let spec;
    try { spec = JSON.parse(reader.result); }
    catch (e) { topoMsg("% invalid JSON: " + e.message, true); return; }
    if (!validSpec(spec)) { topoMsg("% not a topology file (missing devices/links).", true); return; }
    const base = file.name.replace(/\.json$/i, "") || "Imported topology";
    const store = topoStore();
    let name = base, i = 2;
    while (Object.values(store).some((t) => t.name === name)) name = `${base} (${i++})`;
    const id = "t-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    store[id] = { id, name, savedAt: Date.now(), spec };
    topoStoreSave(store); renderTopoList();
    try {
      const res = await applySpec(spec);
      topoMsg(res.errors && res.errors.length
        ? `Imported & loaded "${name}" with ${res.errors.length} issue(s).`
        : `Imported & loaded "${name}".`, res.errors && res.errors.length);
    } catch (e) { topoMsg(`Imported "${name}" (load failed: ${e.message}).`, true); }
  };
  reader.readAsText(file);
  ev.target.value = "";
});
renderTopoList();

// ---- NetDevOps automation -------------------------------------------------
// A two-router OSPF backbone used to demo config-as-code, validation and drift.
const AUTO_DEMO_TOPO = {
  devices: [
    { kind: "host", name: "PC1", ports: 1, x: 80, y: 140 },
    { kind: "router", name: "R1", ports: 2, x: 300, y: 240 },
    { kind: "router", name: "R2", ports: 2, x: 560, y: 240 },
    { kind: "host", name: "PC2", ports: 1, x: 780, y: 140 },
  ],
  links: [
    { dev_a: "PC1", if_a: "eth0", dev_b: "R1", if_b: "GigabitEthernet0/1" },
    { dev_a: "R1", if_a: "GigabitEthernet0/0", dev_b: "R2", if_b: "GigabitEthernet0/0" },
    { dev_a: "R2", if_a: "GigabitEthernet0/1", dev_b: "PC2", if_b: "eth0" },
  ],
  config: {}, checks: [],
};
// Per-device config bodies (push wraps enable/configure terminal/end).
const AUTO_DEMO_CONFIG = {
  R1: ["hostname R1", "interface GigabitEthernet0/1", "ip address 192.168.1.1 255.255.255.0",
    "no shutdown", "exit", "interface GigabitEthernet0/0", "ip address 10.0.0.1 255.255.255.252",
    "no shutdown", "exit", "router ospf 1", "network 192.168.1.0 0.0.0.255 area 0",
    "network 10.0.0.0 0.0.0.3 area 0", "exit"],
  R2: ["hostname R2", "interface GigabitEthernet0/1", "ip address 192.168.2.1 255.255.255.0",
    "no shutdown", "exit", "interface GigabitEthernet0/0", "ip address 10.0.0.2 255.255.255.252",
    "no shutdown", "exit", "router ospf 1", "network 192.168.2.0 0.0.0.255 area 0",
    "network 10.0.0.0 0.0.0.3 area 0", "exit"],
  PC1: ["interface eth0", "ip address 192.168.1.10 255.255.255.0", "no shutdown", "exit",
    "ip default-gateway 192.168.1.1"],
  PC2: ["interface eth0", "ip address 192.168.2.10 255.255.255.0", "no shutdown", "exit",
    "ip default-gateway 192.168.2.1"],
};
const AUTO_TPL_DEFAULT = `hostname {{ hostname }}
{% for i in interfaces %}
interface {{ i.name }}
 ip address {{ i.ip }} {{ i.mask }}
 no shutdown
 exit
{% endfor %}
router ospf {{ ospf.pid }}
{% for n in ospf.networks %}
 network {{ n.net }} {{ n.wildcard }} area {{ n.area }}
{% endfor %}
 exit`;
const AUTO_VARS_DEFAULT = JSON.stringify({
  hostname: "R1",
  interfaces: [
    { name: "GigabitEthernet0/1", ip: "192.168.1.1", mask: "255.255.255.0" },
    { name: "GigabitEthernet0/0", ip: "10.0.0.1", mask: "255.255.255.252" },
  ],
  ospf: { pid: 1, networks: [
    { net: "192.168.1.0", wildcard: "0.0.0.255", area: 0 },
    { net: "10.0.0.0", wildcard: "0.0.0.3", area: 0 },
  ] },
}, null, 2);
const AUTO_ASSERT_DEFAULT = JSON.stringify([
  { type: "no_duplicate_ip" },
  { type: "interface_up", device: "R1", interface: "GigabitEthernet0/0" },
  { type: "route_present", device: "R1", network: "192.168.2.0/24" },
  { type: "reachable", src: "PC1", dst: "192.168.2.10" },
], null, 2);

function autoEl(id) { return document.getElementById(id); }

function refreshAutoDevices() {
  const sel = autoEl("autoDevice");
  if (!sel) return;
  const cur = sel.value;
  const names = (topo.devices || []).map((d) => d.name);
  sel.innerHTML = names.map((n) => `<option value="${n}">${n}</option>`).join("");
  if (names.includes(cur)) sel.value = cur;
  autoEl("autoDevs").textContent = names.length
    ? `${names.length} devices: ${names.join(", ")}` : "no devices — load the OSPF demo";
}

let autoSeeded = false;
function seedAutoFields() {
  if (autoSeeded) return;
  autoEl("autoTpl").value = AUTO_TPL_DEFAULT;
  autoEl("autoVars").value = AUTO_VARS_DEFAULT;
  autoEl("autoAssert").value = AUTO_ASSERT_DEFAULT;
  autoSeeded = true;
}

autoEl("autoDemoBtn").addEventListener("click", async () => {
  const out = autoEl("autoRenderOut");
  try {
    const res = await api("POST", "/lab/apply", { spec: AUTO_DEMO_TOPO });
    topo = res.topology;
    for (const [dev, cfg] of Object.entries(AUTO_DEMO_CONFIG)) {
      await api("POST", "/automation/push", { device: dev, config: cfg });
    }
    topo = await api("GET", "/topology");
    cliHistory = {}; closeCli(); render(); refreshAutoDevices();
    out.textContent = "Provisioned PC1–R1=OSPF=R2–PC2 via automation push.\n" +
      "OSPF area 0 between R1/R2; PC1 and PC2 in different subnets.\n" +
      "Now Render & push a device, Validate, or check Drift.";
  } catch (e) { out.textContent = "% " + e.message; }
});

function parseJson(id, out) {
  try { return JSON.parse(autoEl(id).value); }
  catch (e) { out.textContent = "% invalid JSON in " + id + ": " + e.message; return undefined; }
}

autoEl("autoRenderBtn").addEventListener("click", async () => {
  const out = autoEl("autoRenderOut");
  const variables = parseJson("autoVars", out);
  if (variables === undefined) return;
  try {
    const res = await api("POST", "/automation/render", { template: autoEl("autoTpl").value, variables });
    out.textContent = "Rendered config:\n\n" + res.lines.join("\n");
  } catch (e) { out.textContent = "% " + e.message; }
});

autoEl("autoPushBtn").addEventListener("click", async () => {
  const out = autoEl("autoRenderOut");
  const variables = parseJson("autoVars", out);
  if (variables === undefined) return;
  const device = autoEl("autoDevice").value;
  if (!device) { out.textContent = "% no target device — load the OSPF demo first."; return; }
  try {
    const res = await api("POST", "/automation/render",
      { template: autoEl("autoTpl").value, variables, device, push: true });
    topo = await api("GET", "/topology"); render(); refreshAutoDevices();
    const r = res.result || {};
    out.textContent = `Pushed ${res.lines.length} lines to ${device}:\n\n` +
      res.lines.join("\n") +
      (r.errors && r.errors.length ? "\n\n! errors:\n" + r.errors.join("\n") : "\n\n✓ applied cleanly");
  } catch (e) { out.textContent = "% " + e.message; }
});

autoEl("autoPyBtn").addEventListener("click", async () => {
  const device = autoEl("autoDevice").value || "R1";
  const tpl = autoEl("autoTpl").value;
  const vars = autoEl("autoVars").value.trim();
  const script =
    `from automation import NetSimClient\n` +
    `from automation.netdevops import render_configs\n\n` +
    `nc = NetSimClient("${location.origin}")\n\n` +
    `template = """${tpl}"""\n` +
    `variables = ${vars}\n\n` +
    `# render server-side and push to the device\n` +
    `result = nc.render(template, variables, device="${device}", push=True)\n` +
    `print("\\n".join(result["lines"]))\n`;
  const out = autoEl("autoRenderOut");
  try { await navigator.clipboard.writeText(script); out.textContent = "# copied Python to clipboard:\n\n" + script; }
  catch { out.textContent = "# Python SDK script:\n\n" + script; }
});

autoEl("autoValidateBtn").addEventListener("click", async () => {
  const out = autoEl("autoValidateOut");
  const assertions = parseJson("autoAssert", out);
  if (assertions === undefined) return;
  try {
    const res = await api("POST", "/automation/validate", { assertions });
    const lines = res.results.map((r) => {
      const a = r.assertion;
      const label = a.type + (a.device ? ` ${a.device}` : "") + (a.src ? ` ${a.src}->${a.dst}` : "") +
        (a.network ? ` ${a.network}` : "") + (a.interface ? ` ${a.interface}` : "");
      return `${r.passed ? "PASS" : "FAIL"}  ${label} — ${r.detail}`;
    });
    out.textContent = `${res.compliant ? "✓ COMPLIANT" : "✗ NON-COMPLIANT"} — ` +
      `${res.passed}/${res.total} passed (score ${res.score}/100)\n\n` + lines.join("\n");
  } catch (e) { out.textContent = "% " + e.message; }
});

autoEl("autoSnapBtn").addEventListener("click", async () => {
  const out = autoEl("autoDriftOut");
  try {
    const res = await api("POST", "/automation/snapshot");
    const n = Object.keys(res.baseline || {}).length;
    out.textContent = `Saved running-config baseline for ${n} device(s). ` +
      `Change a config (e.g. shut an interface) then Check drift.`;
  } catch (e) { out.textContent = "% " + e.message; }
});

autoEl("autoDriftBtn").addEventListener("click", async () => {
  const out = autoEl("autoDriftOut");
  try {
    const res = await api("GET", "/automation/drift");
    if (res.in_sync) { out.textContent = "✓ in sync — running-config matches the baseline."; return; }
    const lines = ["✗ drift detected vs baseline:"];
    for (const [dev, change] of Object.entries(res.drift)) {
      (change.removed || []).forEach((l) => lines.push(`  - ${dev}: ${l}`));
      (change.added || []).forEach((l) => lines.push(`  + ${dev}: ${l}`));
    }
    out.textContent = lines.join("\n");
  } catch (e) {
    out.textContent = e.message.includes("baseline")
      ? "% no baseline yet — click Save baseline first." : "% " + e.message;
  }
});

// seed fields + refresh device list whenever the Automation tab opens
document.querySelector('[data-tab="auto"]').addEventListener("click", () => {
  seedAutoFields(); refreshAutoDevices();
});

// ---- boot -----------------------------------------------------------------
(async () => { topo = await api("GET", "/topology"); render(); })();
