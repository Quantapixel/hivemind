/* Hivemind live view. Plain JS, no build step. */
(() => {
"use strict";

// ---------------------------------------------------------------- icons
const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  route: '<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M8.5 19H17a3.5 3.5 0 0 0 0-7H7a3.5 3.5 0 0 1 0-7h8.5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  alert: '<path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  focus: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  fn: '<path d="M10 20c4 0 4-16 8-16M7 12h9"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  task: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  diamond: '<path d="M12 3 21 12 12 21 3 12z"/>',
  check: '<path d="m5 12 5 5L20 7"/>',
  impact: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7" opacity=".6"/><circle cx="12" cy="12" r="10.5" opacity=".3"/>',
  rule: '<path d="M4 6h16M4 12h10M4 18h13"/>',
  swap: '<path d="M7 4 3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/>',
  handoff: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  play: '<path d="M7 4v16l13-8z"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5z"/><path d="m3 13 9 5 9-5"/>',
};
const icon = (name, extra = "") =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ${extra}>${ICONS[name] || ""}</svg>`;
const paintIcons = (root = document) =>
  root.querySelectorAll("i[data-icon]").forEach((el) => { if (!el.firstChild) el.innerHTML = icon(el.dataset.icon); });

// ---------------------------------------------------------------- helpers
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (q) => (q || "").split("::").pop();
const fileOf = (q) => (q || "").split("::")[0];
const ago = (ts) => {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
const store = {
  get(k, d) { try { const v = localStorage.getItem("hivemind." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("hivemind." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const DEV_COLORS = ["#6e9eff", "#ff9f5a", "#b88cff", "#3ccfb4", "#ff7ab6", "#f5cb5c", "#59c2ff", "#a3d977"];
const FILE_COLORS = ["#7c8cf8", "#5fb3a1", "#d4a35a", "#c77dba", "#6fa8dc", "#e07a5f", "#8fb86a", "#b39ddb", "#4db6ac", "#d98aa0"];
const hash = (s) => { let h = 0; for (const c of s || "") h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; };
const devColor = (d) => DEV_COLORS[hash(d) % DEV_COLORS.length];
const initials = (d) => (d || "?").slice(0, 1).toUpperCase();
const avatar = (d, cls = "", online = false) =>
  `<span class="avatar ${cls}" style="--c:${devColor(d)}">${esc(initials(d))}${online ? '<span class="dot"></span>' : ""}</span>`;
const fnChip = (q) => `<button class="fn-chip" data-fn="${esc(q)}" title="${esc(q)}">${esc(short(q))}</button>`;
// arrows[i] is the call direction between path[i] and path[i + 1].
const chain = (path, arrows = []) => `<div class="chain">${path.map((p, i) =>
  `<span class="step">${i ? `<span class="arr">${arrows[i - 1] || "→"}</span>` : ""}<code>${esc(short(p))}</code></span>`).join("")}</div>`;
// Conflicts come from one developer's point of view; say it in third person.
const conflictText = (c) => {
  const fn = (q) => `<code class="mono">${esc(short(q))}</code>`;
  if (c.relation === "same") return `Both are changing ${fn(c.target)}.`;
  if (c.relation === "theirs_depends_on_yours")
    return `${esc(c.other_dev)}'s ${fn(c.other_function)} calls into code ${esc(c.dev)} is changing.`;
  return `${esc(c.dev)}'s ${fn(c.target)} calls into code ${esc(c.other_dev)} is changing.`;
};
const conflictArrows = (c) => c.path.slice(1).map(() => (c.relation === "theirs_depends_on_yours" ? "←" : "→"));
const pathArrows = (r) => {
  if (r.direction === "called_by") return r.path.slice(1).map(() => "←");
  if (r.direction === "shared_caller") { const v = r.path.indexOf(r.via); return r.path.slice(1).map((_, i) => (i < v ? "←" : "→")); }
  return r.path.slice(1).map(() => "→");
};

async function api(path, opts = {}) {
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`/api/${path}${sep}team=${encodeURIComponent(S.team)}`, {
    headers: { "content-type": "application/json" }, ...opts,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function toast(text, ic = "check") {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<i data-icon="${ic}"></i>${esc(text)}`;
  paintIcons(el);
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

// ---------------------------------------------------------------- state
const params = new URLSearchParams(location.search);
const S = {
  team: params.get("team") || "",
  data: null,
  selected: null,        // function id
  detail: null,          // /api/function payload
  hover: null,
  focusDev: null,
  pathMode: false,
  pathFrom: null,
  path: null,            // {path, direction}
  layers: store.get("layers", { calls: true, claims: true, memory: true }),
  colorMode: store.get("colorMode", "file"),
  hotspot: "rank",
  tab: "inspect",
  memKind: "decisions",
  memory: null,
  feedDev: null,
  me: store.get("me", ""),
  seenEvents: new Set(),
  userMoved: false,
};

// ---------------------------------------------------------------- theme
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
let C = {};
function readColors() {
  C = Object.fromEntries(["bg", "panel", "text", "text-2", "text-3", "line", "line-2", "red", "amber", "green", "gold", "accent", "grid", "edge", "node", "blue", "path"].map((k) => [k, cssVar("--" + k)]));
}
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  store.set("theme", t);
  $("#themeBtn").innerHTML = `<i data-icon="${t === "dark" ? "sun" : "moon"}"></i>`;
  paintIcons($("#themeBtn"));
  readColors();
  if (Graph) Graph.backgroundColor(C.bg);
}

// ---------------------------------------------------------------- graph
const el = $("#canvas");
let Graph = null;
const known = new Map();
let sig = "";
let claimedBy = new Map();      // fn -> [dev]
let conflictEdges = new Set();  // "a|b"
let conflictNodes = new Set();
let neighbors = new Map();      // id -> Set(ids)
let attached = new Map();       // function id -> Set(intent / attempt / decision ids)
let fileColor = new Map();

const linkKey = (l) => {
  const a = typeof l.source === "object" ? l.source.id : l.source;
  const b = typeof l.target === "object" ? l.target.id : l.target;
  return [a, b];
};
const hasEdge = (set, l) => { const [a, b] = linkKey(l); return set.has(a + "|" + b) || set.has(b + "|" + a); };

// Which nodes are "lit" right now; everything else is dimmed.
// Add the tasks and memories attached to any lit function.
function withAttached(set) {
  for (const id of [...set]) (attached.get(id) || []).forEach((x) => set.add(x));
  return set;
}
function litSet() {
  if (S.path && S.path.path.length) return withAttached(new Set(S.path.path));
  if (S.hover && S.hover.kind === "function") return withAttached(new Set([S.hover.id, ...(neighbors.get(S.hover.id) || [])]));
  if (S.selected && S.detail) {
    return withAttached(new Set([S.selected, ...S.detail.blast_radius.map((b) => b.caller), ...S.detail.callees,
      ...(neighbors.get(S.selected) || [])]));
  }
  if (S.focusDev) {
    const s = new Set();
    for (const [fn, devs] of claimedBy) if (devs.includes(S.focusDev)) { s.add(fn); (neighbors.get(fn) || []).forEach((n) => s.add(n)); }
    for (const n of known.values()) if (n.dev === S.focusDev) s.add(n.id);
    return s;
  }
  return null;
}
let LIT = null;
let BLAST = new Map();  // fn -> distance from selected
let PATH_EDGES = new Set();

function nodeColor(n) {
  if (S.colorMode === "risk") {
    if (n.failed) return C.red;
    if (n.between > 0.35) return C.amber;
    return C.node;
  }
  return fileColor.get(n.file) || C.node;
}
const radius = (n) => 3.4 + Math.sqrt(n.rank || 0) * 3.6 + Math.min(n.callers || 0, 6) * 0.35;

function drawLabel(ctx, text, x, y, size, color, weight = 500, mono = true) {
  ctx.font = `${weight} ${size}px ${mono ? '"JetBrains Mono", monospace' : "Inter, sans-serif"}`;
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.lineWidth = size * 0.45; ctx.strokeStyle = C.bg; ctx.lineJoin = "round";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color; ctx.fillText(text, x, y);
}

function drawNode(n, ctx, k) {
  const lit = !LIT || LIT.has(n.id);
  ctx.globalAlpha = lit ? 1 : 0.13;
  const fs = 11 / k;
  if (n.kind === "function") {
    const devs = claimedBy.get(n.id) || [];
    const r = radius(n);
    const sel = n.id === S.selected;
    const onPath = S.path && S.path.path.includes(n.id);
    const blast = BLAST.get(n.id);
    if (sel || onPath) {
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 7, 0, 2 * Math.PI);
      ctx.fillStyle = onPath ? C.path + "40" : C.text + "22"; ctx.fill();
    } else if (blast !== undefined) {
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 5, 0, 2 * Math.PI);
      ctx.fillStyle = C.amber + (blast === 1 ? "44" : "26"); ctx.fill();
    }
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = conflictNodes.has(n.id) && !devs.length ? C.red : nodeColor(n);
    ctx.fill();
    if (sel) { ctx.lineWidth = 2 / k; ctx.strokeStyle = C.text; ctx.stroke(); }
    devs.forEach((d, i) => {
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 2.6 + i * 2.6, 0, 2 * Math.PI);
      ctx.strokeStyle = devColor(d); ctx.lineWidth = 2; ctx.stroke();
    });
    const important = devs.length || conflictNodes.has(n.id) || sel || onPath || blast !== undefined || (n.rank || 0) > 0.45;
    const hovered = S.hover && S.hover.id === n.id;
    if (lit && (important || hovered || k > 1.7 || (LIT && LIT.has(n.id)))) {
      const strong = devs.length || sel || onPath || hovered;
      drawLabel(ctx, n.name, n.x, n.y + r + 3 + devs.length * 2.6, fs,
        conflictNodes.has(n.id) && !devs.length ? C.red : strong ? C.text : C["text-2"], strong ? 600 : 500);
    }
  } else if (n.kind === "intent") {
    const t = n.name.length > 34 ? n.name.slice(0, 33) + "…" : n.name;
    ctx.font = `600 ${fs}px Inter, sans-serif`;
    const label = `${n.dev} · ${t}`;
    const w = ctx.measureText(label).width + 14 / k, h = fs + 9 / k, x = n.x - w / 2, y = n.y - h / 2;
    const c = devColor(n.dev);
    roundRect(ctx, x, y, w, h, h / 2);
    if (n.status === "handed_off") {
      ctx.fillStyle = C.panel; ctx.fill(); ctx.setLineDash([3 / k, 2 / k]); ctx.strokeStyle = c; ctx.lineWidth = 1.2 / k; ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = c;
    } else {
      ctx.fillStyle = c; ctx.fill(); ctx.fillStyle = "#0b0b0e";
    }
    ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(label, n.x, n.y + 0.5 / k);
  } else if (n.kind === "attempt") {
    const s = 5.5;
    ctx.fillStyle = n.outcome === "failed" ? C.red : n.outcome === "succeeded" ? C.green : C.amber;
    roundRect(ctx, n.x - s / 2, n.y - s / 2, s, s, 1.2); ctx.fill();
  } else if (n.kind === "decision") {
    const s = 4.2;
    ctx.beginPath(); ctx.moveTo(n.x, n.y - s); ctx.lineTo(n.x + s, n.y); ctx.lineTo(n.x, n.y + s); ctx.lineTo(n.x - s, n.y); ctx.closePath();
    ctx.fillStyle = C.gold; ctx.fill();
  }
  ctx.globalAlpha = 1;
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

function linkColor(l) {
  const [a, b] = linkKey(l);
  const dim = LIT && !(LIT.has(a) && LIT.has(b));
  if (l.kind === "calls") {
    if (PATH_EDGES.size && hasEdge(PATH_EDGES, l)) return C.path;
    if (hasEdge(conflictEdges, l) && !dim) return C.red;
    if (BLAST.size && BLAST.has(a) && (BLAST.has(b) || b === S.selected)) return C.amber + "cc";
    return dim ? C.edge.replace(/[\d.]+\)$/, "0.05)") : C.edge;
  }
  if (dim) return "rgba(0,0,0,0)";
  if (l.kind === "claims") return devColor(l.dev) + "99";
  if (l.kind === "touched") return C.red + "55";
  if (l.kind === "governs") return C.gold + "55";
  return C.edge;
}
function linkVisible(l) {
  if (l.kind === "calls") return S.layers.calls || hasEdge(conflictEdges, l) || hasEdge(PATH_EDGES, l);
  if (l.kind === "claims") return S.layers.claims;
  return S.layers.memory;
}
function nodeVisible(n) {
  if (n.kind === "intent") return S.layers.claims;
  if (n.kind === "attempt" || n.kind === "decision") return S.layers.memory;
  return true;
}

function initGraph() {
  Graph = ForceGraph()(el)
    .backgroundColor(C.bg)
    .nodeId("id")
    .nodeLabel(() => "")
    .nodeVisibility(nodeVisible)
    .linkVisibility(linkVisible)
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint((n, color, ctx) => {
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, n.kind === "function" ? radius(n) + 4 : 7, 0, 2 * Math.PI); ctx.fill();
    })
    .linkColor(linkColor)
    .linkWidth((l) => (hasEdge(PATH_EDGES, l) ? 2.6 : hasEdge(conflictEdges, l) ? 2.2 : l.kind === "claims" ? 1.2 : 0.8))
    .linkLineDash((l) => (l.kind === "claims" || l.kind === "governs" || l.kind === "touched" ? [2, 2] : null))
    .linkDirectionalArrowLength((l) => (l.kind === "calls" ? 3 : 0))
    .linkDirectionalArrowRelPos(0.92)
    .linkDirectionalParticles((l) => (l.kind === "calls" && (hasEdge(conflictEdges, l) || hasEdge(PATH_EDGES, l)) ? 2 : 0))
    .linkDirectionalParticleWidth(2.4)
    .linkDirectionalParticleSpeed(0.006)
    .linkDirectionalParticleColor((l) => (hasEdge(PATH_EDGES, l) ? C.path : C.red))
    .onRenderFramePre(drawGrid)
    .onNodeHover((n) => { S.hover = n || null; el.style.cursor = n ? "pointer" : "grab"; recomputeLit(); })
    .onNodeClick(onNodeClick)
    .onBackgroundClick(() => { if (S.pathMode) return; clearSelection(); })
    .onEngineStop(() => { if (!S.userMoved) Graph.zoomToFit(400, 80); })
    .cooldownTicks(160)
    .d3VelocityDecay(0.32);
  Graph.d3Force("charge").strength(-220).distanceMax(320);
  Graph.d3Force("link").distance((l) => (l.kind === "calls" ? 52 : 30));
  new ResizeObserver(() => {
    Graph.width(el.clientWidth).height(el.clientHeight);
    if (!S.userMoved) Graph.zoomToFit(0, 80);
  }).observe(el);
  ["wheel", "pointerdown"].forEach((t) => el.addEventListener(t, () => { S.userMoved = true; }, { passive: true }));
}

function drawGrid(ctx, k) {
  // Dot grid in screen space so it stays crisp at every zoom level.
  const t = ctx.getTransform();
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const step = 22 * devicePixelRatio;
  const ox = ((t.e % step) + step) % step, oy = ((t.f % step) + step) % step;
  ctx.fillStyle = C.grid;
  for (let x = ox; x < ctx.canvas.width; x += step) for (let y = oy; y < ctx.canvas.height; y += step) ctx.fillRect(x, y, 1.4 * devicePixelRatio, 1.4 * devicePixelRatio);
  ctx.restore();
}

function recomputeLit() {
  LIT = litSet();
  BLAST = new Map();
  if (S.selected && S.detail && !S.path) for (const b of S.detail.blast_radius) BLAST.set(b.caller, b.distance);
  PATH_EDGES = new Set();
  if (S.path) for (let i = 0; i + 1 < S.path.path.length; i++) PATH_EDGES.add(S.path.path[i] + "|" + S.path.path[i + 1]);
}

function updateGraph(d) {
  claimedBy = new Map();
  for (const l of d.links) if (l.kind === "claims") claimedBy.set(l.target, [...(claimedBy.get(l.target) || []), l.dev]);
  conflictEdges = new Set();
  conflictNodes = new Set();
  for (const c of d.conflicts) {
    c.path.forEach((p) => conflictNodes.add(p));
    for (let i = 0; i + 1 < c.path.length; i++) conflictEdges.add(c.path[i] + "|" + c.path[i + 1]);
  }
  neighbors = new Map();
  for (const l of d.links) if (l.kind === "calls") {
    if (!neighbors.has(l.source)) neighbors.set(l.source, new Set());
    if (!neighbors.has(l.target)) neighbors.set(l.target, new Set());
    neighbors.get(l.source).add(l.target); neighbors.get(l.target).add(l.source);
  }
  attached = new Map();
  for (const l of d.links) if (l.kind !== "calls") {
    if (!attached.has(l.target)) attached.set(l.target, new Set());
    attached.get(l.target).add(l.source);
  }
  const files = [...new Set(d.nodes.filter((n) => n.kind === "function").map((n) => n.file))].sort();
  fileColor = new Map(files.map((f, i) => [f, FILE_COLORS[i % FILE_COLORS.length]]));

  const next = JSON.stringify([d.nodes.map((n) => n.id + (n.status || "")), d.links.map((l) => l.source + l.target + l.kind)]);
  if (next !== sig) {
    sig = next;
    const nodes = d.nodes.map((n) => Object.assign(known.get(n.id) || {}, n));
    known.clear(); nodes.forEach((n) => known.set(n.id, n));
    Graph.graphData({ nodes, links: d.links.map((l) => ({ ...l })) });
  } else {
    d.nodes.forEach((n) => { const k = known.get(n.id); if (k) Object.assign(k, n); });
  }
  recomputeLit();
  renderLegend(files);
}

function focusNode(id, zoom = false) {
  const n = known.get(id);
  if (!n || n.x === undefined) return;
  S.userMoved = true;
  Graph.centerAt(n.x, n.y, 500);
  if (zoom) Graph.zoom(Math.max(Graph.zoom(), 2.2), 500);
}

// ---------------------------------------------------------------- interactions
async function onNodeClick(n) {
  if (n.kind === "intent") { setFocusDev(n.dev === S.focusDev ? null : n.dev); return; }
  if (n.kind === "attempt" || n.kind === "decision") { openTab("memory"); return; }
  if (S.pathMode) {
    if (!S.pathFrom) { S.pathFrom = n.id; renderBanner(); return; }
    if (n.id === S.pathFrom) return;
    await runPath(S.pathFrom, n.id);
    return;
  }
  select(n.id);
}

async function select(id, { center = false } = {}) {
  S.selected = id; S.path = null;
  syncUrl();
  openTab("inspect");
  try { S.detail = await api(`function?id=${encodeURIComponent(id)}`); } catch { S.detail = null; }
  if (S.selected !== id) return;
  recomputeLit();
  renderInspector();
  if (center) focusNode(id, true);
}
function clearSelection() {
  S.selected = null; S.detail = null; S.path = null; S.pathFrom = null; S.pathMode = false;
  syncUrl(); recomputeLit(); renderInspector(); renderBanner();
}
function setFocusDev(d) {
  S.focusDev = d; S.feedDev = d;
  recomputeLit(); renderDevs(); renderBanner(); renderFeed();
}
function startPath(from = null) {
  S.pathMode = true; S.path = null; S.pathFrom = from;
  recomputeLit(); renderBanner();
}
async function runPath(a, b) {
  const r = await api(`path?from=${encodeURIComponent(a)}&to=${encodeURIComponent(b)}`);
  S.pathMode = false; S.pathFrom = null;
  S.path = r.path.length ? r : { path: [], direction: "none", a, b };
  recomputeLit(); renderBanner();
  if (r.path.length) toast(r.direction === "shared_caller" ? `Linked through ${short(r.via)}` : `${r.path.length - 1} call${r.path.length > 2 ? "s" : ""} apart`, "route");
}
function syncUrl() {
  const p = new URLSearchParams();
  p.set("team", S.team);
  if (S.selected) p.set("fn", S.selected);
  history.replaceState(null, "", "?" + p.toString());
}

// ---------------------------------------------------------------- rendering: chrome
function renderTop(d) {
  const s = d.stats;
  const stat = (n, label, cls = "") => `<div class="stat ${cls}"><b>${n}</b>${label}</div>`;
  $("#topStats").innerHTML =
    (s.conflicts ? stat(s.conflicts, s.conflicts === 1 ? "conflict" : "conflicts", "alert") : "") +
    stat(s.active, "active") + stat(s.functions, "functions") + stat(s.decisions + s.attempts + s.conventions, "memories");
  $("#teamName").textContent = S.team;
}
function renderLegend(files) {
  const dot = (c) => `<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4" fill="${c}"/></svg>`;
  const ring = `<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="2.5" fill="${C.node}"/><circle cx="6" cy="6" r="4.6" fill="none" stroke="${C.blue}" stroke-width="1.6"/></svg>`;
  const sq = (c) => `<svg viewBox="0 0 12 12"><rect x="3" y="3" width="6" height="6" rx="1.3" fill="${c}"/></svg>`;
  const dia = `<svg viewBox="0 0 12 12"><path d="M6 1.8 10.2 6 6 10.2 1.8 6z" fill="${C.gold}"/></svg>`;
  const line = (c) => `<svg viewBox="0 0 12 12"><path d="M1 6h10" stroke="${c}" stroke-width="2"/></svg>`;
  const parts = S.colorMode === "risk"
    ? [`${dot(C.red)}has failed attempts`, `${dot(C.amber)}chokepoint`, `${dot(C.node)}function`]
    : files.slice(0, 5).map((f) => `${dot(fileColor.get(f))}<span class="mono">${esc(f.split("/").pop())}</span>`).concat(files.length > 5 ? [`+${files.length - 5} files`] : []);
  parts.push(`${ring}claimed`, `${sq(C.red)}failed`, `${sq(C.green)}worked`, `${dia}decision`, `${line(C.red)}conflict`);
  $("#legend").innerHTML = parts.map((p) => `<span>${p}</span>`).join("");
}
function renderBanner() {
  const b = $("#banner");
  $("#pathBtn").classList.toggle("on", S.pathMode || !!S.path);
  const x = `<button class="x" data-act="clear-banner" title="Clear (Esc)"><i data-icon="x"></i></button>`;
  let html = "";
  if (S.pathMode) {
    html = S.pathFrom
      ? `<span>From <code class="mono">${esc(short(S.pathFrom))}</code>. Now click the function to connect to.</span>${x}`
      : `<span>Click the first function, or press <kbd>⌘K</kbd> to search.</span>${x}`;
  } else if (S.path) {
    if (!S.path.path.length) html = `<span>No call path connects these functions.</span>${x}`;
    else {
      const a = esc(short(S.path.path[0])), b = esc(short(S.path.path.at(-1)));
      const label = S.path.label || {
        calls: `<b>${a}</b> calls <b>${b}</b>`,
        called_by: `<b>${a}</b> is called by <b>${b}</b>`,
        shared_caller: `<b>${a}</b> and <b>${b}</b> are both reached from <b>${esc(short(S.path.via))}</b>`,
      }[S.path.direction];
      html = `<div class="banner-main"><div class="banner-label">${label}</div>${chain(S.path.path, S.path.arrows || pathArrows(S.path))}</div>${x}`;
    }
  } else if (S.focusDev) {
    html = `${avatar(S.focusDev, "sm")}<span>Showing <b>${esc(S.focusDev)}</b>'s work</span>${x}`;
  }
  b.hidden = !html;
  b.innerHTML = html;
  paintIcons(b);
}

function renderConflicts(d) {
  const n = d.conflicts.length;
  $("#conflictCount").textContent = n;
  $("#conflictCount").classList.toggle("alert", n > 0);
  $("#conflicts").innerHTML = n ? d.conflicts.map((c, i) => `
    <div class="conflict" data-conflict="${i}">
      <div class="who">${avatar(c.dev, "sm")}${esc(c.dev)}<span class="vs">and</span>${avatar(c.other_dev, "sm")}${esc(c.other_dev)}
        <span class="hops">${c.distance === 0 ? "same function" : `${c.distance} hop${c.distance > 1 ? "s" : ""}`}</span></div>
      ${chain(c.path, conflictArrows(c))}
      <div class="why">${conflictText(c)}</div>
    </div>`).join("") : `<div class="empty">Nobody is working on connected code.</div>`;
}
function renderDevs() {
  const d = S.data; if (!d) return;
  $("#teamCount").textContent = d.devs.length;
  $("#devs").innerHTML = d.devs.length ? d.devs.map((v) => {
    const online = v.last_seen && Date.now() / 1000 - v.last_seen < 600;
    return `<div class="dev ${S.focusDev === v.name ? "on" : ""}" data-dev="${esc(v.name)}" style="--c:${devColor(v.name)}">
      ${avatar(v.name, "", online)}
      <div class="meta"><div class="name">${esc(v.name)}</div>
      <div class="sub">${v.open_tasks ? `${v.open_tasks} open task${v.open_tasks > 1 ? "s" : ""}` : "No open tasks"} · ${ago(v.last_seen)}</div></div>
      <i class="focus" data-icon="focus" title="Focus"></i></div>`;
  }).join("") : `<div class="empty">No one has connected yet.</div>`;
  paintIcons($("#devs"));
}
function renderTasks(d) {
  $("#taskCount").textContent = d.tasks.length;
  $("#tasks").innerHTML = d.tasks.length ? d.tasks.map((t) => `
    <div class="task">
      <div class="task-top">${avatar(t.dev, "sm")}<span class="name">${esc(t.dev)}</span><span class="status ${t.status}">${t.status === "handed_off" ? `handed off${t.handoff_to ? " → " + esc(t.handoff_to) : ""}` : "active"}</span></div>
      <div class="desc">${esc(t.description)}</div>
      ${t.handoff_note ? `<div class="note" title="Click to expand">${esc(t.handoff_note)}</div>` : ""}
      <div class="task-foot">${t.claimed.slice(0, 4).map(fnChip).join("")}${t.claimed.length > 4 ? `<span class="empty">+${t.claimed.length - 4}</span>` : ""}
        <button class="release" data-release="${esc(t.intent_id)}" title="Close this task and free its claims">Release</button></div>
    </div>`).join("") : `<div class="empty">No open tasks.</div>`;
}
function renderHotspots(d) {
  const key = S.hotspot;
  const fns = d.nodes.filter((n) => n.kind === "function" && n[key] > 0).sort((a, b) => b[key] - a[key]).slice(0, 7);
  $("#hotspots").innerHTML = fns.length ? fns.map((n, i) => `
    <div class="hot ${i < 3 ? "top" : ""}" data-fn="${esc(n.id)}" title="${esc(n.id)}">
      <div class="nm">${esc(n.name)}<small>${n.callers} caller${n.callers === 1 ? "" : "s"}</small></div>
      <div class="bar"><span style="width:${Math.max(6, n[key] * 100)}%"></span></div>
    </div>`).join("") : `<div class="empty">Not enough calls to rank yet.</div>`;
}

// ---------------------------------------------------------------- rendering: inspector
function renderInspector() {
  const box = $("#tab-inspect");
  const d = S.detail;
  if (!S.selected || !d || d.error) { box.innerHTML = introHtml(); paintIcons(box); return; }
  const blastByD = {};
  d.blast_radius.forEach((b) => (blastByD[b.distance] ||= []).push(b.caller));
  const conflictHere = (S.data?.conflicts || []).filter((c) => c.path.includes(d.id));
  const failed = d.attempts.filter((a) => a.outcome === "failed").length;
  box.innerHTML = `
    <div class="insp-head">
      <div class="kicker"><i data-icon="fn"></i>Function</div>
      <h2>${esc(d.name)}</h2>
      <div class="loc">${esc(d.file)}:${d.line}${d.end && d.end !== d.line ? `–${d.end}` : ""}</div>
    </div>
    <div class="badges">
      <span class="badge" title="PageRank across the call graph">Core <b>${Math.round(d.rank * 100)}</b></span>
      <span class="badge ${d.between > 0.35 ? "amber" : ""}" title="Share of call paths that run through it">Chokepoint <b>${Math.round(d.between * 100)}</b></span>
      <span class="badge">Used by <b>${d.blast_radius.length}</b></span>
      ${failed ? `<span class="badge red">${failed} failed attempt${failed > 1 ? "s" : ""}</span>` : ""}
      ${conflictHere.length ? `<span class="badge red">On a conflict path</span>` : ""}
    </div>
    <div class="actions">
      <button class="btn sm" data-act="path-from"><i data-icon="route"></i>Path from here</button>
      <button class="btn sm" data-act="add-decision"><i data-icon="diamond"></i>Add decision</button>
      <button class="btn sm" data-act="center"><i data-icon="focus"></i>Center</button>
    </div>

    ${d.claims.length ? `<div class="section"><h4>In progress</h4>${d.claims.map((c) => `
      <div class="card"><div class="title">${avatar(c.dev, "sm")}<span>${esc(c.description)}</span></div>
      <div class="foot"><span>${esc(c.dev)}</span><span class="sep">·</span><span class="status ${c.status}">${c.status === "handed_off" ? "handed off" : "active"}</span></div></div>`).join("")}</div>` : ""}

    ${conflictHere.length ? `<div class="section"><h4>Conflicts</h4>${conflictHere.map((c) => `
      <div class="conflict"><div class="who">${avatar(c.dev, "sm")}${esc(c.dev)}<span class="vs">and</span>${avatar(c.other_dev, "sm")}${esc(c.other_dev)}</div>${chain(c.path, conflictArrows(c))}<div class="why">${conflictText(c)}</div></div>`).join("")}</div>` : ""}

    <div class="section"><h4>Why it's like this <span class="count">${d.decisions.length}</span></h4>
      ${d.decisions.length ? d.decisions.map((x) => `
        <div class="card"><div class="title"><svg class="glyph" viewBox="0 0 14 14"><path d="M7 1.5 12.5 7 7 12.5 1.5 7z" fill="${C.gold}"/></svg><span>${esc(x.title)}</span></div>
          ${x.rationale ? `<div class="body">${esc(x.rationale)}</div>` : ""}
          <div class="foot">${avatar(x.dev, "sm")}<span>${esc(x.dev)}</span>${x.source ? `<span class="sep">·</span><span class="mono">${esc(x.source)}</span>` : ""}<span class="sep">·</span><span>${ago(x.ts)}</span></div></div>`).join("")
        : `<div class="empty">No decisions recorded for this code.</div>`}
    </div>

    <div class="section"><h4>History <span class="count">${d.attempts.length}</span></h4>
      ${d.attempts.length ? `<div class="timeline">${d.attempts.map((a) => `
        <div class="tl" style="--c:${a.outcome === "failed" ? C.red : a.outcome === "succeeded" ? C.green : C.amber}">
          <div class="what">${esc(a.summary)}</div>
          ${a.reason ? `<div class="why">${esc(a.reason)}</div>` : ""}
          <div class="meta"><span class="tag ${esc(a.outcome)}">${esc(a.outcome)}</span><span>${esc(a.dev)}</span><span>·</span><span>${ago(a.ts)}</span>
            ${a.relation === "neighbour" ? `<span>·</span><span>on ${fnChip(a.function)}</span>` : ""}</div>
        </div>`).join("")}</div>` : `<div class="empty">Nothing tried here yet.</div>`}
    </div>

    <div class="section"><h4>Impact if changed <span class="count">${d.blast_radius.length}</span></h4>
      ${d.blast_radius.length ? Object.entries(blastByD).map(([k, fns]) => `
        <div class="impact-row"><div class="d">${k} hop${k > 1 ? "s" : ""}</div><div class="fns">${fns.map(fnChip).join("")}</div></div>`).join("")
        : `<div class="empty">Nothing calls this function.</div>`}
    </div>

    <div class="section two-col">
      <div><h4>Calls</h4><div class="list-fns">${d.callees.map(fnChip).join("") || `<span class="empty">Nothing</span>`}</div></div>
      <div><h4>Called by</h4><div class="list-fns">${d.callers.map(fnChip).join("") || `<span class="empty">Nothing</span>`}</div></div>
    </div>

    <div class="section"><h4>Who knows it</h4>
      ${d.experts.length ? `<div class="people">${d.experts.map((x) => `<span class="person">${avatar(x.dev, "sm")}${esc(x.dev)}<small>${[
        x.claims && `${x.claims} claim${x.claims > 1 ? "s" : ""}`, x.attempts && `${x.attempts} attempt${x.attempts > 1 ? "s" : ""}`, x.decisions && `${x.decisions} decision${x.decisions > 1 ? "s" : ""}`,
      ].filter(Boolean).join(", ")}</small></span>`).join("")}</div>` : `<div class="empty">Nobody has worked on this yet.</div>`}
    </div>
    ${d.conventions.length ? `<div class="section"><h4>Conventions</h4>${d.conventions.map((c) => `<div class="card"><div class="title"><i data-icon="rule"></i><span>${esc(c.rule)}</span></div><div class="foot">${esc(c.dev)} · ${c.scope === "repo" ? "repo-wide" : "this file"}</div></div>`).join("")}</div>` : ""}
  `;
  paintIcons(box);
}
function introHtml() {
  const s = S.data?.stats;
  const key = (keys, text) => `<div>${keys.map((k) => `<kbd>${k}</kbd>`).join("")}</div><div>${text}</div>`;
  return `<div class="intro">
    <h2>Team graph</h2>
    <p>Every coding agent on the team reads and writes this graph. Click a function to see why it is the way it is, who is working on it and what depends on it.</p>
    ${s ? `<div class="grid-stats">
      <div class="gs ${s.conflicts ? "alert" : ""}"><b>${s.conflicts}</b><span>conflicts</span></div>
      <div class="gs"><b>${s.active}</b><span>active tasks</span></div>
      <div class="gs"><b>${s.handed_off}</b><span>handed off</span></div>
      <div class="gs"><b>${s.decisions}</b><span>decisions</span></div>
      <div class="gs"><b>${s.attempts}</b><span>attempts</span></div>
      <div class="gs"><b>${s.functions}</b><span>functions</span></div>
    </div>` : ""}
    <div class="section"><h4>Shortcuts</h4><div class="keys">
      ${key(["⌘", "K"], "Search functions, people and tasks")}
      ${key(["P"], "Find how two functions connect")}
      ${key(["F"], "Fit the graph to the screen")}
      ${key(["D"], "Record a decision")}
      ${key(["Esc"], "Clear selection, path or focus")}
    </div></div>
  </div>`;
}

// ---------------------------------------------------------------- rendering: memory & activity
async function loadMemory() {
  try { S.memory = await api("memory"); } catch { return; }
  const m = S.memory;
  $("#memCount").textContent = m.decisions.length + m.attempts.length + m.conventions.length || "";
  renderMemory();
}
function renderMemory() {
  const m = S.memory; if (!m) return;
  const q = $("#memSearch").value.trim().toLowerCase();
  const match = (...xs) => !q || xs.join(" ").toLowerCase().includes(q);
  $("#memKind").querySelectorAll("button").forEach((b) => { b.textContent = `${b.dataset.v[0].toUpperCase() + b.dataset.v.slice(1)} ${m[b.dataset.v].length}`; });
  let html = "";
  if (S.memKind === "decisions") {
    const xs = m.decisions.filter((x) => match(x.title, x.rationale, x.dev, x.source, ...x.governs));
    html = xs.map((x) => `<div class="card"><div class="title"><svg class="glyph" viewBox="0 0 14 14"><path d="M7 1.5 12.5 7 7 12.5 1.5 7z" fill="${C.gold}"/></svg><span>${esc(x.title)}</span></div>
      ${x.rationale ? `<div class="body">${esc(x.rationale)}</div>` : ""}
      <div class="foot">${avatar(x.dev, "sm")}${esc(x.dev)}${x.source ? `<span class="sep">·</span><span class="mono">${esc(x.source)}</span>` : ""}<span class="sep">·</span>${ago(x.ts)}</div>
      ${x.governs.length ? `<div class="foot">${x.governs.map((g) => (g.includes("::") ? fnChip(g) : `<span class="fn-chip">${esc(g)}</span>`)).join("")}</div>` : ""}</div>`).join("");
  } else if (S.memKind === "attempts") {
    const xs = m.attempts.filter((x) => match(x.summary, x.reason, x.dev, x.outcome, ...x.touched));
    html = xs.map((x) => `<div class="card"><div class="title"><span class="tag ${esc(x.outcome)}">${esc(x.outcome)}</span><span>${esc(x.summary)}</span></div>
      ${x.reason ? `<div class="body">${esc(x.reason)}</div>` : ""}
      <div class="foot">${avatar(x.dev, "sm")}${esc(x.dev)}<span class="sep">·</span>${ago(x.ts)}</div>
      <div class="foot">${x.touched.map(fnChip).join("")}</div></div>`).join("");
  } else {
    const xs = m.conventions.filter((x) => match(x.rule, x.dev, ...x.files));
    html = xs.map((x) => `<div class="card"><div class="title"><i data-icon="rule"></i><span>${esc(x.rule)}</span></div>
      <div class="foot">${avatar(x.dev, "sm")}${esc(x.dev)}<span class="sep">·</span>${x.scope === "repo" ? "repo-wide" : x.files.map((f) => `<span class="fn-chip">${esc(f)}</span>`).join("")}<span class="sep">·</span>${ago(x.ts)}</div></div>`).join("");
  }
  $("#memList").innerHTML = html || `<div class="empty center">${q ? "Nothing matches that filter." : `No ${S.memKind} yet. Agents add them as they work, or add one yourself.`}</div>`;
  paintIcons($("#memList"));
}
const EVENT_STYLE = {
  start: ["play", "var(--blue)"], claim: ["task", "var(--text-3)"], release: ["check", "var(--green)"],
  attempt: ["bolt", "var(--amber)"], decision: ["diamond", "var(--gold)"], convention: ["rule", "var(--text-2)"],
  handoff: ["handoff", "var(--amber)"], resume: ["swap", "var(--blue)"],
};
function renderFeed() {
  const d = S.data; if (!d) return;
  const devs = [...new Set(d.events.map((e) => e.dev))].sort();
  $("#feedFilter").innerHTML = `<button class="chip all ${!S.feedDev ? "on" : ""}" data-feed="">Everyone</button>` +
    devs.map((v) => `<button class="chip ${S.feedDev === v ? "on" : ""}" data-feed="${esc(v)}">${avatar(v, "sm")}${esc(v)}</button>`).join("");
  const first = S.seenEvents.size === 0;
  const evs = d.events.filter((e) => !S.feedDev || e.dev === S.feedDev);
  $("#feed").innerHTML = evs.map((e) => {
    const k = `${e.ts}|${e.dev}|${e.kind}`;
    const fresh = !first && !S.seenEvents.has(k);
    S.seenEvents.add(k);
    const [ic, col] = EVENT_STYLE[e.kind] || ["bolt", "var(--text-3)"];
    let text = e.text;
    if (e.kind === "attempt") {
      const m = /^\[(\w+)\]\s*(.*)$/.exec(text);
      if (m) text = `<span class="tag ${esc(m[1])}">${esc(m[1])}</span> ${esc(m[2])}`; else text = esc(text);
    } else text = esc(text);
    const targets = (e.targets || []).slice(0, 3).map(fnChip).join(" ");
    return `<div class="ev ${fresh ? "new" : ""}"><span class="k" style="color:${col}"><i data-icon="${ic}"></i></span>
      <div class="txt"><b>${esc(e.dev)}</b> ${esc(e.kind === "claim" ? "claimed code" : e.kind)}<span class="t">${text}</span>${targets ? `<div class="task-foot" style="margin-top:5px">${targets}</div>` : ""}</div>
      <time>${ago(e.ts)}</time></div>`;
  }).join("") || `<div class="empty center">Nothing has happened yet.</div>`;
  paintIcons($("#feed"));
}

function openTab(t) {
  S.tab = t;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  ["inspect", "memory", "activity"].forEach((x) => { $("#tab-" + x).hidden = x !== t; });
  if (t === "memory") loadMemory();
}

// ---------------------------------------------------------------- palette
let pItems = [], pIndex = 0;
function openPalette(initial = "") {
  $("#palette").hidden = false;
  const inp = $("#paletteInput");
  inp.value = initial; inp.focus();
  renderPalette();
}
function closePalette() { $("#palette").hidden = true; }
function score(text, q) {
  text = text.toLowerCase();
  const i = text.indexOf(q);
  if (i === 0) return 3; if (i > 0) return 2;
  let j = 0; for (const c of text) if (c === q[j]) j++;
  return j === q.length ? 1 : 0;
}
function hl(text, q) {
  const i = q ? text.toLowerCase().indexOf(q) : -1;
  return i < 0 ? esc(text) : esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
}
function renderPalette() {
  const q = $("#paletteInput").value.trim().toLowerCase();
  const d = S.data;
  const groups = [];
  const actions = [
    { t: "Find path between two functions", ic: "route", hint: "P", run: () => startPath(S.selected) },
    { t: "Record a decision", ic: "diamond", hint: "D", run: () => openForm("decision") },
    { t: "Add a convention", ic: "rule", run: () => openForm("convention") },
    { t: "Fit graph to screen", ic: "fit", hint: "F", run: () => Graph.zoomToFit(400, 80) },
    { t: `Switch to ${document.documentElement.dataset.theme === "dark" ? "light" : "dark"} theme`, ic: "sun", run: () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark") },
    { t: "Show memory", ic: "layers", run: () => openTab("memory") },
    { t: "Show activity", ic: "bolt", run: () => openTab("activity") },
  ];
  if (d) {
    const fns = d.nodes.filter((n) => n.kind === "function")
      .map((n) => ({ n, s: q ? Math.max(score(n.name, q) * 2, score(n.id, q)) : n.rank }))
      .filter((x) => x.s > 0).sort((a, b) => b.s - a.s).slice(0, q ? 8 : 5)
      .map(({ n }) => ({ t: n.name, mono: true, sub: `${n.file}:${n.line}`, ic: "fn", hint: (claimedBy.get(n.id) || []).join(", "), run: () => select(n.id, { center: true }) }));
    if (fns.length) groups.push(["Functions", fns]);
    const devs = d.devs.filter((v) => !q || score(v.name, q)).map((v) => ({
      t: v.name, sub: `${v.open_tasks} open task${v.open_tasks === 1 ? "" : "s"} · ${ago(v.last_seen)}`, ic: "user", run: () => setFocusDev(v.name),
    }));
    if (devs.length) groups.push(["People", devs]);
    const tasks = d.tasks.filter((t) => !q || score(t.description + " " + t.dev, q)).slice(0, 5).map((t) => ({
      t: t.description, sub: `${t.dev} · ${t.status.replace("_", " ")}`, ic: "task", run: () => { setFocusDev(t.dev); if (t.claimed[0]) select(t.claimed[0], { center: true }); },
    }));
    if (tasks.length) groups.push(["Tasks", tasks]);
  }
  const acts = actions.filter((a) => !q || score(a.t, q));
  if (acts.length) groups.push(["Actions", acts]);
  pItems = groups.flatMap(([, xs]) => xs);
  pIndex = Math.min(pIndex, Math.max(0, pItems.length - 1));
  let i = 0;
  $("#paletteList").innerHTML = groups.map(([g, xs]) => `<div class="p-group">${g}</div>` + xs.map((x) => `
    <div class="p-item ${i === pIndex ? "on" : ""}" data-i="${i++}">
      <span class="p-ic"><i data-icon="${x.ic}"></i></span>
      <div class="p-main"><div class="p-title ${x.mono ? "mono" : ""}">${hl(x.t, q)}</div>${x.sub ? `<div class="p-sub">${esc(x.sub)}</div>` : ""}</div>
      ${x.hint ? `<span class="p-hint">${esc(x.hint)}</span>` : ""}
    </div>`).join("")).join("") || `<div class="empty center">No results for “${esc(q)}”.</div>`;
  paintIcons($("#paletteList"));
}
function runPaletteItem(i) { const it = pItems[i]; if (!it) return; closePalette(); it.run(); }

// ---------------------------------------------------------------- forms
function openForm(kind, preset = {}) {
  const f = $("#modalForm");
  const me = `<label>Your name <small>shown as the author</small></label><input name="dev" required value="${esc(S.me)}" placeholder="e.g. alice">`;
  if (kind === "decision") {
    f.innerHTML = `<h2>Record a decision</h2><p class="lede">Every agent that touches the governed code will see this before it edits.</p>
      <label>Decision</label><input name="title" required placeholder="Keep HMAC tokens until mobile v3 ships">
      <label>Why <small>optional</small></label><textarea name="rationale" rows="3" placeholder="Mobile v2 parses the token by hand; changing the format logs users out."></textarea>
      <label>Governs <small>functions or files, comma separated</small></label><input name="targets" list="fnList" value="${esc(preset.targets || "")}" placeholder="validate_token, shop/tokens.py">
      <div class="row"><div>${me}</div><div><label>Source <small>optional</small></label><input name="source" placeholder="PR #42"></div></div>`;
  } else {
    f.innerHTML = `<h2>Add a convention</h2><p class="lede">A rule for how code is written here. Agents get it as context for the files it applies to.</p>
      <label>Rule</label><input name="rule" required placeholder="All DB access goes through shop/db.py helpers">
      <label>Applies to <small>files, comma separated; empty means the whole repo</small></label><input name="files" placeholder="shop/orders.py">
      ${me}`;
  }
  f.innerHTML += `<div class="err" id="formErr"></div><div class="foot"><button type="button" class="btn" data-act="close-modal">Cancel</button><button class="btn primary" type="submit">Save</button></div>`;
  f.dataset.kind = kind;
  $("#modal").hidden = false;
  f.querySelector("input:not([value]), input[value='']")?.focus();
}
async function submitForm(e) {
  e.preventDefault();
  const f = e.target, kind = f.dataset.kind, v = Object.fromEntries(new FormData(f));
  const list = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);
  S.me = v.dev.trim(); store.set("me", S.me);
  try {
    if (kind === "decision") await api("decision", { method: "POST", body: JSON.stringify({ dev: S.me, title: v.title, rationale: v.rationale, source: v.source, targets: list(v.targets) }) });
    else await api("convention", { method: "POST", body: JSON.stringify({ dev: S.me, rule: v.rule, files: list(v.files) }) });
  } catch (err) { $("#formErr").textContent = err.message; return; }
  $("#modal").hidden = true;
  toast(kind === "decision" ? "Decision recorded" : "Convention added");
  await refresh();
  loadMemory();
  if (S.selected) select(S.selected);
}

// ---------------------------------------------------------------- teams
async function loadTeams() {
  const r = await fetch("/api/teams").then((x) => x.json());
  if (!S.team) S.team = r.default;
  if (!r.teams.includes(S.team) && r.teams.length) S.team = r.teams.includes(r.default) ? r.default : r.teams[0];
  $("#teamMenu").innerHTML = r.teams.map((t) => `<button data-team="${esc(t)}" class="${t === S.team ? "on" : ""}"><span>${esc(t)}</span>${t === S.team ? '<i data-icon="check"></i>' : ""}</button>`).join("")
    || `<div class="empty" style="padding:8px 10px">No team graphs yet. Run hivemind index.</div>`;
  paintIcons($("#teamMenu"));
  $("#teamName").textContent = S.team;
}
function switchTeam(t) {
  S.team = t; S.selected = null; S.detail = null; S.path = null; S.focusDev = null; S.feedDev = null; S.memory = null;
  S.seenEvents = new Set(); S.userMoved = false; sig = ""; known.clear();
  syncUrl(); loadTeams(); refresh(); renderInspector(); renderBanner();
}

// ---------------------------------------------------------------- polling
let failures = 0;
async function refresh() {
  try {
    const d = await api("state");
    S.data = d; failures = 0; $("#live").classList.remove("stale");
    updateGraph(d); renderTop(d); renderConflicts(d); renderDevs(); renderTasks(d); renderHotspots(d); renderFeed();
    if (!S.detail && S.tab === "inspect") renderInspector();
    $("#fnList").innerHTML = d.nodes.filter((n) => n.kind === "function").map((n) => `<option value="${esc(n.name)}">${esc(n.file)}</option>`).join("");
  } catch {
    failures++; if (failures > 1) $("#live").classList.add("stale");
  }
}
async function loop() { await refresh(); setTimeout(loop, 2000); }

// ---------------------------------------------------------------- events
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-fn],[data-dev],[data-release],[data-act],[data-conflict],[data-team],[data-feed],.note,[data-tab]");
  if (!t) { if (!e.target.closest("#teamSwitch")) $("#teamMenu").hidden = true; return; }
  if (t.dataset.tab) return openTab(t.dataset.tab);
  if (t.dataset.fn !== undefined && t.dataset.fn) {
    if (S.pathMode && S.pathFrom && S.pathFrom !== t.dataset.fn) return runPath(S.pathFrom, t.dataset.fn);
    return select(t.dataset.fn, { center: true });
  }
  if (t.dataset.dev) return setFocusDev(S.focusDev === t.dataset.dev ? null : t.dataset.dev);
  if (t.dataset.feed !== undefined) { S.feedDev = t.dataset.feed || null; return renderFeed(); }
  if (t.dataset.team) { $("#teamMenu").hidden = true; return switchTeam(t.dataset.team); }
  if (t.dataset.conflict) {
    const c = S.data.conflicts[+t.dataset.conflict];
    S.selected = null; S.detail = null;
    S.path = { path: c.path, direction: "calls", arrows: conflictArrows(c),
      label: conflictText(c) };
    recomputeLit(); renderBanner(); renderInspector(); focusNode(c.path[Math.floor(c.path.length / 2)]);
    return;
  }
  if (t.classList.contains("note")) return t.classList.toggle("full");
  if (t.dataset.release) {
    const who = S.me || prompt("Your name (recorded as who released it)") || "";
    if (!who) return;
    S.me = who; store.set("me", who);
    return api("release", { method: "POST", body: JSON.stringify({ dev: who, intent_id: t.dataset.release }) })
      .then(() => { toast("Task released"); refresh(); });
  }
  const act = t.dataset.act;
  if (act === "clear-banner") { S.path = null; S.pathMode = false; S.pathFrom = null; if (S.focusDev) setFocusDev(null); recomputeLit(); renderBanner(); }
  if (act === "path-from") startPath(S.selected);
  if (act === "add-decision") openForm("decision", { targets: S.detail?.name || "" });
  if (act === "center") focusNode(S.selected, true);
  if (act === "close-modal") $("#modal").hidden = true;
});
$("#teamBtn").addEventListener("click", (e) => { e.stopPropagation(); $("#teamMenu").hidden = !$("#teamMenu").hidden; });
$("#openPalette").addEventListener("click", () => openPalette());
$("#themeBtn").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
$("#pathBtn").addEventListener("click", () => (S.pathMode || S.path ? (S.pathMode = false, S.path = null, S.pathFrom = null, recomputeLit(), renderBanner()) : startPath(S.selected)));
$("#zoomIn").addEventListener("click", () => { S.userMoved = true; Graph.zoom(Graph.zoom() * 1.4, 250); });
$("#zoomOut").addEventListener("click", () => { S.userMoved = true; Graph.zoom(Graph.zoom() / 1.4, 250); });
$("#zoomFit").addEventListener("click", () => Graph.zoomToFit(400, 80));
$("#layers").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.layers[b.dataset.layer] = !S.layers[b.dataset.layer]; b.classList.toggle("on", S.layers[b.dataset.layer]);
  store.set("layers", S.layers); Graph.nodeVisibility(nodeVisible).linkVisibility(linkVisible);
});
$("#colorMode").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.colorMode = b.dataset.v; store.set("colorMode", S.colorMode);
  $("#colorMode").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  renderLegend([...fileColor.keys()]);
});
$("#hotspotMode").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.hotspot = b.dataset.v; $("#hotspotMode").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  if (S.data) renderHotspots(S.data);
});
$("#memKind").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.memKind = b.dataset.v; $("#memKind").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); renderMemory();
});
$("#memSearch").addEventListener("input", renderMemory);
$("#memAdd").addEventListener("click", () => openForm(S.memKind === "conventions" ? "convention" : "decision"));
$("#modalForm").addEventListener("submit", submitForm);
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
$("#palette").addEventListener("mousedown", (e) => { if (e.target.id === "palette") closePalette(); });
$("#paletteInput").addEventListener("input", () => { pIndex = 0; renderPalette(); });
$("#paletteList").addEventListener("click", (e) => { const it = e.target.closest(".p-item"); if (it) runPaletteItem(+it.dataset.i); });
$("#paletteList").addEventListener("mousemove", (e) => {
  const it = e.target.closest(".p-item"); if (!it || +it.dataset.i === pIndex) return;
  pIndex = +it.dataset.i; document.querySelectorAll(".p-item").forEach((x) => x.classList.toggle("on", +x.dataset.i === pIndex));
});
document.addEventListener("keydown", (e) => {
  const typing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName);
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#palette").hidden ? openPalette() : closePalette(); return; }
  if (!$("#palette").hidden) {
    if (e.key === "Escape") closePalette();
    else if (e.key === "ArrowDown") { e.preventDefault(); pIndex = Math.min(pIndex + 1, pItems.length - 1); renderPalette(); $(".p-item.on")?.scrollIntoView({ block: "nearest" }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); pIndex = Math.max(pIndex - 1, 0); renderPalette(); $(".p-item.on")?.scrollIntoView({ block: "nearest" }); }
    else if (e.key === "Enter") { e.preventDefault(); runPaletteItem(pIndex); }
    return;
  }
  if (!$("#modal").hidden) { if (e.key === "Escape") $("#modal").hidden = true; return; }
  if (typing) return;
  if (e.key === "Escape") { if (S.focusDev) setFocusDev(null); clearSelection(); }
  else if (e.key === "/") { e.preventDefault(); openPalette(); }
  else if (e.key === "f") Graph.zoomToFit(400, 80);
  else if (e.key === "p") startPath(S.selected);
  else if (e.key === "d") openForm("decision", { targets: S.detail?.name || "" });
});

// ---------------------------------------------------------------- boot
(async function boot() {
  setTheme(params.get("theme") || store.get("theme", window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  paintIcons();
  initGraph();
  await loadTeams();
  syncUrl();
  await refresh();
  const fn = params.get("fn");
  if (fn) select(fn);
  else renderInspector();
  setTimeout(loop, 2000);
})();
})();
