// Memory · Live Observatory — vanilla JS implementation.
//
// Renders the design described in futureDesign/Memory Observatory.bundle.html
// against the real backend at /memory/ui/state and /memory/ui/events. No
// React, no CDN: this file plus the matching index.html and styles.css are
// the entire client.
//
// Conceptual model (from the design):
//   * The center is the active workspace ("Memory anchor").
//   * Eight family nodes orbit the center. Each maps to a slice of the
//     backend (episodes / decisions / research / tasks / roles / skills /
//     instructions / feedback).
//   * When a memory request flows through, edges light up from the center
//     to each touched family, and used objects POOF into existence at the
//     end of each spur with their `label` (or auto-derived display title).
//   * Ambient breath plays on inactive families so the graph never looks
//     dead between requests.
//
// Real-data wiring:
//   * /memory/ui/state powers counts, recent rows, registered workspaces,
//     and hub-mode flag.
//   * /memory/ui/events SSE drives edge animations and the live trail.
//   * The user-supplied `label` (migration 0015) appears as the object
//     node text when present; otherwise the auto-clipped text snippet.

const NS_SVG = "http://www.w3.org/2000/svg";
const REFRESH_MS = 15000;

// Map our backend groups + tables onto the design's 8 fixed families.
// Each entry: { id, label, blurb, hue (oklch), tables: [...] }
const FAMILIES = [
  {
    id: "episodes",
    label: "Episodes",
    blurb: "Audit log of what happened — sessions, conversations, events.",
    hue: 200,
    tables: ["episodes"],
  },
  {
    id: "decisions",
    label: "Decisions",
    blurb: "Architectural and operational decisions, with rationale and supersedes.",
    hue: 280,
    tables: ["decisions"],
  },
  {
    id: "research",
    label: "Research",
    blurb: "Theories, snapshots, experiments, results, concepts, insights.",
    hue: 160,
    tables: [
      "theories",
      "theory_evidence",
      "research_experiments",
      "experiment_results",
      "memory_snapshots",
      "research_insights",
      "domain_concepts",
    ],
  },
  {
    id: "tasks",
    label: "Tasks",
    blurb: "Active task state and pending review candidates.",
    hue: 35,
    tables: ["task_state", "memory_candidates"],
  },
  {
    id: "roles",
    label: "Roles",
    blurb: "Agent personas — purpose, responsibilities, boundaries.",
    hue: 320,
    tables: ["agent_roles"],
  },
  {
    id: "skills",
    label: "Skills",
    blurb: "Reusable capabilities and the playbooks that compose them.",
    hue: 90,
    tables: ["agent_skills", "agent_playbooks", "capability_links"],
  },
  {
    id: "instructions",
    label: "Instructions",
    blurb: "Behavior instructions: communication style, operating rules.",
    hue: 240,
    tables: ["behavior_instructions"],
  },
  {
    id: "feedback",
    label: "Feedback",
    blurb: "User ranking signal: helpful, noisy, stale memories.",
    hue: 0,
    tables: ["memory_usage_feedback"],
  },
];

const FAMILY_BY_TABLE = (() => {
  const out = {};
  for (const fam of FAMILIES) {
    for (const tbl of fam.tables) out[tbl] = fam.id;
  }
  return out;
})();

// ---- shared state -----------------------------------------------------------

const state = {
  workspace: "",
  workspaceRegistry: new Map(), // id → { db_path, vector_path, label, project_root }
  hubMode: false,
  token: "",
  paused: false,
  memory: null,
  health: null,
  events: [], // life-trail rows (newest first)
  eventIds: new Set(),
  eventSource: null,
  sseReady: false,
  selected: null, // { kind: "family"|"object", famId, obj? }
  tweaks: { hue: 160, speed: 0.7, density: "medium", pulse: true, panelOpen: true },
  litFamilies: new Set(),
  litTimers: new Map(), // famId → timeout that turns lights off
  liveObjects: new Map(), // `${famId}:${objId}` → { obj, famId, born, ttl }
};

const els = {
  workspace: document.getElementById("workspaceInput"),
  token: document.getElementById("tokenInput"),
  pause: document.getElementById("pauseBtn"),
  refresh: document.getElementById("refreshBtn"),
  healthChip: document.getElementById("healthChip"),
  chunksChip: document.getElementById("chunksChip"),
  vectorsChip: document.getElementById("vectorsChip"),
  maintChip: document.getElementById("maintChip"),
  overlayDot: document.getElementById("overlayDot"),
  overlayHealth: document.getElementById("overlayHealth"),
  overlayWorkspace: document.getElementById("overlayWorkspace"),
  overlayCounts: document.getElementById("overlayCounts"),
  overlayPhase: document.getElementById("overlayPhase"),
  overlayIntent: document.getElementById("overlayIntent"),
  overlayPrompt: document.getElementById("overlayPrompt"),
  graphSvg: document.getElementById("graphSvg"),
  inspectorCard: document.getElementById("inspectorCard"),
  query: document.getElementById("queryInput"),
  search: document.getElementById("searchBtn"),
  context: document.getElementById("contextBtn"),
  searchSummary: document.getElementById("searchSummary"),
  contextBox: document.getElementById("contextBox"),
  lifeFeed: document.getElementById("lifeFeed"),
  warningsPanel: document.getElementById("warningsPanel"),
  warningsList: document.getElementById("warningsList"),
  updatedChip: document.getElementById("updatedChip"),
  sseChip: document.getElementById("sseChip"),
  hueRange: document.getElementById("hueRange"),
  hueLabel: document.getElementById("hueLabel"),
  speedRange: document.getElementById("speedRange"),
  speedLabel: document.getElementById("speedLabel"),
  densityInput: document.getElementById("densityInput"),
  pulseInput: document.getElementById("pulseInput"),
  tweaksPanel: document.getElementById("tweaksPanel"),
  tweaksBody: document.getElementById("tweaksBody"),
  tweaksToggle: document.getElementById("tweaksToggle"),
};

// ---- small utilities --------------------------------------------------------

function svg(tag, attrs = {}, parent = null) {
  const el = document.createElementNS(NS_SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    el.setAttribute(k, String(v));
  }
  if (parent) parent.appendChild(el);
  return el;
}

function clip(value, max = 28) {
  const s = value == null ? "" : String(value).replace(/\s+/g, " ").trim();
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}

function fmtTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function clearChildren(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}

function familyById(id) {
  return FAMILIES.find((f) => f.id === id) || null;
}

function selectedWorkspace() {
  return (els.workspace.value || state.workspace || "").trim();
}

function workspaceRoute(id) {
  return id ? state.workspaceRegistry.get(id) || null : null;
}

function buildHeaders(json = false) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (state.token) h["Authorization"] = `Bearer ${state.token}`;
  const route = workspaceRoute(selectedWorkspace());
  if (route) {
    if (route.db_path) h["X-Memory-DB-Path"] = route.db_path;
    if (route.vector_path) h["X-Memory-Vector-Path"] = route.vector_path;
  }
  return h;
}

function appendRouteParams(url, ws) {
  const route = workspaceRoute(ws);
  if (!route) return url;
  const params = [];
  if (route.db_path) params.push(`db_path=${encodeURIComponent(route.db_path)}`);
  if (route.vector_path) params.push(`vector_path=${encodeURIComponent(route.vector_path)}`);
  if (!params.length) return url;
  return url + (url.includes("?") ? "&" : "?") + params.join("&");
}

// ---- geometry: family + object positions -----------------------------------

const CENTRE_R = 96;
const FAMILY_RADIUS = 240;
const FAMILY_NODE_R = 46;
const OBJECT_NODE_R = 12;

function familyPositions() {
  const n = FAMILIES.length;
  return FAMILIES.map((fam, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / n + (i % 2 === 0 ? 0.04 : -0.04);
    const r = FAMILY_RADIUS + (i % 3 === 0 ? 14 : i % 3 === 1 ? -8 : 0);
    return {
      id: fam.id,
      x: Math.cos(angle) * r,
      y: Math.sin(angle) * r,
      angle,
    };
  });
}

function objectPositions(parent, n) {
  if (n === 0) return [];
  const out = [];
  const baseR = 130;
  const ringStep = 60;
  const minGap = OBJECT_NODE_R * 2.8;
  const arcHalf = n >= 8 ? 0.42 : n >= 5 ? 0.38 : 0.32;

  const rings = [];
  let remaining = n;
  let ring = 0;
  while (remaining > 0 && ring < 8) {
    const r = baseR + ring * ringStep;
    const cap = Math.max(1, Math.floor((2 * arcHalf) / (minGap / r)) + 1);
    const take = Math.min(cap, remaining);
    rings.push({ count: take, ring, r });
    remaining -= take;
    ring++;
  }
  for (const { count, ring: ri, r } of rings) {
    const minStep = minGap / r;
    const evenStep = count > 1 ? (2 * arcHalf) / (count - 1) : 0;
    const step = Math.max(minStep, Math.min(evenStep, (2 * arcHalf) / Math.max(count - 1, 1)));
    for (let i = 0; i < count; i++) {
      let frac = 0;
      if (count > 1) {
        const side = i % 2 === 0 ? -1 : 1;
        frac = Math.ceil(i / 2) * side;
      }
      const a = parent.angle + frac * step;
      out.push({ x: parent.x + Math.cos(a) * r, y: parent.y + Math.sin(a) * r, ring: ri });
    }
  }
  return out;
}

// Path: centre boundary → family edge → straight spur to object boundary.
function spurPath(parent, object) {
  const fl = Math.hypot(parent.x, parent.y) || 1;
  const fux = parent.x / fl;
  const fuy = parent.y / fl;
  const cx0 = fux * CENTRE_R;
  const cy0 = fuy * CENTRE_R;
  const dx = object.x - parent.x;
  const dy = object.y - parent.y;
  const lFO = Math.hypot(dx, dy) || 1;
  const ux = dx / lFO;
  const uy = dy / lFO;
  const famExitX = parent.x + ux * (FAMILY_NODE_R + 1.5);
  const famExitY = parent.y + uy * (FAMILY_NODE_R + 1.5);
  const objEdgeX = object.x - ux * (OBJECT_NODE_R + 2);
  const objEdgeY = object.y - uy * (OBJECT_NODE_R + 2);
  const m1x = (cx0 + famExitX) / 2;
  const m1y = (cy0 + famExitY) / 2;
  return `M ${cx0} ${cy0} Q ${m1x} ${m1y} ${famExitX} ${famExitY} L ${objEdgeX} ${objEdgeY}`;
}

function trunkPath(parent) {
  const fl = Math.hypot(parent.x, parent.y) || 1;
  const fux = parent.x / fl;
  const fuy = parent.y / fl;
  const cx0 = fux * CENTRE_R;
  const cy0 = fuy * CENTRE_R;
  const famX = parent.x - fux * (FAMILY_NODE_R + 1);
  const famY = parent.y - fuy * (FAMILY_NODE_R + 1);
  const m1x = (cx0 + famX) / 2;
  const m1y = (cy0 + famY) / 2;
  return `M ${cx0} ${cy0} Q ${m1x} ${m1y} ${famX} ${famY}`;
}

// ---- graph rendering --------------------------------------------------------

function renderGraph() {
  const root = els.graphSvg;
  clearChildren(root);

  // SVG defs (glow filters + centre gradient)
  const defs = svg("defs", {}, root);
  const filt1 = svg("filter", { id: "stroke-glow", x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
  svg("feGaussianBlur", { stdDeviation: "1.6", result: "b" }, filt1);
  const m1 = svg("feMerge", {}, filt1);
  svg("feMergeNode", { in: "b" }, m1);
  svg("feMergeNode", { in: "SourceGraphic" }, m1);
  const filt2 = svg("filter", { id: "big-glow", x: "-100%", y: "-100%", width: "300%", height: "300%" }, defs);
  svg("feGaussianBlur", { stdDeviation: "6", result: "b" }, filt2);
  const m2 = svg("feMerge", {}, filt2);
  svg("feMergeNode", { in: "b" }, m2);
  svg("feMergeNode", { in: "SourceGraphic" }, m2);

  const accent = state.tweaks.hue;
  const grad = svg("radialGradient", { id: "centre-grad" }, defs);
  svg("stop", { offset: "0%", "stop-color": `oklch(0.92 0.14 ${accent})`, "stop-opacity": "0.95" }, grad);
  svg("stop", { offset: "55%", "stop-color": `oklch(0.55 0.12 ${accent})`, "stop-opacity": "0.45" }, grad);
  svg("stop", { offset: "100%", "stop-color": `oklch(0.25 0.04 250)`, "stop-opacity": "0" }, grad);

  // Layer order: ambient ← edges ← families ← objects ← centre
  const ambientGroup = svg("g", { class: "ambient-layer" }, root);
  const edgesGroup = svg("g", { class: "edges-layer" }, root);
  const familiesGroup = svg("g", { class: "families-layer" }, root);
  const objectsGroup = svg("g", { class: "objects-layer" }, root);
  const centreGroup = svg("g", { class: "centre-layer" }, root);

  const positions = familyPositions();

  // Ambient: faint pulsing rings on all families
  for (const p of positions) {
    const fam = familyById(p.id);
    svg(
      "circle",
      {
        cx: p.x,
        cy: p.y,
        r: FAMILY_NODE_R + 6,
        fill: "none",
        stroke: `oklch(0.55 0.06 ${fam.hue} / 0.18)`,
        "stroke-width": "0.6",
        class: "ambient-pulse",
      },
      ambientGroup,
    );
  }

  // Edges: trunk centre→family per family, drawn statically; brightness via class
  for (const p of positions) {
    const fam = familyById(p.id);
    svg(
      "path",
      {
        class: `edge-path edge-trunk edge-${p.id}`,
        d: trunkPath(p),
        stroke: `oklch(0.55 0.12 ${fam.hue} / 0.45)`,
        "stroke-width": "1.2",
        opacity: state.litFamilies.has(p.id) ? "0.95" : "0.18",
        filter: state.litFamilies.has(p.id) ? "url(#stroke-glow)" : undefined,
      },
      edgesGroup,
    );
  }

  // Family nodes
  for (const p of positions) {
    const fam = familyById(p.id);
    const lit = state.litFamilies.has(p.id);
    const counts = state.memory?.counts || {};
    const total = fam.tables.reduce((acc, tbl) => acc + (counts[tbl] || 0), 0);
    const g = svg(
      "g",
      {
        class: `fam-node ${lit ? "is-lit" : ""}`,
        transform: `translate(${p.x},${p.y})`,
        "data-family-id": p.id,
      },
      familiesGroup,
    );
    g.addEventListener("click", () => selectFamily(p.id));

    if (lit) {
      svg(
        "circle",
        {
          class: "fam-aura",
          r: FAMILY_NODE_R + 14,
          fill: `oklch(0.85 0.17 ${fam.hue} / 0.25)`,
          stroke: "none",
          filter: "url(#big-glow)",
        },
        g,
      );
    }

    svg(
      "circle",
      {
        r: FAMILY_NODE_R,
        fill: `oklch(0.22 0.05 ${fam.hue} / ${lit ? 0.85 : 0.5})`,
        stroke: `oklch(${lit ? 0.85 : 0.5} 0.18 ${fam.hue} / ${lit ? 0.95 : 0.55})`,
        "stroke-width": lit ? "2" : "1.2",
      },
      g,
    );
    svg("text", { class: "fam-label-inside", "text-anchor": "middle", y: -3 }, g).textContent = fam.label;
    svg("text", { class: "fam-count-inside", "text-anchor": "middle", y: 14 }, g).textContent = String(total);
  }

  // Object nodes: only render currently-live objects (recent or on-event)
  const liveByFamily = {};
  for (const [, entry] of state.liveObjects) {
    if (!liveByFamily[entry.famId]) liveByFamily[entry.famId] = [];
    liveByFamily[entry.famId].push(entry);
  }
  for (const p of positions) {
    const fam = familyById(p.id);
    const liveList = liveByFamily[p.id] || [];
    const objPos = objectPositions(p, liveList.length);
    for (let i = 0; i < liveList.length; i++) {
      const entry = liveList[i];
      const pos = objPos[i] || { x: p.x, y: p.y, ring: 0 };

      // Spur edge centre→family→object
      svg(
        "path",
        {
          class: "edge-path edge-spur",
          d: spurPath(p, pos),
          stroke: `oklch(0.85 0.18 ${fam.hue})`,
          "stroke-width": "1.4",
          opacity: "0.9",
          filter: "url(#stroke-glow)",
        },
        edgesGroup,
      );

      const og = svg(
        "g",
        {
          class: "obj-node",
          transform: `translate(${pos.x},${pos.y})`,
          "data-object-id": entry.obj.id || "",
        },
        objectsGroup,
      );
      og.addEventListener("click", (ev) => {
        ev.stopPropagation();
        selectObject(entry.obj, p.id);
      });
      svg(
        "circle",
        {
          class: "obj-halo",
          r: OBJECT_NODE_R + 8,
          fill: `oklch(0.85 0.18 ${fam.hue} / 0.25)`,
          filter: "url(#big-glow)",
        },
        og,
      );
      svg(
        "circle",
        {
          r: OBJECT_NODE_R,
          fill: `oklch(0.24 0.05 ${fam.hue})`,
          stroke: `oklch(0.92 0.18 ${fam.hue})`,
          "stroke-width": "1.4",
          filter: "url(#stroke-glow)",
        },
        og,
      );

      // Label: prefer user-supplied label, else clipped title.
      const labelText = entry.obj.short_label || entry.obj.label || entry.obj.id || "—";
      const dx = pos.x - p.x;
      const dy = pos.y - p.y;
      const dl = Math.hypot(dx, dy) || 1;
      const ux = dx / dl;
      const uy = dy / dl;
      const labelGap = OBJECT_NODE_R + 14 + (pos.ring || 0) * 6;
      let anchor = "middle";
      if (ux > 0.35) anchor = "start";
      else if (ux < -0.35) anchor = "end";
      const t = svg(
        "text",
        {
          class: "obj-label",
          x: ux * labelGap,
          y: uy * labelGap,
          "dominant-baseline": "middle",
          "text-anchor": anchor,
          fill: `oklch(0.96 0.08 ${fam.hue})`,
        },
        og,
      );
      t.textContent = clip(labelText, 22);
    }
  }

  // Centre node — workspace anchor
  svg(
    "circle",
    {
      class: "centre-aura",
      r: 180,
      fill: "url(#centre-grad)",
    },
    centreGroup,
  );
  svg(
    "circle",
    {
      class: "centre-orbit-slow",
      r: 128,
      fill: "none",
      stroke: `oklch(0.85 0.16 ${accent} / 0.22)`,
      "stroke-width": "0.8",
    },
    centreGroup,
  );
  svg(
    "circle",
    {
      r: CENTRE_R,
      fill: "oklch(0.18 0.03 250)",
      stroke: `oklch(0.95 0.18 ${accent})`,
      "stroke-width": "2.2",
      filter: "url(#stroke-glow)",
    },
    centreGroup,
  );
  svg(
    "circle",
    {
      class: "centre-orbit-fast",
      r: CENTRE_R,
      fill: "none",
      stroke: `oklch(0.95 0.18 ${accent})`,
      "stroke-width": "1.2",
      "stroke-dasharray": "80 400",
      opacity: "0.7",
    },
    centreGroup,
  );
  const wsTitle = svg(
    "text",
    { class: "centre-label", "text-anchor": "middle", y: -10 },
    centreGroup,
  );
  wsTitle.textContent = state.workspace || "workspace";
  const wsSub = svg("text", { class: "centre-sub", "text-anchor": "middle", y: 12 }, centreGroup);
  wsSub.textContent = "memory anchor";
  const stage = svg(
    "text",
    { class: "centre-stage", "text-anchor": "middle", y: 36, fill: `oklch(0.95 0.2 ${accent})` },
    centreGroup,
  );
  stage.textContent = state.litFamilies.size > 0 ? "ACTIVE" : "IDLE";
}

// ---- live state fetch + SSE -------------------------------------------------

async function fetchState({ manual = false } = {}) {
  if (state.paused && !manual) return;
  const ws = selectedWorkspace();
  const url = ws
    ? `/memory/ui/state?workspace_id=${encodeURIComponent(ws)}&recent_limit=12`
    : `/memory/ui/state?recent_limit=12`;
  const headers = { ...buildHeaders() };
  delete headers["Content-Type"];
  let memory = null;
  try {
    const r = await fetch(url, { headers });
    if (!r.ok) throw new Error(`/memory/ui/state ${r.status}`);
    memory = await r.json();
  } catch (err) {
    setSseChip("offline", "is-warn");
    return;
  }
  state.memory = memory;
  state.workspace = memory.workspace_id || ws;
  state.hubMode = Boolean(memory.hub_mode);
  state.workspaceRegistry.clear();
  for (const entry of memory.registered_workspaces || []) {
    state.workspaceRegistry.set(entry.id, {
      db_path: entry.db_path || "",
      vector_path: entry.vector_path || "",
      project_root: entry.project_root || "",
      label: entry.label || entry.id,
    });
  }
  populateWorkspaceDropdown(memory.workspaces || [state.workspace]);
  ingestRecentRows(memory.recent || []);
  renderHeader(memory);
  renderInspector();
  renderGraph();
  renderFeed();
  renderWarnings(memory.warnings || []);
  els.updatedChip.textContent = `updated ${fmtTime(memory.generated_at)}`;
  ensureSse();
}

function populateWorkspaceDropdown(list) {
  const labels = new Map();
  for (const id of list) labels.set(id, id);
  for (const [id, route] of state.workspaceRegistry) {
    if (route.label && route.label !== id) labels.set(id, `${id} · ${route.label}`);
    else labels.set(id, id);
  }
  if (state.workspace && !labels.has(state.workspace)) labels.set(state.workspace, state.workspace);
  const current = state.workspace || list[0] || "default";
  clearChildren(els.workspace);
  for (const [id, name] of labels) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = name;
    els.workspace.appendChild(opt);
  }
  els.workspace.value = current;
}

function ingestRecentRows(rows) {
  // Keep up to 5 most-recent objects per family alive in the graph so the
  // observatory has something to render even when no SSE event is firing.
  const byFamily = {};
  for (const row of rows) {
    const famId = FAMILY_BY_TABLE[row.table];
    if (!famId) continue;
    if (!byFamily[famId]) byFamily[famId] = [];
    if (byFamily[famId].length < 5) byFamily[famId].push(row);
  }
  state.liveObjects.clear();
  for (const [famId, list] of Object.entries(byFamily)) {
    for (const row of list) {
      const key = `${famId}:${row.id}`;
      state.liveObjects.set(key, { famId, obj: row, born: Date.now(), ttl: 0 });
    }
  }
}

function ensureSse() {
  if (state.eventSource || state.paused) return;
  if (state.token) {
    setSseChip("token blocks SSE", "is-warn");
    return;
  }
  const ws = selectedWorkspace();
  if (!ws) return;
  const url = appendRouteParams(`/memory/ui/events?workspace_id=${encodeURIComponent(ws)}`, ws);
  const src = new EventSource(url);
  state.eventSource = src;
  src.addEventListener("open", () => {
    state.sseReady = true;
    setSseChip("SSE live", "");
  });
  const onMsg = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      onMemoryEvent(ev);
    } catch (err) {
      // ignore
    }
  };
  src.addEventListener("memory", onMsg);
  src.onmessage = onMsg;
  src.onerror = () => {
    state.sseReady = false;
    src.close();
    state.eventSource = null;
    setSseChip("SSE polling", "is-warn");
  };
}

function resetSse() {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = null;
  state.sseReady = false;
}

function setSseChip(text, cls = "") {
  els.sseChip.textContent = text;
  els.sseChip.className = `foot-mute ${cls}`;
}

// ---- event-driven graph behavior -------------------------------------------

function onMemoryEvent(ev) {
  // Append to trail
  if (ev.id && state.eventIds.has(ev.id)) return;
  if (ev.id) state.eventIds.add(ev.id);
  const trailRow = {
    kind: trailKindOf(ev),
    t: fmtTime(ev.timestamp || new Date()),
    text: ev.label || ev.endpoint || ev.operation || ev.kind || "memory event",
  };
  state.events.unshift(trailRow);
  state.events = state.events.slice(0, 60);

  // Light up touched families based on graph_delta entries
  if (ev.kind === "graph_delta" && ev.counts && typeof ev.counts.table === "string") {
    const famId = FAMILY_BY_TABLE[ev.counts.table];
    if (famId) lightFamily(famId, 5000 / Math.max(0.3, state.tweaks.speed));
  }
  if (ev.kind === "stage" && Array.isArray(ev.tables)) {
    for (const tbl of ev.tables) {
      const famId = FAMILY_BY_TABLE[tbl];
      if (famId) lightFamily(famId, 4000 / Math.max(0.3, state.tweaks.speed));
    }
  }

  // Overlay phase / intent updates
  if (ev.endpoint || ev.operation) {
    els.overlayPhase.textContent = ev.stage || ev.kind || "active";
    els.overlayIntent.textContent = ev.endpoint || ev.operation || "";
  }
  if (ev.snippet) {
    els.overlayPrompt.textContent = `"${clip(ev.snippet, 140)}"`;
  }

  renderFeed();
  renderGraph();
}

function trailKindOf(ev) {
  if (ev.kind === "graph_delta") return "pick";
  if (ev.kind === "stage") return "route";
  if (ev.endpoint && ev.endpoint.includes("ingest")) return "in";
  if (ev.endpoint && ev.endpoint.includes("get_context")) return "out";
  if (ev.severity === "warn" || ev.severity === "warning") return "warn";
  return "route";
}

function lightFamily(famId, ms) {
  state.litFamilies.add(famId);
  const prev = state.litTimers.get(famId);
  if (prev) clearTimeout(prev);
  state.litTimers.set(
    famId,
    setTimeout(() => {
      state.litFamilies.delete(famId);
      renderGraph();
    }, ms),
  );
  renderGraph();
}

// ---- header / inspector / feed / warnings ----------------------------------

function renderHeader(memory) {
  const counts = memory.counts || {};
  const chunks = counts.chunks || 0;
  els.chunksChip.querySelector(".chip-value").textContent = String(chunks);
  els.vectorsChip.querySelector(".chip-value").textContent = String(chunks);
  const maint = (memory.warnings || []).length;
  const maintChipVal = els.maintChip.querySelector(".chip-value");
  maintChipVal.textContent = `${maint}`;
  els.maintChip.classList.toggle("is-warn", maint > 0);

  const status = state.health?.status || "ok";
  const retrievalStatus = state.health?.retrieval_integrity?.status || "ok";
  const overallOk = status === "ok" && retrievalStatus === "ok";
  els.healthChip.querySelector(".chip-value").textContent = overallOk ? "ok" : status;
  els.healthChip.classList.toggle("is-warn", !overallOk);
  els.overlayDot.className = overallOk ? "dot-ok" : "dot-ok is-warn";
  els.overlayHealth.textContent = overallOk ? "health ok" : `health ${status}`;
  els.overlayWorkspace.textContent = state.workspace || "workspace";
  els.overlayCounts.textContent = `${chunks} chunks · ${state.events.length} events`;
}

function renderInspector() {
  const card = els.inspectorCard;
  clearChildren(card);
  if (!state.selected) {
    const empty = document.createElement("div");
    empty.className = "inspector-empty";
    empty.innerHTML = `
      <div class="empty-circle"></div>
      <div class="empty-title">Live overview</div>
      <div class="empty-sub">Click any family or object to inspect. The center node is the current workspace.</div>
    `;
    card.appendChild(empty);
    return;
  }
  if (state.selected.kind === "family") {
    const fam = familyById(state.selected.famId);
    const counts = state.memory?.counts || {};
    const total = fam.tables.reduce((acc, tbl) => acc + (counts[tbl] || 0), 0);
    const head = document.createElement("div");
    head.className = "inspector-kicker";
    head.textContent = `family · ${fam.id}`;
    const title = document.createElement("div");
    title.className = "inspector-title";
    title.style.color = `oklch(0.92 0.1 ${fam.hue})`;
    title.textContent = fam.label;
    const blurb = document.createElement("div");
    blurb.className = "inspector-blurb";
    blurb.textContent = `${fam.blurb} · ${total} object${total === 1 ? "" : "s"}`;
    card.append(head, title, blurb);

    const list = document.createElement("div");
    list.className = "inspector-list";
    const recent = (state.memory?.recent || []).filter((r) => fam.tables.includes(r.table));
    if (!recent.length) {
      const empty = document.createElement("div");
      empty.className = "inspector-blurb";
      empty.textContent = "No recent objects in this family.";
      list.appendChild(empty);
    } else {
      for (const row of recent.slice(0, 12)) {
        const btn = document.createElement("button");
        btn.className = "inspector-row";
        btn.type = "button";
        const titleEl = document.createElement("span");
        titleEl.className = "row-title";
        const labelText = row.short_label || row.label || row.id;
        titleEl.textContent = labelText;
        if (row.short_label) {
          const hint = document.createElement("span");
          hint.className = "row-label-hint";
          hint.textContent = "label";
          titleEl.appendChild(hint);
        }
        const typeEl = document.createElement("span");
        typeEl.className = "row-type";
        typeEl.textContent = row.table.replace(/_/g, " ");
        btn.append(titleEl, typeEl);
        btn.addEventListener("click", () => selectObject(row, fam.id));
        list.appendChild(btn);
      }
    }
    card.appendChild(list);
    return;
  }
  if (state.selected.kind === "object") {
    const fam = familyById(state.selected.famId);
    const obj = state.selected.obj;
    const head = document.createElement("div");
    head.className = "inspector-kicker";
    head.textContent = `object · ${fam.label.toLowerCase()}`;
    const title = document.createElement("div");
    title.className = "inspector-title";
    title.style.color = `oklch(0.92 0.1 ${fam.hue})`;
    title.textContent = obj.short_label || obj.label || obj.id || "—";
    card.append(head, title);

    const grid = document.createElement("div");
    grid.className = "kv-grid";
    grid.innerHTML = `
      <div class="kv"><span>id</span><code>${obj.id || "—"}</code></div>
      <div class="kv"><span>table</span><span>${obj.table || "—"}</span></div>
      <div class="kv"><span>updated</span><span>${fmtTime(obj.updated_at)}</span></div>
      <div class="kv"><span>label</span><span>${obj.short_label || "—"}</span></div>
    `;
    card.appendChild(grid);

    const desc = document.createElement("div");
    desc.className = "inspector-section";
    const descH = document.createElement("div");
    descH.className = "section-h";
    descH.textContent = "display text";
    const descP = document.createElement("p");
    descP.style.fontSize = "12px";
    descP.style.color = "var(--text-1)";
    descP.style.lineHeight = "1.45";
    descP.textContent = obj.label || "—";
    desc.append(descH, descP);
    card.appendChild(desc);

    const closeBtn = document.createElement("button");
    closeBtn.className = "x-btn";
    closeBtn.style.position = "absolute";
    closeBtn.style.top = "10px";
    closeBtn.style.right = "12px";
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", () => {
      state.selected = null;
      renderInspector();
    });
    card.style.position = "relative";
    card.appendChild(closeBtn);
  }
}

function renderFeed() {
  clearChildren(els.lifeFeed);
  for (const ev of state.events.slice(0, 30)) {
    const row = document.createElement("div");
    row.className = `trail-row trail-${ev.kind}`;
    const time = document.createElement("span");
    time.className = "trail-time";
    time.textContent = ev.t;
    const txt = document.createElement("span");
    txt.className = "trail-text";
    txt.textContent = ev.text;
    row.append(time, txt);
    els.lifeFeed.appendChild(row);
  }
}

function renderWarnings(list) {
  if (!list || !list.length) {
    els.warningsPanel.hidden = true;
    return;
  }
  els.warningsPanel.hidden = false;
  clearChildren(els.warningsList);
  for (const w of list.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = "warning-row";
    const kind = document.createElement("span");
    kind.className = "warn-kind";
    kind.textContent = w.kind || "open";
    const txt = document.createElement("span");
    txt.textContent = w.summary || JSON.stringify(w);
    row.append(kind, txt);
    els.warningsList.appendChild(row);
  }
}

function selectFamily(famId) {
  state.selected = { kind: "family", famId };
  renderInspector();
}

function selectObject(obj, famId) {
  state.selected = { kind: "object", famId, obj };
  renderInspector();
}

// ---- query controls --------------------------------------------------------

async function postJson(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: buildHeaders(true),
    body: JSON.stringify(body),
  });
  const text = await r.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!r.ok) throw new Error(`${path} ${r.status}: ${text.slice(0, 240)}`);
  return data;
}

async function searchMemory() {
  const q = els.query.value.trim();
  if (!q) return;
  els.searchSummary.textContent = "searching…";
  try {
    const data = await postJson("/memory/search", {
      workspace_id: state.workspace,
      query: q,
      mode: "fts",
      limit: 5,
    });
    const hits = data.hits || data.results || [];
    els.searchSummary.textContent = `${hits.length} hit${hits.length === 1 ? "" : "s"}`;
  } catch (err) {
    els.searchSummary.textContent = String(err);
  }
}

async function explainContext() {
  const q = els.query.value.trim();
  if (!q) return;
  els.searchSummary.textContent = "fetching context…";
  try {
    const data = await postJson("/memory/get_context", {
      workspace_id: state.workspace,
      query: q,
      max_tokens: 1500,
    });
    els.contextBox.textContent = data.context_text || "";
    const tokens = data.budget_diagnostics?.estimated_tokens || 0;
    els.searchSummary.textContent = `${data.sources?.length || 0} sources · ~${tokens} tokens`;
  } catch (err) {
    els.searchSummary.textContent = String(err);
  }
}

// ---- tweaks ----------------------------------------------------------------

function applyTweaks() {
  document.documentElement.style.setProperty("--accent-hue", String(state.tweaks.hue));
  els.hueLabel.textContent = `${state.tweaks.hue}°`;
  els.speedLabel.textContent = `${state.tweaks.speed.toFixed(2)}×`;
  renderGraph();
}

// ---- wiring ----------------------------------------------------------------

els.workspace.addEventListener("change", () => {
  resetSse();
  fetchState({ manual: true });
});
els.token.addEventListener("change", () => {
  state.token = els.token.value.trim();
  resetSse();
  fetchState({ manual: true });
});
els.refresh.addEventListener("click", () => fetchState({ manual: true }));
els.pause.addEventListener("click", () => {
  state.paused = !state.paused;
  els.pause.classList.toggle("is-paused", state.paused);
  els.pause.textContent = state.paused ? "▶ resume" : "❚❚ pause";
  if (state.paused) {
    resetSse();
  } else {
    fetchState({ manual: true });
  }
});
els.search.addEventListener("click", () => searchMemory());
els.context.addEventListener("click", () => explainContext());
els.query.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") searchMemory();
});

els.hueRange.addEventListener("input", () => {
  state.tweaks.hue = Number(els.hueRange.value);
  applyTweaks();
});
els.speedRange.addEventListener("input", () => {
  state.tweaks.speed = Number(els.speedRange.value);
  applyTweaks();
});
els.densityInput.addEventListener("change", () => {
  state.tweaks.density = els.densityInput.value;
  renderGraph();
});
els.pulseInput.addEventListener("change", () => {
  state.tweaks.pulse = els.pulseInput.checked;
});
els.tweaksToggle.addEventListener("click", () => {
  state.tweaks.panelOpen = !state.tweaks.panelOpen;
  els.tweaksPanel.classList.toggle("is-collapsed", !state.tweaks.panelOpen);
  els.tweaksToggle.textContent = state.tweaks.panelOpen ? "−" : "+";
});

// initial paint + polling fallback
renderGraph();
applyTweaks();
fetchState({ manual: true });
setInterval(() => {
  if (!state.sseReady && !state.paused) fetchState();
}, REFRESH_MS);
