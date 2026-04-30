const state = {
  workspace: "",
  token: localStorage.getItem("memoryUiToken") || "",
  paused: false,
  previousSignature: "",
  previousCounts: new Map(),
  previousNodes: new Set(),
  selectedNode: null,
  graph: { nodes: [], edges: [] },
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
  svg: document.getElementById("graphSvg"),
  timeline: document.getElementById("timeline"),
  query: document.getElementById("queryInput"),
  search: document.getElementById("searchBtn"),
  results: document.getElementById("searchResults"),
  context: document.getElementById("contextBtn"),
  contextBox: document.getElementById("contextBox"),
  details: document.getElementById("nodeDetails"),
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

function headers() {
  const h = { "Content-Type": "application/json" };
  if (state.token) h.Authorization = `Bearer ${state.token}`;
  return h;
}

function fmt(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function clip(text, n = 120) {
  const value = String(text || "");
  return value.length > n ? `${value.slice(0, n - 1)}...` : value;
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

async function refresh() {
  if (state.paused) return;
  const params = new URLSearchParams({ recent_limit: "2" });
  if (state.workspace) params.set("workspace_id", state.workspace);
  const [healthResult, memory] = await Promise.all([
    fetch("/health").then((r) => (r.ok ? r.json() : Promise.reject(new Error(`health ${r.status}`)))).catch((error) => ({
      status: "unknown",
      retrieval_integrity: { status: "unknown" },
      error: error.message,
    })),
    requestJson(`/memory/ui/state?${params.toString()}`),
  ]);

  if (!state.workspace) {
    state.workspace = memory.workspace_id;
    els.workspace.value = memory.workspace_id;
  }
  renderHealth(healthResult);
  renderMemory(memory);
}

function renderHealth(health) {
  const retrieval = health.retrieval_integrity?.status || "unknown";
  const ok = health.status === "ok" && retrieval === "ok";
  const label = health.error ? `health ${health.error}` : `${health.status || "unknown"} / ${retrieval}`;
  setChip(els.health, label, ok ? "ok" : "degraded");
}

function renderMemory(memory) {
  const changed = state.previousSignature && state.previousSignature !== memory.signature;
  const now = new Date();
  setChip(els.live, state.paused ? "paused" : "live", state.paused ? "" : "live");
  setChip(els.updated, `updated ${now.toLocaleTimeString()}`);
  setChip(els.signature, memory.signature, changed ? "ok" : "");
  renderMetrics(memory.counts || {});
  renderTimeline(memory.recent || []);
  renderGraph(memory.graph || { nodes: [], edges: [] });
  state.previousSignature = memory.signature;
}

function renderMetrics(counts) {
  const cards = [
    ["episodes", "Episodes"],
    ["chunks", "Chunks"],
    ["theories", "Theories"],
    ["research_experiments", "Experiments"],
    ["agent_roles", "Roles"],
    ["capability_links", "Links"],
  ];
  clear(els.metrics);
  for (const [key, label] of cards) {
    const value = Number(counts[key] || 0);
    const previous = state.previousCounts.get(key);
    const div = document.createElement("div");
    div.className = `metric ${previous !== undefined && previous !== value ? "changed" : ""}`;
    div.append(textEl("span", label), textEl("strong", fmt(value)));
    els.metrics.appendChild(div);
    state.previousCounts.set(key, value);
  }
}

function renderTimeline(items) {
  const previousIds = new Set([...state.previousNodes]);
  clear(els.timeline);
  for (const item of items.slice(0, 16)) {
    const key = `${item.table}:${item.id}`;
    const div = document.createElement("article");
    div.className = `timeline-item ${previousIds.size && !previousIds.has(key) ? "new" : ""}`;
    div.append(
      textEl("small", `${item.table}${item.status ? ` / ${item.status}` : ""}`),
      textEl("strong", clip(item.label || item.id, 72)),
      textEl("p", item.updated_at || "no timestamp"),
    );
    els.timeline.appendChild(div);
  }
}

function layout(nodes, edges, width, height) {
  const centerX = width / 2;
  const centerY = height / 2;
  const minSide = Math.min(width, height);
  const groupRadius = Math.max(88, minSide * 0.18);
  const tableRadius = Math.max(170, minSide * 0.34);
  const itemRadius = Math.max(245, minSide * 0.48);
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
    const spread = Math.min(0.88, 0.2 + tables.length * 0.08);
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
    const spread = Math.min(0.7, 0.16 + items.length * 0.09);
    items.forEach((node, index) => {
      const offset = items.length === 1 ? 0 : -spread / 2 + (spread * index) / (items.length - 1);
      const ringOffset = (index % 3) * 18;
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
  const height = Math.max(420, rect.height || 520);
  els.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  layout(nodes, edges, width, height);

  const nextNodeIds = new Set(nodes.map((node) => node.id));
  const freshNodes = new Set([...nextNodeIds].filter((id) => state.previousNodes.size && !state.previousNodes.has(id)));
  state.previousNodes = nextNodeIds;
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));

  els.svg.innerHTML = "";
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
    const radius = node.kind === "workspace" ? 17 : node.kind === "group" ? 13 : node.kind === "table" ? 10 : 7;
    circle.setAttribute("r", radius);
    circle.setAttribute("fill", groupColor[node.group] || groupColor.reference);
    circle.setAttribute("fill-opacity", node.kind === "table" ? "0.74" : "0.92");
    group.appendChild(circle);

    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${node.label}${node.count !== null && node.count !== undefined ? ` (${fmt(node.count)})` : ""}`;
    group.appendChild(title);

    const showLabel = ["workspace", "group", "table"].includes(node.kind);
    const labelToLeft = node.x > width - 190;
    if (showLabel) {
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", labelToLeft ? -radius - 6 : radius + 6);
      text.setAttribute("y", "4");
      if (labelToLeft) text.setAttribute("text-anchor", "end");
      text.textContent = clip(node.label, 34);
      group.appendChild(text);
    }

    if (showLabel && node.count !== null && node.count !== undefined) {
      const count = document.createElementNS("http://www.w3.org/2000/svg", "text");
      count.setAttribute("class", "count-label");
      count.setAttribute("x", labelToLeft ? -radius - 6 : radius + 6);
      count.setAttribute("y", "18");
      if (labelToLeft) count.setAttribute("text-anchor", "end");
      count.textContent = fmt(node.count);
      group.appendChild(count);
    }
    nodeLayer.appendChild(group);
  }
}

function selectNode(node) {
  state.selectedNode = node;
  clear(els.details);
  els.details.append(
    textEl("strong", node.label),
    textEl("p", `id: ${node.id}`),
    textEl("p", `kind: ${node.kind} / ${node.group}`),
    textEl("p", `count: ${node.count ?? "-"}`),
    textEl("p", `status: ${node.status || "-"}`),
    textEl("p", `updated: ${node.updated_at || "-"}`),
    textEl("p", node.detail || ""),
  );
}

async function search() {
  const query = els.query.value.trim();
  if (!query) return;
  clear(els.results);
  els.results.appendChild(textEl("p", "Searching..."));
  try {
    const body = await requestJson("/memory/search", {
      method: "POST",
      body: JSON.stringify({ workspace_id: state.workspace || undefined, query, limit: 8 }),
    });
    clear(els.results);
    for (const hit of body.hits || []) {
      const div = document.createElement("div");
      div.className = "result";
      div.append(textEl("strong", hit.chunk_id), textEl("p", clip(hit.text, 180)));
      els.results.appendChild(div);
    }
    if (!els.results.children.length) els.results.appendChild(textEl("p", "No hits."));
  } catch (error) {
    clear(els.results);
    els.results.appendChild(textEl("p", error.message, "error"));
  }
}

async function getContext() {
  const query = els.query.value.trim() || "memory health graph";
  els.contextBox.textContent = "Loading context...";
  try {
    const body = await requestJson("/memory/get_context", {
      method: "POST",
      body: JSON.stringify({
        workspace_id: state.workspace || undefined,
        query,
        max_tokens: 1800,
      }),
    });
    els.contextBox.textContent = body.context_text || "";
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
  refresh().catch(showError);
});
els.refresh.addEventListener("click", () => refresh().catch(showError));
els.pause.addEventListener("click", () => {
  state.paused = !state.paused;
  els.pause.textContent = state.paused ? "Resume live" : "Pause live";
  setChip(els.live, state.paused ? "paused" : "live", state.paused ? "" : "live");
});
els.search.addEventListener("click", () => search());
els.query.addEventListener("keydown", (event) => {
  if (event.key === "Enter") search();
});
els.context.addEventListener("click", () => getContext());

function showError(error) {
  setChip(els.health, error.message, "degraded");
  clear(els.timeline);
  const item = document.createElement("div");
  item.className = "timeline-item";
  item.append(textEl("strong", "UI error", "error"), textEl("p", error.message));
  els.timeline.appendChild(item);
}

refresh().catch(showError);
setInterval(() => refresh().catch(showError), 2500);
