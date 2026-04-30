const state = {
  workspace: "",
  token: "",
  paused: false,
  memory: null,
  health: null,
  events: [],
  eventIds: new Set(),
  activeRequests: [],
  graphDeltas: [],
  eventSource: null,
  sseReady: false,
  sseFailed: false,
  lastRequestId: "",
  taskGraph: { query: "", mode: "empty", nodes: [], edges: [], contextObjects: [] },
  rawContext: "",
  graphZoom: 0.82,
  graphDragging: false,
  graphDragStart: null,
  graphIndex: { nodesById: new Map(), edges: [], activeRoutes: [], activeRouteIds: new Set() },
  liveRequestId: "",
  retiringRoutes: [],
  graphEffects: new Map(),
  graphCleanupTimer: null,
  demoRoute: null,
  demoSequence: 0,
  demoTimers: [],
  animationQueue: [],
  activeGraphJob: null,
  lastGraphRoutes: [],
  graphAnimationTimers: [],
  pendingGraphRequests: new Map(),
  graphJobSequence: 0,
  animatedRouteKeys: new Set(),
  uiStartedAtMs: Date.now(),
  graphRendering: false,
  graphResizeTimer: null,
  lastGraphSize: { width: 0, height: 0 },
  graphLayout: null,
  graphClickCandidate: null,
};

const els = {
  workspace: document.getElementById("workspaceInput"),
  token: document.getElementById("tokenInput"),
  refresh: document.getElementById("refreshBtn"),
  pause: document.getElementById("pauseBtn"),
  healthChip: document.getElementById("healthChip"),
  sseChip: document.getElementById("sseChip"),
  updatedChip: document.getElementById("updatedChip"),
  warningsPanel: document.getElementById("warningsPanel"),
  warningsList: document.getElementById("warningsList"),
  warningsCount: document.getElementById("warningsCountChip"),
  lifeFeed: document.getElementById("lifeFeed"),
  eventCountChip: document.getElementById("eventCountChip"),
  query: document.getElementById("queryInput"),
  search: document.getElementById("searchBtn"),
  context: document.getElementById("contextBtn"),
  rawContext: document.getElementById("rawContextBtn"),
  taskGraph: document.getElementById("taskGraphSvg"),
  taskGraphMeta: document.getElementById("taskGraphMeta"),
  contextSummary: document.getElementById("contextSummary"),
  metrics: document.getElementById("metrics"),
  signature: document.getElementById("signatureChip"),
  graphViewport: document.getElementById("graphViewport"),
  graphZoomIn: document.getElementById("graphZoomIn"),
  graphZoomOut: document.getElementById("graphZoomOut"),
  graphZoomReset: document.getElementById("graphZoomReset"),
  graphTest: document.getElementById("graphTestBtn"),
  graphZoomLabel: document.getElementById("graphZoomLabel"),
  graphInspector: document.getElementById("graphInspector"),
  graph: document.getElementById("graphSvg"),
  process: document.getElementById("processSvg"),
  stageList: document.getElementById("stageList"),
  searchSummary: document.getElementById("searchSummary"),
  searchResults: document.getElementById("searchResults"),
  contextBox: document.getElementById("contextBox"),
  timeline: document.getElementById("timeline"),
};

const svgNS = "http://www.w3.org/2000/svg";

if ("scrollRestoration" in window.history) {
  window.history.scrollRestoration = "manual";
}
window.addEventListener("load", () => window.scrollTo({ top: 0, left: 0 }));

const stageCatalog = [
  { id: "input", label: "Input", color: "cyan" },
  { id: "redact", label: "Redact", color: "amber" },
  { id: "persist", label: "Persist", color: "green" },
  { id: "chunk", label: "Chunk", color: "green" },
  { id: "fts", label: "FTS", color: "cyan" },
  { id: "vector", label: "Vector", color: "violet" },
  { id: "retrieve", label: "Retrieve", color: "cyan" },
  { id: "rank", label: "Rank", color: "violet" },
  { id: "budget", label: "Budget", color: "amber" },
  { id: "context", label: "Context", color: "violet" },
  { id: "candidates", label: "Review", color: "amber" },
  { id: "response", label: "Response", color: "green" },
];

const processColors = {
  capture: "#29d3ff",
  index: "#25f0a4",
  retrieve: "#29d3ff",
  context: "#a78bfa",
  research: "#f6c85f",
  capabilities: "#5eead4",
  governance: "#fb7185",
};

const memoryGraphSize = { width: 1320, height: 940 };
const semanticHubs = [
  {
    id: "roles",
    label: "Roles",
    detail: "agent identities",
    tables: ["agent_roles"],
    color: "#29d3ff",
    angle: -90,
  },
  {
    id: "skills",
    label: "Skills",
    detail: "reusable methods",
    tables: ["agent_skills", "agent_playbooks"],
    color: "#25f0a4",
    angle: -45,
  },
  {
    id: "instructions",
    label: "Instructions",
    detail: "behavior rules",
    tables: ["behavior_instructions"],
    color: "#a78bfa",
    angle: 0,
  },
  {
    id: "tasks",
    label: "Tasks",
    detail: "current work",
    tables: ["task_state", "memory_candidates", "maintenance_events"],
    color: "#f6c85f",
    angle: 45,
  },
  {
    id: "research",
    label: "Research",
    detail: "theories and evidence",
    tables: [
      "theories",
      "theory_evidence",
      "research_experiments",
      "experiment_results",
      "memory_snapshots",
      "research_insights",
      "domain_concepts",
    ],
    color: "#fb7185",
    angle: 90,
  },
  {
    id: "decisions",
    label: "Decisions",
    detail: "chosen architecture",
    tables: ["decisions", "capability_links"],
    color: "#5eead4",
    angle: 135,
  },
  {
    id: "episodes",
    label: "Episodes",
    detail: "what happened",
    tables: ["episodes", "chunks", "files"],
    color: "#38bdf8",
    angle: 180,
  },
  {
    id: "feedback",
    label: "Feedback",
    detail: "retrieval quality",
    tables: ["memory_usage_feedback"],
    color: "#f43f5e",
    angle: 225,
  },
];
const hubById = new Map(semanticHubs.map((hub) => [hub.id, hub]));
const tableToHub = new Map(semanticHubs.flatMap((hub) => hub.tables.map((table) => [table, hub.id])));

function headers() {
  const output = { "Content-Type": "application/json" };
  if (state.token) {
    output.Authorization = `Bearer ${state.token}`;
  }
  return output;
}

function selectedWorkspace() {
  return (els.workspace.value || state.workspace || "").trim();
}

function requireWorkspace() {
  const workspace = selectedWorkspace();
  if (!workspace) {
    throw new Error("Workspace is not loaded yet.");
  }
  return workspace;
}

function clear(el) {
  while (el.firstChild) {
    el.removeChild(el.firstChild);
  }
}

function textEl(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text == null ? "" : String(text);
  if (className) {
    node.className = className;
  }
  return node;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(svgNS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function setChip(el, text, status = "") {
  el.textContent = text;
  el.className = `chip ${status}`.trim();
}

function fmt(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function clip(value, limit = 120) {
  const text = value == null ? "" : String(value).replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}...`;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function nowMs() {
  return Date.now();
}

function pruneGraphEffects() {
  const current = nowMs();
  state.retiringRoutes = state.retiringRoutes.filter((route) => route.retireUntil > current);
  for (const [key, effect] of state.graphEffects.entries()) {
    if (effect.until <= current) {
      state.graphEffects.delete(key);
    }
  }
}

function scheduleGraphCleanup() {
  if (state.graphCleanupTimer) {
    window.clearTimeout(state.graphCleanupTimer);
  }
  state.graphCleanupTimer = window.setTimeout(() => {
    pruneGraphEffects();
    renderGraphRouteLayer();
    renderLive();
  }, 1100);
}

function renderGraphIfReady({ routeOnly = false } = {}) {
  if (routeOnly && renderGraphRouteLayer()) {
    renderLive();
    return;
  }
  if (state.memory?.graph) {
    renderStructuralGraph(state.memory.graph);
  }
  renderLive();
}

function clearDemoTimers() {
  for (const timer of state.demoTimers) {
    window.clearTimeout(timer);
  }
  state.demoTimers = [];
}

function currentActiveRoutesForRetire() {
  if (state.graphIndex.activeRoutes?.length) {
    return state.graphIndex.activeRoutes;
  }
  if (state.demoRoute && state.demoRoute.phase === "object") {
    return [state.demoRoute];
  }
  return [];
}

function retireActiveRoutes() {
  const retiring = currentActiveRoutesForRetire();
  const expireAt = nowMs() + 950;
  state.retiringRoutes = retiring.map((route) => ({
    ...route,
    retiring: true,
    retireUntil: expireAt,
  }));
  if (retiring.length) {
    state.taskGraph = { ...state.taskGraph, contextObjects: [] };
  }
}

function setGraphZoom(value, { keepCenter = true } = {}) {
  const viewport = els.graphViewport;
  if (!viewport) {
    return;
  }
  const previous = Math.max(state.graphZoom, 1);
  const before = {
    x: viewport.scrollLeft + viewport.clientWidth / 2,
    y: viewport.scrollTop + viewport.clientHeight / 2,
  };
  const base = graphViewportSize();
  state.graphZoom = clamp(value, 0.72, 2.2);
  const physicalZoom = Math.max(state.graphZoom, 1);
  const nextWidth = Math.round(base.width * physicalZoom);
  const nextHeight = Math.round(base.height * physicalZoom);
  if (state.lastGraphSize.width !== nextWidth) {
    els.graph.style.width = `${nextWidth}px`;
    state.lastGraphSize.width = nextWidth;
  }
  if (state.lastGraphSize.height !== nextHeight) {
    els.graph.style.height = `${nextHeight}px`;
    state.lastGraphSize.height = nextHeight;
  }
  els.graphZoomLabel.textContent = `${Math.round(state.graphZoom * 100)}%`;
  if (keepCenter && previous) {
    const ratio = physicalZoom / previous;
    viewport.scrollLeft = before.x * ratio - viewport.clientWidth / 2;
    viewport.scrollTop = before.y * ratio - viewport.clientHeight / 2;
  }
}

function zoomGraph(delta) {
  setGraphZoom(state.graphZoom + delta);
  if (state.memory?.graph) {
    renderStructuralGraph(state.memory.graph);
  }
}

function graphViewportSize() {
  const viewport = els.graphViewport;
  return {
    width: Math.max(320, Math.round(viewport?.clientWidth || memoryGraphSize.width)),
    height: Math.max(320, Math.round(viewport?.clientHeight || memoryGraphSize.height)),
  };
}

function graphWorldSize() {
  const viewport = graphViewportSize();
  const visualZoom = Math.min(state.graphZoom || 1, 1);
  const aspect = viewport.width / viewport.height || memoryGraphSize.width / memoryGraphSize.height;
  let width = Math.max(memoryGraphSize.width / visualZoom, viewport.width / visualZoom);
  let height = width / aspect;
  if (height < memoryGraphSize.height / visualZoom) {
    height = memoryGraphSize.height / visualZoom;
    width = height * aspect;
  }
  return { width: Math.round(width), height: Math.round(height) };
}

function renderWorkspaceOptions(workspaces) {
  const options = Array.from(new Set((workspaces || []).filter(Boolean)));
  if (state.workspace && !options.includes(state.workspace)) {
    options.unshift(state.workspace);
  }
  if (!options.length) {
    options.push("default");
  }
  const current = state.workspace || options[0];
  clear(els.workspace);
  for (const workspace of options) {
    const option = document.createElement("option");
    option.value = workspace;
    option.textContent = workspace;
    els.workspace.appendChild(option);
  }
  els.workspace.value = current;
}

function dateLabel(value) {
  if (!value) {
    return "unknown time";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

async function requestJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let parsed = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { raw: text };
  }
  if (!response.ok) {
    throw new Error(`${path} ${response.status}: ${clip(text, 220)}`);
  }
  return parsed;
}

function mergeEvents(events) {
  if (!Array.isArray(events)) {
    return false;
  }
  let changed = false;
  const accepted = [];
  for (const event of events) {
    if (!event || !event.event_id || state.eventIds.has(event.event_id)) {
      continue;
    }
    state.eventIds.add(event.event_id);
    state.events.push(event);
    accepted.push(event);
    changed = true;
  }
  if (state.events.length > 320) {
    const keep = state.events.slice(-320);
    state.events = keep;
    state.eventIds = new Set(keep.map((event) => event.event_id));
  }
  state.events.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  if (accepted.length) {
    updateLiveGraphFromEvents(accepted);
  }
  return changed;
}

function routeFromGraphDelta(event) {
  const counts = event.counts || {};
  const table = tableForObjectType(counts.object_type);
  const id = counts.object_id || counts.chunk_id || "";
  if (!id) {
    return null;
  }
  const nodeId = counts.object_id ? `${table}:${id}` : `chunks:${id}`;
  const routeTable = nodeTable(nodeId);
  return {
    source: "live-event",
    eventId: event.event_id,
    requestId: event.request_id,
    hubId: hubForTable(routeTable),
    table: routeTable,
    id,
    nodeId,
    label: event.label || id,
    relation: counts.action || event.type,
    updated_at: counts.updated_at || event.created_at || "",
    rank: 0,
  };
}

function routeFromStageEvent(event, index = 0) {
  const hubId = hubForEventStage(event);
  if (!hubId) {
    return null;
  }
  const stage = event.stage || event.operation || event.type || "event";
  return {
    source: "request-stage",
    eventId: event.event_id,
    requestId: event.request_id || event.event_id,
    hubId,
    table: `stage_${hubId}`,
    id: stage,
    nodeId: `stage:${hubId}:${stage}:${event.event_id || index}`,
    label: event.label || humanStage(stage),
    relation: stage,
    updated_at: event.created_at || "",
    rank: index,
  };
}

function routeVisualPriority(route) {
  const tablePriority = {
    agent_roles: 0,
    agent_skills: 1,
    agent_playbooks: 2,
    behavior_instructions: 3,
    decisions: 4,
    theories: 5,
    theory_evidence: 6,
    research_experiments: 7,
    experiment_results: 8,
    research_insights: 9,
    domain_concepts: 10,
    task_state: 11,
    chunks: 12,
    episodes: 13,
    files: 14,
    maintenance_events: 15,
  };
  if (Object.prototype.hasOwnProperty.call(tablePriority, route.table)) {
    return tablePriority[route.table];
  }
  if (route.source === "request-stage") {
    return 30 + Math.max(0, semanticHubs.findIndex((hub) => hub.id === route.hubId));
  }
  return 20 + Math.max(0, semanticHubs.findIndex((hub) => hub.id === route.hubId));
}

function prioritizeGraphRoutes(routes) {
  return routes
    .map((route, index) => ({ route, index, priority: routeVisualPriority(route) }))
    .sort((left, right) => left.priority - right.priority || left.index - right.index)
    .map((item) => item.route);
}

function graphJobRoutesFromEvents(events) {
  const routes = [];
  const seen = new Set();
  events.forEach((event, index) => {
    const graphRoute = event.type === "graph_delta" ? routeFromGraphDelta(event) : null;
    if (graphRoute) {
      const key = `${graphRoute.source}:${graphRoute.hubId}:${graphRoute.nodeId}:${graphRoute.relation}`;
      if (!seen.has(key)) {
        seen.add(key);
        routes.push(graphRoute);
      }
      return;
    }
    const stageRoute = routeFromStageEvent(event, index);
    if (!stageRoute) {
      return;
    }
    const key = `${stageRoute.source}:${stageRoute.hubId}:${stageRoute.nodeId}:${stageRoute.relation}`;
    if (!seen.has(key)) {
      seen.add(key);
      routes.push(stageRoute);
    }
  });
  const objectRoutes = routes.filter((route) => route.source !== "request-stage");
  const stageRoutes = routes.filter((route) => route.source === "request-stage");
  return prioritizeGraphRoutes([...objectRoutes, ...stageRoutes]).slice(0, 18);
}

function clearGraphAnimationTimers() {
  for (const timer of state.graphAnimationTimers) {
    window.clearTimeout(timer);
  }
  state.graphAnimationTimers = [];
}

function graphAnimationInProgress() {
  return Boolean(state.activeGraphJob) || state.retiringRoutes.length > 0 || state.graphEffects.size > 0 || state.animationQueue.length > 0;
}

function retireLastRoutesBeforeNextJob() {
  if (!state.lastGraphRoutes.length || !state.animationQueue.length || state.retiringRoutes.length) {
    return false;
  }
  const expireAt = nowMs() + 980;
  state.retiringRoutes = state.lastGraphRoutes.map((route) => ({
    ...route,
    phase: "object",
    retiring: true,
    retireUntil: expireAt,
  }));
  state.lastGraphRoutes = [];
  renderGraphIfReady({ routeOnly: true });
  state.graphAnimationTimers.push(
    window.setTimeout(() => {
      pruneGraphEffects();
      startNextGraphJob();
      renderGraphIfReady({ routeOnly: true });
    }, 980),
  );
  return true;
}

function enqueueGraphJob(job, { priority = false } = {}) {
  if (!job.routes.length) {
    return;
  }
  if (priority) {
    const firstTelemetry = state.animationQueue.findIndex((queued) => queued.source !== "demo");
    if (firstTelemetry === -1) {
      state.animationQueue.push(job);
    } else {
      state.animationQueue.splice(firstTelemetry, 0, job);
    }
  } else {
    state.animationQueue.push(job);
  }
  state.animationQueue = state.animationQueue.slice(-24);
  startNextGraphJob();
}

function schedulePendingGraphJob(requestId, delay) {
  const pending = state.pendingGraphRequests.get(requestId);
  if (!pending) {
    return;
  }
  if (pending.timer) {
    window.clearTimeout(pending.timer);
  }
  pending.timer = window.setTimeout(() => flushPendingGraphJob(requestId), delay);
}

function rememberGraphEvent(event) {
  const requestId = event.request_id || event.event_id;
  if (!requestId) {
    return;
  }
  if (!state.pendingGraphRequests.has(requestId)) {
    state.pendingGraphRequests.set(requestId, { events: [], timer: null });
  }
  const pending = state.pendingGraphRequests.get(requestId);
  pending.events.push(event);
  const finalEvent = event.type === "request_done" || event.type === "request_failed";
  schedulePendingGraphJob(requestId, finalEvent ? 90 : 420);
}

function shouldAnimateGraphEvent(event) {
  const createdAt = Date.parse(event.created_at || "");
  if (Number.isNaN(createdAt)) {
    return true;
  }
  return createdAt >= state.uiStartedAtMs - 1000;
}

function flushPendingGraphJob(requestId) {
  const pending = state.pendingGraphRequests.get(requestId);
  if (!pending) {
    return;
  }
  if (pending.timer) {
    window.clearTimeout(pending.timer);
  }
  state.pendingGraphRequests.delete(requestId);
  const events = pending.events.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  const routes = graphJobRoutesFromEvents(events);
  if (!routes.length) {
    return;
  }
  state.graphJobSequence += 1;
  enqueueGraphJob({
    jobId: `job_${state.graphJobSequence}_${requestId}`,
    requestId,
    source: "telemetry",
    routes,
    phase: "queued",
  });
}

function startNextGraphJob() {
  if (state.activeGraphJob || !state.animationQueue.length) {
    return;
  }
  if (state.retiringRoutes.length) {
    return;
  }
  clearGraphAnimationTimers();
  if (retireLastRoutesBeforeNextJob()) {
    return;
  }
  const job = state.animationQueue.shift();
  state.activeGraphJob = { ...job, phase: "hub" };
  state.liveRequestId = job.requestId;
  state.demoRoute = null;
  renderGraphIfReady({ routeOnly: true });
  state.graphAnimationTimers.push(window.setTimeout(() => setActiveGraphJobPhase("object"), 1360));
  state.graphAnimationTimers.push(window.setTimeout(finishActiveGraphJob, 2850));
}

function setActiveGraphJobPhase(phase) {
  if (!state.activeGraphJob) {
    return;
  }
  state.activeGraphJob = { ...state.activeGraphJob, phase };
  if (phase === "object") {
    for (const route of state.activeGraphJob.routes) {
      state.graphEffects.set(`object:${route.nodeId}`, { kind: "entering", until: nowMs() + 1400 });
    }
  }
  renderGraphIfReady({ routeOnly: true });
}

function finishActiveGraphJob() {
  if (!state.activeGraphJob) {
    startNextGraphJob();
    return;
  }
  state.lastGraphRoutes = state.activeGraphJob.routes.map((route) => ({
    ...route,
    phase: "object",
  }));
  state.activeGraphJob = null;
  els.graphTest?.classList.toggle("demo-running", state.animationQueue.some((job) => job.source === "demo"));
  renderGraphIfReady({ routeOnly: true });
  startNextGraphJob();
}

function beginLiveRequest(event) {
  if (!event.request_id || event.request_id === state.liveRequestId) {
    return;
  }
  state.liveRequestId = event.request_id;
}

function updateLiveGraphFromEvents(events) {
  const sorted = [...events].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  for (const event of sorted) {
    if (event.type === "request_started") {
      beginLiveRequest(event);
    }
    if (event.type === "graph_delta") {
      state.graphDeltas.push(event);
      state.graphDeltas = state.graphDeltas.slice(-50);
    }
    if (shouldAnimateGraphEvent(event)) {
      rememberGraphEvent(event);
    }
  }
  if (state.retiringRoutes.length || state.graphEffects.size) {
    scheduleGraphCleanup();
  }
}

function latestRequestId() {
  if (state.activeRequests.length) {
    return state.activeRequests[0].request_id;
  }
  for (let index = state.events.length - 1; index >= 0; index -= 1) {
    const event = state.events[index];
    if (event.request_id) {
      return event.request_id;
    }
  }
  return "";
}

function eventsForRequest(requestId) {
  return state.events.filter((event) => event.request_id === requestId);
}

function friendlyEvent(event) {
  if (!event) {
    return "Waiting for memory activity";
  }
  if (event.type === "graph_delta") {
    const objectType = event.counts?.object_type || "object";
    const action = event.counts?.action || "changed";
    return `${humanObject(objectType)} ${action}`;
  }
  if (event.type === "request_started") {
    return `${humanOperation(event.operation)} started`;
  }
  if (event.type === "request_done") {
    return `${humanOperation(event.operation)} completed`;
  }
  if (event.type === "request_failed") {
    return `${humanOperation(event.operation)} failed`;
  }
  if (event.type === "stage_started") {
    return `${humanStage(event.stage)} running`;
  }
  if (event.type === "stage_done") {
    return event.label || `${humanStage(event.stage)} done`;
  }
  return event.label || event.type;
}

function humanOperation(operation) {
  const names = {
    search: "Search",
    explain_context: "Explain",
    get_context: "Context build",
    ingest: "Episode ingest",
    ingest_file: "File ingest",
    write_decision: "Decision write",
    write_theory: "Theory write",
    add_theory_evidence: "Evidence write",
    upsert_role: "Role update",
    upsert_skill: "Skill update",
    upsert_playbook: "Playbook update",
    link_capability: "Capability link",
    update_task_state: "Task state",
  };
  return names[operation] || clip(operation || "Memory request", 40);
}

function humanStage(stage) {
  const match = stageCatalog.find((item) => item.id === stage);
  return match ? match.label : clip(stage || "Stage", 40);
}

function humanObject(objectType) {
  const names = {
    episode: "Episode",
    file: "File",
    decision: "Decision",
    theory: "Theory",
    theory_evidence: "Evidence",
    role: "Role",
    skill: "Skill",
    playbook: "Playbook",
    capability_link: "Capability link",
    task_state: "Task state",
    snapshot: "Snapshot",
    experiment: "Experiment",
    experiment_result: "Experiment result",
    concept: "Concept",
    insight: "Insight",
    behavior_instruction: "Behavior instruction",
  };
  return names[objectType] || clip(objectType || "Object", 40);
}

async function refresh({ manual = false } = {}) {
  if (state.paused && !manual) {
    return;
  }
  const workspace = selectedWorkspace();
  const started = performance.now();
  const stateUrl = workspace
    ? `/memory/ui/state?workspace_id=${encodeURIComponent(workspace)}&recent_limit=4`
    : "/memory/ui/state?recent_limit=4";
  const [memoryResult, healthResult] = await Promise.allSettled([
    fetch(stateUrl, {
      headers: state.token ? { Authorization: `Bearer ${state.token}` } : {},
    }),
    fetch("/health"),
  ]);

  if (memoryResult.status === "fulfilled") {
    const response = memoryResult.value;
    if (!response.ok) {
      throw new Error(`/memory/ui/state ${response.status}: ${clip(await response.text(), 180)}`);
    }
    const memory = await response.json();
    state.memory = memory;
    state.workspace = memory.workspace_id || workspace;
    renderWorkspaceOptions(memory.workspaces || [state.workspace]);
    mergeEvents(memory.latest_events || []);
    state.activeRequests = memory.active_requests || [];
    state.graphDeltas = memory.graph_deltas || [];
    renderMemory(memory, Math.round(performance.now() - started));
  }

  if (healthResult.status === "fulfilled" && healthResult.value.ok) {
    state.health = await healthResult.value.json();
  } else {
    state.health = null;
  }
  renderStatus();
  ensureEventSource();
  renderLive();
}

function ensureEventSource() {
  if (state.eventSource || state.sseReady || state.paused) {
    return;
  }
  if (state.token) {
    state.sseFailed = true;
    setChip(els.sseChip, "SSE blocked by token", "warning");
    return;
  }
  const workspace = selectedWorkspace();
  if (!workspace) {
    return;
  }
  const url = `/memory/ui/events?workspace_id=${encodeURIComponent(workspace)}`;
  const source = new EventSource(url);
  state.eventSource = source;
  source.addEventListener("open", () => {
    state.sseReady = true;
    state.sseFailed = false;
    setChip(els.sseChip, "SSE live", "ok");
  });
  const onEvent = (message) => {
    try {
      if (mergeEvents([JSON.parse(message.data)])) {
        if (state.memory?.graph && !graphAnimationInProgress()) {
          renderStructuralGraph(state.memory.graph);
        }
        renderLive();
      }
    } catch (error) {
      showError(error);
    }
  };
  source.addEventListener("memory", onEvent);
  source.onmessage = onEvent;
  source.onerror = () => {
    state.sseFailed = true;
    state.sseReady = false;
    source.close();
    state.eventSource = null;
    setChip(els.sseChip, "SSE fallback polling", "warning");
  };
}

function resetEventSource() {
  if (state.eventSource) {
    state.eventSource.close();
  }
  state.eventSource = null;
  state.sseReady = false;
  state.sseFailed = false;
}

function renderStatus() {
  const healthStatus = state.health?.status || "unknown";
  const retrieval = state.health?.retrieval_integrity?.status || "";
  setChip(
    els.healthChip,
    retrieval ? `health ${healthStatus} / retrieval ${retrieval}` : `health ${healthStatus}`,
    healthStatus === "ok" && (!retrieval || retrieval === "ok") ? "ok" : "warning",
  );
  if (!state.sseReady && !state.sseFailed) {
    setChip(els.sseChip, "SSE starting", "cyan");
  }
}

function renderMemory(memory, duration) {
  setChip(els.updatedChip, `${dateLabel(memory.generated_at)} / ${duration}ms`, "ok");
  if (els.signature) {
    setChip(els.signature, `signature ${memory.signature || "-"}`, "");
  }
  renderMetrics(memory.counts || {});
  renderWarnings(memory.warnings || []);
  renderProcess(memory.process || { stages: [], edges: [], events: [] });
  renderStructuralGraph(memory.graph || { nodes: [], edges: [] });
  renderTimeline(memory.recent || []);
}

function detailPairs(details) {
  if (!details || typeof details !== "object") {
    return [];
  }
  return Object.entries(details)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 5);
}

function renderWarnings(warnings) {
  clear(els.warningsList);
  const count = warnings.length;
  setChip(els.warningsCount, `${fmt(count)} open`, count ? "warning" : "ok");
  els.warningsPanel.classList.toggle("clear", !count);
  if (!count) {
    const item = document.createElement("article");
    item.className = "warning-item clear";
    item.appendChild(textEl("strong", "No open maintenance warnings"));
    item.appendChild(textEl("span", "Retrieval integrity has no open maintenance events for this workspace."));
    els.warningsList.appendChild(item);
    return;
  }
  for (const warning of warnings.slice(0, 8)) {
    const item = document.createElement("article");
    item.className = `warning-item ${warning.severity || "warning"}`;
    item.appendChild(textEl("strong", clip(warning.summary || warning.kind || warning.event_id, 140)));
    item.appendChild(
      textEl(
        "span",
        `${warning.severity || "warning"} / ${warning.kind || "maintenance"} / ${dateLabel(warning.created_at)}`,
      ),
    );
    if (warning.target_type || warning.target_id) {
      item.appendChild(textEl("em", `${warning.target_type || "target"}: ${clip(warning.target_id || "-", 90)}`));
    }
    const details = detailPairs(warning.details);
    if (details.length) {
      const detailList = document.createElement("dl");
      for (const [key, value] of details) {
        detailList.appendChild(textEl("dt", key));
        detailList.appendChild(textEl("dd", clip(JSON.stringify(value), 160)));
      }
      item.appendChild(detailList);
    }
    els.warningsList.appendChild(item);
  }
}

function renderMetrics(counts) {
  clear(els.metrics);
  const keyItems = [
    ["Episodes", counts.episodes],
    ["Chunks", counts.chunks],
    ["Theories", counts.theories],
    ["Decisions", counts.decisions],
    ["Roles", counts.agent_roles],
    ["Skills", counts.agent_skills],
    ["Links", counts.capability_links],
    ["Warnings", counts.maintenance_events],
  ];
  for (const [label, value] of keyItems) {
    const item = document.createElement("div");
    item.className = "metric-line";
    item.appendChild(textEl("span", label));
    item.appendChild(textEl("strong", fmt(value)));
    els.metrics.appendChild(item);
  }
}

function renderLive() {
  const requestId = latestRequestId();
  state.lastRequestId = requestId;
  setChip(els.eventCountChip, `${state.events.length} events`, "");
  renderLifeFeed();
  renderTaskGraph();
}

function renderLifeFeed() {
  clear(els.lifeFeed);
  const events = state.events.slice(-28).reverse();
  if (!events.length) {
    els.lifeFeed.appendChild(textEl("p", "No live events yet. Run a memory request.", "empty"));
    return;
  }
  for (const event of events) {
    const item = document.createElement("article");
    item.className = `feed-item ${event.status || "ok"} ${event.type}`;
    item.appendChild(textEl("strong", friendlyEvent(event)));
    item.appendChild(
      textEl(
        "span",
        `${dateLabel(event.created_at)} / ${humanStage(event.stage)}${event.duration_ms != null ? ` / ${event.duration_ms}ms` : ""}`,
      ),
    );
    if (event.snippet) {
      item.appendChild(textEl("em", clip(event.snippet, 120)));
    }
    els.lifeFeed.appendChild(item);
  }
}

function renderProcess(process) {
  renderProcessSvg(process);
  renderStageList(process.stages || []);
}

function renderProcessSvg(process) {
  const svg = els.process;
  clear(svg);
  svg.setAttribute("viewBox", "0 0 1000 230");
  const stages = process.stages || [];
  const step = stages.length > 1 ? 880 / (stages.length - 1) : 1;
  stages.forEach((stage, index) => {
    const x = 60 + index * step;
    const y = 90;
    if (index < stages.length - 1) {
      svg.appendChild(
        svgEl("line", {
          x1: x + 42,
          y1: y,
          x2: x + step - 42,
          y2: y,
          class: "static-edge",
        }),
      );
    }
    const group = svgEl("g", { class: `static-stage ${stage.status || "active"}` });
    group.appendChild(svgEl("circle", { cx: x, cy: y, r: 31, fill: processColors[stage.id] || "#29d3ff" }));
    const label = svgEl("text", { x, y: y + 62, "text-anchor": "middle" });
    label.textContent = stage.label;
    group.appendChild(label);
    const count = svgEl("text", { x, y: y + 84, "text-anchor": "middle", class: "stage-count" });
    count.textContent = fmt(stage.count);
    group.appendChild(count);
    svg.appendChild(group);
  });
}

function renderStageList(stages) {
  clear(els.stageList);
  for (const stage of stages) {
    const item = document.createElement("article");
    item.className = `stage-card ${stage.status || "active"}`;
    item.appendChild(textEl("strong", stage.label));
    item.appendChild(textEl("span", stage.verb || ""));
    if (stage.latest) {
      item.appendChild(textEl("em", `Latest: ${clip(stage.latest.label, 80)}`));
    }
    els.stageList.appendChild(item);
  }
}

function renderTimeline(items) {
  clear(els.timeline);
  if (!items.length) {
    els.timeline.appendChild(textEl("p", "No durable rows in this workspace.", "empty"));
    return;
  }
  for (const item of items.slice(0, 18)) {
    const row = document.createElement("article");
    row.className = "timeline-item";
    row.appendChild(textEl("strong", clip(item.label || item.table, 90)));
    row.appendChild(textEl("span", `${item.table} / ${dateLabel(item.updated_at)}`));
    els.timeline.appendChild(row);
  }
}

function tableForObjectType(objectType) {
  const names = {
    episode: "episodes",
    chunk: "chunks",
    file: "files",
    decision: "decisions",
    theory: "theories",
    theory_evidence: "theory_evidence",
    experiment: "research_experiments",
    experiment_result: "experiment_results",
    snapshot: "memory_snapshots",
    insight: "research_insights",
    concept: "domain_concepts",
    role: "agent_roles",
    skill: "agent_skills",
    playbook: "agent_playbooks",
    capability_link: "capability_links",
    task_state: "task_state",
    behavior_instruction: "behavior_instructions",
    maintenance_event: "maintenance_events",
    procedural_rule: "procedural_rules",
    retrieved_fact: "retrieved_facts",
    retrieved_chunk: "chunks",
  };
  return names[objectType] || objectType || "";
}

function nodeTable(nodeId) {
  return String(nodeId || "").split(":")[0] || "";
}

function tableNodeName(tableNodeId) {
  return String(tableNodeId || "").replace(/^table:/, "");
}

function shortObjectId(nodeId) {
  const id = String(nodeId || "").split(":").pop() || "";
  return id.length <= 9 ? id : `${id.slice(0, 7)}...`;
}

function objectLabel(node) {
  const id = shortObjectId(node.detail || node.id);
  const label = clip(node.label || node.kind || "", 18);
  return label && label !== id ? `${id} ${label}` : id;
}

function objectNodeBadge(nodeId) {
  const parts = String(nodeId || "").split(":");
  if (parts[0] === "stage") {
    return stageBadge(parts[2]);
  }
  if (parts[0] === "demo") {
    return stageBadge(parts[2] || parts[1]);
  }
  const id = parts.pop() || "";
  const compact = id.replace(/^(ep|chk|dec|th|role|skill|task|cand|caplink|beh|insight|snap|exp)_/i, "");
  return (compact || id || "?").slice(0, 4);
}

function offsetPoint(from, to, distance) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  return {
    x: from.x + (dx / length) * distance,
    y: from.y + (dy / length) * distance,
  };
}

function curvedPathBetween(from, to, fromRadius, toRadius, curve = 0.18) {
  const start = offsetPoint(from, to, fromRadius);
  const end = offsetPoint(to, from, toRadius);
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const normal = { x: -dy, y: dx };
  const length = Math.hypot(normal.x, normal.y) || 1;
  const bend = Math.hypot(dx, dy) * curve;
  const mid = {
    x: (start.x + end.x) / 2 + (normal.x / length) * bend,
    y: (start.y + end.y) / 2 + (normal.y / length) * bend,
  };
  return `M ${start.x.toFixed(1)} ${start.y.toFixed(1)} Q ${mid.x.toFixed(1)} ${mid.y.toFixed(1)} ${end.x.toFixed(1)} ${end.y.toFixed(1)}`;
}

function quadraticControl(from, to, curve) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const normal = { x: -dy, y: dx };
  const length = Math.hypot(normal.x, normal.y) || 1;
  const bend = Math.hypot(dx, dy) * curve;
  return {
    x: (from.x + to.x) / 2 + (normal.x / length) * bend,
    y: (from.y + to.y) / 2 + (normal.y / length) * bend,
  };
}

function lineCircleDistance(from, to, circle) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const lengthSq = dx * dx + dy * dy || 1;
  const t = clamp(((circle.x - from.x) * dx + (circle.y - from.y) * dy) / lengthSq, 0, 1);
  const x = from.x + dx * t;
  const y = from.y + dy * t;
  return Math.hypot(circle.x - x, circle.y - y);
}

function routePositionKey(route, fallback = "") {
  return routeStableKey(route) || fallback || route?.nodeId || route?.id || "";
}

function continuousRoutePath(root, hub, object, rootRadius, objectRadius, options = {}) {
  const start = offsetPoint(root, hub, rootRadius);
  const join = hub;
  const end = offsetPoint(object, hub, objectRadius);
  const firstControl = quadraticControl(start, join, 0.08);
  const secondControl = quadraticControl(join, end, 0.04);
  const normal = { x: -(end.y - join.y), y: end.x - join.x };
  const normalLength = Math.hypot(normal.x, normal.y) || 1;
  const routeKey = options.routeKey || `${hub.x}:${hub.y}:${object.x}:${object.y}`;
  const sideSeed = hashUnit(routeKey, "avoid-side") >= 0.5 ? 1 : -1;
  const laneOffset = Number(options.laneOffset || 0);
  let bend = laneOffset * 12;
  for (const obstacle of options.obstacles || []) {
    if (obstacle.key === options.targetKey) {
      continue;
    }
    const clearance = (obstacle.r || 22) + 20;
    const distance = lineCircleDistance(join, end, obstacle);
    if (distance >= clearance) {
      continue;
    }
    const signedSide =
      Math.sign((obstacle.x - join.x) * normal.x + (obstacle.y - join.y) * normal.y) ||
      sideSeed;
    bend += -signedSide * (clearance - distance + 34);
  }
  if (bend) {
    secondControl.x += (normal.x / normalLength) * bend;
    secondControl.y += (normal.y / normalLength) * bend;
  }
  return [
    `M ${start.x.toFixed(1)} ${start.y.toFixed(1)}`,
    `Q ${firstControl.x.toFixed(1)} ${firstControl.y.toFixed(1)} ${join.x.toFixed(1)} ${join.y.toFixed(1)}`,
    `Q ${secondControl.x.toFixed(1)} ${secondControl.y.toFixed(1)} ${end.x.toFixed(1)} ${end.y.toFixed(1)}`,
  ].join(" ");
}

function hubForTable(table) {
  return tableToHub.get(table) || "episodes";
}

function hubForObjectType(objectType) {
  return hubForTable(tableForObjectType(objectType));
}

function hubForUsedObject(item) {
  return hubForTable(item.table || tableForObjectType(item.object_type || item.kind));
}

function normalizeContextObject(item, index = 0) {
  const table = item.table || tableForObjectType(item.object_type || item.kind) || "chunks";
  const id = item.id || item.chunk_id || item.object_id || `${table}_${index + 1}`;
  return {
    ...item,
    table,
    id,
    nodeId: `${table}:${id}`,
    label: item.label || item.summary || item.path || item.relation || id,
    updated_at: item.updated_at || item.created_at || item.metadata?.created_at || item.metadata?.updated_at || "",
    rank: Number.isFinite(Number(item.rank)) ? Number(item.rank) : index,
  };
}

function activeGraphSignal(nodesById) {
  if (state.activeGraphJob) {
    const route = state.activeGraphJob.routes[0];
    return {
      source: state.activeGraphJob.source,
      hubId: route?.hubId || "",
      objectId: state.activeGraphJob.phase === "object" ? route?.nodeId || "" : "",
      label: route?.label || "Memory route",
      status: "running",
      createdAt: route?.updated_at,
      event: null,
    };
  }
  return { source: "idle", hubId: "", objectId: "", label: "Waiting for memory activity" };
}

function hubForEventStage(event) {
  const stageHub = {
    input: "tasks",
    redact: "tasks",
    persist: "episodes",
    chunk: "episodes",
    fts: "episodes",
    vector: "episodes",
    retrieve: "feedback",
    rank: "feedback",
    budget: "tasks",
    context: "instructions",
    candidates: "tasks",
    response: "tasks",
  };
  if (event?.type === "graph_delta") {
    return hubForObjectType(event.counts?.object_type);
  }
  return stageHub[event?.stage] || "tasks";
}

function stageBadge(stage) {
  const labels = {
    input: "in",
    redact: "red",
    persist: "per",
    chunk: "chk",
    fts: "fts",
    vector: "vec",
    retrieve: "ret",
    rank: "rnk",
    budget: "bud",
    context: "ctx",
    candidates: "rev",
    response: "res",
    roles: "role",
    skills: "skil",
    instructions: "inst",
    tasks: "task",
    research: "rsch",
    decisions: "dec",
    episodes: "epis",
    feedback: "feed",
  };
  return labels[String(stage || "").toLowerCase()] || String(stage || "evt").slice(0, 4);
}

function activeRequestHubIds() {
  const requestEvents = eventsForRequest(latestRequestId()).slice(-18);
  return new Set(requestEvents.map((event) => hubForEventStage(event)).filter(Boolean));
}

function routesFromActiveRequestEvents() {
  const requestEvents = eventsForRequest(latestRequestId()).slice(-24);
  const routes = [];
  const seen = new Set();
  requestEvents.forEach((event, index) => {
    const route = routeFromStageEvent(event, index);
    if (!route) {
      return;
    }
    const key = `${route.hubId}:${route.nodeId}:${route.relation}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    routes.push({ ...route, phase: "object" });
  });
  return routes.slice(0, 18);
}

function activeContextRoutes(nodesById) {
  const contextObjects = state.taskGraph?.contextObjects || [];
  const routes = [];
  if (state.activeGraphJob) {
    return state.activeGraphJob.routes.map((route) => ({
      ...route,
      phase: state.activeGraphJob.phase === "object" ? "object" : "route",
    }));
  }
  if (contextObjects.length) {
    routes.push(...contextObjects.map((item, index) => {
      const normalized = normalizeContextObject(item, index);
      return {
        ...normalized,
        source: "context",
        hubId: hubForUsedObject(normalized),
      };
    }));
  }
  if (!routes.length && state.lastGraphRoutes.length) {
    routes.push(...state.lastGraphRoutes);
  }
  return prioritizeGraphRoutes(routes);
}

function demoRouteForHub(hubId, sequence, itemIndex = 0) {
  const hub = hubById.get(hubId) || semanticHubs[0];
  const phase = hub.id === "feedback" ? "rank" : hub.id === "instructions" ? "context" : hub.id;
  const id = `${stageBadge(phase)}_${String(sequence).padStart(2, "0")}_${String(itemIndex + 1).padStart(2, "0")}`;
  return {
    source: "demo",
    eventId: `demo_${sequence}_${itemIndex + 1}`,
    requestId: state.liveRequestId,
    hubId: hub.id,
    table: `demo_${hub.id}`,
    id,
    nodeId: `demo:${hub.id}:${phase}:${id}`,
    label: `${hub.label} ${itemIndex + 1}`,
    relation: "simulated request",
    updated_at: new Date().toISOString(),
    rank: itemIndex,
    phase: "hub",
  };
}

function demoRoutesForRequest(sequence, requestId) {
  const scenario = [
    ["skills", 2],
    ["roles", 1],
    ["decisions", 3],
    ["research", 1],
    ["tasks", 1],
  ];
  const routes = [];
  scenario.forEach(([hubId, count]) => {
    for (let index = 0; index < count; index += 1) {
      routes.push({
        ...demoRouteForHub(hubId, sequence, index),
        requestId,
        phase: "object",
      });
    }
  });
  return routes;
}

function setDemoPhase(phase) {
  if (!state.demoRoute) {
    return;
  }
  state.demoRoute = { ...state.demoRoute, phase };
  if (phase === "object") {
    state.graphEffects.set(`object:${state.demoRoute.nodeId}`, {
      kind: "entering",
      until: nowMs() + 1400,
    });
  }
  renderGraphIfReady({ routeOnly: true });
}

function startGraphDemo() {
  state.demoSequence += 1;
  const requestId = `demo_${Date.now()}_${state.demoSequence}`;
  const routes = demoRoutesForRequest(state.demoSequence, requestId);
  els.graphTest?.classList.add("demo-running");
  state.graphJobSequence += 1;
  enqueueGraphJob({
    jobId: `demo_job_${state.graphJobSequence}`,
    requestId,
    source: "demo",
    routes,
    phase: "queued",
  }, { priority: true });
}

function routeAnimationKey(kind, hubId, route = null) {
  const requestId = route?.requestId || state.liveRequestId || latestRequestId() || "idle";
  const objectId = route?.nodeId || "";
  return `${requestId}:${kind}:${hubId}:${objectId}`;
}

function shouldAnimateRoute(key) {
  if (state.animatedRouteKeys.has(key)) {
    return false;
  }
  state.animatedRouteKeys.add(key);
  if (state.animatedRouteKeys.size > 260) {
    state.animatedRouteKeys = new Set([...state.animatedRouteKeys].slice(-160));
  }
  return true;
}

function activeObjectForSignal(signal, nodesById) {
  if (!signal.objectId) {
    return null;
  }
  return (
    nodesById.get(signal.objectId) || {
      id: signal.objectId,
      label: signal.objectId.split(":").pop(),
      kind: nodeTable(signal.objectId),
      detail: signal.objectId,
      placeholder: true,
    }
  );
}

function activeObjectPoint(hub, hubPos) {
  const angle = (hub.angle * Math.PI) / 180;
  const distance = 235;
  return {
    x: hubPos.x + Math.cos(angle) * distance,
    y: hubPos.y + Math.sin(angle) * distance,
  };
}

function stableHash(value) {
  const text = String(value || "");
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function hashUnit(value, salt = "") {
  return stableHash(`${salt}:${value}`) / 4294967295;
}

function routeStableKey(route) {
  return [
    route?.nodeId,
    route?.id,
    route?.eventId,
    route?.requestId,
    route?.label,
    route?.relation,
  ]
    .filter(Boolean)
    .join(":");
}

function routeTimestamp(route) {
  const raw =
    route?.updated_at ||
    route?.created_at ||
    route?.metadata?.updated_at ||
    route?.metadata?.created_at ||
    "";
  const value = Date.parse(raw);
  return Number.isFinite(value) ? value : null;
}

function routeAgeFactor(route, index, routesForHub) {
  const timedRoutes = routesForHub
    .map((item, itemIndex) => ({
      key: routeStableKey(item),
      time: routeTimestamp(item),
      fallbackRank: Number.isFinite(Number(item.rank)) ? Number(item.rank) : itemIndex,
    }))
    .filter((item) => item.time !== null);

  if (timedRoutes.length >= 2) {
    const newest = Math.max(...timedRoutes.map((item) => item.time));
    const oldest = Math.min(...timedRoutes.map((item) => item.time));
    const current = timedRoutes.find((item) => item.key === routeStableKey(route));
    if (current && newest > oldest) {
      return clamp((newest - current.time) / (newest - oldest), 0, 1);
    }
  }

  const ranks = routesForHub.map((item, itemIndex) =>
    Number.isFinite(Number(item.rank)) ? Number(item.rank) : itemIndex,
  );
  const minRank = Math.min(...ranks);
  const maxRank = Math.max(...ranks);
  const currentRank = Number.isFinite(Number(route.rank)) ? Number(route.rank) : index;
  if (maxRank > minRank) {
    return clamp((currentRank - minRank) / (maxRank - minRank), 0, 1);
  }
  return routesForHub.length <= 1 ? 0 : clamp(index / (routesForHub.length - 1), 0, 1);
}

function routeObjectPoint(hub, hubPos, index, routesForHub, bounds) {
  const angleBase = (hub.angle * Math.PI) / 180;
  const route = routesForHub[index];
  const routeKey = routeStableKey(route) || `${hub.id}:${index}`;
  const ageFactor = routeAgeFactor(route, index, routesForHub);
  const laneOffset = index - (routesForHub.length - 1) / 2;
  const count = Math.max(1, routesForHub.length);
  const fan = clamp(0.58 + count * 0.16, 0.72, 1.7);
  const angleStep = count <= 1 ? 0 : clamp(fan / (count - 1), 0.22, 0.42);
  const randomFan = (hashUnit(routeKey, "angle") - 0.5) * 0.08;
  const angle = angleBase + laneOffset * angleStep + randomFan;
  const jitter = (hashUnit(routeKey, "distance") - 0.5) * 28;
  const distance = 176 + ageFactor * 260 + Math.abs(laneOffset) * 18 + jitter;
  const paddingX = bounds.paddingX || bounds.width * 0.1;
  const paddingY = bounds.paddingY || bounds.height * 0.1;
  return {
    x: clamp(hubPos.x + Math.cos(angle) * distance, paddingX + 36, bounds.width - paddingX - 36),
    y: clamp(hubPos.y + Math.sin(angle) * distance, paddingY + 36, bounds.height - paddingY - 36),
  };
}

function clampRoutePoint(point, radius, bounds) {
  const paddingX = bounds.paddingX || bounds.width * 0.1;
  const paddingY = bounds.paddingY || bounds.height * 0.1;
  point.x = clamp(point.x, paddingX + radius, bounds.width - paddingX - radius);
  point.y = clamp(point.y, paddingY + radius, bounds.height - paddingY - radius);
}

function pushRoutePointAway(entry, obstacle, minDistance, bounds) {
  const dx = entry.point.x - obstacle.x;
  const dy = entry.point.y - obstacle.y;
  let distance = Math.hypot(dx, dy);
  let nx = dx / (distance || 1);
  let ny = dy / (distance || 1);
  if (!distance) {
    const angle = ((hubById.get(entry.hubId)?.angle || 0) * Math.PI) / 180;
    nx = Math.cos(angle);
    ny = Math.sin(angle);
    distance = 0.01;
  }
  const overlap = minDistance - distance;
  if (overlap <= 0) {
    return false;
  }
  entry.point.x += nx * overlap;
  entry.point.y += ny * overlap;
  clampRoutePoint(entry.point, entry.r, bounds);
  return true;
}

function buildRouteObjectLayout(routesByHub, positions, layout) {
  const bounds = {
    width: layout.width,
    height: layout.height,
    paddingX: layout.paddingX,
    paddingY: layout.paddingY,
  };
  const entries = [];
  for (const hub of semanticHubs) {
    const hubPos = positions.get(`hub:${hub.id}`);
    const routes = routesByHub.get(hub.id) || [];
    if (!hubPos || !routes.length) {
      continue;
    }
    routes.forEach((route, index) => {
      const key = routePositionKey(route, `${hub.id}:${index}`);
      entries.push({
        key,
        hubId: hub.id,
        point: routeObjectPoint(hub, hubPos, index, routes, bounds),
        r: 27,
      });
    });
  }
  const staticObstacles = [
    { key: "root", x: layout.root.x, y: layout.root.y, r: layout.rootRadius + 48 },
    ...semanticHubs
      .map((hub) => {
        const point = positions.get(`hub:${hub.id}`);
        return point ? { key: `hub:${hub.id}`, x: point.x, y: point.y, r: layout.hubRingRadius + 42 } : null;
      })
      .filter(Boolean),
  ];
  for (let pass = 0; pass < 90; pass += 1) {
    let changed = false;
    for (const entry of entries) {
      for (const obstacle of staticObstacles) {
        changed = pushRoutePointAway(
          entry,
          obstacle,
          entry.r + obstacle.r,
          bounds,
        ) || changed;
      }
    }
    for (let left = 0; left < entries.length; left += 1) {
      for (let right = left + 1; right < entries.length; right += 1) {
        const a = entries[left];
        const b = entries[right];
        const dx = b.point.x - a.point.x;
        const dy = b.point.y - a.point.y;
        let distance = Math.hypot(dx, dy);
        const minDistance = a.r + b.r + 22;
        if (distance >= minDistance) {
          continue;
        }
        let nx = dx / (distance || 1);
        let ny = dy / (distance || 1);
        if (!distance) {
          const seed = hashUnit(`${a.key}:${b.key}`, "split") * Math.PI * 2;
          nx = Math.cos(seed);
          ny = Math.sin(seed);
          distance = 0.01;
        }
        const push = (minDistance - distance) / 2;
        a.point.x -= nx * push;
        a.point.y -= ny * push;
        b.point.x += nx * push;
        b.point.y += ny * push;
        clampRoutePoint(a.point, a.r, bounds);
        clampRoutePoint(b.point, b.r, bounds);
        changed = true;
      }
    }
    if (!changed) {
      break;
    }
  }
  const points = new Map(entries.map((entry) => [entry.key, entry.point]));
  const obstacles = entries.map((entry) => ({
    key: entry.key,
    x: entry.point.x,
    y: entry.point.y,
    r: entry.r + 8,
  }));
  return { points, obstacles };
}

function closeInspector() {
  els.graphInspector.hidden = true;
  clear(els.graphInspector);
  document.body.classList.remove("inspector-open");
}

function inspectorHeader(title, subtitle) {
  const head = document.createElement("div");
  head.className = "inspector-head";
  const copy = document.createElement("div");
  copy.appendChild(textEl("strong", title));
  copy.appendChild(textEl("span", subtitle));
  const close = document.createElement("button");
  close.type = "button";
  close.className = "icon-button";
  close.textContent = "x";
  close.addEventListener("click", closeInspector);
  head.appendChild(copy);
  head.appendChild(close);
  return head;
}

function showInspector(title, subtitle, body) {
  clear(els.graphInspector);
  const card = document.createElement("section");
  card.className = "inspector-card";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-label", title);
  card.appendChild(inspectorHeader(title, subtitle));
  card.appendChild(body);
  els.graphInspector.appendChild(card);
  els.graphInspector.hidden = false;
  document.body.classList.add("inspector-open");
}

function nodeLinks(nodeId) {
  const { nodesById, edges } = state.graphIndex;
  return edges
    .filter((edge) => edge.source === nodeId || edge.target === nodeId)
    .map((edge) => {
      const otherId = edge.source === nodeId ? edge.target : edge.source;
      const other = nodesById.get(otherId);
      return { edge, nodeId: otherId, node: other };
    })
    .filter((item) => item.node)
    .slice(0, 8);
}

function showHubInspectorById(hubId) {
  const hub = hubById.get(hubId);
  const layout = state.graphLayout;
  if (!hub || !layout) {
    return;
  }
  showHubInspector(
    hub,
    layout.tablesByName || new Map(),
    layout.latestEdgesByTable || new Map(),
    state.graphIndex.activeRoutes || [],
  );
}

function showObjectInspector(nodeId, fallback = null) {
  const { nodesById } = state.graphIndex;
  const node = nodesById.get(nodeId) || fallback;
  if (!node) {
    return;
  }
  const table = node.table || node.kind || nodeTable(nodeId);
  const hubId = hubForTable(table);
  const hub = hubById.get(hubId);
  const body = document.createElement("div");
  body.className = "inspector-body";
  const facts = document.createElement("dl");
  const pairs = [
    ["family", hub?.label || hubId || "-"],
    ["type", table || "-"],
    ["id", node.detail || node.id || nodeId],
    ["status", node.status || "-"],
    ["updated", node.updated_at ? dateLabel(node.updated_at) : "-"],
  ];
  for (const [key, value] of pairs) {
    facts.appendChild(textEl("dt", key));
    facts.appendChild(textEl("dd", clip(value, 180)));
  }
  body.appendChild(facts);
  if (hub) {
    const actions = document.createElement("div");
    actions.className = "inspector-actions";
    const familyButton = document.createElement("button");
    familyButton.type = "button";
    familyButton.className = "ghost";
    familyButton.textContent = `Open ${hub.label} family`;
    familyButton.addEventListener("click", () => showHubInspectorById(hub.id));
    actions.appendChild(familyButton);
    body.appendChild(actions);
  }
  const links = nodeLinks(nodeId);
  if (links.length) {
    body.appendChild(textEl("h3", "Linked graph nodes"));
    const list = document.createElement("div");
    list.className = "inspector-list compact";
    for (const link of links) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "inspector-row";
      button.appendChild(textEl("strong", clip(link.node.label || link.nodeId, 80)));
      button.appendChild(textEl("span", `${link.edge.label || "linked"} / ${link.node.kind || nodeTable(link.nodeId)}`));
      button.addEventListener("click", () => showObjectInspector(link.nodeId));
      list.appendChild(button);
    }
    body.appendChild(list);
  }
  showInspector(clip(node.label || nodeId, 120), node.kind || nodeTable(nodeId), body);
}

function hubObjects(hub, tablesByName, latestEdgesByTable, activeRoutes) {
  const { nodesById, activeRouteIds } = state.graphIndex;
  const rows = [];
  for (const route of activeRoutes.filter((item) => item.hubId === hub.id)) {
    rows.push({
      id: route.nodeId,
      label: route.label || route.id,
      detail: route.relation || route.table,
      used: true,
      updated_at: route.updated_at,
    });
  }
  for (const table of hub.tables) {
    const tableNode = tablesByName.get(table);
    if (!tableNode) {
      continue;
    }
    for (const edge of latestEdgesByTable.get(tableNode.id) || []) {
      const node = nodesById.get(edge.target);
      if (!node || rows.some((row) => row.id === node.id)) {
        continue;
      }
      rows.push({
        id: node.id,
        label: node.label,
        detail: `${table} / ${node.status || "latest"}`,
        used: activeRouteIds.has(node.id),
        updated_at: node.updated_at,
      });
    }
  }
  return rows
    .sort((a, b) => Number(b.used) - Number(a.used) || String(b.updated_at || "").localeCompare(String(a.updated_at || "")))
    .slice(0, 18);
}

function showHubInspector(hub, tablesByName, latestEdgesByTable, activeRoutes) {
  const count = hub.tables.reduce((total, table) => total + Number(tablesByName.get(table)?.count || 0), 0);
  const body = document.createElement("div");
  body.className = "inspector-body";
  body.appendChild(textEl("p", `${fmt(count)} objects across ${hub.tables.join(", ")}.`));
  const rows = hubObjects(hub, tablesByName, latestEdgesByTable, activeRoutes);
  if (!rows.length) {
    body.appendChild(textEl("p", "No recent objects in this family.", "empty"));
  } else {
    const list = document.createElement("div");
    list.className = "inspector-list";
    for (const row of rows) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `inspector-row ${row.used ? "used" : ""}`;
      button.appendChild(textEl("strong", clip(row.label || row.id, 90)));
      button.appendChild(textEl("span", `${row.used ? "used in current route" : "recent object"} / ${clip(row.detail || row.id, 90)}`));
      button.addEventListener("click", () => showObjectInspector(row.id, row));
      list.appendChild(button);
    }
    body.appendChild(list);
  }
  showInspector(hub.label, hub.detail, body);
}

function showWorkspaceInspector(workspace, nodes, edges, tablesByName, activeRoutes) {
  const body = document.createElement("div");
  body.className = "inspector-body";
  body.appendChild(textEl("p", "Start point for the live memory graph. Open a family to inspect recent objects and currently used nodes."));
  const facts = document.createElement("dl");
  const pairs = [
    ["workspace", workspace.label || workspace.id || "-"],
    ["nodes", fmt(nodes.length)],
    ["links", fmt(edges.length)],
    ["active route objects", fmt(activeRoutes.length)],
  ];
  for (const [key, value] of pairs) {
    facts.appendChild(textEl("dt", key));
    facts.appendChild(textEl("dd", value));
  }
  body.appendChild(facts);
  const list = document.createElement("div");
  list.className = "inspector-list compact";
  for (const hub of semanticHubs) {
    const count = hub.tables.reduce((total, table) => total + Number(tablesByName.get(table)?.count || 0), 0);
    const used = activeRoutes.some((route) => route.hubId === hub.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `inspector-row ${used ? "used" : ""}`;
    button.appendChild(textEl("strong", hub.label));
    button.appendChild(textEl("span", `${fmt(count)} objects / ${used ? "used in current route" : hub.detail}`));
    button.addEventListener("click", () => showHubInspectorById(hub.id));
    list.appendChild(button);
  }
  body.appendChild(textEl("h3", "Memory families"));
  body.appendChild(list);
  showInspector(workspace.label || "Workspace", "project start point", body);
}

function graphInteractiveNode(target) {
  if (!(target instanceof Element)) {
    return null;
  }
  return target.closest(".semantic-root, .semantic-hub, .semantic-object");
}

function routeFallbackForNode(nodeId) {
  const route = [
    ...(state.graphIndex.activeRoutes || []),
    ...state.lastGraphRoutes,
    ...state.retiringRoutes,
  ].find((item) => item.nodeId === nodeId);
  if (!route) {
    return null;
  }
  return {
    id: route.nodeId,
    label: route.label,
    kind: route.table,
    detail: route.id || route.nodeId,
    status: route.relation,
    updated_at: route.updated_at,
  };
}

function showWorkspaceInspectorFromLayout() {
  const layout = state.graphLayout;
  if (!layout?.workspace) {
    return;
  }
  showWorkspaceInspector(
    layout.workspace,
    [...(layout.nodesById || new Map()).values()],
    layout.edges || [],
    layout.tablesByName || new Map(),
    state.graphIndex.activeRoutes || [],
  );
}

function showGraphNodeInspectorFromElement(nodeElement) {
  const nodeId = nodeElement?.dataset?.nodeId || "";
  if (!nodeId) {
    return;
  }
  if (nodeElement.classList.contains("semantic-root")) {
    showWorkspaceInspectorFromLayout();
    return;
  }
  if (nodeElement.classList.contains("semantic-hub")) {
    showHubInspectorById(nodeId.replace(/^hub:/, ""));
    return;
  }
  if (nodeElement.classList.contains("semantic-object")) {
    showObjectInspector(nodeId, routeFallbackForNode(nodeId));
  }
}

function clearGraphRouteLayer(svg) {
  svg
    .querySelectorAll(".semantic-link.root-link, .semantic-link.object-link, .semantic-link.route-link, .semantic-object.active, .semantic-object.retiring, .live-path-pulse")
    .forEach((node) => node.remove());
  svg.querySelectorAll(".graph-route-layer, .graph-pulse-layer, .graph-object-layer").forEach((node) => node.remove());
}

function ensureGraphLayer(svg, className, placement = "front") {
  let layer = svg.querySelector(`.${className}`);
  if (!layer) {
    layer = svgEl("g", { class: className });
  }
  if (placement === "back") {
    const foreground = svg.querySelector(".semantic-root, .semantic-hub");
    if (foreground) {
      svg.insertBefore(layer, foreground);
    } else if (!layer.parentNode) {
      svg.appendChild(layer);
    }
    return layer;
  }
  svg.appendChild(layer);
  return layer;
}

function renderGraphRouteLayer() {
  const layout = state.graphLayout;
  const svg = els.graph;
  if (!layout || !svg) {
    return false;
  }
  pruneGraphEffects();
  clearGraphRouteLayer(svg);
  const {
    nodesById,
    edges,
    positions,
    root,
    rootRadius,
    hubNodeRadius,
    hubRingRadius,
    width,
    height,
    paddingX,
    paddingY,
  } = layout;
  const routeLayer = ensureGraphLayer(svg, "graph-route-layer", "back");
  const pulseLayer = ensureGraphLayer(svg, "graph-pulse-layer", "back");
  const objectLayer = ensureGraphLayer(svg, "graph-object-layer");
  const signal = activeGraphSignal(nodesById);
  const activeRoutes = activeContextRoutes(nodesById).slice(0, 22);
  const activeHubIds = new Set(activeRoutes.map((route) => route.hubId));
  if (state.activeGraphJob) {
    for (const route of state.activeGraphJob.routes) {
      activeHubIds.add(route.hubId);
    }
  }
  const highlightedHubIds = new Set(activeHubIds);
  for (const route of state.retiringRoutes) {
    highlightedHubIds.add(route.hubId);
  }
  if (signal.hubId) {
    activeHubIds.add(signal.hubId);
    highlightedHubIds.add(signal.hubId);
  }
  svg.querySelectorAll(".semantic-hub").forEach((hubNode) => {
    const hubId = String(hubNode.dataset.nodeId || "").replace(/^hub:/, "");
    hubNode.classList.toggle("active", highlightedHubIds.has(hubId));
  });
  const routesByHub = new Map();
  for (const route of activeRoutes) {
    if (!routesByHub.has(route.hubId)) {
      routesByHub.set(route.hubId, []);
    }
    routesByHub.get(route.hubId).push(route);
  }
  const renderedRoutesByHub = new Map();
  for (const hub of semanticHubs) {
    const routes = [
      ...(routesByHub.get(hub.id) || []),
      ...state.retiringRoutes.filter((route) => route.hubId === hub.id),
    ];
    if (routes.length) {
      renderedRoutesByHub.set(hub.id, routes);
    }
  }
  const routeLayout = buildRouteObjectLayout(renderedRoutesByHub, positions, {
    width,
    height,
    paddingX,
    paddingY,
    root,
    rootRadius,
    hubRingRadius,
  });
  state.graphIndex = {
    nodesById,
    edges,
    activeRoutes,
    activeRouteIds: new Set(activeRoutes.map((route) => route.nodeId)),
  };
  const activeObjectPositions = [];
  for (const hub of semanticHubs) {
    const hubPos = positions.get(`hub:${hub.id}`);
    if (!hubPos) {
      continue;
    }
    const retiringHubRoutes = state.retiringRoutes.filter((route) => route.hubId === hub.id);
    const hubRoutes = routesByHub.get(hub.id) || [];
    retiringHubRoutes.forEach((route, index) => {
      const routeKey = routePositionKey(route, `${hub.id}:retiring:${index}`);
      const pos = routeLayout.points.get(routeKey) || routeObjectPoint(hub, hubPos, index, retiringHubRoutes, { width, height, paddingX, paddingY });
      routeLayer.appendChild(
        svgEl("path", {
          d: continuousRoutePath(root, hubPos, pos, rootRadius, 18, {
            hubRadius: hubRingRadius,
            laneOffset: index - (retiringHubRoutes.length - 1) / 2,
            obstacles: routeLayout.obstacles,
            routeKey,
            targetKey: routeKey,
          }),
          class: "semantic-link route-link retiring-path",
          pathLength: "1",
        }),
      );
      const object = svgEl("g", { class: "semantic-object retiring" });
      object.style.setProperty("--hub-color", hub.color);
      object.dataset.nodeId = route.nodeId;
      object.appendChild(svgEl("circle", { cx: pos.x, cy: pos.y, r: 17 }));
      const badge = svgEl("text", {
        x: pos.x,
        y: pos.y + 4,
        "text-anchor": "middle",
        class: "semantic-object-id",
      });
      badge.textContent = objectNodeBadge(route.nodeId);
      object.appendChild(badge);
      objectLayer.appendChild(object);
    });
    hubRoutes.forEach((route, index) => {
      const routeKey = routePositionKey(route, `${hub.id}:active:${index}`);
      const pos = routeLayout.points.get(routeKey) || routeObjectPoint(hub, hubPos, index, hubRoutes, { width, height, paddingX, paddingY });
      const entering = state.graphEffects.get(`object:${route.nodeId}`)?.kind === "entering";
      const routeOnly = route.phase === "route";
      const animationKey = routeAnimationKey("route", hub.id, route);
      const routeAnimated = routeOnly || shouldAnimateRoute(animationKey);
      state.animatedRouteKeys.add(animationKey);
      routeLayer.appendChild(
        svgEl("path", {
          d: continuousRoutePath(root, hubPos, pos, rootRadius, 18, {
            hubRadius: hubRingRadius,
            laneOffset: index - (hubRoutes.length - 1) / 2,
            obstacles: routeLayout.obstacles,
            routeKey,
            targetKey: routeKey,
          }),
          class: `semantic-link route-link active-path ${routeAnimated ? "live-forward" : "steady-path"} ${entering ? "entering-path" : ""}`,
          pathLength: "1",
        }),
      );
      if (routeOnly) {
        return;
      }
      activeObjectPositions.push(pos);
      const object = svgEl("g", {
        class: `semantic-object active ${entering ? "entering" : ""}`,
      });
      object.style.setProperty("--hub-color", hub.color);
      object.dataset.nodeId = route.nodeId;
      object.appendChild(svgEl("circle", { cx: pos.x, cy: pos.y, r: 17 }));
      const badge = svgEl("text", {
        x: pos.x,
        y: pos.y + 4,
        "text-anchor": "middle",
        class: "semantic-object-id",
      });
      badge.textContent = objectNodeBadge(route.nodeId);
      object.appendChild(badge);
      const titleNode = svgEl("title");
      titleNode.textContent = `${route.table}: ${route.label || route.id}`;
      object.appendChild(titleNode);
      object.addEventListener("click", (event) => {
        event.stopPropagation();
        showObjectInspector(route.nodeId, {
          id: route.nodeId,
          label: route.label,
          kind: route.table,
          detail: route.id || route.nodeId,
          status: route.relation,
          updated_at: route.updated_at,
        });
      });
      objectLayer.appendChild(object);
    });
  }
  const statusHubId = activeRoutes[0]?.hubId || signal.hubId;
  if (statusHubId) {
    const hub = hubById.get(statusHubId);
    const hubPos = positions.get(`hub:${statusHubId}`);
    if (hub && hubPos) {
      pulseLayer.appendChild(svgEl("circle", { cx: hubPos.x, cy: hubPos.y, r: hubRingRadius + 2, class: "live-path-pulse" }));
      activeObjectPositions.forEach((pos) => {
        pulseLayer.appendChild(svgEl("circle", { cx: pos.x, cy: pos.y, r: 24, class: "live-path-pulse object" }));
      });
    }
  }
  return true;
}

function renderStructuralGraph(graph) {
  state.graphRendering = true;
  const svg = els.graph;
  clear(svg);
  pruneGraphEffects();
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const { width, height } = graphWorldSize();
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  setGraphZoom(state.graphZoom, { keepCenter: false });
  const workspace = nodes.find((node) => node.kind === "workspace");
  if (!workspace) {
    state.graphLayout = null;
    state.graphRendering = false;
    return;
  }
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const tables = nodes.filter((node) => node.kind === "table");
  const tablesByName = new Map(tables.map((table) => [tableNodeName(table.id), table]));
  const latestEdgesByTable = new Map();
  const tableIds = new Set(tables.map((node) => node.id));
  for (const edge of edges) {
    if (edge.kind === "latest" && tableIds.has(edge.source)) {
      if (!latestEdgesByTable.has(edge.source)) {
        latestEdgesByTable.set(edge.source, []);
      }
      latestEdgesByTable.get(edge.source).push(edge);
    }
  }
  const signal = activeGraphSignal(nodesById);
  const activeRoutes = activeContextRoutes(nodesById).slice(0, 22);
  const activeHubIds = new Set(activeRoutes.map((route) => route.hubId));
  if (state.activeGraphJob) {
    for (const route of state.activeGraphJob.routes) {
      activeHubIds.add(route.hubId);
    }
  }
  if (signal.hubId) {
    activeHubIds.add(signal.hubId);
  }
  const routesByHub = new Map();
  for (const route of activeRoutes) {
    if (!routesByHub.has(route.hubId)) {
      routesByHub.set(route.hubId, []);
    }
    routesByHub.get(route.hubId).push(route);
  }
  state.graphIndex = {
    nodesById,
    edges,
    activeRoutes,
    activeRouteIds: new Set(activeRoutes.map((route) => route.nodeId)),
  };
  const root = { x: width / 2, y: height / 2 };
  const minDimension = Math.min(width, height);
  const rootRadius = clamp(minDimension * 0.06, 60, 82);
  const hubNodeRadius = clamp(minDimension * 0.05, 56, 66);
  const hubRingRadius = hubNodeRadius + 18;
  const paddingX = width * 0.1;
  const paddingY = height * 0.1;
  const objectReserve = Math.max(170, hubRingRadius + 92);
  const maxHubRadiusX = width / 2 - paddingX - hubRingRadius - objectReserve;
  const maxHubRadiusY = height / 2 - paddingY - hubRingRadius - objectReserve;
  const hubRadiusX = clamp(width * 0.31, 320, Math.max(320, maxHubRadiusX));
  const hubRadiusY = clamp(height * 0.3, 300, Math.max(300, maxHubRadiusY));
  const positions = new Map([[workspace.id, root]]);
  const activeObjectPositions = [];

  svg.appendChild(svgEl("rect", { x: 18, y: 18, width: width - 36, height: height - 36, rx: 38, class: "graph-shell" }));
  const routeLayer = svgEl("g", { class: "graph-route-layer" });
  const pulseLayer = svgEl("g", { class: "graph-pulse-layer" });
  const objectLayer = svgEl("g", { class: "graph-object-layer" });
  svg.appendChild(routeLayer);
  svg.appendChild(pulseLayer);

  const rootGroup = svgEl("g", { class: "semantic-root" });
  rootGroup.dataset.nodeId = workspace.id;
  rootGroup.appendChild(svgEl("circle", { cx: root.x, cy: root.y, r: rootRadius }));
  rootGroup.appendChild(svgEl("circle", { cx: root.x, cy: root.y, r: rootRadius + 26, class: "semantic-root-ring" }));
  const rootTitle = svgEl("text", { x: root.x, y: root.y - 8, "text-anchor": "middle", class: "semantic-root-title" });
  rootTitle.textContent = workspace.label;
  rootGroup.appendChild(rootTitle);
  const rootMeta = svgEl("text", { x: root.x, y: root.y + 20, "text-anchor": "middle", class: "semantic-root-meta" });
  rootMeta.textContent = "start point";
  rootGroup.appendChild(rootMeta);
  rootGroup.addEventListener("click", (event) => {
    event.stopPropagation();
    showWorkspaceInspector(workspace, nodes, edges, tablesByName, activeRoutes);
  });
  svg.appendChild(rootGroup);

  for (const hub of semanticHubs) {
    const angle = (hub.angle * Math.PI) / 180;
    const hubPos = {
      x: root.x + Math.cos(angle) * hubRadiusX,
      y: root.y + Math.sin(angle) * hubRadiusY,
    };
    positions.set(`hub:${hub.id}`, hubPos);
  }
  const renderedRoutesByHub = new Map();
  for (const hub of semanticHubs) {
    const routes = [
      ...(routesByHub.get(hub.id) || []),
      ...state.retiringRoutes.filter((route) => route.hubId === hub.id),
    ];
    if (routes.length) {
      renderedRoutesByHub.set(hub.id, routes);
    }
  }
  const routeLayout = buildRouteObjectLayout(renderedRoutesByHub, positions, {
    width,
    height,
    paddingX,
    paddingY,
    root,
    rootRadius,
    hubRingRadius,
  });

  for (const hub of semanticHubs) {
    const hubPos = positions.get(`hub:${hub.id}`);
    if (!hubPos) {
      continue;
    }
    const active = activeHubIds.has(hub.id);
    const retiringHubRoutes = state.retiringRoutes.filter((route) => route.hubId === hub.id);
    const hubGroup = svgEl("g", { class: `semantic-hub ${active ? "active" : ""}` });
    hubGroup.style.setProperty("--hub-color", hub.color);
    hubGroup.dataset.nodeId = `hub:${hub.id}`;
    hubGroup.appendChild(svgEl("circle", { cx: hubPos.x, cy: hubPos.y, r: hubNodeRadius }));
    hubGroup.appendChild(svgEl("circle", { cx: hubPos.x, cy: hubPos.y, r: hubRingRadius, class: "semantic-hub-ring" }));
    const title = svgEl("text", { x: hubPos.x, y: hubPos.y - 8, "text-anchor": "middle", class: "semantic-hub-title" });
    title.textContent = hub.label;
    hubGroup.appendChild(title);
    const count = hub.tables.reduce((total, table) => total + Number(tablesByName.get(table)?.count || 0), 0);
    const countText = svgEl("text", { x: hubPos.x, y: hubPos.y + 18, "text-anchor": "middle", class: "semantic-hub-count" });
    countText.textContent = fmt(count);
    hubGroup.appendChild(countText);
    const detail = svgEl("text", { x: hubPos.x, y: hubPos.y + 40, "text-anchor": "middle", class: "semantic-hub-detail" });
    detail.textContent = hub.detail;
    hubGroup.appendChild(detail);
    hubGroup.addEventListener("click", (event) => {
      event.stopPropagation();
      showHubInspector(hub, tablesByName, latestEdgesByTable, activeRoutes);
    });
    svg.appendChild(hubGroup);

    const hubRoutes = routesByHub.get(hub.id) || [];
    retiringHubRoutes.forEach((route, index) => {
      const routeKey = routePositionKey(route, `${hub.id}:retiring:${index}`);
      const pos = routeLayout.points.get(routeKey) || routeObjectPoint(hub, hubPos, index, retiringHubRoutes, { width, height, paddingX, paddingY });
      const objectLink = svgEl("path", {
        d: continuousRoutePath(root, hubPos, pos, rootRadius, 18, {
          hubRadius: hubRingRadius,
          laneOffset: index - (retiringHubRoutes.length - 1) / 2,
          obstacles: routeLayout.obstacles,
          routeKey,
          targetKey: routeKey,
        }),
        class: "semantic-link route-link retiring-path",
        pathLength: "1",
      });
      routeLayer.appendChild(objectLink);
      const object = svgEl("g", {
        class: "semantic-object retiring",
      });
      object.style.setProperty("--hub-color", hub.color);
      object.dataset.nodeId = route.nodeId;
      object.appendChild(svgEl("circle", { cx: pos.x, cy: pos.y, r: 17 }));
      const badge = svgEl("text", {
        x: pos.x,
        y: pos.y + 4,
        "text-anchor": "middle",
        class: "semantic-object-id",
      });
      badge.textContent = objectNodeBadge(route.nodeId);
      object.appendChild(badge);
      objectLayer.appendChild(object);
    });
    hubRoutes.forEach((route, index) => {
      const routeKey = routePositionKey(route, `${hub.id}:active:${index}`);
      const pos = routeLayout.points.get(routeKey) || routeObjectPoint(hub, hubPos, index, hubRoutes, { width, height, paddingX, paddingY });
      const entering = state.graphEffects.get(`object:${route.nodeId}`)?.kind === "entering";
      const routeOnly = route.phase === "route";
      const animationKey = routeAnimationKey("route", hub.id, route);
      const routeAnimated = routeOnly || shouldAnimateRoute(animationKey);
      state.animatedRouteKeys.add(animationKey);
      const objectLink = svgEl("path", {
        d: continuousRoutePath(root, hubPos, pos, rootRadius, 18, {
          hubRadius: hubRingRadius,
          laneOffset: index - (hubRoutes.length - 1) / 2,
          obstacles: routeLayout.obstacles,
          routeKey,
          targetKey: routeKey,
        }),
        class: `semantic-link route-link active-path ${routeAnimated ? "live-forward" : "steady-path"} ${entering ? "entering-path" : ""}`,
        pathLength: "1",
      });
      routeLayer.appendChild(objectLink);
      if (routeOnly) {
        return;
      }
      activeObjectPositions.push(pos);
      const object = svgEl("g", {
        class: `semantic-object active ${entering ? "entering" : ""}`,
      });
      object.style.setProperty("--hub-color", hub.color);
      object.dataset.nodeId = route.nodeId;
      object.appendChild(svgEl("circle", { cx: pos.x, cy: pos.y, r: 17 }));
      const badge = svgEl("text", {
        x: pos.x,
        y: pos.y + 4,
        "text-anchor": "middle",
        class: "semantic-object-id",
      });
      badge.textContent = objectNodeBadge(route.nodeId);
      object.appendChild(badge);
      const label = svgEl("text", {
        x: pos.x,
        y: pos.y + 36,
        "text-anchor": "middle",
        class: "semantic-object-label active-label",
      });
      label.textContent = objectLabel({ id: route.nodeId, label: route.label, detail: route.id || route.nodeId });
      object.appendChild(label);
      const titleNode = svgEl("title");
      titleNode.textContent = `${route.table}: ${route.label || route.id}`;
      object.appendChild(titleNode);
      object.addEventListener("click", (event) => {
        event.stopPropagation();
        showObjectInspector(route.nodeId, {
          id: route.nodeId,
          label: route.label,
          kind: route.table,
          detail: route.id || route.nodeId,
          status: route.relation,
          updated_at: route.updated_at,
        });
      });
      objectLayer.appendChild(object);
    });
  }
  svg.appendChild(objectLayer);

  state.graphLayout = {
    workspace,
    nodesById,
    edges,
    tablesByName,
    latestEdgesByTable,
    positions,
    root,
    rootRadius,
    hubNodeRadius,
    hubRingRadius,
    width,
    height,
    paddingX,
    paddingY,
  };

  const statusHubId = activeRoutes[0]?.hubId || signal.hubId;
  if (statusHubId) {
    const hub = hubById.get(statusHubId);
    const hubPos = positions.get(`hub:${statusHubId}`);
    if (hub && hubPos) {
      const pulse = svgEl("circle", { cx: hubPos.x, cy: hubPos.y, r: hubRingRadius + 2, class: "live-path-pulse" });
      pulseLayer.appendChild(pulse);
      activeObjectPositions.forEach((pos) => {
        pulseLayer.appendChild(svgEl("circle", { cx: pos.x, cy: pos.y, r: 24, class: "live-path-pulse object" }));
      });
    }
  }
  state.graphRendering = false;
}

function renderTaskGraph() {
  const svg = els.taskGraph;
  clear(svg);
  svg.setAttribute("viewBox", "0 0 1000 420");
  const graph = state.taskGraph;
  if (!graph.nodes.length) {
    els.taskGraphMeta.textContent = "Run Search or Explain. This panel will show which memories entered the agent context and which were rejected.";
    svg.appendChild(svgEl("rect", { x: 18, y: 18, width: 964, height: 384, rx: 28, class: "graph-shell" }));
    const text = svgEl("text", { x: 500, y: 210, "text-anchor": "middle", class: "placeholder-text" });
    text.textContent = "No selected request yet";
    svg.appendChild(text);
    return;
  }
  els.taskGraphMeta.textContent = `${graph.mode}: green items entered context; gray items were found but left out. Query: ${clip(graph.query, 90)}`;
  svg.appendChild(svgEl("rect", { x: 18, y: 18, width: 964, height: 384, rx: 28, class: "graph-shell" }));
  const queryPos = { x: 120, y: 210 };
  const positions = new Map([["query", queryPos]]);
  const usedNodes = graph.nodes.filter((node) => node.kind === "used").slice(0, 6);
  const excludedNodes = graph.nodes.filter((node) => node.kind !== "used").slice(0, 8);
  usedNodes.forEach((node, index) => {
    positions.set(node.id, { x: 425, y: 115 + index * 56 });
  });
  excludedNodes.forEach((node, index) => {
    positions.set(node.id, { x: 745, y: 82 + index * 42 });
  });
  const usedHeader = svgEl("text", { x: 425, y: 58, "text-anchor": "middle", class: "lane-title used" });
  usedHeader.textContent = "Used by agent";
  svg.appendChild(usedHeader);
  const excludedHeader = svgEl("text", {
    x: 745,
    y: 58,
    "text-anchor": "middle",
    class: "lane-title excluded",
  });
  excludedHeader.textContent = "Found but not used";
  svg.appendChild(excludedHeader);
  for (const edge of graph.edges) {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) {
      continue;
    }
    svg.appendChild(
      svgEl("line", {
        x1: source.x,
        y1: source.y,
        x2: target.x,
        y2: target.y,
        class: edge.kind === "used" ? "task-edge used" : "task-edge excluded",
      }),
    );
  }
  const query = svgEl("g", { class: "task-node query" });
  query.appendChild(svgEl("circle", { cx: queryPos.x, cy: queryPos.y, r: 42 }));
  const queryLabel = svgEl("text", { x: queryPos.x, y: queryPos.y + 62, "text-anchor": "middle" });
  queryLabel.textContent = "Query";
  query.appendChild(queryLabel);
  svg.appendChild(query);
  for (const node of [...usedNodes, ...excludedNodes]) {
    const pos = positions.get(node.id);
    if (!pos) {
      continue;
    }
    const group = svgEl("g", { class: `task-node ${node.kind} box` });
    group.appendChild(
      svgEl("rect", {
        x: pos.x - 95,
        y: pos.y - 19,
        width: 190,
        height: 38,
        rx: 12,
      }),
    );
    const label = svgEl("text", { x: pos.x, y: pos.y + 5, "text-anchor": "middle" });
    label.textContent = clip(node.label, 26);
    group.appendChild(label);
    svg.appendChild(group);
  }
}

function renderContextSummaryFromExplain(body, duration) {
  clear(els.contextSummary);
  const included = new Set(body.included_ids || []);
  const scored = body.scored_candidates || [];
  const used = scored.filter((item) => included.has(item.id));
  const excluded = scored.filter((item) => !included.has(item.id));
  const top = document.createElement("div");
  top.className = "summary-strip";
  top.appendChild(textEl("span", `${used.length} used by agent`));
  top.appendChild(textEl("span", `${excluded.length} found but not used`));
  top.appendChild(textEl("span", `${body.context_tokens || 0} context tokens`));
  top.appendChild(textEl("span", `${duration}ms`));
  els.contextSummary.appendChild(top);
  const list = document.createElement("div");
  list.className = "context-list";
  for (const item of used.slice(0, 6)) {
    const row = document.createElement("article");
    row.className = "context-hit used";
    row.appendChild(textEl("strong", "Used by agent"));
    row.appendChild(textEl("span", clip(item.path || item.metadata?.kind || item.id, 80)));
    row.appendChild(textEl("em", clip(item.reason || "Included in context budget", 100)));
    list.appendChild(row);
  }
  for (const item of excluded.slice(0, 4)) {
    const row = document.createElement("article");
    row.className = "context-hit excluded";
    row.appendChild(textEl("strong", "Found but not used"));
    row.appendChild(textEl("span", clip(item.path || item.metadata?.kind || item.id, 80)));
    row.appendChild(textEl("em", clip(item.reason || "Excluded from context budget", 100)));
    list.appendChild(row);
  }
  els.contextSummary.appendChild(list);
}

function contextObjectsFromSectionCounts(body) {
  const counts = body.section_counts || {};
  const scored = body.scored_candidates || [];
  const included = new Set(body.included_ids || []);
  const chunkObjects = scored
    .filter((item) => included.has(item.id))
    .slice(0, 8)
    .map((item, index) =>
      normalizeContextObject(
        {
          table: "chunks",
          id: item.id,
          label: item.path || item.metadata?.kind || item.id,
          relation: "retrieved chunk",
          updated_at: item.metadata?.created_at || item.metadata?.updated_at || "",
          rank: index,
        },
        index,
      ),
    );
  const countSections = [
    ["behavior_instructions", "behavior_instructions", "instructions"],
    ["decisions", "decisions", "decisions"],
    ["theories", "theories", "theories"],
    ["experiments", "research_experiments", "experiments"],
    ["insights", "research_insights", "insights"],
    ["concepts", "domain_concepts", "concepts"],
    ["snapshots", "memory_snapshots", "snapshots"],
    ["roles", "agent_roles", "roles"],
    ["skills", "agent_skills", "skills"],
    ["playbooks", "agent_playbooks", "playbooks"],
    ["rules", "procedural_rules", "rules"],
    ["facts", "retrieved_facts", "facts"],
  ];
  const sectionObjects = countSections
    .filter(([key]) => Number(counts[key] || 0) > 0)
    .map(([key, table, relation], index) =>
      normalizeContextObject(
        {
          table,
          id: `${key}_${counts[key]}`,
          label: `${fmt(counts[key])} ${relation}`,
          relation,
          rank: chunkObjects.length + index,
        },
        chunkObjects.length + index,
      ),
    );
  return [...sectionObjects, ...chunkObjects];
}

function setTaskGraphFromExplain(body) {
  const included = new Set(body.included_ids || []);
  const candidates = (body.scored_candidates || []).slice(0, 18);
  const nodes = candidates.map((item) => ({
    id: item.id,
    kind: included.has(item.id) ? "used" : "excluded",
    label: item.path || item.metadata?.kind || item.metadata?.episode_id || "Memory",
    detail: item.path || item.metadata?.kind || item.id,
  }));
  state.taskGraph = {
    query: body.query || els.query.value,
    mode: "Explain",
    nodes,
    edges: nodes.map((node) => ({
      source: "query",
      target: node.id,
      kind: node.kind,
      label: node.kind === "used" ? "included" : "excluded",
    })),
    contextObjects: (body.used_context_objects?.length ? body.used_context_objects : contextObjectsFromSectionCounts(body)).map(
      (item, index) => normalizeContextObject(item, index),
    ),
  };
}

function setTaskGraphFromSearch(body, query) {
  const nodes = (body.hits || []).slice(0, 12).map((hit) => ({
    id: hit.chunk_id,
    kind: "used",
    label: hit.path || hit.summary || "Exact memory match",
    detail: hit.path || hit.summary || hit.chunk_id,
  }));
  state.taskGraph = {
    query,
    mode: "Search",
    nodes,
    edges: nodes.map((node) => ({
      source: "query",
      target: node.id,
      kind: "used",
      label: "matched",
    })),
    contextObjects: (body.hits || []).slice(0, 12).map((hit, index) =>
      normalizeContextObject(
        {
          table: "chunks",
          id: hit.chunk_id || hit.id,
          label: hit.path || hit.summary || "Exact memory match",
          relation: "search hit",
          updated_at: hit.metadata?.created_at || hit.metadata?.updated_at || "",
          rank: index,
        },
        index,
      ),
    ),
  };
}

function renderSearchResults(body, duration, query) {
  clear(els.searchSummary);
  clear(els.searchResults);
  const hits = body.hits || [];
  els.searchSummary.appendChild(textEl("span", `${hits.length} exact matches`));
  els.searchSummary.appendChild(textEl("span", `${duration}ms`));
  els.searchSummary.appendChild(textEl("span", clip(query, 80)));
  if (!hits.length) {
    els.searchResults.appendChild(textEl("p", "No exact matches. Try Explain for semantic retrieval.", "empty"));
    return;
  }
  for (const hit of hits.slice(0, 10)) {
    const card = document.createElement("article");
    card.className = "result-card";
    card.appendChild(textEl("strong", clip(hit.summary || hit.path || "Memory chunk", 90)));
    card.appendChild(textEl("span", clip(hit.text, 180)));
    card.appendChild(textEl("em", clip(hit.path || hit.chunk_id, 90)));
    els.searchResults.appendChild(card);
  }
}

async function searchMemory() {
  const query = els.query.value.trim();
  if (!query) {
    return;
  }
  clear(els.searchSummary);
  els.searchSummary.appendChild(textEl("span", "Searching memory..."));
  const started = performance.now();
  try {
    const body = await requestJson("/memory/search", {
      workspace_id: requireWorkspace(),
      query,
      mode: "fts",
      limit: 10,
    });
    const duration = Math.round(performance.now() - started);
    setTaskGraphFromSearch(body, query);
    renderSearchResults(body, duration, query);
    if (state.memory?.graph) {
      renderStructuralGraph(state.memory.graph);
    }
    renderLive();
  } catch (error) {
    showError(error);
  }
}

async function explainContext() {
  const query = els.query.value.trim();
  if (!query) {
    return;
  }
  clear(els.contextSummary);
  els.contextSummary.appendChild(textEl("p", "Explaining context selection..."));
  const started = performance.now();
  try {
    const body = await requestJson("/memory/explain_context", {
      workspace_id: requireWorkspace(),
      query,
      max_tokens: 3500,
      files_in_scope: [],
      historical: false,
    });
    const duration = Math.round(performance.now() - started);
    setTaskGraphFromExplain(body);
    renderContextSummaryFromExplain(body, duration);
    if (state.memory?.graph) {
      renderStructuralGraph(state.memory.graph);
    }
    renderLive();
  } catch (error) {
    showError(error);
  }
}

async function loadRawContext() {
  const query = els.query.value.trim();
  if (!query) {
    return;
  }
  els.contextBox.textContent = "Loading raw XML...";
  try {
    const body = await requestJson("/memory/get_context", {
      workspace_id: requireWorkspace(),
      query,
      max_tokens: 3500,
      files_in_scope: [],
      historical: false,
    });
    state.rawContext = body.context_text || "";
    els.contextBox.textContent = state.rawContext || "No context returned.";
  } catch (error) {
    showError(error);
  }
}

function showError(error) {
  const message = error instanceof Error ? error.message : String(error);
  setChip(els.healthChip, "error", "error");
  const event = {
    event_id: `local_${Date.now()}`,
    request_id: "local",
    workspace_id: selectedWorkspace(),
    type: "request_failed",
    endpoint: "browser",
    operation: "ui",
    stage: "response",
    label: "UI request failed",
    status: "error",
    snippet: message,
    created_at: new Date().toISOString(),
  };
  mergeEvents([event]);
  renderLive();
}

function setupGraphInteractions() {
  const viewport = els.graphViewport;
  if (!viewport) {
    return;
  }
  els.graphZoomOut.addEventListener("click", () => zoomGraph(-0.12));
  els.graphZoomIn.addEventListener("click", () => zoomGraph(0.12));
  els.graphZoomReset.addEventListener("click", () => {
    setGraphZoom(0.82);
    viewport.scrollLeft = 0;
    viewport.scrollTop = 0;
    closeInspector();
  });
  els.graphTest?.addEventListener("click", () => startGraphDemo());
  els.graphInspector?.addEventListener("click", (event) => {
    if (event.target === els.graphInspector) {
      closeInspector();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.graphInspector.hidden) {
      closeInspector();
    }
  });
  els.graph?.addEventListener("click", (event) => {
    const nodeElement = graphInteractiveNode(event.target);
    if (!nodeElement) {
      return;
    }
    event.stopPropagation();
    showGraphNodeInspectorFromElement(nodeElement);
  });
  viewport.addEventListener(
    "wheel",
    (event) => {
      if (!event.ctrlKey && !event.metaKey) {
        return;
      }
      event.preventDefault();
      zoomGraph(event.deltaY > 0 ? -0.08 : 0.08);
    },
    { passive: false },
  );
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) {
      return;
    }
    const interactiveNode = graphInteractiveNode(event.target);
    if (interactiveNode) {
      state.graphClickCandidate = {
        node: interactiveNode,
        x: event.clientX,
        y: event.clientY,
      };
      return;
    }
    state.graphClickCandidate = null;
    state.graphDragging = true;
    state.graphDragStart = {
      x: event.clientX,
      y: event.clientY,
      left: viewport.scrollLeft,
      top: viewport.scrollTop,
    };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("dragging");
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!state.graphDragging || !state.graphDragStart) {
      return;
    }
    viewport.scrollLeft = state.graphDragStart.left - (event.clientX - state.graphDragStart.x);
    viewport.scrollTop = state.graphDragStart.top - (event.clientY - state.graphDragStart.y);
  });
  const endDrag = (event) => {
    const candidate = state.graphClickCandidate;
    state.graphClickCandidate = null;
    if (candidate) {
      const moved = Math.hypot(event.clientX - candidate.x, event.clientY - candidate.y);
      if (moved <= 10 && candidate.node.isConnected) {
        event.preventDefault();
        event.stopPropagation();
        showGraphNodeInspectorFromElement(candidate.node);
      }
      return;
    }
    if (!state.graphDragging) {
      return;
    }
    state.graphDragging = false;
    state.graphDragStart = null;
    viewport.classList.remove("dragging");
    try {
      viewport.releasePointerCapture(event.pointerId);
    } catch {
      // The pointer may already have been released by the browser.
    }
  };
  viewport.addEventListener("pointerup", endDrag);
  viewport.addEventListener("pointercancel", endDrag);
}

function setupGraphResize() {
  const viewport = els.graphViewport;
  if (!viewport) {
    return;
  }
  const rerender = () => {
    if (state.graphRendering || graphAnimationInProgress()) {
      return;
    }
    if (state.graphResizeTimer) {
      window.clearTimeout(state.graphResizeTimer);
    }
    state.graphResizeTimer = window.setTimeout(() => {
      state.graphResizeTimer = null;
      if (state.graphRendering || graphAnimationInProgress()) {
        return;
      }
      if (state.memory?.graph) {
        renderStructuralGraph(state.memory.graph);
      } else {
        setGraphZoom(state.graphZoom, { keepCenter: false });
      }
    }, 120);
  };
  const immediateRender = () => {
    if (state.memory?.graph) {
      renderStructuralGraph(state.memory.graph);
    } else {
      setGraphZoom(state.graphZoom, { keepCenter: false });
    }
  };
  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(rerender);
    observer.observe(viewport);
  } else {
    window.addEventListener("resize", immediateRender);
  }
}

els.workspace.addEventListener("change", () => {
  resetEventSource();
  refresh({ manual: true }).catch(showError);
});
els.token.addEventListener("change", () => {
  state.token = els.token.value.trim();
  resetEventSource();
  refresh({ manual: true }).catch(showError);
});
els.refresh.addEventListener("click", () => refresh({ manual: true }).catch(showError));
els.pause.addEventListener("click", () => {
  state.paused = !state.paused;
  els.pause.textContent = state.paused ? "Resume" : "Pause";
  if (state.paused) {
    resetEventSource();
    setChip(els.sseChip, "paused", "warning");
  } else {
    refresh({ manual: true }).catch(showError);
  }
});
els.search.addEventListener("click", () => searchMemory());
els.context.addEventListener("click", () => explainContext());
els.rawContext.addEventListener("click", () => loadRawContext());
els.query.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    searchMemory();
  }
});

setupGraphInteractions();
setupGraphResize();
setGraphZoom(state.graphZoom, { keepCenter: false });
renderLive();
refresh({ manual: true }).catch(showError);
setInterval(() => {
  if (!state.sseReady && !state.paused) {
    refresh().catch(showError);
  }
}, 15000);
