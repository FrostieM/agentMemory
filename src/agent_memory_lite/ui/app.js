// Memory · Live Observatory — vanilla JS, action-driven.
//
// The observatory has a centre workspace anchor and eight family orbs
// (Episodes / Decisions / Research / Tasks / Roles / Skills / Instructions /
// Feedback). The graph stays IDLE when nothing is happening — no synthetic
// demo cycle. It animates exactly when a REAL action occurs:
//
//   * The user types a query and clicks Search → /memory/search runs, the
//     hits drive a forward→hold→reverse stroke animation that lights only
//     the families the hits actually came from. The right rail shows the
//     hit list with their stored labels.
//   * The user clicks Explain → /memory/get_context runs, the returned
//     sources drive the animation; the raw context drawer fills with the
//     real XML envelope.
//   * The user clicks a family node → the matching backend list endpoint
//     loads (list_decisions / list_theories / list_agent_capabilities /
//     list_behavior_instructions / list_research_agenda / fall back to
//     /memory/ui/state.recent). The graph animates that single family
//     with its real recent objects, and the inspector shows their full
//     bodies (decision_text+rationale, theory claim+mechanism+predictions,
//     etc.).
//   * The user clicks an object node → the inspector opens that object's
//     real body — raw_text for episodes, decision_text+rationale for
//     decisions, claim+predictions for theories, rule+rationale for
//     behavior instructions, full backend row for everything else.
//   * Live SSE events from /memory/ui/events light the matching family
//     briefly (~5 s, additive) and append to the trail card without
//     starting a full cycle.

const NS_SVG = "http://www.w3.org/2000/svg";
const POLL_MS = 15000;

const FAMILIES = [
  { id: "episodes",     label: "Episodes",     hue: 200, blurb: "Audit log of what happened — sessions, conversations, events.", tables: ["episodes"] },
  { id: "decisions",    label: "Decisions",    hue: 280, blurb: "Architectural and operational decisions.",                       tables: ["decisions"] },
  { id: "research",     label: "Research",     hue: 160, blurb: "Theories, snapshots, experiments, results, concepts, insights.", tables: ["theories","theory_evidence","research_experiments","experiment_results","memory_snapshots","research_insights","domain_concepts"] },
  { id: "tasks",        label: "Tasks",        hue:  35, blurb: "Active task state and pending review candidates.",                tables: ["task_state","memory_candidates"] },
  { id: "roles",        label: "Roles",        hue: 320, blurb: "Agent personas — purpose, responsibilities, boundaries.",        tables: ["agent_roles"] },
  { id: "skills",       label: "Skills",       hue:  90, blurb: "Reusable capabilities and the playbooks that compose them.",     tables: ["agent_skills","agent_playbooks","capability_links"] },
  { id: "instructions", label: "Instructions", hue: 240, blurb: "Behaviour instructions — communication style, operating rules.", tables: ["behavior_instructions"] },
  { id: "feedback",     label: "Feedback",     hue:   0, blurb: "User ranking signal — helpful, noisy, stale memories.",          tables: ["memory_usage_feedback"] },
];
const FAMILY_BY_ID = Object.fromEntries(FAMILIES.map(f => [f.id, f]));
const FAMILY_BY_TABLE = (() => {
  const m = {};
  for (const f of FAMILIES) for (const t of f.tables) m[t] = f.id;
  return m;
})();

// ---- state ------------------------------------------------------------------

const state = {
  workspace: "",
  hubMode: false,
  workspaceRegistry: new Map(),
  token: "",
  paused: false,
  memory: null,
  health: null,
  events: [],
  eventIds: new Set(),
  eventSource: null,
  sseReady: false,
  liveLight: new Map(), // famId → expiresAtMs
  selected: null,
  detailCache: new Map(), // famId → fetched detail rows
  tweaks: { hue: 160, speed: 0.7, density: "medium", pulse: true, panelOpen: true },
  // animation
  activeQuery: null,           // { intent, prompt, families, objects, source }
  cycleStart: 0,               // ms timestamp of the running cycle, or 0 if idle
  phase: "idle",
  progress: 0,
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
  liveIntent: document.getElementById("liveIntent"),
  liveIntentKind: document.getElementById("liveIntentKind"),
  liveIntentPrompt: document.getElementById("liveIntentPrompt"),
  metricObjects: document.getElementById("metricObjects"),
  metricFamilies: document.getElementById("metricFamilies"),
  metricStage: document.getElementById("metricStage"),
  metricPhase: document.getElementById("metricPhase"),
  familiesTouched: document.getElementById("familiesTouched"),
  objectsInContext: document.getElementById("objectsInContext"),
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
  tweaksToggle: document.getElementById("tweaksToggle"),
};

// ---- utilities --------------------------------------------------------------

function svg(tag, attrs = {}, parent = null) {
  const el = document.createElementNS(NS_SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    el.setAttribute(k, String(v));
  }
  if (parent) parent.appendChild(el);
  return el;
}
function clip(s, max = 22) {
  const t = (s == null ? "" : String(s)).replace(/\s+/g, " ").trim();
  return t.length <= max ? t : t.slice(0, max - 1) + "…";
}
function fmtTime(v) {
  if (!v) return "—";
  const d = v instanceof Date ? v : new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
function easeInCubic(t)  { return t * t * t; }
function selectedWorkspace() { return (els.workspace.value || state.workspace || "").trim(); }
function workspaceRoute(id) { return id ? state.workspaceRegistry.get(id) || null : null; }

function buildHeaders(json = false) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (state.token) h["Authorization"] = `Bearer ${state.token}`;
  const r = workspaceRoute(selectedWorkspace());
  if (r) {
    if (r.db_path) h["X-Memory-DB-Path"] = r.db_path;
    if (r.vector_path) h["X-Memory-Vector-Path"] = r.vector_path;
  }
  return h;
}
function appendRouteParams(url, ws) {
  const r = workspaceRoute(ws);
  if (!r) return url;
  const parts = [];
  if (r.db_path) parts.push(`db_path=${encodeURIComponent(r.db_path)}`);
  if (r.vector_path) parts.push(`vector_path=${encodeURIComponent(r.vector_path)}`);
  if (!parts.length) return url;
  return url + (url.includes("?") ? "&" : "?") + parts.join("&");
}

// ---- geometry ---------------------------------------------------------------

const CENTRE_R = 96;
const FAMILY_RING = 280;
const FAMILY_R = 46;
const OBJECT_R = 11;

const FAM_POSITIONS = (() => {
  const n = FAMILIES.length;
  return FAMILIES.map((f, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n + (i % 2 === 0 ? 0.04 : -0.04);
    const r = FAMILY_RING + (i % 3 === 0 ? 14 : i % 3 === 1 ? -8 : 0);
    return { id: f.id, x: Math.cos(a) * r, y: Math.sin(a) * r, angle: a };
  });
})();
const POS_BY_ID = Object.fromEntries(FAM_POSITIONS.map(p => [p.id, p]));

function objectPositions(parent, n) {
  if (!n) return [];
  const out = [];
  const baseR = 120, step = 60, minGap = OBJECT_R * 2.8;
  const arcHalf = n >= 8 ? 0.42 : n >= 5 ? 0.38 : 0.32;
  const rings = [];
  let rem = n, k = 0;
  while (rem > 0 && k < 6) {
    const r = baseR + k * step;
    const cap = Math.max(1, Math.floor((2 * arcHalf) / (minGap / r)) + 1);
    const take = Math.min(cap, rem);
    rings.push({ count: take, ring: k, r });
    rem -= take; k++;
  }
  for (const { count, ring, r } of rings) {
    const minStep = minGap / r;
    const evenStep = count > 1 ? (2 * arcHalf) / (count - 1) : 0;
    const stp = Math.max(minStep, Math.min(evenStep, (2 * arcHalf) / Math.max(count - 1, 1)));
    for (let i = 0; i < count; i++) {
      let frac = 0;
      if (count > 1) {
        const side = i % 2 === 0 ? -1 : 1;
        frac = Math.ceil(i / 2) * side;
      }
      const a = parent.angle + frac * stp;
      out.push({ x: parent.x + Math.cos(a) * r, y: parent.y + Math.sin(a) * r, ring });
    }
  }
  return out;
}

function trunkPath(p) {
  const fl = Math.hypot(p.x, p.y) || 1;
  const ux = p.x / fl, uy = p.y / fl;
  const cx0 = ux * CENTRE_R, cy0 = uy * CENTRE_R;
  const fx = p.x - ux * (FAMILY_R + 1), fy = p.y - uy * (FAMILY_R + 1);
  const mx = (cx0 + fx) / 2, my = (cy0 + fy) / 2;
  return `M ${cx0} ${cy0} Q ${mx} ${my} ${fx} ${fy}`;
}
function spurPath(parent, obj) {
  const dx = obj.x - parent.x, dy = obj.y - parent.y;
  const d = Math.hypot(dx, dy) || 1;
  const ux = dx / d, uy = dy / d;
  const fx = parent.x + ux * (FAMILY_R + 1), fy = parent.y + uy * (FAMILY_R + 1);
  const ox = obj.x - ux * (OBJECT_R + 2), oy = obj.y - uy * (OBJECT_R + 2);
  return `M ${fx} ${fy} L ${ox} ${oy}`;
}

// ---- timeline (ONE cycle per real action; idle otherwise) ------------------

const PHASE_MS = { forward: 3200, hold: 4500, reverse: 2400 };
const FAMILY_ARRIVAL = 0.35;
const PER_RING_DELTA  = 0.18;
const STAGE_BY_PHASE = (phase, p) => {
  if (phase === "forward") return p < 0.32 ? "PARSE" : p < 0.7 ? "RECALL" : "FUSE";
  if (phase === "hold")    return "ANSWER";
  if (phase === "reverse") return "RELEASE";
  return "IDLE";
};

function runQueryAnimation(query) {
  state.activeQuery = query;
  state.cycleStart = performance.now();
  state.phase = "forward";
  state.progress = 0;
}

function tick() {
  requestAnimationFrame(tick);
  if (state.paused) return;
  if (!shellMounted) return;

  if (!state.activeQuery || !state.cycleStart) {
    state.phase = "idle";
    state.progress = 0;
    paintFrame();
    return;
  }
  const sp = Math.max(0.3, state.tweaks.speed);
  const F = PHASE_MS.forward / sp, H = PHASE_MS.hold / sp, R = PHASE_MS.reverse / sp;
  const elapsed = performance.now() - state.cycleStart;
  if (elapsed >= F + H + R) {
    // cycle done — settle to idle but keep activeQuery so the rail
    // continues to display the last result.
    state.phase = "idle";
    state.progress = 0;
    state.cycleStart = 0;
    paintFrame();
    return;
  }
  let phase = "forward", progress = 0;
  if (elapsed < F) { phase = "forward"; progress = easeOutCubic(elapsed / F); }
  else if (elapsed < F + H) { phase = "hold"; progress = 1; }
  else { phase = "reverse"; progress = 1 - easeInCubic((elapsed - F - H) / R); }
  state.phase = phase;
  state.progress = progress;
  paintFrame();
}

// ---- rendering: shell (mounted once) ---------------------------------------

let shellMounted = false;
let dynamicLayer = null;
let famGroupsById = new Map();
let stageText = null;
let centreLabel = null;

function mountShell() {
  const root = els.graphSvg;
  clear(root);
  famGroupsById = new Map();

  const defs = svg("defs", {}, root);
  const f1 = svg("filter", { id: "stroke-glow", x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
  svg("feGaussianBlur", { stdDeviation: "1.6", result: "b" }, f1);
  const m1 = svg("feMerge", {}, f1);
  svg("feMergeNode", { in: "b" }, m1); svg("feMergeNode", { in: "SourceGraphic" }, m1);
  const f2 = svg("filter", { id: "big-glow", x: "-100%", y: "-100%", width: "300%", height: "300%" }, defs);
  svg("feGaussianBlur", { stdDeviation: "6", result: "b" }, f2);
  const m2 = svg("feMerge", {}, f2);
  svg("feMergeNode", { in: "b" }, m2); svg("feMergeNode", { in: "SourceGraphic" }, m2);
  const grad = svg("radialGradient", { id: "centre-grad" }, defs);
  svg("stop", { offset: "0%",  "stop-color": `oklch(0.92 0.14 ${state.tweaks.hue})`, "stop-opacity": "0.95" }, grad);
  svg("stop", { offset: "55%", "stop-color": `oklch(0.55 0.12 ${state.tweaks.hue})`, "stop-opacity": "0.45" }, grad);
  svg("stop", { offset: "100%","stop-color": "oklch(0.25 0.04 250)",                "stop-opacity": "0" }, grad);

  const ambient = svg("g", { class: "ambient-layer" }, root);
  for (const p of FAM_POSITIONS) {
    const fam = FAMILY_BY_ID[p.id];
    svg("circle", {
      cx: p.x, cy: p.y, r: FAMILY_R + 6,
      fill: "none",
      stroke: `oklch(0.55 0.06 ${fam.hue} / 0.18)`,
      "stroke-width": "0.6",
      class: "ambient-pulse",
    }, ambient);
  }

  dynamicLayer = svg("g", { class: "dynamic-layer" }, root);

  const famLayer = svg("g", { class: "families-layer" }, root);
  for (const p of FAM_POSITIONS) {
    const fam = FAMILY_BY_ID[p.id];
    const g = svg("g", { class: "fam-node", transform: `translate(${p.x},${p.y})`, "data-family-id": p.id }, famLayer);
    g.addEventListener("click", () => selectFamily(p.id));
    svg("circle", {
      class: "fam-aura", r: FAMILY_R + 14,
      fill: `oklch(0.85 0.17 ${fam.hue} / 0.25)`,
      stroke: "none",
      filter: "url(#big-glow)",
      style: "opacity: 0;",
    }, g);
    svg("circle", {
      class: "fam-base", r: FAMILY_R,
      fill: `oklch(0.22 0.05 ${fam.hue} / 0.55)`,
      stroke: `oklch(0.55 0.12 ${fam.hue} / 0.55)`,
      "stroke-width": "1.2",
    }, g);
    svg("text", { class: "fam-label-inside", "text-anchor": "middle", y: -3 }, g).textContent = fam.label;
    const counter = svg("text", { class: "fam-count-inside", "text-anchor": "middle", y: 14 }, g);
    counter.textContent = "0";
    counter.dataset.role = "fam-count";
    famGroupsById.set(p.id, g);
  }

  const centre = svg("g", { class: "centre-layer" }, root);
  svg("circle", { class: "centre-aura", r: 180, fill: "url(#centre-grad)" }, centre);
  svg("circle", {
    class: "centre-orbit-slow", r: 128, fill: "none",
    stroke: `oklch(0.85 0.16 ${state.tweaks.hue} / 0.22)`,
    "stroke-width": "0.8",
  }, centre);
  svg("circle", {
    r: CENTRE_R, fill: "oklch(0.18 0.03 250)",
    stroke: `oklch(0.95 0.18 ${state.tweaks.hue})`, "stroke-width": "2.2",
    filter: "url(#stroke-glow)",
  }, centre);
  svg("circle", {
    class: "centre-orbit-fast", r: CENTRE_R, fill: "none",
    stroke: `oklch(0.95 0.18 ${state.tweaks.hue})`,
    "stroke-width": "1.2", "stroke-dasharray": "80 400", opacity: "0.7",
  }, centre);
  centreLabel = svg("text", { class: "centre-label", "text-anchor": "middle", y: -16 }, centre);
  centreLabel.textContent = state.workspace || "workspace";
  svg("text", { class: "centre-sub", "text-anchor": "middle", y: 8 }, centre).textContent = "memory anchor";
  stageText = svg("text", {
    class: "centre-stage", "text-anchor": "middle", y: 36,
    fill: `oklch(0.95 0.2 ${state.tweaks.hue})`,
  }, centre);
  stageText.textContent = "IDLE";

  shellMounted = true;
}

// ---- per-frame paint --------------------------------------------------------

function paintFrame() {
  const q = state.activeQuery;
  const progress = state.progress;
  const phase = state.phase;
  const counts = state.memory?.counts || {};
  const showAnimation = !!q && state.cycleStart > 0;

  // Family base style + counter
  for (const f of FAMILIES) {
    const total = f.tables.reduce((acc, t) => acc + (counts[t] || 0), 0);
    const g = famGroupsById.get(f.id);
    if (!g) continue;
    const counter = g.querySelector('[data-role="fam-count"]');
    if (counter) counter.textContent = String(total);

    let lit = 0;
    if (q && q.families.includes(f.id)) {
      if (showAnimation) {
        const start = FAMILY_ARRIVAL - 0.08, end = FAMILY_ARRIVAL;
        lit = Math.max(0, Math.min(1, (progress - start) / (end - start)));
        if (phase === "reverse") lit = Math.min(lit, progress);
      } else {
        // post-cycle: keep selected family softly lit so the user still sees focus
        lit = state.selected?.kind === "family" && state.selected.famId === f.id ? 0.5 : 0;
      }
    }
    const liveTtl = state.liveLight.get(f.id) || 0;
    if (liveTtl > performance.now()) lit = Math.max(lit, 0.85);
    g.classList.toggle("is-lit", lit > 0.4);
    const aura = g.querySelector(".fam-aura");
    if (aura) aura.style.opacity = String(lit);
    const base = g.querySelector(".fam-base");
    if (base) {
      base.setAttribute("fill", `oklch(0.22 0.05 ${f.hue} / ${0.55 + 0.4 * lit})`);
      base.setAttribute("stroke", `oklch(${0.5 + 0.4 * lit} 0.18 ${f.hue} / ${0.55 + 0.45 * lit})`);
      base.setAttribute("stroke-width", String(1.2 + 1.2 * lit));
    }
  }

  // dynamic layer: edges + objects, only while a cycle is running
  clear(dynamicLayer);
  if (q && showAnimation && q.families.length) {
    for (const fid of q.families) {
      const fp = POS_BY_ID[fid];
      const fam = FAMILY_BY_ID[fid];
      const trunkProg = Math.max(0, Math.min(1, progress / FAMILY_ARRIVAL));
      drawEdge(dynamicLayer, trunkPath(fp), trunkProg, fam.hue, 1.8);

      const objs = q.objects.filter(o => o.famId === fid);
      const positions = objectPositions(fp, objs.length);
      for (let i = 0; i < objs.length; i++) {
        const op = positions[i] || { x: fp.x, y: fp.y, ring: 0 };
        const arrival = Math.min(0.96, FAMILY_ARRIVAL + 0.15 + op.ring * PER_RING_DELTA);
        const spurStart = FAMILY_ARRIVAL + 0.02;
        const spurProg = Math.max(0, Math.min(1, (progress - spurStart) / (arrival - spurStart)));
        drawEdge(dynamicLayer, spurPath(fp, op), spurProg, fam.hue, 1.4);
        const start = arrival - 0.04, end = arrival + 0.04;
        const vis = Math.max(0, Math.min(1, (progress - start) / (end - start)));
        if (vis > 0.02) drawObject(dynamicLayer, op, fp, fam, objs[i], vis);
      }
    }
  }

  // centre stage + labels
  const stage = STAGE_BY_PHASE(phase, progress);
  if (stageText) stageText.textContent = stage;
  if (centreLabel) centreLabel.textContent = clip(state.workspace || "workspace", 18);

  els.metricStage.textContent = stage;
  els.metricPhase.textContent = phase.toUpperCase();
  if (q) {
    els.metricObjects.textContent = String(q.objects.length);
    els.metricFamilies.textContent = String(q.families.length);
    els.liveIntentKind.textContent = q.intent;
    els.liveIntentPrompt.textContent = `"${clip(q.prompt, 120)}"`;
    els.overlayPhase.textContent = phase;
    els.overlayIntent.textContent = q.intent;
    els.overlayPrompt.textContent = `"${clip(q.prompt, 140)}"`;
    renderFamiliesTouched(q);
    renderObjectsInContext(q);
  } else {
    els.metricObjects.textContent = "0";
    els.metricFamilies.textContent = "0";
    els.liveIntentKind.textContent = "idle";
    els.liveIntentPrompt.textContent = "Search, Explain, or click a family node to drive the graph.";
    els.overlayPhase.textContent = "idle";
    els.overlayIntent.textContent = "awaiting request";
    els.overlayPrompt.textContent = "Run Search or Explain — the graph reacts to real backend traffic.";
    clear(els.familiesTouched);
    clear(els.objectsInContext);
  }
}

function drawEdge(layer, d, prog, hue, width) {
  if (prog <= 0) return;
  svg("path", {
    d, fill: "none",
    stroke: `oklch(0.85 0.18 ${hue})`,
    "stroke-width": width + 4,
    "stroke-linecap": "round",
    opacity: "0.18",
    filter: "url(#big-glow)",
    pathLength: 1, "stroke-dasharray": `${prog} 1`,
  }, layer);
  const main = svg("path", {
    d, fill: "none",
    stroke: `oklch(0.88 0.18 ${hue})`,
    "stroke-width": width,
    "stroke-linecap": "round",
    filter: "url(#stroke-glow)",
    pathLength: 1, "stroke-dasharray": `${prog} 1`,
  }, layer);
  svg("path", {
    d, fill: "none",
    stroke: `oklch(0.96 0.2 ${hue})`,
    "stroke-width": width * 0.4,
    "stroke-linecap": "round",
    opacity: "0.85",
    pathLength: 1, "stroke-dasharray": `${prog} 1`,
  }, layer);
  if (prog > 0.02 && prog < 0.99) {
    try {
      const len = main.getTotalLength();
      const pt = main.getPointAtLength(len * prog);
      const head = svg("g", { transform: `translate(${pt.x},${pt.y})` }, layer);
      svg("circle", { r: 7, fill: `oklch(0.96 0.2 ${hue})`, opacity: "0.3", filter: "url(#big-glow)" }, head);
      svg("circle", { r: 2.6, fill: `oklch(0.96 0.2 ${hue})`, filter: "url(#stroke-glow)" }, head);
      svg("circle", { r: 1.2, fill: "white" }, head);
    } catch { /* path not measurable yet */ }
  }
}

function drawObject(layer, pos, parent, fam, obj, vis) {
  const r = OBJECT_R * vis;
  const haloR = OBJECT_R + 10 * vis;
  const g = svg("g", { class: "obj-node", transform: `translate(${pos.x},${pos.y})` }, layer);
  g.addEventListener("click", (ev) => { ev.stopPropagation(); selectObject(obj); });
  svg("circle", {
    class: "obj-halo", r: haloR,
    fill: `oklch(0.85 0.18 ${fam.hue} / ${0.25 * vis})`,
    filter: "url(#big-glow)",
  }, g);
  svg("circle", {
    r, fill: `oklch(0.24 0.05 ${fam.hue})`,
    stroke: `oklch(0.92 0.18 ${fam.hue} / ${vis})`,
    "stroke-width": 1.4,
    filter: "url(#stroke-glow)",
  }, g);
  svg("circle", { r: 1.6 * vis, fill: `oklch(0.98 0.16 ${fam.hue})`, opacity: vis }, g);
  if (vis > 0.85) {
    const dx = pos.x - parent.x, dy = pos.y - parent.y;
    const dl = Math.hypot(dx, dy) || 1;
    const ux = dx / dl, uy = dy / dl;
    const gap = OBJECT_R + 14 + (pos.ring || 0) * 6;
    let anchor = "middle";
    if (ux > 0.35) anchor = "start";
    else if (ux < -0.35) anchor = "end";
    const txt = svg("text", {
      class: "obj-label",
      x: ux * gap, y: uy * gap,
      "dominant-baseline": "middle",
      "text-anchor": anchor,
      fill: `oklch(0.96 0.08 ${fam.hue})`,
    }, g);
    txt.textContent = clip(obj.label, 22);
  }
}

// ---- live intent rail panel -------------------------------------------------

function renderFamiliesTouched(q) {
  clear(els.familiesTouched);
  for (const fid of q.families) {
    const fam = FAMILY_BY_ID[fid];
    const pill = document.createElement("button");
    pill.className = "link-pill";
    pill.type = "button";
    pill.textContent = fam.label;
    pill.style.borderColor = `oklch(0.55 0.1 ${fam.hue} / 0.6)`;
    pill.style.color = `oklch(0.88 0.1 ${fam.hue})`;
    pill.style.cursor = "pointer";
    pill.addEventListener("click", () => selectFamily(fid));
    els.familiesTouched.appendChild(pill);
  }
}
function renderObjectsInContext(q) {
  clear(els.objectsInContext);
  if (!q.objects.length) {
    const empty = document.createElement("div");
    empty.className = "inspector-blurb";
    empty.style.fontSize = "11px";
    empty.textContent = "No objects in this result.";
    els.objectsInContext.appendChild(empty);
    return;
  }
  for (const obj of q.objects) {
    const fam = FAMILY_BY_ID[obj.famId];
    const row = document.createElement("button");
    row.className = "inspector-row is-used";
    row.type = "button";
    row.style.borderColor = `oklch(0.5 0.1 ${fam.hue} / 0.5)`;
    const titleEl = document.createElement("span");
    titleEl.className = "row-title";
    titleEl.textContent = obj.label;
    if (obj.raw && obj.raw.short_label) {
      const hint = document.createElement("span");
      hint.className = "row-label-hint";
      hint.textContent = "label";
      titleEl.appendChild(hint);
    }
    const t = document.createElement("span");
    t.className = "row-type";
    t.textContent = fam.label.toLowerCase();
    t.style.color = `oklch(0.78 0.1 ${fam.hue})`;
    row.append(titleEl, t);
    row.addEventListener("click", () => selectObject(obj));
    els.objectsInContext.appendChild(row);
  }
}

// ---- click handlers: real backend calls ------------------------------------

async function selectFamily(fid) {
  state.selected = { kind: "family", famId: fid };
  renderInspector();
  let detail = state.detailCache.get(fid);
  if (!detail) {
    try {
      detail = await fetchFamilyDetail(fid);
      state.detailCache.set(fid, detail);
    } catch (err) {
      detail = { objects: [], error: String(err) };
    }
  }
  state.selected = { kind: "family", famId: fid, detail };
  renderInspector();
  if (detail.objects?.length) {
    runQueryAnimation({
      intent: fid,
      prompt: `Inspecting ${FAMILY_BY_ID[fid].label}`,
      families: [fid],
      objects: detail.objects.slice(0, 5),
      source: "family-click",
    });
  }
}

function selectObject(obj) {
  state.selected = { kind: "object", famId: obj.famId, obj };
  renderInspector();
}

async function fetchFamilyDetail(fid) {
  const ws = state.workspace;
  const adapt = (rows, table, kind) => rows.map(r => ({
    id: r.id || r[`${kind}_id`],
    label: r.title || r.name || r.label || r.summary || r.id,
    famId: fid,
    table,
    raw: r,
  }));

  if (fid === "decisions") {
    const data = await postJson("/memory/list_decisions", { workspace_id: ws, query: "", include_superseded: false, limit: 12 });
    return { objects: adapt(data.decisions || data.items || [], "decisions", "decision") };
  }
  if (fid === "research") {
    const data = await postJson("/memory/list_theories", { workspace_id: ws, query: "", include_evidence: true, limit: 12 });
    return { objects: adapt(data.theories || data.items || [], "theories", "theory") };
  }
  if (fid === "instructions") {
    const data = await postJson("/memory/list_behavior_instructions", { workspace_id: ws, query: "", limit: 12 });
    return { objects: adapt(data.instructions || data.items || [], "behavior_instructions", "instruction") };
  }
  if (fid === "roles" || fid === "skills") {
    const data = await postJson("/memory/list_agent_capabilities", { workspace_id: ws, query: "", limit: 18 });
    let rows = [];
    if (fid === "roles")  rows = data.roles || [];
    if (fid === "skills") rows = [...(data.skills || []), ...(data.playbooks || [])];
    return { objects: adapt(rows, fid === "roles" ? "agent_roles" : "agent_skills", fid) };
  }
  // episodes / tasks / feedback — fall back to /memory/ui/state.recent
  const recent = (state.memory?.recent || [])
    .filter(r => FAMILY_BY_ID[fid].tables.includes(r.table))
    .slice(0, 12);
  return {
    objects: recent.map(r => ({
      id: r.id,
      label: r.short_label || r.label || r.id,
      famId: fid,
      table: r.table,
      raw: r,
    })),
  };
}

// ---- inspector card render -------------------------------------------------

function renderInspector() {
  const card = els.inspectorCard;
  card.hidden = !state.selected;
  if (!state.selected) return;
  clear(card);
  card.style.position = "relative";
  const close = document.createElement("button");
  close.className = "x-btn";
  close.style.position = "absolute";
  close.style.top = "10px";
  close.style.right = "12px";
  close.textContent = "×";
  close.addEventListener("click", () => { state.selected = null; renderInspector(); });
  card.appendChild(close);

  if (state.selected.kind === "family") {
    const fam = FAMILY_BY_ID[state.selected.famId];
    const k = document.createElement("div"); k.className = "inspector-kicker"; k.textContent = `family · ${fam.id}`;
    const t = document.createElement("div"); t.className = "inspector-title"; t.style.color = `oklch(0.92 0.1 ${fam.hue})`; t.textContent = fam.label;
    const b = document.createElement("div"); b.className = "inspector-blurb"; b.textContent = fam.blurb;
    card.append(k, t, b);
    const list = document.createElement("div"); list.className = "inspector-list";
    const detail = state.selected.detail;
    const rows = detail?.objects || [];
    if (detail?.error) {
      const e = document.createElement("div"); e.className = "inspector-blurb"; e.textContent = `Error: ${detail.error}`; list.appendChild(e);
    } else if (!rows.length) {
      const e = document.createElement("div"); e.className = "inspector-blurb"; e.textContent = "No objects yet in this family.";
      list.appendChild(e);
    } else {
      for (const r of rows) {
        const btn = document.createElement("button"); btn.className = "inspector-row"; btn.type = "button";
        const tt = document.createElement("span"); tt.className = "row-title"; tt.textContent = r.label;
        const tp = document.createElement("span"); tp.className = "row-type"; tp.textContent = r.table.replace(/_/g, " ");
        btn.append(tt, tp);
        btn.addEventListener("click", () => selectObject(r));
        list.appendChild(btn);
      }
    }
    card.appendChild(list);
    return;
  }

  if (state.selected.kind === "object") {
    const fam = FAMILY_BY_ID[state.selected.famId];
    const obj = state.selected.obj;
    const raw = obj.raw || {};
    const k = document.createElement("div"); k.className = "inspector-kicker"; k.textContent = `object · ${fam.label.toLowerCase()}`;
    const t = document.createElement("div"); t.className = "inspector-title"; t.style.color = `oklch(0.92 0.1 ${fam.hue})`;
    t.textContent = obj.label || obj.id || "—";
    card.append(k, t);

    const grid = document.createElement("div"); grid.className = "kv-grid";
    grid.innerHTML = `
      <div class="kv"><span>id</span><code>${escapeHtml(obj.id || raw.id || "—")}</code></div>
      <div class="kv"><span>table</span><span>${escapeHtml(obj.table || "—")}</span></div>
      <div class="kv"><span>updated</span><span>${escapeHtml(fmtTime(raw.updated_at || raw.created_at))}</span></div>
      <div class="kv"><span>label</span><span>${escapeHtml(raw.short_label || raw.label || "—")}</span></div>
    `;
    card.appendChild(grid);

    // Body — what content matters depends on the table.
    const body = renderObjectBody(obj.table, raw);
    if (body) card.appendChild(body);
  }
}

function renderObjectBody(table, raw) {
  const make = (sections) => {
    const wrap = document.createElement("div");
    for (const [title, content] of sections) {
      if (!content) continue;
      const sec = document.createElement("div"); sec.className = "inspector-section";
      const h = document.createElement("div"); h.className = "section-h"; h.textContent = title;
      const p = document.createElement("p");
      p.style.fontSize = "12px"; p.style.color = "var(--text-1)"; p.style.lineHeight = "1.45";
      p.style.whiteSpace = "pre-wrap"; p.style.wordBreak = "break-word";
      p.textContent = String(content);
      sec.append(h, p);
      wrap.appendChild(sec);
    }
    return wrap;
  };
  if (table === "decisions") {
    return make([
      ["title", raw.title],
      ["decision", raw.decision_text],
      ["rationale", raw.rationale],
      ["status", raw.status],
    ]);
  }
  if (table === "theories") {
    return make([
      ["claim", raw.claim],
      ["mechanism", raw.mechanism],
      ["predictions", Array.isArray(raw.predictions) ? raw.predictions.join("\n") : raw.predictions],
      ["validation criteria", Array.isArray(raw.validation_criteria) ? raw.validation_criteria.join("\n") : raw.validation_criteria],
      ["status", raw.status ? `${raw.status} (confidence ${raw.confidence ?? "—"})` : null],
    ]);
  }
  if (table === "behavior_instructions") {
    return make([
      ["rule", raw.rule],
      ["rationale", raw.rationale],
      ["scope / priority", `${raw.scope || "—"} · ${raw.priority || "—"}`],
    ]);
  }
  if (table === "agent_roles") {
    return make([
      ["purpose", raw.purpose],
      ["responsibilities", Array.isArray(raw.responsibilities) ? raw.responsibilities.join("\n") : raw.responsibilities],
      ["boundaries", Array.isArray(raw.boundaries) ? raw.boundaries.join("\n") : raw.boundaries],
    ]);
  }
  if (table === "agent_skills") {
    return make([
      ["summary", raw.summary],
      ["when to use", Array.isArray(raw.when_to_use) ? raw.when_to_use.join("\n") : raw.when_to_use],
    ]);
  }
  // generic fallback — episodes (recent rows), tasks, etc.
  const text = raw.raw_text || raw.text || raw.summary || raw.label || raw.short_label;
  if (text) return make([["body", text]]);
  return null;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ---- backend wiring ---------------------------------------------------------

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
    if (!r.ok) throw new Error(`${r.status}`);
    memory = await r.json();
  } catch {
    setSseChip("offline", "is-warn");
    return;
  }
  state.memory = memory;
  state.workspace = memory.workspace_id || ws;
  state.hubMode = Boolean(memory.hub_mode);
  state.workspaceRegistry.clear();
  for (const e of memory.registered_workspaces || []) {
    state.workspaceRegistry.set(e.id, {
      db_path: e.db_path || "", vector_path: e.vector_path || "",
      project_root: e.project_root || "", label: e.label || e.id,
    });
  }
  state.detailCache.clear();
  populateWorkspaceDropdown(memory.workspaces || [state.workspace]);
  if (!shellMounted) mountShell();
  renderHeader(memory);
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
  clear(els.workspace);
  for (const [id, name] of labels) {
    const opt = document.createElement("option");
    opt.value = id; opt.textContent = name;
    els.workspace.appendChild(opt);
  }
  els.workspace.value = current;
}

function ensureSse() {
  if (state.eventSource || state.paused || state.token) return;
  const ws = selectedWorkspace();
  if (!ws) return;
  const url = appendRouteParams(`/memory/ui/events?workspace_id=${encodeURIComponent(ws)}`, ws);
  const src = new EventSource(url);
  state.eventSource = src;
  src.addEventListener("open", () => { state.sseReady = true; setSseChip("SSE live", ""); });
  const onMsg = (m) => {
    try { onMemoryEvent(JSON.parse(m.data)); } catch { /* ignore */ }
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
function resetSse() { if (state.eventSource) state.eventSource.close(); state.eventSource = null; state.sseReady = false; }
function setSseChip(text, cls = "") { els.sseChip.textContent = text; els.sseChip.className = `foot-mute ${cls}`; }

function onMemoryEvent(ev) {
  if (ev.id && state.eventIds.has(ev.id)) return;
  if (ev.id) state.eventIds.add(ev.id);
  state.events.unshift({
    kind: trailKindOf(ev),
    t: fmtTime(ev.timestamp || new Date()),
    text: ev.label || ev.endpoint || ev.operation || ev.kind || "memory event",
  });
  state.events = state.events.slice(0, 60);
  if (ev.kind === "graph_delta" && ev.counts && typeof ev.counts.table === "string") {
    const fid = FAMILY_BY_TABLE[ev.counts.table];
    if (fid) state.liveLight.set(fid, performance.now() + 5000 / Math.max(0.3, state.tweaks.speed));
  }
  renderFeed();
}
function trailKindOf(ev) {
  if (ev.kind === "graph_delta") return "pick";
  if (ev.kind === "stage") return "route";
  if (ev.endpoint && ev.endpoint.includes("ingest")) return "in";
  if (ev.endpoint && ev.endpoint.includes("get_context")) return "out";
  if (ev.severity === "warn" || ev.severity === "warning") return "warn";
  return "route";
}

function renderHeader(memory) {
  const counts = memory.counts || {};
  const chunks = counts.chunks || 0;
  els.chunksChip.querySelector(".chip-value").textContent = String(chunks);
  els.vectorsChip.querySelector(".chip-value").textContent = String(chunks);
  const maint = (memory.warnings || []).length;
  els.maintChip.querySelector(".chip-value").textContent = String(maint);
  els.maintChip.classList.toggle("is-warn", maint > 0);

  const status = state.health?.status || "ok";
  const retr = state.health?.retrieval_integrity?.status || "ok";
  const ok = status === "ok" && retr === "ok";
  els.healthChip.querySelector(".chip-value").textContent = ok ? "ok" : status;
  els.healthChip.classList.toggle("is-warn", !ok);
  els.overlayDot.className = ok ? "dot-ok" : "dot-ok is-warn";
  els.overlayHealth.textContent = ok ? "health ok" : `health ${status}`;
  els.overlayWorkspace.textContent = state.workspace || "workspace";
  els.overlayCounts.textContent = `${chunks} chunks · ${state.events.length} events`;
}
function renderFeed() {
  clear(els.lifeFeed);
  for (const ev of state.events.slice(0, 30)) {
    const row = document.createElement("div");
    row.className = `trail-row trail-${ev.kind}`;
    const t = document.createElement("span"); t.className = "trail-time"; t.textContent = ev.t;
    const x = document.createElement("span"); x.className = "trail-text"; x.textContent = ev.text;
    row.append(t, x);
    els.lifeFeed.appendChild(row);
  }
}
function renderWarnings(list) {
  if (!list || !list.length) { els.warningsPanel.hidden = true; return; }
  els.warningsPanel.hidden = false;
  clear(els.warningsList);
  for (const w of list.slice(0, 8)) {
    const row = document.createElement("div"); row.className = "warning-row";
    const k = document.createElement("span"); k.className = "warn-kind"; k.textContent = w.kind || "open";
    const x = document.createElement("span"); x.textContent = w.summary || JSON.stringify(w);
    row.append(k, x);
    els.warningsList.appendChild(row);
  }
}

// ---- Search / Explain → real animation -------------------------------------

async function postJson(path, body) {
  const r = await fetch(path, { method: "POST", headers: buildHeaders(true), body: JSON.stringify(body) });
  const text = await r.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!r.ok) throw new Error(`${path} ${r.status}: ${text.slice(0, 240)}`);
  return data;
}

function buildQueryFromHits(prompt, intent, hits, defaultFamId = "research") {
  const objects = hits.slice(0, 8).map(h => {
    const meta = h.metadata || {};
    const kind = meta.kind || h.type || "chunk";
    let famId = defaultFamId;
    if (kind === "decision") famId = "decisions";
    else if (kind === "theory" || kind === "experiment" || kind === "snapshot" || kind === "insight" || kind === "concept") famId = "research";
    else if (kind === "episode") famId = "episodes";
    else if (kind === "behavior_instruction") famId = "instructions";
    else if (kind === "role") famId = "roles";
    else if (kind === "skill" || kind === "playbook") famId = "skills";
    return {
      id: h.id || h.chunk_id,
      label: meta.label || h.label || meta.path || clip(h.text || h.snippet || h.id, 22),
      famId,
      table: meta.kind === "decision" ? "decisions" : meta.kind === "theory" ? "theories" : "chunks",
      raw: h,
    };
  });
  const families = Array.from(new Set(objects.map(o => o.famId)));
  return { intent, prompt, families, objects, source: intent };
}

async function searchMemory() {
  const q = els.query.value.trim();
  if (!q) return;
  els.searchSummary.textContent = "searching…";
  try {
    const data = await postJson("/memory/search", { workspace_id: state.workspace, query: q, mode: "fts", limit: 6 });
    const hits = data.hits || data.results || [];
    runQueryAnimation(buildQueryFromHits(q, "search", hits));
    els.searchSummary.textContent = `${hits.length} hit${hits.length === 1 ? "" : "s"} from /memory/search`;
  } catch (err) { els.searchSummary.textContent = String(err); }
}

async function explainContext() {
  const q = els.query.value.trim();
  if (!q) return;
  els.searchSummary.textContent = "fetching context…";
  try {
    const data = await postJson("/memory/get_context", { workspace_id: state.workspace, query: q, max_tokens: 1500 });
    els.contextBox.textContent = data.context_text || "";
    const sources = data.sources || [];
    runQueryAnimation(buildQueryFromHits(q, "explain", sources));
    const tokens = data.budget_diagnostics?.estimated_tokens || 0;
    els.searchSummary.textContent = `${sources.length} sources · ~${tokens} tokens from /memory/get_context`;
  } catch (err) { els.searchSummary.textContent = String(err); }
}

function applyTweaks() {
  document.documentElement.style.setProperty("--accent-hue", String(state.tweaks.hue));
  els.hueLabel.textContent = `${state.tweaks.hue}°`;
  els.speedLabel.textContent = `${state.tweaks.speed.toFixed(2)}×`;
  if (shellMounted) mountShell();
}

// ---- wiring ----------------------------------------------------------------

els.workspace.addEventListener("change", () => { resetSse(); fetchState({ manual: true }); });
els.token.addEventListener("change", () => { state.token = els.token.value.trim(); resetSse(); fetchState({ manual: true }); });
els.refresh.addEventListener("click", () => fetchState({ manual: true }));
els.pause.addEventListener("click", () => {
  state.paused = !state.paused;
  els.pause.classList.toggle("is-paused", state.paused);
  els.pause.textContent = state.paused ? "▶ resume" : "❚❚ pause";
});
els.search.addEventListener("click", () => searchMemory());
els.context.addEventListener("click", () => explainContext());
els.query.addEventListener("keydown", (ev) => { if (ev.key === "Enter") searchMemory(); });

els.hueRange.addEventListener("input", () => { state.tweaks.hue = Number(els.hueRange.value); applyTweaks(); });
els.speedRange.addEventListener("input", () => { state.tweaks.speed = Number(els.speedRange.value); applyTweaks(); });
els.densityInput.addEventListener("change", () => { state.tweaks.density = els.densityInput.value; });
els.pulseInput.addEventListener("change", () => { state.tweaks.pulse = els.pulseInput.checked; });
els.tweaksToggle.addEventListener("click", () => {
  state.tweaks.panelOpen = !state.tweaks.panelOpen;
  els.tweaksPanel.classList.toggle("is-collapsed", !state.tweaks.panelOpen);
  els.tweaksToggle.textContent = state.tweaks.panelOpen ? "−" : "+";
});

mountShell();
applyTweaks();
fetchState({ manual: true });
requestAnimationFrame(tick);
setInterval(() => { if (!state.sseReady && !state.paused) fetchState(); }, POLL_MS);
