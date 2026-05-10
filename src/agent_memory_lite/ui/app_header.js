/**
 * Shared header logic for the dashboard pages /ui/code and /ui/graph.
 *
 * Phase 2.3 of v2.2 consolidation: the three /ui/* pages used to render
 * three different headers with three different style sources. This file
 * gives /ui/code and /ui/graph the same look as /ui/index — brand mark,
 * workspace dropdown, health/chunks/vectors chips, and the nav strip.
 *
 * /ui/index.html keeps its dedicated app.js because it owns the live
 * observatory; this script is intentionally small (~80 LoC) and only
 * does the unified-header parts /ui/code and /ui/graph need.
 *
 * The host page must include three things:
 *
 *   1. <link rel="stylesheet" href="/ui/styles.css?v=..." />
 *   2. The standard header markup (app-header, brand, header-pills,
 *      header-meta) and nav strip — see /ui/code or /ui/graph for the
 *      reference snippet.
 *   3. <script src="/ui/app_header.js"></script>
 *
 * Then call AppHeader.init({ active: "code" | "graph" | "ui",
 *                            onWorkspaceChange: (ws) => ... }).
 *
 * The callback runs on initial bootstrap and every dropdown change.
 */
(function () {
  const NAV_BASES = {
    ui: "/ui",
    code: "/ui/code",
    graph: "/ui/graph",
    review: "/ui/review",
  };

  async function populateWorkspaces(select, preselect) {
    try {
      const r = await fetch("/memory/workspaces");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      select.innerHTML = "";
      const target =
        preselect || data.current_workspace_id ||
        (data.workspaces[0] && data.workspaces[0].id);
      for (const w of data.workspaces || []) {
        const opt = document.createElement("option");
        opt.value = w.id;
        opt.textContent =
          w.label && w.label !== w.id ? `${w.id} · ${w.label}` : w.id;
        if (w.id === target) opt.selected = true;
        select.appendChild(opt);
      }
    } catch (_e) {
      select.innerHTML = `<option value="${preselect || "default"}">${preselect || "default"}</option>`;
    }
  }

  async function refreshChips() {
    const setChip = (id, value, cls) => {
      const el = document.getElementById(id);
      if (!el) return;
      const v = el.querySelector(".chip-value");
      if (v) v.textContent = value;
      if (cls !== undefined) {
        el.classList.remove("is-warn", "is-bad");
        if (cls) el.classList.add(cls);
      }
    };
    try {
      const r = await fetch("/health");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const h = await r.json();
      const integrity = h.retrieval_integrity || {};
      const status = integrity.status || (h.ok ? "ok" : "warn");
      const cls = status === "ok" ? "" : status === "degraded" ? "is-warn" : "is-bad";
      setChip("healthChip", status, cls);
      setChip("chunksChip", String(h.chunks ?? 0));
      setChip("vectorsChip", String(h.vectors ?? 0));
      setChip("maintChip", String(h.maintenance_open ?? 0));
    } catch (_e) {
      setChip("healthChip", "down", "is-bad");
    }
  }

  function syncNavHrefs(select) {
    const ws = select && select.value;
    document.querySelectorAll(".app-nav-link[data-nav]").forEach((a) => {
      const base = NAV_BASES[a.dataset.nav] || "/ui";
      a.href = ws ? `${base}?workspace_id=${encodeURIComponent(ws)}` : base;
    });
  }

  async function init(options) {
    const opts = options || {};
    const select = document.getElementById("workspaceInput");
    const preselect = new URLSearchParams(location.search).get("workspace_id") || "";
    if (select) {
      await populateWorkspaces(select, preselect);
      syncNavHrefs(select);
      select.addEventListener("change", () => {
        const u = new URL(location.href);
        u.searchParams.set("workspace_id", select.value);
        history.replaceState(null, "", u);
        syncNavHrefs(select);
        if (typeof opts.onWorkspaceChange === "function") {
          opts.onWorkspaceChange(select.value);
        }
      });
    }
    refreshChips();
    if (typeof opts.onWorkspaceChange === "function" && select) {
      opts.onWorkspaceChange(select.value);
    }
  }

  window.AppHeader = { init };
})();
