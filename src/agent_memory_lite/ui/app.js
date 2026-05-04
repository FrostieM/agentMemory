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
// graph_delta events carry counts.object_type (the logical kind: "decision",
// "theory", "episode", "skill", …). Map those to families directly so the
// SSE pipeline doesn't depend on table-name conventions.
const FAMILY_BY_OBJECT_TYPE = {
  episode: "episodes",
  chunk: "episodes",
  file: "episodes",
  decision: "decisions",
  theory: "research",
  theory_evidence: "research",
  experiment: "research",
  experiment_result: "research",
  snapshot: "research",
  insight: "research",
  concept: "research",
  task_state: "tasks",
  candidate: "tasks",
  memory_candidate: "tasks",
  role: "roles",
  skill: "skills",
  playbook: "skills",
  capability_link: "skills",
  behavior_instruction: "instructions",
  procedural_rule: "instructions",
  feedback: "feedback",
  memory_usage_feedback: "feedback",
};
function familyForEvent(counts) {
  if (!counts) return null;
  const ot = counts.object_type || counts.kind || "";
  if (ot && FAMILY_BY_OBJECT_TYPE[ot]) return FAMILY_BY_OBJECT_TYPE[ot];
  if (counts.table && FAMILY_BY_TABLE[counts.table]) return FAMILY_BY_TABLE[counts.table];
  return null;
}

// ---- state ------------------------------------------------------------------

const state = {
  workspace: "",
  hubMode: false,
  workspaceRegistry: new Map(),
  token: "",
  paused: false,
  memory: null,
  health: null,
  // Trail groups: one entry per memory operation (request_id). A single
  // ingest typically fires 8-12 SSE events (request_started → stage_*
  // ×N → graph_delta ×M → request_done) within 50 ms; rendering them as
  // separate rows looks like a flickering wall of noise. Group them
  // here so the trail reads as "1 row = 1 operation" with stages and
  // deltas rolled up inside each group.
  trailGroups: [],
  trailGroupsById: new Map(), // requestId → group reference (same object as in trailGroups)
  events: [],                 // legacy raw event count for the chip
  eventIds: new Set(),
  eventSource: null,
  sseReady: false,
  liveLight: new Map(), // famId → expiresAtMs
  selected: null,
  inspectorHistory: [],   // back-button stack of previous selections
  detailCache: new Map(), // famId → fetched detail rows
  recentLabels: new Map(),// id → short_label cache from /memory/ui/state.recent
  countDeltas: new Map(), // famId → optimistic count delta since last fetchState
  tweaks: { hue: 160, speed: 0.7, density: "medium", pulse: true, panelOpen: true },
  // animation pipeline:
  //   • Every memory operation has one request_id and emits a sequence
  //     of SSE events: request_started → graph_delta(s) → request_done.
  //   • state.requestBuffer collects all events for a request_id; on
  //     request_done (or 1.5 s of silence after the last event), the
  //     buffered events are coalesced into ONE query (all touched
  //     families + all touched objects of that operation) and pushed
  //     to state.queue.
  //   • tick() plays one cycle (forward → hold → reverse), then a
  //     short IDLE gap with an empty graph, then pulls the next query.
  //   • There is NO synthetic auto-cycle. If the service is silent the
  //     graph stays idle. The cycle is a faithful replay of real
  //     memory traffic — what you see is what just happened.
  queue: [],
  requestBuffer: new Map(),     // request_id → { families, objects, … }
  activeQuery: null,
  cycleStart: 0,
  reverseStart: 0,              // when the reverse easing started for this cycle
  idleStart: 0,
  lastIntent: "",
  phase: "idle",
  progress: 0,
};
const QUEUE_CAP = 24;
const IDLE_GAP_MS = 800;
// Per-cycle small-node cap, driven by the Tweaks panel's "density" knob.
// Geometry in objectPositions() can fit ~47 slots across 6 rings, so even
// the dense ceiling is comfortably below the geometric limit.
const OBJECTS_CAP_BY_DENSITY = { sparse: 5, medium: 12, dense: 24 };
function objectsCap() {
  return OBJECTS_CAP_BY_DENSITY[state.tweaks.density] || 12;
}
const REQUEST_FLUSH_AFTER_MS = 1500;
// Events older than this (vs Date.now() at the moment they arrive) are
// considered SSE replay/history and won't trigger an animation cycle.
// 10 s is comfortably longer than any single live operation but short
// enough that the snapshot replay (which dumps up to 80 events) doesn't
// queue dozens of past cycles when the user opens the UI or switches
// workspaces.
const REPLAY_LIVE_WINDOW_MS = 10_000;
// How long a "this is our own search/explain" fingerprint stays valid.
// SSE for the local read usually arrives within ~200 ms, so 5 s is a
// generous window without clobbering legitimate reads from other
// agents that happen seconds later.
const LOCAL_READ_TTL_MS = 5_000;

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

// A family with several distinct sub-types (research = theories +
// snapshots + experiments + insights + concepts; skills = skill +
// playbook + capability_link; tasks = task_state + candidate; ...)
// gets a third tier in the graph: anchor → family → sub-family →
// object. Sub-family nodes are smaller than family centres but larger
// than object nodes so the eye can read the hierarchy at a glance.
//
// Geometry budget: at SUB_FAMILY_RING=175 the available arc fans out
// across most of the family's outward cone; each sub-family then owns
// a tight cluster of objects (SUB_FAMILY_OBJ_RING=55) whose own arc
// stays small enough not to wander into the neighbouring sub-family's
// territory. This keeps a 4-7-way split (Research at full spread)
// readable instead of a tangled overlap.
const SUB_FAMILY_R = 26;
const SUB_FAMILY_RING = 175;
const SUB_FAMILY_OBJ_RING = 55;
const SUB_FAMILY_OBJ_STEP = 28;
const SUB_FAMILY_OBJ_ARC_HALF = 0.22;

// Pretty labels for the sub-family centre. Keys are the SQLite table
// names (matching `obj.table`) the trace pipeline already attaches to
// every active object. Anything missing falls back to the table name
// with underscores stripped.
const SUB_FAMILY_LABELS = {
  episodes: "Episodes",
  chunks: "Chunks",
  files: "Files",
  decisions: "Decisions",
  theories: "Theories",
  theory_evidence: "Evidence",
  research_experiments: "Experiments",
  experiment_results: "Results",
  memory_snapshots: "Snapshots",
  research_insights: "Insights",
  domain_concepts: "Concepts",
  agent_roles: "Roles",
  agent_skills: "Skills",
  agent_playbooks: "Playbooks",
  capability_links: "Links",
  task_state: "Task state",
  memory_candidates: "Candidates",
  behavior_instructions: "Behavior",
  procedural_rules: "Rules",
  memory_usage_feedback: "Feedback",
  retrieved_facts: "Facts",
};

function _subFamilyLabel(table) {
  return SUB_FAMILY_LABELS[table] || (table || "").replace(/_/g, " ");
}

const FAM_POSITIONS = (() => {
  const n = FAMILIES.length;
  return FAMILIES.map((f, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n + (i % 2 === 0 ? 0.04 : -0.04);
    const r = FAMILY_RING + (i % 3 === 0 ? 14 : i % 3 === 1 ? -8 : 0);
    return { id: f.id, x: Math.cos(a) * r, y: Math.sin(a) * r, angle: a };
  });
})();
const POS_BY_ID = Object.fromEntries(FAM_POSITIONS.map(p => [p.id, p]));

function objectPositions(parent, n, opts = {}) {
  if (!n) return [];
  const out = [];
  const baseR = opts.baseR ?? 120;
  const step = opts.step ?? 60;
  const minGap = OBJECT_R * 2.8;
  // The default arcHalf widens with object count so a flat-layout
  // family still reads as a fan, not a stack. The sub-family branch
  // overrides this with a tight cone (opts.arcHalf=0.22) so each
  // sub-family's children stay packed beneath their own centre and
  // don't bleed into the neighbouring sub-family's territory.
  const arcHalf =
    opts.arcHalf ?? (n >= 8 ? 0.42 : n >= 5 ? 0.38 : 0.32);
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

// Count how many small nodes of a given family are visually "active" at
// the current visualProgress. Mirrors the easing math used in the
// object-drawing loop (vis = (visualProgress - arrival±0.04) eased)
// so the displayed X/Y matches what the user sees on screen.
function activeObjectsForFamily(famId, objsThisFamily, visualProgress) {
  const fp = POS_BY_ID[famId];
  if (!fp || !objsThisFamily.length) return 0;
  const positions = objectPositions(fp, objsThisFamily.length);
  let count = 0;
  for (let i = 0; i < objsThisFamily.length; i++) {
    const op = positions[i] || { ring: 0 };
    const arrival = Math.min(0.96, FAMILY_ARRIVAL + 0.15 + op.ring * PER_RING_DELTA);
    const start = arrival - 0.04, end = arrival + 0.04;
    const vis = Math.max(0, Math.min(1, (visualProgress - start) / (end - start)));
    if (vis > 0.5) count++;
  }
  return count;
}

function trunkPath(p) {
  const fl = Math.hypot(p.x, p.y) || 1;
  const ux = p.x / fl, uy = p.y / fl;
  const cx0 = ux * CENTRE_R, cy0 = uy * CENTRE_R;
  const fx = p.x - ux * (FAMILY_R + 1), fy = p.y - uy * (FAMILY_R + 1);
  const mx = (cx0 + fx) / 2, my = (cy0 + fy) / 2;
  return `M ${cx0} ${cy0} Q ${mx} ${my} ${fx} ${fy}`;
}
function spurPath(parent, obj, parentR = FAMILY_R) {
  const dx = obj.x - parent.x, dy = obj.y - parent.y;
  const d = Math.hypot(dx, dy) || 1;
  const ux = dx / d, uy = dy / d;
  const fx = parent.x + ux * (parentR + 1), fy = parent.y + uy * (parentR + 1);
  const ox = obj.x - ux * (OBJECT_R + 2), oy = obj.y - uy * (OBJECT_R + 2);
  return `M ${fx} ${fy} L ${ox} ${oy}`;
}

// Edge from a family centre (radius FAMILY_R) to a sub-family centre
// (radius SUB_FAMILY_R). Straight line trimmed to each circle's edge
// so it terminates cleanly without crossing into either node.
function subFamilyPath(family, sub) {
  const dx = sub.x - family.x, dy = sub.y - family.y;
  const d = Math.hypot(dx, dy) || 1;
  const ux = dx / d, uy = dy / d;
  const fx = family.x + ux * (FAMILY_R + 1);
  const fy = family.y + uy * (FAMILY_R + 1);
  const sx = sub.x - ux * (SUB_FAMILY_R + 1);
  const sy = sub.y - uy * (SUB_FAMILY_R + 1);
  return `M ${fx} ${fy} L ${sx} ${sy}`;
}

// Lay out N sub-family centres on an arc around a family centre,
// pointing AWAY from the anchor (same direction objects already fan
// out). Wider arc when there are more sub-families so the labels
// don't overlap. Single sub-family stays directly outward — collapses
// the hierarchy back to "flat" visually because there's nothing to
// disambiguate.
function subFamilyPositions(parent, n) {
  if (!n) return [];
  if (n === 1) {
    const r = SUB_FAMILY_RING;
    return [
      {
        x: parent.x + Math.cos(parent.angle) * r,
        y: parent.y + Math.sin(parent.angle) * r,
        angle: parent.angle,
      },
    ];
  }
  // Arc is centred on the family's outward direction. We push close to
  // the geometric maximum (≈ 0.95 rad ≈ 54°) when there are many
  // sub-types so a 7-way Research split (theories + evidence +
  // experiments + results + snapshots + insights + concepts) still has
  // breathing room between the per-sub-family object clusters.
  const r = SUB_FAMILY_RING;
  const arcHalf = Math.min(0.95, 0.28 + 0.14 * (n - 1));
  const out = [];
  for (let i = 0; i < n; i++) {
    const frac = (i / (n - 1)) * 2 - 1; // -1 … +1
    const a = parent.angle + frac * arcHalf;
    out.push({
      x: parent.x + Math.cos(a) * r,
      y: parent.y + Math.sin(a) * r,
      angle: a,
    });
  }
  return out;
}

function _groupObjectsByTable(objs, fallbackFid) {
  const map = new Map();
  for (const o of objs) {
    const t = o.table || fallbackFid || "items";
    if (!map.has(t)) map.set(t, []);
    map.get(t).push(o);
  }
  return map;
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
  // Explicit Search / Explain — bypass the queue and start immediately.
  startQuery(query);
}

function enqueueQuery(query) {
  if (!query || !query.families?.length) return;
  // Coalesce: when the new event targets the same family + intent as
  // the most recent queued event, merge their object lists rather
  // than queueing two separate cycles. Without this a burst of 11
  // UPSERT_SKILL events takes 11 × 10s cycles to drain; with this
  // they collapse into one cycle that shows all 11 objects together.
  const tail = state.queue[state.queue.length - 1];
  if (
    tail &&
    query.intent &&
    tail.intent === query.intent &&
    sameFamilySet(tail.families, query.families)
  ) {
    tail.objects = mergeObjects(tail.objects || [], query.objects || []);
    tail.prompt = query.prompt || tail.prompt;
    return;
  }
  state.queue.push(query);
  if (state.queue.length > QUEUE_CAP) state.queue.shift();
}

function sameFamilySet(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  const sa = new Set(a);
  for (const x of b) if (!sa.has(x)) return false;
  return true;
}

function mergeObjects(existing, incoming) {
  const seen = new Set();
  const out = [];
  for (const o of [...existing, ...incoming]) {
    const key = o?.id || `${o?.famId}:${o?.label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(o);
  }
  return out.slice(0, objectsCap());
}

function startQuery(query) {
  state.activeQuery = query;
  state.cycleStart = performance.now();
  state.reverseStart = 0;
  state.idleStart = 0;
  state.phase = "forward";
  state.progress = 0;
  state.lastIntent = query?.intent || state.lastIntent;
}

function startNextFromQueue() {
  if (!state.queue.length) return false;
  startQuery(state.queue.shift());
  return true;
}


// Driver: setTimeout instead of requestAnimationFrame so the cycle keeps
// ticking when the tab is in the background, the window is minimized, or
// the page is rendered headless (the Preview MCP / Puppeteer-style
// inspectors hold a Chromium that throttles rAF to 0 Hz). 30 ms ≈ 33 fps,
// plenty smooth for these multi-second easings, and the cycle keeps real
// data flowing into the rail even when the user is on another screen.
function tick() {
  setTimeout(tick, 30);
  if (state.paused) return;
  if (!shellMounted) return;

  const sp = Math.max(0.3, state.tweaks.speed);
  // When the queue piles up (burst of writes — see scripts/crash_test_*),
  // sticking to the full 3.2s/4.5s/2.4s phases means each event takes
  // ~10s on screen. With 100+ real events arriving in 30s the user
  // sees a "stuck" graph because old events linger and new ones fall
  // off the queue. Compress phases proportionally to queue depth so a
  // burst plays back quickly and the user can see what just happened.
  // Solo events keep the readable 10s pacing.
  const pressure = Math.min(state.queue.length / 6, 4); // 0…4 multiplier
  const compress = 1 + pressure;
  const F = PHASE_MS.forward / sp / compress;
  const H = PHASE_MS.hold / sp / compress;
  const R = PHASE_MS.reverse / sp / compress;
  const G = IDLE_GAP_MS / sp / compress;

  // No cycle running.
  if (!state.cycleStart) {
    // Real SSE events drive everything. After a short idle gap, pull the
    // next coalesced operation off the queue. If the queue is empty we
    // simply stay idle — there is no synthetic auto-cycle.
    if (!state.idleStart) state.idleStart = performance.now();
    const idleElapsed = performance.now() - state.idleStart;
    if (idleElapsed >= G && state.queue.length) {
      state.idleStart = 0;
      startNextFromQueue();
      paintFrame();
      return;
    }
    // Idle: empty graph, waiting for the next real operation.
    state.activeQuery = null;
    state.phase = "idle";
    state.progress = 0;
    paintFrame();
    return;
  }

  const elapsed = performance.now() - state.cycleStart;

  // Forward phase: strokes draw outward.
  if (elapsed < F) {
    state.phase = "forward";
    state.progress = easeOutCubic(elapsed / F);
    paintFrame();
    return;
  }

  // Hold phase: strokes fully drawn.
  if (elapsed < F + H) {
    state.phase = "hold";
    state.progress = 1;
    paintFrame();
    return;
  }

  // Past hold. The LAST cycle freezes at hold for a bounded time so
  // the user can read the result without the graph snapping back
  // immediately. When the queue is empty, we still allow up to
  // ~6s of post-hold dwell, then auto-retract so the graph doesn't
  // look "stuck" on a stale event after a burst finishes. Reverse
  // also kicks in immediately when the queue has more queued work.
  const dwellLimit = (PHASE_MS.hold * 1.5) / sp;
  if (!state.queue.length && (elapsed - F - H) < dwellLimit) {
    state.phase = "hold";
    state.progress = 1;
    state.reverseStart = 0;
    paintFrame();
    return;
  }

  // Reverse phase: a new query is waiting, so retract the current one.
  // We anchor reverse to the moment we start it, not to cycleStart, so
  // freezing at hold for any duration doesn't break the easing.
  if (!state.reverseStart) state.reverseStart = performance.now();
  const reverseElapsed = performance.now() - state.reverseStart;
  if (reverseElapsed >= R) {
    state.lastIntent = state.activeQuery?.intent || state.lastIntent;
    state.activeQuery = null;
    state.cycleStart = 0;
    state.reverseStart = 0;
    state.idleStart = performance.now();
    state.phase = "idle";
    state.progress = 0;
    paintFrame();
    return;
  }
  state.phase = "reverse";
  state.progress = 1 - easeInCubic(reverseElapsed / R);
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
    class: "centre-core",
    r: CENTRE_R, fill: "oklch(0.18 0.03 250)",
    stroke: `oklch(0.95 0.18 ${state.tweaks.hue})`, "stroke-width": "2.2",
    filter: "url(#stroke-glow)",
  }, centre);
  svg("circle", {
    class: "centre-ring-breath",
    r: CENTRE_R + 4, fill: "none",
    stroke: `oklch(0.85 0.17 ${state.tweaks.hue} / 0.4)`,
    "stroke-width": "1",
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
  const cycleRunning = !!q && state.cycleStart > 0;
  // When no cycle is running, the graph is fully idle — no edges, no
  // objects. The reverse phase already retracts everything visually,
  // so post-cycle the graph reads as a clean orbit of dim families.
  const drawFamilies = cycleRunning ? (q?.families || []) : [];
  const visualProgress = cycleRunning ? progress : 0;
  const visualPhase = cycleRunning ? phase : "idle";

  // Family base style + counter.
  //   • Idle / no cycle               → counter = total in DB (with
  //                                     optimistic SSE deltas applied).
  //   • Cycle touches this family     → counter = X/TOTAL where X = how
  //                                     many small nodes from this
  //                                     family are currently lit
  //                                     (vis>0.5) and TOTAL is the same
  //                                     DB total shown in idle. So
  //                                     "2/3" reads as "2 of the 3
  //                                     items in this family are
  //                                     currently active".
  for (const f of FAMILIES) {
    const baseTotal = f.tables.reduce((acc, t) => acc + (counts[t] || 0), 0);
    const total = Math.max(0, baseTotal + (state.countDeltas?.get(f.id) || 0));
    const g = famGroupsById.get(f.id);
    if (!g) continue;
    const counter = g.querySelector('[data-role="fam-count"]');
    const objsThisFamily = cycleRunning ? (q.objects || []).filter(o => o.famId === f.id) : [];
    if (counter) {
      if (cycleRunning && objsThisFamily.length && total > 0) {
        const active = activeObjectsForFamily(f.id, objsThisFamily, visualProgress);
        counter.textContent = `${active}/${total}`;
      } else {
        counter.textContent = String(total);
      }
    }

    let lit = 0;
    if (drawFamilies.includes(f.id)) {
      if (cycleRunning) {
        const start = FAMILY_ARRIVAL - 0.08, end = FAMILY_ARRIVAL;
        lit = Math.max(0, Math.min(1, (progress - start) / (end - start)));
        if (phase === "reverse") lit = Math.min(lit, progress);
      } else {
        lit = 0.85;
      }
    }
    const liveTtl = state.liveLight.get(f.id) || 0;
    if (liveTtl > performance.now()) lit = Math.max(lit, 0.85);

    const isSelectedFamily = state.selected?.kind === "family" && state.selected.famId === f.id;
    const isObjectFamily   = state.selected?.kind === "object" && state.selected.famId === f.id;
    const isHighlighted    = isSelectedFamily || isObjectFamily;
    if (isHighlighted) lit = Math.max(lit, 0.9);

    g.classList.toggle("is-lit", lit > 0.4);
    g.classList.toggle("is-highlight", isHighlighted);
    const aura = g.querySelector(".fam-aura");
    if (aura) aura.style.opacity = String(lit);
    const base = g.querySelector(".fam-base");
    if (base) {
      base.setAttribute("fill", `oklch(0.22 0.05 ${f.hue} / ${0.55 + 0.4 * lit})`);
      base.setAttribute("stroke", `oklch(${0.5 + 0.4 * lit} 0.18 ${f.hue} / ${0.55 + 0.45 * lit})`);
      base.setAttribute("stroke-width", String(1.2 + 1.2 * lit));
    }
  }

  // Dynamic layer: persists between cycles. Holds the last query's
  // edges + object nodes at progress=1 so post-cycle the graph reads
  // like a frozen result, not a blank canvas.
  clear(dynamicLayer);
  if (q && drawFamilies.length) {
    for (const fid of drawFamilies) {
      const fp = POS_BY_ID[fid];
      const fam = FAMILY_BY_ID[fid];
      const trunkProg = Math.max(0, Math.min(1, visualProgress / FAMILY_ARRIVAL));
      drawEdge(dynamicLayer, trunkPath(fp), trunkProg, fam.hue, 1.8);

      const objs = q.objects.filter(o => o.famId === fid);
      // Group active objects by their SQLite table — every "family"
      // can host several distinct sub-types (research = theories +
      // snapshots + experiments + insights + concepts; skills = skill
      // + playbook + capability_link, …). When the active query
      // touches more than one of those sub-types we render an
      // intermediate sub-family node per table so the spokes don't
      // all collapse onto the family centre. With a single sub-type
      // the layout is identical to the previous flat version.
      const grouped = _groupObjectsByTable(objs, fid);
      if (grouped.size <= 1) {
        const positions = objectPositions(fp, objs.length);
        for (let i = 0; i < objs.length; i++) {
          const op = positions[i] || { x: fp.x, y: fp.y, ring: 0 };
          const arrival = Math.min(0.96, FAMILY_ARRIVAL + 0.15 + op.ring * PER_RING_DELTA);
          const spurStart = FAMILY_ARRIVAL + 0.02;
          const spurProg = Math.max(
            0,
            Math.min(1, (visualProgress - spurStart) / (arrival - spurStart)),
          );
          const obj = objs[i];
          const objHue = actionHueFor(fam.hue, obj?.action);
          drawEdge(dynamicLayer, spurPath(fp, op), spurProg, objHue, 1.4);
          const start = arrival - 0.04, end = arrival + 0.04;
          const vis = Math.max(0, Math.min(1, (visualProgress - start) / (end - start)));
          if (vis > 0.02) {
            const isSelectedObj = state.selected?.kind === "object" && state.selected.obj?.id === obj.id;
            drawObject(dynamicLayer, op, fp, { ...fam, hue: objHue }, obj, vis, isSelectedObj);
          }
        }
      } else {
        const tables = Array.from(grouped.keys());
        const subPositions = subFamilyPositions(fp, tables.length);
        const subStart = FAMILY_ARRIVAL + 0.02;
        const subEnd = FAMILY_ARRIVAL + 0.18;
        const subProg = Math.max(
          0,
          Math.min(1, (visualProgress - subStart) / (subEnd - subStart)),
        );
        for (let si = 0; si < tables.length; si++) {
          const table = tables[si];
          const subPos = subPositions[si];
          const subObjs = grouped.get(table);
          drawEdge(dynamicLayer, subFamilyPath(fp, subPos), subProg, fam.hue, 1.5);
          if (subProg > 0.05) {
            drawSubFamily(dynamicLayer, subPos, fam, table, subProg);
          }
          // Compact object layout: tight cone (arcHalf=0.22) at small
          // baseR so each sub-family's children stay clustered under
          // their own centre instead of fanning into the neighbouring
          // sub-family's territory.
          const positions = objectPositions(subPos, subObjs.length, {
            baseR: SUB_FAMILY_OBJ_RING,
            step: SUB_FAMILY_OBJ_STEP,
            arcHalf: SUB_FAMILY_OBJ_ARC_HALF,
          });
          for (let i = 0; i < subObjs.length; i++) {
            const op = positions[i] || { x: subPos.x, y: subPos.y, ring: 0 };
            const arrival = Math.min(0.96, FAMILY_ARRIVAL + 0.28 + op.ring * PER_RING_DELTA);
            const spurStart = FAMILY_ARRIVAL + 0.18;
            const spurProg = Math.max(
              0,
              Math.min(1, (visualProgress - spurStart) / (arrival - spurStart)),
            );
            const obj = subObjs[i];
            const objHue = actionHueFor(fam.hue, obj?.action);
            drawEdge(dynamicLayer, spurPath(subPos, op, SUB_FAMILY_R), spurProg, objHue, 1.2);
            const start = arrival - 0.04, end = arrival + 0.04;
            const vis = Math.max(0, Math.min(1, (visualProgress - start) / (end - start)));
            if (vis > 0.02) {
              const isSelectedObj =
                state.selected?.kind === "object" && state.selected.obj?.id === obj.id;
              drawObject(dynamicLayer, op, subPos, { ...fam, hue: objHue }, obj, vis, isSelectedObj);
            }
          }
        }
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
    const last = state.lastIntent || "idle";
    els.liveIntentKind.textContent = last;
    els.liveIntentPrompt.textContent = state.lastIntent
      ? "Cycle complete — next memory event or auto-cycle will redraw the graph."
      : "Run Search, Explain, or click a family node to drive the graph.";
    els.overlayPhase.textContent = "idle";
    els.overlayIntent.textContent = state.lastIntent || "awaiting request";
    els.overlayPrompt.textContent = state.lastIntent
      ? `Last cycle · ${last}`
      : "Run Search or Explain — the graph reacts to real backend traffic.";
    clear(els.familiesTouched);
    clear(els.objectsInContext);
  }
}

// Action-colored hue overrides. The family hue identifies WHICH part
// of memory is touched; the action tint tells the operator WHAT
// happened to it so a write burst, an archive flip, and a read pull
// look visually distinct. Reads keep the family hue so the graph
// doesn't strobe every time a context is fetched.
const _ACTION_HUE = {
  created: 150,    // bright green — new row landed
  upserted: 150,   // same as created — write that may overwrite
  pinned: 90,      // yellow-green — flag flip toward "more visible"
  archived: 25,    // red-orange — soft delete
  deleted: 15,     // red — hard delete
  rejected: 15,    // red — candidate killed
  unpinned: 50,    // amber — flag flip toward "less visible"
  restored: 130,   // green — un-archive
};

function actionHueFor(famHue, action) {
  const override = _ACTION_HUE[(action || "").toLowerCase()];
  return override === undefined ? famHue : override;
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

// Sub-family centre: same dome as the family centre but smaller, with
// the table label inside (Theories / Snapshots / Experiments / …).
// Only rendered when the active query splits an outer family into
// multiple SQLite-table buckets.
function drawSubFamily(layer, pos, fam, table, vis) {
  const r = SUB_FAMILY_R * Math.max(0.4, vis);
  const g = svg(
    "g",
    {
      class: "subfam-node",
      transform: `translate(${pos.x},${pos.y})`,
    },
    layer,
  );
  svg(
    "circle",
    {
      class: "subfam-aura",
      r: SUB_FAMILY_R + 8,
      fill: `oklch(0.85 0.17 ${fam.hue} / ${0.18 * vis})`,
      stroke: "none",
      filter: "url(#big-glow)",
    },
    g,
  );
  svg(
    "circle",
    {
      class: "subfam-base",
      r,
      fill: `oklch(0.22 0.05 ${fam.hue} / ${0.55 + 0.3 * vis})`,
      stroke: `oklch(0.6 0.16 ${fam.hue} / ${0.5 + 0.45 * vis})`,
      "stroke-width": 1 + 0.6 * vis,
    },
    g,
  );
  if (vis > 0.55) {
    const label = _subFamilyLabel(table);
    // Suppress the sub-family label when it would duplicate the parent
    // family label. Without this guard the agent_skills bubble inside
    // the Skills family renders as "Skills" inside "Skills", and
    // similarly for episodes/decisions/theories whose own table label
    // matches the family group name. The bubble itself stays so the
    // structural grouping of multiple sub-tables (e.g. Skills →
    // {agent_skills, capability_links, agent_playbooks}) remains
    // visible; only the redundant text is dropped.
    const famLabel = (fam.label || "").toLowerCase();
    if (label.toLowerCase() !== famLabel) {
      svg(
        "text",
        {
          class: "subfam-label",
          "text-anchor": "middle",
          y: 3,
          "font-size": "9.5",
          fill: `oklch(0.92 0.08 ${fam.hue})`,
        },
        g,
      ).textContent = clip(label, 14);
    }
  }
}

function drawObject(layer, pos, parent, fam, obj, vis, isHighlighted = false) {
  const r = OBJECT_R * vis;
  const haloR = OBJECT_R + 10 * vis;
  const g = svg("g", {
    class: `obj-node ${isHighlighted ? "is-highlight" : ""}`,
    transform: `translate(${pos.x},${pos.y})`,
  }, layer);
  g.addEventListener("click", (ev) => { ev.stopPropagation(); selectObject(obj); });
  if (isHighlighted) {
    svg("circle", {
      r: OBJECT_R + 14,
      fill: "none",
      stroke: `oklch(0.96 0.2 ${fam.hue})`,
      "stroke-width": 2,
      opacity: 0.85,
      filter: "url(#big-glow)",
    }, g);
  }
  svg("circle", {
    class: "obj-halo", r: haloR,
    fill: `oklch(0.85 0.18 ${fam.hue} / ${(isHighlighted ? 0.45 : 0.25) * vis})`,
    filter: "url(#big-glow)",
  }, g);
  svg("circle", {
    class: "obj-core",
    r, fill: `oklch(0.24 0.05 ${fam.hue})`,
    stroke: `oklch(0.92 0.18 ${fam.hue} / ${vis})`,
    "stroke-width": isHighlighted ? 2 : 1.4,
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
    // CSS handles background, border, text colour, padding, shape;
    // the only per-family knob is the hue, exposed as a custom
    // property so the rule can derive every colour from one number.
    pill.style.setProperty("--link-pill-hue", String(fam.hue));
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

function pushSelection(next) {
  // Keep a back-stack so the × on a deeper inspector pops back to the
  // previous selection (object → family → null).
  if (state.selected && !sameSelection(state.selected, next)) {
    state.inspectorHistory.push(state.selected);
  }
  state.selected = next;
  renderInspector();
}
function sameSelection(a, b) {
  if (!a || !b) return false;
  if (a.kind !== b.kind) return false;
  if (a.famId !== b.famId) return false;
  if (a.kind === "object") return (a.obj?.id || "") === (b.obj?.id || "");
  return true;
}
function closeInspector() {
  if (state.inspectorHistory.length) {
    state.selected = state.inspectorHistory.pop();
  } else {
    state.selected = null;
  }
  renderInspector();
}

async function selectFamily(fid) {
  // Click = highlight only. We do NOT redraw the graph or run a new
  // animation cycle — whatever was drawn from the last Search/Explain
  // stays visible. The user reads the family's full list in the rail
  // and drills into specific objects from there.
  pushSelection({ kind: "family", famId: fid });
  paintFrame();
  let detail = state.detailCache.get(fid);
  if (!detail) {
    try {
      detail = await fetchFamilyDetail(fid);
      state.detailCache.set(fid, detail);
    } catch (err) {
      detail = { objects: [], error: String(err) };
    }
  }
  if (state.selected?.kind === "family" && state.selected.famId === fid) {
    state.selected.detail = detail;
    renderInspector();
    paintFrame();
  }
}

// Called from the graph_delta handler when a family receives a new
// row. If the inspector is currently open on that family, re-fetch
// the detail list and re-render so the new row appears without a
// page reload. The cache delete in the caller ensures the next
// passive ``selectFamily`` call also fetches fresh.
async function refreshOpenFamilyInspector(fid) {
  if (state.selected?.kind !== "family" || state.selected.famId !== fid) {
    return;
  }
  try {
    const detail = await fetchFamilyDetail(fid);
    state.detailCache.set(fid, detail);
    if (state.selected?.kind === "family" && state.selected.famId === fid) {
      state.selected.detail = detail;
      renderInspector();
    }
  } catch (_err) {
    /* leave the stale list rather than blanking the inspector */
  }
}

function selectObject(obj) {
  // Click = highlight + open body in rail. No new edges drawn.
  pushSelection({ kind: "object", famId: obj.famId, obj });
  paintFrame();
}

// Resolve an item's canonical id from a list-endpoint row regardless
// of which `<kind>_id` field the route uses. Different list endpoints
// return different field names: /memory/list_decisions →
// `decision_id`, /memory/list_agent_capabilities returns
// `role_id` / `skill_id` / `playbook_id`, etc. Without a tolerant
// resolver, the UI's `adapt` reads `r[`${kind}_id`]` where the
// requested kind doesn't always match the field name (the bug
// was specifically Skills/Roles where `kind="roles"` looked for
// `r.roles_id` while the route returns `r.role_id`). The agent's
// own canonical id stored in the table is always one of these
// fields, so we try them all in priority order.
const _ID_FIELDS = [
  "id",
  "role_id",
  "skill_id",
  "playbook_id",
  "theory_id",
  "decision_id",
  "snapshot_id",
  "experiment_id",
  "result_id",
  "insight_id",
  "concept_id",
  "instruction_id",
  "evidence_id",
  "link_id",
  "candidate_id",
  "feedback_id",
  "task_id",
  "event_id",
];

function _resolveItemId(r) {
  if (!r) return undefined;
  for (const field of _ID_FIELDS) {
    const v = r[field];
    if (v != null && v !== "") return v;
  }
  return undefined;
}

async function fetchFamilyDetail(fid) {
  const ws = state.workspace;
  const adapt = (rows, table) =>
    rows.map((r) => ({
      id: _resolveItemId(r),
      label: r.title || r.name || r.label || r.summary || _resolveItemId(r),
      famId: fid,
      table,
      raw: r,
    }));

  if (fid === "decisions") {
    const data = await postJson("/memory/list_decisions", { workspace_id: ws, query: "", include_superseded: false, limit: 12 });
    return { objects: adapt(data.decisions || data.items || [], "decisions") };
  }
  if (fid === "research") {
    const data = await postJson("/memory/list_theories", { workspace_id: ws, query: "", include_evidence: true, limit: 12 });
    // /memory/list_theories returns wrapped rows
    // {"theories": [{"theory": {...}, "evidence": [...]}]} so the
    // theory body is one level deeper than `adapt` expects. Unwrap
    // before adapting; otherwise label/id resolution falls back to
    // empty strings and the inspector renders rows with no title.
    const rawRows = (data.theories || data.items || []).map((item) => item?.theory || item);
    return { objects: adapt(rawRows, "theories") };
  }
  if (fid === "instructions") {
    const data = await postJson("/memory/list_behavior_instructions", { workspace_id: ws, query: "", limit: 12 });
    return { objects: adapt(data.instructions || data.items || [], "behavior_instructions") };
  }
  if (fid === "roles" || fid === "skills") {
    const data = await postJson("/memory/list_agent_capabilities", { workspace_id: ws, query: "", limit: 18 });
    let rows = [];
    if (fid === "roles") rows = (data.roles || []).map((r) => ({ ...r, _kind: "role" }));
    if (fid === "skills") {
      rows = [
        ...(data.skills || []).map((r) => ({ ...r, _kind: "skill" })),
        ...(data.playbooks || []).map((r) => ({ ...r, _kind: "playbook" })),
      ];
    }
    return {
      objects: rows.map((r) => ({
        id: _resolveItemId(r),
        label: r.name || r.title || r.label || _resolveItemId(r),
        famId: fid,
        table: r._kind === "playbook" ? "agent_playbooks" : `agent_${r._kind}s`,
        raw: r,
      })),
    };
  }
  // episodes / tasks / feedback / candidates — fall back to /memory/ui/state.recent
  // First try the cached recent list. If the cached snapshot doesn't
  // contain rows for this family (the recent slice is dominated by
  // chatty tables like chunks/episodes/capability_links and a
  // smaller table like task_state or memory_usage_feedback gets
  // pushed out), fall back to a targeted /memory/ui/state fetch with
  // a higher recent_limit so we definitely get some rows.
  const tables = FAMILY_BY_ID[fid].tables;
  let recent = (state.memory?.recent || []).filter((r) => tables.includes(r.table));
  if (recent.length === 0) {
    try {
      const headers = { ...buildHeaders() };
      delete headers["Content-Type"];
      const r = await fetch(
        `/memory/ui/state?workspace_id=${encodeURIComponent(ws)}&recent_limit=80`,
        { headers },
      );
      if (r.ok) {
        const fresh = await r.json();
        recent = (fresh?.recent || []).filter((r2) => tables.includes(r2.table));
      }
    } catch (_err) {
      /* fall through with empty list */
    }
  }
  recent = recent.slice(0, 12);
  return {
    objects: recent.map((r) => ({
      id: r.id,
      label: r.short_label || r.label || r.id,
      famId: fid,
      table: r.table,
      raw: r,
    })),
  };
}

// ---- inspector card render -------------------------------------------------

function makeKv(label, value, opts = {}) {
  const wrap = document.createElement("div");
  wrap.className = "kv";
  const k = document.createElement("div");
  k.className = "kv-key";
  k.textContent = label;
  const v = document.createElement("div");
  v.className = "kv-val" + (opts.text ? " kv-val-text" : "");
  v.textContent = value == null || value === "" ? "—" : String(value);
  wrap.append(k, v);
  return wrap;
}

function makeToolbar(card) {
  const bar = document.createElement("div");
  bar.className = "inspector-toolbar";
  if (state.inspectorHistory.length) {
    const back = document.createElement("button");
    back.className = "back-btn";
    back.type = "button";
    back.title = "Back";
    back.textContent = "←";
    back.addEventListener("click", () => closeInspector());
    bar.appendChild(back);
  }
  const close = document.createElement("button");
  close.className = "x-btn";
  close.type = "button";
  close.title = "Close";
  close.textContent = "×";
  close.addEventListener("click", () => {
    // × always closes fully. Use ← (or click outside) for back.
    state.inspectorHistory = [];
    state.selected = null;
    renderInspector();
  });
  bar.appendChild(close);
  card.appendChild(bar);
}

function renderInspector() {
  const card = els.inspectorCard;
  card.hidden = !state.selected;
  // The "Live · intent" panel and the inspector occupy the same slot
  // in the rail — show whichever one matches the current state.
  if (els.liveIntent) els.liveIntent.hidden = !!state.selected;
  if (!state.selected) {
    state.inspectorHistory = [];
    return;
  }
  clear(card);
  card.style.position = "relative";
  makeToolbar(card);

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
    } else if (!detail) {
      const e = document.createElement("div"); e.className = "inspector-blurb"; e.textContent = "Loading…"; list.appendChild(e);
    } else {
      // The family detail list comes from a topic-ranked API
      // (/memory/list_decisions, /memory/list_theories, …) with a
      // fixed page size. The "active query" — what the most recent
      // get_context / search rendered into the live graph — picks
      // items by query relevance. Those two sets often disagree:
      // the spoke that's currently lit on the graph may rank low in
      // the unfiltered list and end up missing from the inspector
      // entirely. Visual symptom: the user clicks the family bubble,
      // the inspector lists 7 unrelated decisions, and the
      // highlighted "Lower hardEarlySlPct…" decision isn't shown.
      //
      // Merge: active-for-this-family items go FIRST in their
      // original trace order; then the family detail rows follow,
      // de-duplicated by id. This keeps "used now" pinned to the top
      // AND surfaces every active item even when the family API
      // didn't return it.
      const famId = state.selected.famId;
      const activeForFamily = (state.activeQuery?.objects || []).filter(
        (o) => o.famId === famId,
      );
      const usedIds = new Set(activeForFamily.map((o) => o.id).filter(Boolean));
      const merged = [];
      const seen = new Set();
      for (const o of activeForFamily) {
        merged.push(o);
        if (o.id) seen.add(o.id);
      }
      for (const r of rows) {
        if (r.id && seen.has(r.id)) continue;
        merged.push(r);
        if (r.id) seen.add(r.id);
      }
      if (!merged.length) {
        const e = document.createElement("div");
        e.className = "inspector-blurb";
        e.textContent = "No objects yet in this family.";
        list.appendChild(e);
      } else {
        for (const r of merged) {
          const isUsed = r.id && usedIds.has(r.id);
          // is_archived can live either on the row body (chunk hits
          // from /memory/search) or in the row's status (decisions
          // mark themselves "superseded", theories mark themselves
          // "archived", instructions/roles flip to active=false). We
          // surface a single visual: a small "archived" pill so the
          // agent sees at a glance which list rows live in the
          // archive.
          const status = String(r.raw?.status || "").toLowerCase();
          const isArchived =
            Boolean(r.raw?.is_archived) ||
            Boolean(r.is_archived) ||
            status === "archived" ||
            status === "superseded" ||
            status === "rejected" ||
            (r.raw?.active === false);
          const btn = document.createElement("button");
          btn.className =
            "inspector-row" +
            (isUsed ? " is-used-now" : "") +
            (isArchived ? " is-archived" : "");
          btn.type = "button";
          const tt = document.createElement("span");
          tt.className = "row-title";
          tt.textContent = r.label;
          if (isUsed) {
            const used = document.createElement("span");
            used.className = "row-used-now";
            used.textContent = "used now";
            tt.appendChild(used);
          }
          if (isArchived) {
            const archived = document.createElement("span");
            archived.className = "row-archived";
            archived.textContent = "archived";
            tt.appendChild(archived);
          }
          const tp = document.createElement("span");
          tp.className = "row-type";
          tp.textContent = (r.table || famId).replace(/_/g, " ");
          btn.append(tt, tp);
          btn.addEventListener("click", () => selectObject(r));
          list.appendChild(btn);
        }
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
    grid.append(
      makeKv("id", obj.id || raw.id),
      makeKv("table", obj.table, { text: true }),
      makeKv("updated", fmtTime(raw.updated_at || raw.created_at), { text: true }),
      makeKv("label", raw.short_label || raw.label, { text: true }),
    );
    card.appendChild(grid);

    // Action toolbar: pin / archive flips that round-trip to the
    // service via /memory/pin and /memory/archive. The toolbar only
    // renders buttons the kind actually supports — pinning is
    // limited to decision / behavior_instruction / core_memory;
    // roles / skills / playbooks intentionally stay un-pinned.
    const actions = renderObjectActions(obj, raw);
    if (actions) card.appendChild(actions);

    // Body — what content matters depends on the table.
    const body = renderObjectBody(obj.table, raw);
    if (body) card.appendChild(body);
  }
}

// table → ({ pin_kind?, archive_kind? }). Tables not in this map get
// no actions. Archive vs pin support follows the route contracts in
// CLAUDE.md (memory_archive supports more kinds; memory_pin is the
// short list).
const _ACTION_KINDS = {
  decisions:              { pin: "decision",            archive: "decision" },
  theories:               {                              archive: "theory" },
  research_insights:      {                              archive: "insight" },
  agent_roles:            {                              archive: "role" },
  agent_skills:           {                              archive: "skill" },
  agent_playbooks:        {                              archive: "playbook" },
  behavior_instructions:  { pin: "behavior_instruction", archive: "behavior_instruction" },
  core_memory:            { pin: "core_memory" },
  episodes:               {                              archive: "episode" },
  chunks:                 {                              archive: "chunk" },
  files:                  {                              archive: "file" },
  memory_candidates:      {                              archive: "candidate" },
};

function _isObjectArchived(table, raw) {
  const status = String(raw?.status || "").toLowerCase();
  if (raw?.is_archived) return true;
  if (status === "archived" || status === "superseded" || status === "rejected") return true;
  if (raw?.active === false) return true;
  return false;
}

function renderObjectActions(obj, raw) {
  const kinds = _ACTION_KINDS[obj.table];
  if (!kinds) return null;
  const id = obj.id || raw.id;
  if (!id) return null;
  const bar = document.createElement("div");
  bar.className = "inspector-actions";

  if (kinds.pin) {
    const pinned = Boolean(raw.pinned);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "action-btn" + (pinned ? " is-on" : "");
    btn.textContent = pinned ? "Unpin" : "Pin";
    btn.title = pinned
      ? "Remove from always-included context"
      : "Always include in active context";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await postJson("/memory/pin", {
          workspace_id: state.workspace,
          kind: kinds.pin,
          id,
          pinned: !pinned,
        });
        if (raw) raw.pinned = !pinned;
        renderInspector();
        fetchState({ manual: true });
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "Pin error";
      }
    });
    bar.appendChild(btn);
  }

  if (kinds.archive) {
    const archived = _isObjectArchived(obj.table, raw);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "action-btn" + (archived ? " is-on" : "");
    btn.textContent = archived ? "Restore" : "Archive";
    btn.title = archived
      ? "Restore so it appears in get_context again"
      : "Hide from get_context (still searchable)";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await postJson("/memory/archive", {
          workspace_id: state.workspace,
          kind: kinds.archive,
          id,
          archive: !archived,
        });
        renderInspector();
        fetchState({ manual: true });
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "Archive error";
      }
    });
    bar.appendChild(btn);
  }

  return bar.children.length ? bar : null;
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
  // Build a quick id → label lookup from /memory/ui/state.recent so
  // graph_delta events can render a meaningful node label even when
  // the trace itself only carries the raw object_id.
  state.recentLabels = new Map();
  for (const row of memory.recent || []) {
    const lbl = row.short_label || row.label || "";
    if (row.id && lbl) state.recentLabels.set(row.id, lbl);
  }
  // The polled counts are now authoritative — drop optimistic deltas
  // that were applied on top of the previous snapshot.
  state.countDeltas = new Map();
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
  // Backend uses `type` (request_started / stage_started / stage_done /
  // graph_delta / request_done / request_failed). Older code in this file
  // still falls back to `kind` for safety, but the SSE stream is the
  // canonical wire format.
  const evType = ev.type || ev.kind || "";
  const evId = ev.event_id || ev.id || "";
  if (evId && state.eventIds.has(evId)) return;
  // Defence in depth: backend already filters /memory/ui/events by
  // workspace, but if a stale SSE connection from a previous workspace
  // somehow delivers a frame after the user switched, skip it client-side.
  const evWs = ev.workspace_id || ev.workspace || "";
  if (evWs && state.workspace && evWs !== state.workspace) return;
  if (evId) state.eventIds.add(evId);

  // Group the event into a per-operation trail entry keyed by request_id.
  // One ingest fires ~10 SSE events in <100 ms; they all collapse into
  // one trail row instead of flooding the feed.
  pushTrailEvent(ev, evType);
  // Legacy raw count for the header chip ("X events").
  state.events.unshift({ ts: ev.created_at || "" });
  state.events = state.events.slice(0, 60);
  renderFeed();

  // Family additive light (kept for non-queued events too — gives
  // background activity a soft pulse independent of the cycle queue).
  // Compute "is this a live event or SSE replay" once so we can apply
  // it to both the optimistic count update and (later) the queue.
  const evTimeMsForLight = Date.parse(ev.created_at || "") || 0;
  const isLiveEvent = !evTimeMsForLight || (Date.now() - evTimeMsForLight) <= REPLAY_LIVE_WINDOW_MS;
  if (evType === "graph_delta") {
    const fid = familyForEvent(ev.counts);
    if (fid && isLiveEvent) {
      // Family additive light is a LIVE-only signal: it pulses a family
      // centre when something just happened in the graph. Replay frames
      // (events older than REPLAY_LIVE_WINDOW_MS, e.g. when the user
      // switches workspace and the server replays its recent snapshot)
      // do NOT count as "just happened" — lighting them up makes the
      // observatory show a phantom flash for old activity that then
      // fades to idle, which the user reads as "the switch broke
      // something." Replayed activity is rendered through the trail
      // (renderFeed) and the synthesized last-request animation; no
      // need to also flash the family centre.
      //
      // We ALSO skip the pulse when the event has a request_id —
      // those events always produce their own animation cycle (write
      // events flow through state.requestBuffer → enqueueQuery; local
      // reads invoke runQueryAnimation directly from the search /
      // explain handlers; external reads also flow through the
      // buffer). Without this guard, queued events would light up
      // their family bubble immediately while the cycle that actually
      // draws spokes for that family was still waiting in the queue
      // behind the active cycle — the user saw "orb lit but spokes
      // never drew" during bursts. Keep the pulse only for bare
      // graph_delta events with no request_id, where the pulse is the
      // user's only visual feedback.
      if (!ev.request_id) {
        state.liveLight.set(fid, performance.now() + 5000 / Math.max(0.3, state.tweaks.speed));
      }
      // Optimistic count update so the family centre counter ticks the
      // moment a row is created / deleted, instead of waiting for the
      // next /memory/ui/state poll. fetchState resets these deltas
      // when the authoritative counts come back. Only LIVE: SSE replay
      // events represent creations already in the polled counts;
      // bumping countDeltas for them would inflate the total.
      const action = String(ev.counts?.action || "").toLowerCase();
      if (action === "created") {
        state.countDeltas.set(fid, (state.countDeltas.get(fid) || 0) + 1);
      } else if (action === "deleted") {
        state.countDeltas.set(fid, (state.countDeltas.get(fid) || 0) - 1);
      }
      // Invalidate the family-detail cache for this family so the
      // next inspector render fetches fresh rows. Without this, an
      // open Decisions/Theories/etc. inspector keeps showing the
      // pre-write list even though the graph and counts already
      // reflect the new row. When the inspector is currently open
      // on this family, re-fetch immediately and re-render.
      state.detailCache.delete(fid);
      refreshOpenFamilyInspector(fid);
    }
  }

  // ---- request_id buffering -------------------------------------------------
  //
  // Every memory operation produces a sequence of events sharing one
  // request_id: request_started → stage_* → graph_delta(s) → request_done
  // (or request_failed). The observatory plays ONE animation per
  // operation, not one per event — otherwise an ingest that touches
  // five tables would flicker five tiny graphs in a row instead of
  // showing the actual operation.
  //
  // We skip ONLY OUR OWN read events. Search/Explain triggered from this
  // UI's buttons play their animation directly via runQueryAnimation
  // with the full HTTP response, so the SSE re-play would just queue a
  // duplicate cycle behind it. Reads triggered by ANY other client
  // (another agent, MCP server, curl, second browser tab) come through
  // SSE and absolutely SHOULD animate — that's the whole point of a
  // live observatory. We tell them apart via a short-lived endpoint +
  // snippet fingerprint that searchMemory / explainContext set right
  // before they fetch.
  const requestId = ev.request_id || "";
  if (!requestId) return;
  const operation = String(ev.operation || ev.endpoint || "").toLowerCase();
  const isReadEndpoint =
    operation.includes("search") ||
    operation.includes("get_context") ||
    operation.includes("explain_context");
  if (isReadEndpoint && isLocalRead(ev)) return;

  // SSE replay guard: when the client subscribes to /memory/ui/events,
  // the server replays its recent snapshot (up to 80 events). Without
  // this guard, every past request_id would land in the queue and the
  // observatory would spend the next minute animating yesterday's
  // ingests. Old events still land in the trail (so the user sees
  // recent history), but they do NOT get queued for animation. Anything
  // within REPLAY_LIVE_WINDOW_MS counts as "live" and animates normally.
  //
  // Replay events still feed the "last request" preview: after the
  // replay burst settles, scheduleLastRequestPreview() picks the most
  // recent trail group and plays it as a forward-only animation that
  // holds at the end. That gives the user a stable, visible
  // representation of "what just happened in this workspace" instead
  // of the empty IDLE graph they used to see right after switching.
  if (!isLiveEvent) {
    if (evType === "graph_delta") scheduleLastRequestPreview();
    return;
  }

  if (evType === "request_started") {
    state.requestBuffer.set(requestId, {
      requestId,
      endpoint: ev.endpoint || "",
      operation: ev.operation || "",
      label: ev.label || "",
      snippet: ev.snippet || "",
      families: new Set(),
      objects: new Map(), // key = `${famId}:${objectId}`
      lastEventAt: performance.now(),
      flushed: false,
    });
    return;
  }

  // For graph_delta / stage_done events, ensure a buffer exists even if
  // we missed request_started (SSE backfill races, late subscribe, etc.).
  let entry = state.requestBuffer.get(requestId);
  if (!entry && (evType === "graph_delta" || evType === "stage_done")) {
    entry = {
      requestId,
      endpoint: ev.endpoint || "",
      operation: ev.operation || "",
      label: ev.label || "",
      snippet: ev.snippet || "",
      families: new Set(),
      objects: new Map(),
      lastEventAt: performance.now(),
      flushed: false,
    };
    state.requestBuffer.set(requestId, entry);
  }
  if (!entry) return;

  entry.lastEventAt = performance.now();

  if (evType === "graph_delta") {
    const counts = ev.counts || {};
    const fid = familyForEvent(counts);
    if (fid) {
      entry.families.add(fid);
      const objectId = counts.object_id || counts.target_id || evId || "";
      const key = `${fid}:${objectId || counts.object_type || ""}:${entry.objects.size}`;
      // Prefer a human-readable label over the raw id. Order:
      //   1. counts.label / counts.short_label — per-object label the
      //      route attached for this exact graph_delta. Highest signal.
      //   2. recent.short_label cache from /memory/ui/state — the
      //      object's short label from the polled state snapshot.
      //   3. ev.label when it looks per-object (e.g. context's
      //      _trace_used_context_objects sets it to item.label). This
      //      is the second-best per-object source.
      //   4. The request snippet (entry.snippet) — only when nothing
      //      object-specific is available. This is the LAST resort
      //      because the snippet is the user's query text and may be
      //      corrupted by upstream encoding bugs in any client; it's
      //      also identical for every object in a given request.
      //   5. The graph_delta trace label as a final fallback.
      //   6. The raw object_id if everything else is empty.
      const recentLabel = state.recentLabels?.get(objectId);
      const traceLabel = ev.label || "";
      // Heuristic: the trace.label is "per-object" when the route
      // explicitly set it to the row's own label, not a generic
      // "{Action} {table}" string. We treat it as per-object whenever
      // it doesn't end with the verbs the trace decorates request_done
      // events with.
      const looksGenericTraceLabel = /\b(written|persisted|completed|ready|created|updated|failed)\b/i.test(traceLabel);
      const rawLabel =
        counts.label
        || counts.short_label
        || recentLabel
        || (traceLabel && !looksGenericTraceLabel ? traceLabel : "")
        || (entry.snippet && entry.snippet.length > 4 ? entry.snippet : "")
        || traceLabel
        || objectId
        || `${counts.action || "active"} ${fid}`;
      entry.objects.set(key, {
        id: objectId || key,
        label: clip(String(rawLabel), 24),
        famId: fid,
        table: counts.table || counts.object_type || fid,
        action: counts.action || "",
        raw: ev,
      });
    }
    return;
  }

  if (evType === "request_done" || evType === "request_failed") {
    flushRequest(requestId);
    // The operation finished — schedule a state refresh so the
    // authoritative counts replace any optimistic deltas. Debounced
    // so a burst of writes (e.g. workspace ingest) doesn't hammer the
    // /memory/ui/state endpoint.
    scheduleStateRefresh();
    return;
  }
}

let _refreshTimer = null;
function scheduleStateRefresh(delayMs = 600) {
  if (_refreshTimer) return;
  _refreshTimer = setTimeout(() => {
    _refreshTimer = null;
    // No `manual: true` here — if the user paused the observatory we
    // honour that and skip the refresh.
    fetchState().catch(() => {});
  }, delayMs);
}

// Replay-snapshot debounce: SSE replays the recent server snapshot in a
// burst, then goes quiet. We wait one quiet window after the last
// replay graph_delta before deciding what the "last request" is, so we
// pick the truly newest trail group instead of one mid-replay. The
// debounce also means a flurry of replay events for the same request
// only triggers ONE animation, not one per graph_delta.
let _lastRequestPreviewTimer = null;
const LAST_REQUEST_PREVIEW_QUIET_MS = 250;

function scheduleLastRequestPreview() {
  if (_lastRequestPreviewTimer) clearTimeout(_lastRequestPreviewTimer);
  _lastRequestPreviewTimer = setTimeout(() => {
    _lastRequestPreviewTimer = null;
    playLastRequestFromTrail();
  }, LAST_REQUEST_PREVIEW_QUIET_MS);
}

function playLastRequestFromTrail() {
  // If a live cycle is already running or queued, the user is watching
  // real activity — don't yank it back to a replay snapshot.
  if (state.activeQuery || state.queue.length) return;

  // Most recent first (trail groups are unshifted on creation).
  // Skip groups with no graph_delta payload — they have nothing to
  // draw on the family graph.
  let group = null;
  for (const g of state.trailGroups) {
    if (g && g.touchedFamilies && g.touchedFamilies.size && g.deltas.length) {
      group = g;
      break;
    }
  }
  if (!group) return;

  const families = Array.from(group.touchedFamilies);
  // Reuse the trail group's graph_delta records. They already carry a
  // family id and a per-object label, which is exactly what the
  // animation expects in `objects[]`.
  const seen = new Map();
  for (const d of group.deltas) {
    if (!d.familyId) continue;
    const key = `${d.familyId}:${d.objectId || d.label || seen.size}`;
    if (seen.has(key)) continue;
    seen.set(key, {
      id: d.objectId || key,
      label: clip(String(d.label || d.objectId || d.action || "active"), 24),
      famId: d.familyId,
      table: d.objectType || d.familyId,
      raw: d,
    });
  }
  // Cap PER FAMILY so every touched family gets at least one
  // representative node — otherwise dense families starve the rest.
  const objects = _capObjectsPerFamily(
    Array.from(seen.values()),
    families,
    objectsCap(),
  );
  const intent = group.intent || "memory";
  const prompt =
    (group.prompt && group.prompt.replace(/\s+(accepted|completed|failed)$/i, "")) ||
    group.endpoint ||
    intent;

  // Forward animation that holds at the end. tick() already keeps the
  // last cycle frozen at hold whenever the queue is empty, so simply
  // starting the cycle gives us "draw outward, then stay drawn until
  // a new operation arrives" — exactly the behaviour the user asked
  // for after a workspace switch.
  runQueryAnimation({
    intent,
    prompt,
    families,
    objects,
    source: "sse-replay-preview",
  });
}

// Distribute the global objectsCap across touched families so that every
// family that fired a graph_delta gets at least one representative node
// in the rendered animation. Without this, the cap fills up with the
// FIRST family in trace order (instructions, currently — they're
// rendered first by `used_context_objects`) and any later family is
// "touched" without any node attached to it. Visual symptom: the family
// bubble lights up but no spokes radiate from it.
//
// Behaviour:
//   1. Walk the family list in order; give each family up to
//      `ceil(cap / familyCount)` slots, while preserving insertion
//      order inside each family.
//   2. If the per-family quota leaves headroom, backfill from the tail
//      of the original list so dense families don't lose more than
//      necessary.
function _capObjectsPerFamily(allObjects, families, totalCap) {
  if (totalCap <= 0 || allObjects.length === 0) return [];
  const familyList = (families && families.length)
    ? Array.from(new Set(families))
    : Array.from(new Set(allObjects.map((o) => o.famId).filter(Boolean)));
  if (familyList.length <= 1) return allObjects.slice(0, totalCap);
  const perFamily = Math.max(1, Math.ceil(totalCap / familyList.length));
  const taken = new Set();
  const usedByFamily = new Map();
  const out = [];
  for (let i = 0; i < allObjects.length && out.length < totalCap; i++) {
    const obj = allObjects[i];
    const used = usedByFamily.get(obj.famId) || 0;
    if (used >= perFamily) continue;
    out.push(obj);
    taken.add(i);
    usedByFamily.set(obj.famId, used + 1);
  }
  // Backfill any slack with the leftovers in original order.
  for (let i = 0; i < allObjects.length && out.length < totalCap; i++) {
    if (taken.has(i)) continue;
    out.push(allObjects[i]);
  }
  return out;
}

function flushRequest(requestId) {
  const entry = state.requestBuffer.get(requestId);
  if (!entry || entry.flushed) return;
  entry.flushed = true;
  state.requestBuffer.delete(requestId);

  // Without any touched family the cycle has nothing to draw — skip.
  if (!entry.families.size) return;

  const families = Array.from(entry.families);
  const objects = _capObjectsPerFamily(
    Array.from(entry.objects.values()),
    families,
    objectsCap(),
  );
  const intent = entry.operation || "memory";
  const prompt =
    (entry.label && entry.label.replace(/\s+(accepted|completed|failed)$/i, "")) ||
    entry.endpoint ||
    intent;

  enqueueQuery({
    intent,
    prompt,
    families,
    objects,
    source: "sse-request",
  });
}

// Periodically flush request buffers that went silent. A real operation
// always emits request_done, but a backend crash, dropped SSE frame, or a
// long-tail ingestion sub-task can leave entries in the buffer; without
// this sweep they'd never reach the queue.
setInterval(() => {
  if (state.requestBuffer.size === 0) return;
  const now = performance.now();
  for (const [rid, entry] of state.requestBuffer) {
    if (now - entry.lastEventAt > REQUEST_FLUSH_AFTER_MS) flushRequest(rid);
  }
}, 500);

function trailKindOf(ev) {
  const t = ev.type || ev.kind || "";
  if (t === "graph_delta") return "pick";
  if (t === "stage_done" || t === "stage_started" || t === "stage") return "route";
  if (t === "request_started") return "in";
  if (t === "request_done") return "out";
  if (t === "request_failed") return "warn";
  if (ev.endpoint && ev.endpoint.includes("ingest")) return "in";
  if (ev.endpoint && ev.endpoint.includes("get_context")) return "out";
  if (ev.severity === "warn" || ev.severity === "warning") return "warn";
  return "route";
}

const TRAIL_GROUP_CAP = 30;

function _ensureTrailGroup(rid, ev) {
  let g = state.trailGroupsById.get(rid);
  if (g) return g;
  g = {
    id: rid,
    intent: ev.operation || "memory",
    endpoint: ev.endpoint || "",
    prompt: "",
    status: "running",
    startedAt: ev.created_at || new Date().toISOString(),
    completedAt: null,
    durationMs: null,
    stages: [],
    deltas: [],
    touchedFamilies: new Set(),
    lastTouchedAt: performance.now(),
  };
  // newest at the front
  state.trailGroups.unshift(g);
  state.trailGroupsById.set(rid, g);
  // cap by dropping the oldest tail (and unmapping its id)
  while (state.trailGroups.length > TRAIL_GROUP_CAP) {
    const dropped = state.trailGroups.pop();
    if (dropped) state.trailGroupsById.delete(dropped.id);
  }
  return g;
}

function pushTrailEvent(ev, evType) {
  const rid = ev.request_id || ev.event_id || ev.id || "no_request_id";
  const g = _ensureTrailGroup(rid, ev);
  g.lastTouchedAt = performance.now();

  // Keep the most informative metadata.
  if (!g.endpoint && ev.endpoint) g.endpoint = ev.endpoint;
  if (!g.intent || g.intent === "memory") g.intent = ev.operation || g.intent;

  switch (evType) {
    case "request_started":
      g.startedAt = ev.created_at || g.startedAt;
      // The snippet at request_started is the user-facing content
      // (query / decision title / episode raw_text). It's the best
      // anchor for the group's prompt label.
      if (ev.snippet) g.prompt = ev.snippet;
      else if (ev.label) g.prompt = ev.label;
      g.status = "running";
      break;
    case "stage_started":
    case "stage_done":
      g.stages.push({
        stage: ev.stage || "",
        label: ev.label || "",
        status: ev.status || "ok",
        durationMs: ev.duration_ms ?? null,
        counts: ev.counts || {},
        t: ev.created_at || "",
        type: evType,
      });
      // Cap stage list per group so a chatty op can't grow without bound.
      if (g.stages.length > 60) g.stages.shift();
      break;
    case "graph_delta": {
      const counts = ev.counts || {};
      const fid = familyForEvent(counts);
      if (fid) g.touchedFamilies.add(fid);
      g.deltas.push({
        objectType: counts.object_type || "",
        objectId: counts.object_id || counts.target_id || "",
        action: counts.action || "",
        label: counts.label || ev.label || "",
        familyId: fid || "",
        t: ev.created_at || "",
      });
      if (g.deltas.length > 60) g.deltas.shift();
      break;
    }
    case "request_done":
    case "request_failed":
      g.completedAt = ev.created_at || g.completedAt;
      g.durationMs = ev.duration_ms ?? g.durationMs;
      g.status = evType === "request_done" ? "ok" : "error";
      break;
    default:
      // unknown event type — just record it as a stage
      g.stages.push({
        stage: ev.stage || "",
        label: ev.label || evType,
        status: ev.status || "ok",
        durationMs: ev.duration_ms ?? null,
        counts: ev.counts || {},
        t: ev.created_at || "",
        type: evType || "memory",
      });
  }
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
// Persist which group ids the user expanded across re-renders.
const _expandedTrailGroups = new Set();

function _trailKindForGroup(g) {
  if (g.status === "error") return "warn";
  if (g.endpoint && g.endpoint.includes("ingest")) return "in";
  if (g.endpoint && (g.endpoint.includes("get_context") || g.endpoint.includes("search") || g.endpoint.includes("explain"))) return "out";
  if (g.deltas.length) return "pick";
  return "route";
}

function renderFeed() {
  clear(els.lifeFeed);
  const groups = state.trailGroups.slice(0, 30);
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i];
    const row = document.createElement("div");
    const fresh = i === 0 && g.status === "running" ? " trail-fresh" : "";
    const kind = _trailKindForGroup(g);
    const expanded = _expandedTrailGroups.has(g.id);
    row.className = `trail-row trail-${kind} trail-group${expanded ? " is-expanded" : ""}${fresh}`;
    row.dataset.requestId = g.id;

    // ---- header line: time · intent · prompt · status badge ----
    const header = document.createElement("button");
    header.type = "button";
    header.className = "trail-group-head";
    const t = document.createElement("span");
    t.className = "trail-time";
    t.textContent = fmtTime(g.startedAt);
    const intentEl = document.createElement("span");
    intentEl.className = "trail-intent";
    intentEl.textContent = g.intent || "memory";
    const prompt = document.createElement("span");
    prompt.className = "trail-text";
    const promptText = clip(g.prompt || g.endpoint || "", 80);
    prompt.textContent = promptText || g.endpoint || "(no prompt)";
    const stats = document.createElement("span");
    stats.className = "trail-stats";
    const fams = g.touchedFamilies.size;
    const stageCount = g.stages.length;
    const deltaCount = g.deltas.length;
    const dur = g.durationMs != null ? `${g.durationMs}ms` : (g.status === "running" ? "…" : "");
    stats.textContent = [
      fams ? `${fams}f` : "",
      deltaCount ? `${deltaCount}o` : "",
      stageCount ? `${stageCount}s` : "",
      dur,
    ].filter(Boolean).join(" · ");
    const statusEl = document.createElement("span");
    statusEl.className = `trail-status trail-status-${g.status}`;
    statusEl.textContent = g.status === "running" ? "···" : g.status === "ok" ? "✓" : "✗";

    header.append(t, intentEl, prompt, stats, statusEl);
    header.addEventListener("click", () => {
      if (_expandedTrailGroups.has(g.id)) _expandedTrailGroups.delete(g.id);
      else _expandedTrailGroups.add(g.id);
      renderFeed();
    });
    row.appendChild(header);

    // ---- expandable detail: stages + deltas ----
    if (expanded) {
      const detail = document.createElement("div");
      detail.className = "trail-group-detail";
      // Stages first (ordered)
      for (const st of g.stages) {
        const line = document.createElement("div");
        line.className = `trail-substage trail-substage-${st.type}`;
        const head = `${st.type === "stage_started" ? "→" : st.type === "stage_done" ? "✓" : "·"} ${st.stage || ""}`;
        const tail = (st.label && st.label !== st.stage) ? ` · ${st.label}` : "";
        const counts = st.counts && Object.keys(st.counts).length
          ? " · " + Object.entries(st.counts).slice(0, 2).map(([k, v]) => `${k}=${v}`).join(" ")
          : "";
        const dms = st.durationMs != null ? ` (${st.durationMs}ms)` : "";
        line.textContent = head + tail + counts + dms;
        detail.appendChild(line);
      }
      // Deltas with their family chip
      for (const d of g.deltas) {
        const line = document.createElement("div");
        line.className = "trail-subdelta";
        const fam = d.familyId ? `[${d.familyId}] ` : "";
        const idShort = d.objectId ? d.objectId.slice(0, 18) : "";
        line.textContent = `Δ ${fam}${d.action || "active"} · ${clip(d.label || idShort, 50)}`;
        detail.appendChild(line);
      }
      row.appendChild(detail);
    }

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

function buildQueryFromHits(prompt, intent, hits, defaultFamId = "episodes") {
  // Map a /memory/search or /memory/get_context hit to its family.
  // /memory/search returns FTS chunks (kind=undefined or "chunk") whose
  // source is an episode or file — those belong to the Episodes family,
  // NOT Research. Default to "episodes" so the chunk case lands there
  // unless the metadata says otherwise. The cap follows the Tweaks
  // density knob — sparse=5, medium=12, dense=24.
  const objects = hits.slice(0, objectsCap()).map(h => {
    const meta = h.metadata || {};
    const kind = meta.kind || h.type || "chunk";
    let famId = defaultFamId;
    if (kind === "decision") famId = "decisions";
    else if (
      kind === "theory" || kind === "experiment" || kind === "snapshot" ||
      kind === "insight" || kind === "concept" || kind === "theory_evidence" ||
      kind === "experiment_result"
    ) famId = "research";
    else if (kind === "episode" || kind === "chunk" || kind === "file") famId = "episodes";
    else if (kind === "behavior_instruction" || kind === "procedural_rule") famId = "instructions";
    else if (kind === "role") famId = "roles";
    else if (kind === "skill" || kind === "playbook" || kind === "capability_link") famId = "skills";
    else if (kind === "task_state" || kind === "candidate") famId = "tasks";
    else if (kind === "feedback") famId = "feedback";
    let table = "chunks";
    if (meta.kind === "decision") table = "decisions";
    else if (meta.kind === "theory") table = "theories";
    else if (kind === "behavior_instruction") table = "behavior_instructions";
    else if (kind === "role") table = "agent_roles";
    else if (kind === "skill") table = "agent_skills";
    else if (kind === "playbook") table = "agent_playbooks";
    return {
      id: h.id || h.chunk_id,
      label: meta.label || h.label || meta.path || clip(h.text || h.snippet || h.id, 22),
      famId,
      table,
      raw: h,
    };
  });
  const families = Array.from(new Set(objects.map(o => o.famId)));
  return { intent, prompt, families, objects, source: intent };
}

// Local read fingerprints: when the local Search/Explain button fires
// an HTTP request, we record (endpoint, query, timestamp) so the SSE
// replay of THAT specific request can be skipped (we already animate it
// via runQueryAnimation). Reads from other agents/MCP/curl don't have a
// matching fingerprint and animate normally.
const _localReadFingerprints = [];
function markLocalRead(endpoint, query) {
  const now = Date.now();
  // Drop expired entries while we're here.
  while (_localReadFingerprints.length && now - _localReadFingerprints[0].t > LOCAL_READ_TTL_MS) {
    _localReadFingerprints.shift();
  }
  _localReadFingerprints.push({ endpoint, query: (query || "").trim(), t: now });
}
function isLocalRead(ev) {
  const endpoint = String(ev.endpoint || "").trim();
  const snippet = String(ev.snippet || "").trim();
  const now = Date.now();
  for (let i = _localReadFingerprints.length - 1; i >= 0; i--) {
    const f = _localReadFingerprints[i];
    if (now - f.t > LOCAL_READ_TTL_MS) continue;
    if (endpoint.endsWith(f.endpoint) && snippet === f.query) return true;
  }
  return false;
}

async function searchMemory() {
  const q = els.query.value.trim();
  if (!q) return;
  els.searchSummary.textContent = "searching…";
  markLocalRead("/memory/search", q);
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
  markLocalRead("/memory/get_context", q);
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

function resetWorkspaceState() {
  // Switching workspaces means EVERY in-memory trail/queue/cycle from
  // the previous one is now stale. Drop them so the new workspace's
  // SSE stream starts cleanly without mixing in old events.
  resetSse();
  setSseChip("connecting…", "is-warn");
  // Cancel any pending debounced refresh — it would still target the
  // current selectedWorkspace() but spurious double-fetches confuse
  // the state.workspace consistency window.
  if (_refreshTimer) { clearTimeout(_refreshTimer); _refreshTimer = null; }
  // Cancel any pending "last request" preview from the previous
  // workspace's replay; otherwise the new workspace's empty trail
  // window could play an animation built from the old workspace's
  // groups that haven't been cleared yet.
  if (_lastRequestPreviewTimer) {
    clearTimeout(_lastRequestPreviewTimer);
    _lastRequestPreviewTimer = null;
  }
  state.events = [];
  state.eventIds = new Set();
  state.trailGroups = [];
  state.trailGroupsById = new Map();
  _expandedTrailGroups.clear();
  state.queue = [];
  state.activeQuery = null;
  state.cycleStart = 0;
  state.idleStart = 0;
  state.lastIntent = "";
  state.phase = "idle";
  state.progress = 0;
  state.liveLight = new Map();
  state.detailCache = new Map();
  state.selected = null;
  state.inspectorHistory = [];
  state.requestBuffer = new Map();
  state.recentLabels = new Map();
  state.countDeltas = new Map();
  state.reverseStart = 0;
  // Drop the cached /memory/ui/state from the previous workspace so the
  // family centre counters and graph don't render stale numbers during
  // the brief window between dropdown change and the new fetchState
  // arriving. Without this, the user sees "old workspace's 22 episodes"
  // for ~200 ms and assumes the switch didn't take effect.
  state.memory = null;
  renderFeed();
  renderInspector();
  // Force one paint immediately so the visual reset is visible the
  // same frame as the dropdown change, not on the next tick.
  if (shellMounted) paintFrame();
}

els.workspace.addEventListener("change", () => {
  // Pin state.workspace to the new value the moment the user picks it,
  // so the SSE cross-workspace guard accepts replay events from the
  // new workspace's stream as soon as they arrive.
  state.workspace = els.workspace.value || state.workspace;
  resetWorkspaceState();
  fetchState({ manual: true });
});
els.token.addEventListener("change", () => {
  state.token = els.token.value.trim();
  resetWorkspaceState();
  fetchState({ manual: true });
});
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
setTimeout(tick, 30);
setInterval(() => { if (!state.sseReady && !state.paused) fetchState(); }, POLL_MS);
