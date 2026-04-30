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
  framePhase: 0,
  lastFrameDraw: 0,
  lastRequestId: "",
  taskGraph: { query: "", mode: "empty", nodes: [], edges: [] },
  rawContext: "",
};

const els = {
  workspace: document.getElementById("workspaceInput"),
  token: document.getElementById("tokenInput"),
  refresh: document.getElementById("refreshBtn"),
  pause: document.getElementById("pauseBtn"),
  healthChip: document.getElementById("healthChip"),
  sseChip: document.getElementById("sseChip"),
  updatedChip: document.getElementById("updatedChip"),
  requestChip: document.getElementById("requestChip"),
  activeTitle: document.getElementById("activeTitle"),
  activeMeta: document.getElementById("activeMeta"),
  liveGraph: document.getElementById("liveGraphSvg"),
  stageRail: document.getElementById("stageRail"),
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
  graph: document.getElementById("graphSvg"),
  process: document.getElementById("processSvg"),
  stageList: document.getElementById("stageList"),
  searchSummary: document.getElementById("searchSummary"),
  searchResults: document.getElementById("searchResults"),
  contextBox: document.getElementById("contextBox"),
  timeline: document.getElementById("timeline"),
};

const svgNS = "http://www.w3.org/2000/svg";
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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

function stageIndex(stage) {
  const index = stageCatalog.findIndex((item) => item.id === stage);
  return index >= 0 ? index : 0;
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
  for (const event of events) {
    if (!event || !event.event_id || state.eventIds.has(event.event_id)) {
      continue;
    }
    state.eventIds.add(event.event_id);
    state.events.push(event);
    changed = true;
  }
  if (state.events.length > 320) {
    const keep = state.events.slice(-320);
    state.events = keep;
    state.eventIds = new Set(keep.map((event) => event.event_id));
  }
  state.events.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  return changed;
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
    els.workspace.value = state.workspace;
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
  setChip(els.signature, `signature ${memory.signature || "-"}`, "");
  renderMetrics(memory.counts || {});
  renderProcess(memory.process || { stages: [], edges: [], events: [] });
  renderStructuralGraph(memory.graph || { nodes: [], edges: [] });
  renderTimeline(memory.recent || []);
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
  const requestEvents = requestId ? eventsForRequest(requestId) : [];
  const latest = requestEvents[requestEvents.length - 1] || state.events[state.events.length - 1];
  const active = state.activeRequests.find((item) => item.request_id === requestId);
  const isRunning = Boolean(active) || latest?.type === "stage_started" || latest?.status === "running";

  els.activeTitle.textContent = latest ? friendlyEvent(latest) : "Waiting for memory activity";
  els.activeMeta.textContent = latest
    ? `${humanOperation(latest.operation)} on ${latest.endpoint || "memory API"} at ${dateLabel(latest.created_at)}${latest.snippet ? ` - ${clip(latest.snippet, 100)}` : ""}`
    : "Run Search or Explain to see a request moving through retrieval and context stages.";
  setChip(els.requestChip, isRunning ? "running" : latest ? "last request" : "idle", isRunning ? "cyan" : "ok");
  setChip(els.eventCountChip, `${state.events.length} events`, "");
  renderLiveGraph(requestEvents, latest, isRunning);
  renderStageRail(requestEvents, latest);
  renderLifeFeed();
  renderTaskGraph();
}

function renderLiveGraph(requestEvents, latest, isRunning) {
  const svg = els.liveGraph;
  clear(svg);
  const width = 1000;
  const height = 360;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const usedStages = new Set(requestEvents.map((event) => event.stage));
  const currentStage = latest?.stage || "input";
  const currentIndex = stageIndex(currentStage);
  const columns = 6;
  const points = stageCatalog.map((stage, index) => {
    const row = Math.floor(index / columns);
    const col = index % columns;
    return {
      ...stage,
      x: 90 + col * 164,
      y: 105 + row * 135,
      visited: usedStages.has(stage.id),
      current: stage.id === currentStage,
    };
  });

  svg.appendChild(
    svgEl("rect", {
      x: 14,
      y: 16,
      width: width - 28,
      height: height - 32,
      rx: 28,
      class: "graph-shell",
    }),
  );

  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    const activeEdge = isRunning && (index === currentIndex - 1 || (currentIndex === 0 && index === 0));
    const line = svgEl("path", {
      d: `M ${from.x + 38} ${from.y} C ${from.x + 80} ${from.y} ${to.x - 80} ${to.y} ${to.x - 38} ${to.y}`,
      class: activeEdge ? "flow-edge active" : usedStages.has(to.id) ? "flow-edge visited" : "flow-edge",
    });
    svg.appendChild(line);
    if (activeEdge && !reducedMotion) {
      const t = state.framePhase;
      const px = from.x + (to.x - from.x) * t;
      const py = from.y + (to.y - from.y) * t;
      svg.appendChild(svgEl("circle", { cx: px, cy: py, r: 5, class: "particle" }));
    }
  }

  for (const point of points) {
    const group = svgEl("g", {
      class: `flow-node ${point.color} ${point.visited ? "visited" : ""} ${point.current ? "current" : ""}`,
    });
    group.appendChild(svgEl("circle", { cx: point.x, cy: point.y, r: point.current ? 34 : 28 }));
    const label = svgEl("text", { x: point.x, y: point.y + 55, "text-anchor": "middle" });
    label.textContent = point.label;
    group.appendChild(label);
    svg.appendChild(group);
  }
}

function renderStageRail(requestEvents, latest) {
  clear(els.stageRail);
  const seen = new Set(requestEvents.map((event) => event.stage));
  const current = latest?.stage || "";
  for (const stage of stageCatalog) {
    const item = document.createElement("div");
    item.className = `rail-stage ${stage.color} ${seen.has(stage.id) ? "seen" : ""} ${current === stage.id ? "current" : ""}`;
    item.appendChild(textEl("strong", stage.label));
    const event = [...requestEvents].reverse().find((entry) => entry.stage === stage.id);
    item.appendChild(textEl("span", event ? clip(event.label, 42) : "not touched"));
    els.stageRail.appendChild(item);
  }
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

function renderStructuralGraph(graph) {
  const svg = els.graph;
  clear(svg);
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  svg.setAttribute("viewBox", "0 0 1000 520");
  const important = nodes.filter((node) => ["workspace", "group", "table"].includes(node.kind));
  if (!important.length) {
    return;
  }
  const center = { x: 500, y: 260 };
  const positions = new Map();
  const workspace = important.find((node) => node.kind === "workspace") || important[0];
  positions.set(workspace.id, center);
  const orbit = important.filter((node) => node.id !== workspace.id);
  orbit.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(1, orbit.length);
    const radius = node.kind === "group" ? 150 : 230;
    positions.set(node.id, {
      x: center.x + Math.cos(angle) * radius,
      y: center.y + Math.sin(angle) * radius,
    });
  });
  for (const edge of edges) {
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
        class: "object-edge",
      }),
    );
  }
  for (const node of important) {
    const pos = positions.get(node.id);
    if (!pos) {
      continue;
    }
    const group = svgEl("g", { class: `object-node ${node.kind}` });
    group.appendChild(svgEl("circle", { cx: pos.x, cy: pos.y, r: node.kind === "workspace" ? 42 : 26 }));
    const label = svgEl("text", { x: pos.x, y: pos.y + 48, "text-anchor": "middle" });
    label.textContent = clip(node.label, 24);
    group.appendChild(label);
    svg.appendChild(group);
  }
}

function renderTaskGraph() {
  const svg = els.taskGraph;
  clear(svg);
  svg.setAttribute("viewBox", "0 0 1000 420");
  const graph = state.taskGraph;
  if (!graph.nodes.length) {
    els.taskGraphMeta.textContent = "Search or Explain will create a live query graph here.";
    svg.appendChild(svgEl("rect", { x: 18, y: 18, width: 964, height: 384, rx: 28, class: "graph-shell" }));
    const text = svgEl("text", { x: 500, y: 210, "text-anchor": "middle", class: "placeholder-text" });
    text.textContent = "No selected request yet";
    svg.appendChild(text);
    return;
  }
  els.taskGraphMeta.textContent = `${graph.mode}: ${clip(graph.query, 110)}`;
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

function animate(timestamp) {
  if (!reducedMotion) {
    state.framePhase = (state.framePhase + 0.04) % 1;
    if (timestamp - state.lastFrameDraw > 140) {
      state.lastFrameDraw = timestamp;
      const requestId = latestRequestId();
      const requestEvents = requestId ? eventsForRequest(requestId) : [];
      const latest = requestEvents[requestEvents.length - 1] || state.events[state.events.length - 1];
      const active = state.activeRequests.find((item) => item.request_id === requestId);
      const isRunning = Boolean(active) || latest?.type === "stage_started" || latest?.status === "running";
      if (isRunning) {
        renderLiveGraph(requestEvents, latest, isRunning);
      }
    }
  }
  requestAnimationFrame(animate);
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

renderLive();
refresh({ manual: true }).catch(showError);
setInterval(() => refresh().catch(showError), 5000);
requestAnimationFrame(animate);
