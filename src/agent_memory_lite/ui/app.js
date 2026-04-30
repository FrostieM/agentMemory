const state = {
  workspace: "",
  token: localStorage.getItem("memoryUiToken") || "",
  paused: false,
  previousSignature: "",
  previousCounts: new Map(),
  previousNodes: new Set(),
  previousStageCounts: new Map(),
  selectedNode: null,
  graph: { nodes: [], edges: [] },
  process: { stages: [], edges: [], events: [] },
  activity: [],
  activeOperation: null,
  rawContext: "",
  tick: 0,
};

const els = {
  workspace: document.getElementById("workspaceInput"),
  token: document.getElementById("tokenInput"),
  refresh: document.getElementById("refreshBtn"),
  pause: document.getElementById("pauseBtn"),
  health: document.getElementById("healthChip"),
  live: document.getElementById("liveChip"),
  updated: document.getElementById("updatedChip"),
  signature: document.getElementById("signatureChip"),
  metrics: document.getElementById("metrics"),
  processSvg: document.getElementById("processSvg"),
  stageList: document.getElementById("stageList"),
  svg: document.getElementById("graphSvg"),
  timeline: document.getElementById("timeline"),
  query: document.getElementById("queryInput"),
  search: document.getElementById("searchBtn"),
  searchSummary: document.getElementById("searchSummary"),
  results: document.getElementById("searchResults"),
  context: document.getElementById("contextBtn"),
  rawContext: document.getElementById("rawContextBtn"),
  contextSummary: document.getElementById("contextSummary"),
  contextBox: document.getElementById("contextBox"),
  activeTitle: document.getElementById("activeTitle"),
  activeMeta: document.getElementById("activeMeta"),
  activityPath: document.getElementById("activityPath"),
  activityLog: document.getElementById("activityLog"),
};

const groupColor = {
  workspace: "#3ec5ff",
  episodic: "#55d6ff",
  retrieval: "#7dd3fc",
  research: "#20e0a1",
  capability: "#ffd166",
  governance: "#9b8cff",
  operations: "#ff7a90",
  feedback: "#f2a65a",
  reference: "#8ea7bb",
};

const stageColor = {
  capture: "#55d6ff",
  index: "#7dd3fc",
  retrieve: "#38bdf8",
  context: "#9b8cff",
  research: "#20e0a1",
  capabilities: "#ffd166",
  governance: "#ff7a90",
};

const operationSteps = {
  sync: ["Poll service", "Read counts", "Compare signature", "Render changes"],
  search: ["Read query", "FTS lookup", "Rank hits", "Render digest"],
  context: ["Read query", "Retrieve chunks", "Build envelope", "Summarize sections"],
  raw: ["Read query", "Build context", "Load raw XML", "Keep collapsed"],
};

function headers() {
  const h = { "Content-Type": "application/json" };
  if (state.token) h.Authorization = `Bearer ${state.token}`;
  return h;
}

function selectedWorkspace() {
  return (els.workspace.value || state.workspace || "").trim();
}

function requireWorkspace() {
  const workspace = selectedWorkspace();
  if (!workspace) {
    throw new Error("Workspace is not loaded yet. Refresh memory state first.");
  }
  state.workspace = workspace;
  return workspace;
}

function fmt(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function clip(text, n = 120) {
  const value = String(text || "");
  return value.length > n ? `${value.slice(0, n - 1)}...` : value;
}

function leadSentence(text, n = 110) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (!value) return "Memory item";
  const sentenceEnd = value.search(/[.!?](\s|$)/);
  const lead = sentenceEnd > 24 ? value.slice(0, sentenceEnd + 1) : value;
  return clip(lead, n);
}

function dateLabel(value) {
  if (!value) return "unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  return date.toLocaleString();
}

function candidateTitle(candidate) {
  const meta = candidate.metadata || {};
  const kind = meta.kind || (candidate.path ? "doc" : "memory");
  const location = candidate.path || meta.episode_id || candidate.id || "local memory";
  return `${kind} - ${clip(location, 54)}`;
}

function clear(el) {
  el.replaceChildren();
}

function textEl(tag, text, className = "") {
  const el = document.createElement(tag);
  if (className) el.className = className;
  el.textContent = text;
  return el;
}

function setChip(el, text, status = "") {
  el.textContent = text;
  el.className = `chip ${status}`.trim();
}

function nowLabel() {
  return new Date().toLocaleTimeString();
}

function pushActivity(event) {
  state.activity.unshift({
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    at: new Date().toISOString(),
    status: "ok",
    ...event,
  });
  state.activity = state.activity.slice(0, 18);
  renderActivity();
}

function setActiveOperation(operation) {
  state.activeOperation = operation;
  els.activeTitle.textContent = operation.title;
  els.activeMeta.textContent = operation.meta;
  renderOperationPath(operation.steps || []);
  renderProcess(state.process);
}

function renderOperationPath(steps) {
  clear(els.activityPath);
  steps.forEach((step, index) => {
    const item = document.createElement("span");
    item.className = `path-step ${index === steps.length - 1 ? "active" : ""}`;
    item.textContent = step;
    els.activityPath.appendChild(item);
  });
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text };
  }
  if (!response.ok) {
    throw new Error(body.detail || body.error || response.statusText || `HTTP ${response.status}`);
  }
  return body;
}

async function trackedRequest({ title, stage, query, steps, fn, log = true }) {
  const started = performance.now();
  setActiveOperation({
    title,
    stage,
    meta: query ? `query: ${query}` : "local service request in progress",
    steps,
  });
  try {
    const result = await fn();
    const duration = Math.round(performance.now() - started);
    setActiveOperation({
      title: `${title} complete`,
      stage,
      meta: `${duration}ms${query ? ` - ${query}` : ""}`,
      steps,
    });
    if (log) {
      pushActivity({ title, stage, query, duration, status: "ok" });
    }
    return { result, duration };
  } catch (error) {
    const duration = Math.round(performance.now() - started);
    setActiveOperation({
      title: `${title} failed`,
      stage,
      meta: `${duration}ms - ${error.message}`,
      steps,
    });
    pushActivity({ title, stage, query, duration, status: "error", detail: error.message });
    throw error;
  }
}

async function refresh({ manual = false } = {}) {
  if (state.paused && !manual) return;
  const params = new URLSearchParams({ recent_limit: "3" });
  const workspace = selectedWorkspace();
  if (workspace) {
    state.workspace = workspace;
    params.set("workspace_id", workspace);
  }
  const { result: memory, duration } = await trackedRequest({
    title: manual ? "Manual refresh" : "Live sync",
    stage: "capture",
    steps: operationSteps.sync,
    log: manual,
    fn: () => requestJson(`/memory/ui/state?${params.toString()}`),
  });
  const health = await fetch("/health")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`health ${r.status}`))))
    .catch((error) => ({
      status: "unknown",
      retrieval_integrity: { status: "unknown" },
      error: error.message,
    }));

  if (memory.workspace_id) {
    state.workspace = memory.workspace_id;
    els.workspace.value = memory.workspace_id;
  }
  renderHealth(health);
  renderMemory(memory, { duration });
}

function renderHealth(health) {
  const retrieval = health.retrieval_integrity?.status || "unknown";
  const ok = health.status === "ok" && retrieval === "ok";
  const label = health.error ? `health ${health.error}` : `${health.status || "unknown"} / ${retrieval}`;
  setChip(els.health, label, ok ? "ok" : "degraded");
}

function renderMemory(memory, { duration } = {}) {
  const changed = state.previousSignature && state.previousSignature !== memory.signature;
  setChip(els.live, state.paused ? "paused" : "live", state.paused ? "" : "live");
  setChip(els.updated, `updated ${nowLabel()}`);
  setChip(els.signature, memory.signature, changed ? "ok" : "");
  state.process = memory.process || { stages: [], edges: [], events: [] };
  renderMetrics(memory.counts || {});
  renderTimeline(memory.process?.events || memory.recent || []);
  renderProcess(state.process);
  renderGraph(memory.graph || { nodes: [], edges: [] });
  if (changed) {
    pushActivity({
      title: "Memory state changed",
      stage: "capture",
      duration,
      status: "changed",
      detail: `signature ${memory.signature}`,
    });
  }
  state.previousSignature = memory.signature;
}

function renderMetrics(counts) {
  const cards = [
    { key: "episodes", label: "Captured", hint: "raw events" },
    { key: "chunks", label: "Searchable", hint: "chunks" },
    { key: "theories", label: "Theories", hint: "claims" },
    { key: "research_experiments", label: "Experiments", hint: "tests" },
    { key: "capability_links", label: "Influence", hint: "role/skill links" },
    { key: "maintenance_events", label: "Maintenance", hint: "events" },
  ];
  clear(els.metrics);
  for (const card of cards) {
    const value = Number(counts[card.key] || 0);
    const previous = state.previousCounts.get(card.key);
    const div = document.createElement("article");
    div.className = `metric ${previous !== undefined && previous !== value ? "changed" : ""}`;
    div.append(textEl("span", card.label), textEl("strong", fmt(value)), textEl("p", card.hint));
    els.metrics.appendChild(div);
    state.previousCounts.set(card.key, value);
  }
}

function renderProcess(process) {
  renderStageList(process.stages || []);
  renderProcessSvg(process);
}

function renderStageList(stages) {
  clear(els.stageList);
  const activeStage = state.activeOperation?.stage;
  for (const stage of stages) {
    const previous = state.previousStageCounts.get(stage.id);
    const changed = previous !== undefined && previous !== stage.count;
    const card = document.createElement("article");
    card.className = `stage-card ${stage.id === activeStage ? "active" : ""} ${changed ? "changed" : ""}`;
    card.style.setProperty("--stage", stageColor[stage.id] || "#7dd3fc");
    card.append(
      textEl("small", stage.status),
      textEl("strong", stage.label),
      textEl("p", stage.verb),
      textEl("b", fmt(stage.count)),
    );
    if (stage.latest) {
      const latest = document.createElement("div");
      latest.className = "stage-latest";
      latest.append(textEl("span", stage.latest.table), textEl("em", clip(stage.latest.label, 70)));
      card.appendChild(latest);
    }
    els.stageList.appendChild(card);
    state.previousStageCounts.set(stage.id, stage.count);
  }
}

function renderProcessSvg(process) {
  const stages = process.stages || [];
  const edges = process.edges || [];
  const rect = els.processSvg.getBoundingClientRect();
  const width = Math.max(720, rect.width || 900);
  const height = 210;
  const y = 95;
  const gap = width / Math.max(1, stages.length + 1);
  const positions = new Map();
  els.processSvg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  els.processSvg.replaceChildren();

  stages.forEach((stage, index) => {
    positions.set(stage.id, { x: gap * (index + 1), y });
  });

  const edgeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
  const nodeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
  els.processSvg.append(edgeLayer, nodeLayer);
  const activeStage = state.activeOperation?.stage;

  for (const edge of edges) {
    const a = positions.get(edge.source);
    const b = positions.get(edge.target);
    if (!a || !b) continue;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("class", `process-edge ${edge.source === activeStage || edge.target === activeStage ? "active" : ""}`);
    line.setAttribute("x1", a.x + 34);
    line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x - 34);
    line.setAttribute("y2", b.y);
    edgeLayer.appendChild(line);
  }

  for (const stage of stages) {
    const pos = positions.get(stage.id);
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const active = stage.id === activeStage;
    group.setAttribute("class", `process-node ${active ? "active" : ""}`);
    group.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("r", active ? 30 : 25);
    circle.setAttribute("fill", stageColor[stage.id] || "#7dd3fc");
    group.appendChild(circle);

    const count = document.createElementNS("http://www.w3.org/2000/svg", "text");
    count.setAttribute("class", "process-count");
    count.setAttribute("y", "5");
    count.textContent = fmt(stage.count);
    group.appendChild(count);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("class", "process-label");
    label.setAttribute("y", "49");
    label.textContent = stage.label;
    group.appendChild(label);
    nodeLayer.appendChild(group);
  }
}

function renderTimeline(items) {
  const previousIds = new Set([...state.previousNodes]);
  clear(els.timeline);
  for (const item of items.slice(0, 18)) {
    const key = `${item.table}:${item.id}`;
    const div = document.createElement("article");
    div.className = `timeline-item ${previousIds.size && !previousIds.has(key) ? "new" : ""}`;
    div.append(
      textEl("small", `${item.stage || "memory"} / ${item.table}${item.status ? ` / ${item.status}` : ""}`),
      textEl("strong", clip(item.label || item.id, 86)),
      textEl("p", item.updated_at || "no timestamp"),
    );
    els.timeline.appendChild(div);
  }
}

function layout(nodes, edges, width, height) {
  const centerX = width / 2;
  const centerY = height / 2;
  const minSide = Math.min(width, height);
  const groupRadius = Math.max(74, minSide * 0.17);
  const tableRadius = Math.max(138, minSide * 0.31);
  const itemRadius = Math.max(190, minSide * 0.43);
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const parentById = new Map();
  for (const edge of edges) {
    if (edge.kind === "table" || edge.kind === "latest") {
      parentById.set(edge.target, edge.source);
    } else if (edge.kind === "reference" && !parentById.has(edge.target)) {
      parentById.set(edge.target, edge.source);
    }
  }

  const groups = nodes
    .filter((node) => node.kind === "group")
    .sort((a, b) => a.label.localeCompare(b.label));
  const angleById = new Map();
  groups.forEach((node, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(1, groups.length)) * Math.PI * 2;
    angleById.set(node.id, angle);
    node.x = centerX + Math.cos(angle) * groupRadius;
    node.y = centerY + Math.sin(angle) * groupRadius;
  });

  const tablesByGroup = new Map();
  for (const node of nodes.filter((item) => item.kind === "table")) {
    const parent = parentById.get(node.id);
    if (!tablesByGroup.has(parent)) tablesByGroup.set(parent, []);
    tablesByGroup.get(parent).push(node);
  }
  for (const [groupId, tables] of tablesByGroup.entries()) {
    const baseAngle = angleById.get(groupId) ?? 0;
    const spread = Math.min(0.8, 0.2 + tables.length * 0.08);
    tables.sort((a, b) => a.label.localeCompare(b.label));
    tables.forEach((node, index) => {
      const offset = tables.length === 1 ? 0 : -spread / 2 + (spread * index) / (tables.length - 1);
      const angle = baseAngle + offset;
      angleById.set(node.id, angle);
      node.x = centerX + Math.cos(angle) * tableRadius;
      node.y = centerY + Math.sin(angle) * tableRadius;
    });
  }

  const itemsByParent = new Map();
  for (const node of nodes.filter((item) => !["workspace", "group", "table"].includes(item.kind))) {
    const parent = parentById.get(node.id) || `group:${node.group}`;
    if (!itemsByParent.has(parent)) itemsByParent.set(parent, []);
    itemsByParent.get(parent).push(node);
  }
  for (const [parentId, items] of itemsByParent.entries()) {
    const parent = nodeById.get(parentId);
    const baseAngle = angleById.get(parentId) ?? angleById.get(`group:${parent?.group || "research"}`) ?? 0;
    const spread = Math.min(0.62, 0.16 + items.length * 0.08);
    items.forEach((node, index) => {
      const offset = items.length === 1 ? 0 : -spread / 2 + (spread * index) / (items.length - 1);
      const ringOffset = (index % 3) * 14;
      const angle = baseAngle + offset + Math.sin(state.tick * 0.04 + index) * 0.006;
      const radius = itemRadius - ringOffset;
      node.x = centerX + Math.cos(angle) * radius;
      node.y = centerY + Math.sin(angle) * radius;
    });
  }

  for (const node of nodes.filter((item) => item.kind === "workspace")) {
    node.x = centerX;
    node.y = centerY;
  }
}

function renderGraph(graph) {
  const nodes = (graph.nodes || []).map((node) => ({ ...node }));
  const edges = graph.edges || [];
  state.graph = { nodes, edges };
  state.tick += 1;
  const rect = els.svg.getBoundingClientRect();
  const width = Math.max(640, rect.width || 900);
  const height = Math.max(300, rect.height || 420);
  els.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  layout(nodes, edges, width, height);

  const nextNodeIds = new Set(nodes.map((node) => node.id));
  const freshNodes = new Set([...nextNodeIds].filter((id) => state.previousNodes.size && !state.previousNodes.has(id)));
  state.previousNodes = nextNodeIds;
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));

  els.svg.replaceChildren();
  const edgeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
  const nodeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
  els.svg.append(edgeLayer, nodeLayer);

  for (const edge of edges) {
    const a = nodeMap.get(edge.source);
    const b = nodeMap.get(edge.target);
    if (!a || !b) continue;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("class", "edge");
    line.setAttribute("x1", a.x);
    line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x);
    line.setAttribute("y2", b.y);
    edgeLayer.appendChild(line);
  }

  for (const node of nodes) {
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.setAttribute("class", `node ${freshNodes.has(node.id) ? "new" : ""}`);
    group.setAttribute("transform", `translate(${node.x}, ${node.y})`);
    group.addEventListener("click", () => selectNode(node));

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    const radius = node.kind === "workspace" ? 16 : node.kind === "group" ? 12 : node.kind === "table" ? 9 : 5;
    circle.setAttribute("r", radius);
    circle.setAttribute("fill", groupColor[node.group] || groupColor.reference);
    circle.setAttribute("fill-opacity", node.kind === "table" ? "0.74" : "0.92");
    group.appendChild(circle);

    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${node.label}${node.count !== null && node.count !== undefined ? ` (${fmt(node.count)})` : ""}`;
    group.appendChild(title);

    const showLabel = ["workspace", "group", "table"].includes(node.kind);
    if (showLabel) {
      const labelToLeft = node.x > width - 170;
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", labelToLeft ? -radius - 6 : radius + 6);
      text.setAttribute("y", "4");
      if (labelToLeft) text.setAttribute("text-anchor", "end");
      text.textContent = clip(node.label, 28);
      group.appendChild(text);
    }
    nodeLayer.appendChild(group);
  }
}

function selectNode(node) {
  pushActivity({
    title: "Selected graph node",
    stage: node.group === "research" ? "research" : "context",
    detail: `${node.kind}: ${node.label}`,
    status: "selected",
  });
}

function renderActivity() {
  clear(els.activityLog);
  for (const event of state.activity.slice(0, 12)) {
    const item = document.createElement("article");
    item.className = `activity-item ${event.status}`;
    item.append(
      textEl("small", `${event.stage || "memory"} - ${event.duration ? `${event.duration}ms` : nowLabel()}`),
      textEl("strong", event.title),
      textEl("p", event.detail || event.query || "local UI event"),
    );
    els.activityLog.appendChild(item);
  }
}

function renderSearchSummary(body, duration, query) {
  clear(els.searchSummary);
  const hits = body.hits || [];
  const best = hits[0];
  const fragments = [
    `exact search`,
    `${hits.length} hits`,
    `${duration}ms`,
    best ? `top score ${Number(best.score || 0).toFixed(2)}` : "no top hit",
  ];
  fragments.forEach((fragment) => els.searchSummary.appendChild(textEl("span", fragment)));
  if (query) els.searchSummary.appendChild(textEl("span", clip(query, 36)));
}

async function search() {
  const query = els.query.value.trim();
  if (!query) return;
  const workspace = requireWorkspace();
  clear(els.results);
  els.results.appendChild(textEl("p", "Searching memory..."));
  try {
    const { result: body, duration } = await trackedRequest({
      title: "Search memory",
      stage: "retrieve",
      query,
      steps: operationSteps.search,
      fn: () =>
        requestJson("/memory/search", {
          method: "POST",
          body: JSON.stringify({ workspace_id: workspace, query, limit: 8 }),
        }),
    });
    renderSearchSummary(body, duration, query);
    clear(els.results);
    for (const hit of body.hits || []) {
      const div = document.createElement("article");
      div.className = "result";
      const text = hit.summary || hit.text || "";
      div.append(
        textEl("small", `${hit.path || "memory episode"} - ${hit.chunk_id} - score ${Number(hit.score || 0).toFixed(2)}`),
        textEl("strong", leadSentence(text, 100)),
        textEl("p", clip(text, 240)),
      );
      els.results.appendChild(div);
    }
    if (!els.results.children.length) els.results.appendChild(textEl("p", "No hits."));
  } catch (error) {
    clear(els.results);
    els.results.appendChild(textEl("p", error.message, "error"));
  }
}

function sectionLabel(section) {
  return section.replaceAll("_", " ");
}

function renderContextExplain(body, duration) {
  clear(els.contextSummary);
  const included = new Set(body.included_ids || []);
  const scored = body.scored_candidates || [];
  const includedCandidates = scored.filter((candidate) => included.has(candidate.id)).slice(0, 6);
  const skippedCandidates = scored.filter((candidate) => !included.has(candidate.id)).slice(0, 4);
  const sourceCounts = (body.source_candidates || []).reduce((acc, candidate) => {
    const source = candidate.source || "unknown";
    acc[source] = (acc[source] || 0) + 1;
    return acc;
  }, {});

  const header = document.createElement("div");
  header.className = "context-hero";
  header.append(
    textEl("strong", `Context built from ${included.size} memories`),
    textEl("p", `${body.context_tokens || 0} tokens in ${duration}ms for "${clip(body.query, 86)}"`),
  );
  els.contextSummary.appendChild(header);

  const flow = document.createElement("div");
  flow.className = "context-flow";
  [
    ["Query", clip(body.query, 34)],
    ["Candidates", fmt((body.source_candidates || []).length)],
    ["Included", fmt(included.size)],
    ["Sections", fmt(Object.values(body.section_counts || {}).reduce((sum, count) => sum + Number(count || 0), 0))],
  ].forEach(([label, value]) => {
    const step = document.createElement("article");
    step.append(textEl("small", label), textEl("strong", value));
    flow.appendChild(step);
  });
  els.contextSummary.appendChild(flow);

  const sections = document.createElement("div");
  sections.className = "section-pills";
  Object.entries(body.section_counts || {}).forEach(([section, count]) => {
    if (Number(count || 0) > 0) {
      sections.appendChild(textEl("span", `${sectionLabel(section)} ${count}`));
    }
  });
  Object.entries(sourceCounts).forEach(([source, count]) => {
    sections.appendChild(textEl("span", `${source} candidates ${count}`));
  });
  els.contextSummary.appendChild(sections);

  const sources = document.createElement("div");
  sources.className = "source-list";
  if (includedCandidates.length) {
    sources.appendChild(textEl("h3", "Used by the agent"));
  }
  for (const candidate of includedCandidates) {
    const item = document.createElement("article");
    item.append(
      textEl("small", `${candidate.sources?.join("+") || candidate.source || "source"} - included - ${dateLabel(candidate.metadata?.created_at)}`),
      textEl("strong", candidateTitle(candidate)),
      textEl("p", `${candidate.reason || "selected for final context"} - ${candidate.id}`),
    );
    sources.appendChild(item);
  }
  if (skippedCandidates.length) {
    sources.appendChild(textEl("h3", "Retrieved but not used"));
  }
  for (const candidate of skippedCandidates) {
    const item = document.createElement("article");
    item.className = "muted-card";
    item.append(
      textEl("small", `${candidate.sources?.join("+") || candidate.source || "source"} - ${dateLabel(candidate.metadata?.created_at)}`),
      textEl("strong", candidateTitle(candidate)),
      textEl("p", `${candidate.reason || "not selected"} - ${candidate.id}`),
    );
    sources.appendChild(item);
  }
  els.contextSummary.appendChild(sources);
}

async function explainContext() {
  const query = els.query.value.trim() || "memory health graph";
  const workspace = requireWorkspace();
  clear(els.contextSummary);
  els.contextSummary.appendChild(textEl("p", "Explaining context..."));
  try {
    const { result: body, duration } = await trackedRequest({
      title: "Explain context",
      stage: "context",
      query,
      steps: operationSteps.context,
      fn: () =>
        requestJson("/memory/explain_context", {
          method: "POST",
          body: JSON.stringify({
            workspace_id: workspace,
            query,
            max_tokens: 2200,
          }),
        }),
    });
    renderContextExplain(body, duration);
  } catch (error) {
    clear(els.contextSummary);
    els.contextSummary.appendChild(textEl("p", error.message, "error"));
  }
}

async function loadRawContext() {
  const query = els.query.value.trim() || "memory health graph";
  const workspace = requireWorkspace();
  els.contextBox.textContent = "Loading raw context...";
  try {
    const { result: body } = await trackedRequest({
      title: "Load raw context",
      stage: "context",
      query,
      steps: operationSteps.raw,
      fn: () =>
        requestJson("/memory/get_context", {
          method: "POST",
          body: JSON.stringify({
            workspace_id: workspace,
            query,
            max_tokens: 2200,
          }),
        }),
    });
    state.rawContext = body.context_text || "";
    els.contextBox.textContent = state.rawContext;
  } catch (error) {
    els.contextBox.textContent = error.message;
  }
}

els.token.value = state.token;
els.token.addEventListener("change", () => {
  state.token = els.token.value.trim();
  localStorage.setItem("memoryUiToken", state.token);
});
els.workspace.addEventListener("change", () => {
  state.workspace = els.workspace.value.trim();
  refresh({ manual: true }).catch(showError);
});
els.refresh.addEventListener("click", () => refresh({ manual: true }).catch(showError));
els.pause.addEventListener("click", () => {
  state.paused = !state.paused;
  els.pause.textContent = state.paused ? "Resume live" : "Pause live";
  setChip(els.live, state.paused ? "paused" : "live", state.paused ? "" : "live");
});
els.search.addEventListener("click", () => search());
els.query.addEventListener("keydown", (event) => {
  if (event.key === "Enter") search();
});
els.context.addEventListener("click", () => explainContext());
els.rawContext.addEventListener("click", () => loadRawContext());

function showError(error) {
  setChip(els.health, error.message, "degraded");
  pushActivity({ title: "UI error", stage: "governance", status: "error", detail: error.message });
}

setActiveOperation({
  title: "Waiting for memory activity",
  stage: "capture",
  meta: "Run search, explain context, or wait for live sync.",
  steps: operationSteps.sync,
});
renderActivity();
refresh().catch(showError);
setInterval(() => refresh().catch(showError), 3500);
