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
    browse: "/ui/browse",
  };

  // Workspace registry cache: id -> { db_path, vector_path, label }.
  // Populated by populateWorkspaces; consumed by routeFor() / dbHeaders()
  // so any UI page can attach X-Memory-DB-Path to API calls and route
  // hub-mode HTTP requests to the right physical DB. Without this, the
  // server falls back to settings.db_path and POST /memory/promote_candidate
  // 404s because the candidate lives in a foreign DB.
  const workspaceRegistry = new Map();

  async function populateWorkspaces(select, preselect) {
    try {
      const r = await fetch("/memory/workspaces");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      select.innerHTML = "";
      workspaceRegistry.clear();
      const target =
        preselect || data.current_workspace_id ||
        (data.workspaces[0] && data.workspaces[0].id);
      for (const w of data.workspaces || []) {
        workspaceRegistry.set(w.id, {
          db_path: w.db_path || "",
          vector_path: w.vector_path || "",
          label: w.label || "",
        });
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

  function routeFor(id) {
    return id ? workspaceRegistry.get(id) || null : null;
  }

  function dbHeaders(id) {
    const r = routeFor(id);
    const h = {};
    if (r) {
      if (r.db_path) h["X-Memory-DB-Path"] = r.db_path;
      if (r.vector_path) h["X-Memory-Vector-Path"] = r.vector_path;
    }
    return h;
  }

  function jsonHeaders(id) {
    return Object.assign({ "Content-Type": "application/json" }, dbHeaders(id));
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

  // Phase 4.1: shared toast helper. Other pages can call
  // window.AppHeader.toast(msg, "success" | "error" | "") for inline
  // notifications without rolling their own DOM/CSS.
  function toast(msg, kind) {
    const t = document.createElement("div");
    t.className = "app-toast" + (kind ? " is-" + kind : "");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => {
      t.style.transition = "opacity 0.25s";
      t.style.opacity = "0";
      setTimeout(() => t.remove(), 260);
    }, 3500);
  }

  // Phase 4.2: keyboard shortcuts. Single-key navigation between pages
  // when the focus is not in a text-editable element.
  //   "/" → focus the page's search box (id="find-input" or "query-input"
  //         or the first <input type=text> inside .browse-controls)
  //   "o" → /ui   "c" → /ui/code   "g" → /ui/graph
  //   "r" → /ui/review   "b" → /ui/browse
  //   "?" → toggle a small help overlay
  function isEditable(el) {
    if (!el) return false;
    const tag = el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return el.isContentEditable === true;
  }

  function navigate(target, currentSelect) {
    const ws = currentSelect && currentSelect.value;
    const base = NAV_BASES[target];
    if (!base) return;
    const u = ws ? `${base}?workspace_id=${encodeURIComponent(ws)}` : base;
    location.href = u;
  }

  function focusSearch() {
    const candidates = ["#find-input", "#query-input", ".find-symbols input"];
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (el) { el.focus(); el.select && el.select(); return; }
    }
  }

  function showHelp() {
    if (document.getElementById("app-help-overlay")) {
      document.getElementById("app-help-overlay").remove();
      return;
    }
    const back = document.createElement("div");
    back.id = "app-help-overlay";
    back.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.7);"
      + "display:flex;align-items:center;justify-content:center;z-index:9998;";
    back.innerHTML = `
      <div style="background:var(--bg-0);border:1px solid var(--line-soft);
                  border-radius:10px;padding:1.4em 1.6em;font-size:0.92em;
                  font-family:ui-monospace,monospace;color:var(--text-0);
                  box-shadow:0 4px 14px rgba(0,0,0,0.5);">
        <h3 style="margin:0 0 0.6em;color:var(--accent);font-size:1em">keyboard shortcuts</h3>
        <table style="border-collapse:collapse">
          <tr><td style="padding:0.2em 0.6em">/</td><td>focus search</td></tr>
          <tr><td style="padding:0.2em 0.6em">o</td><td>Observatory  (/ui)</td></tr>
          <tr><td style="padding:0.2em 0.6em">c</td><td>Code         (/ui/code)</td></tr>
          <tr><td style="padding:0.2em 0.6em">g</td><td>Graph        (/ui/graph)</td></tr>
          <tr><td style="padding:0.2em 0.6em">r</td><td>Review       (/ui/review)</td></tr>
          <tr><td style="padding:0.2em 0.6em">b</td><td>Browse       (/ui/browse)</td></tr>
          <tr><td style="padding:0.2em 0.6em">?</td><td>this overlay</td></tr>
          <tr><td style="padding:0.2em 0.6em">esc</td><td>close overlay</td></tr>
        </table>
        <div style="margin-top:0.8em;color:var(--text-2);font-size:0.78em">
          shortcuts ignored when typing in a text field.
        </div>
      </div>`;
    back.addEventListener("click", () => back.remove());
    document.body.appendChild(back);
  }

  function bindKeyboardShortcuts(select) {
    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") {
        const overlay = document.getElementById("app-help-overlay");
        if (overlay) { overlay.remove(); e.preventDefault(); }
        return;
      }
      if (isEditable(e.target)) return;
      switch (e.key) {
        case "/": focusSearch(); e.preventDefault(); break;
        case "?": showHelp(); e.preventDefault(); break;
        case "o": navigate("ui",    select); break;
        case "c": navigate("code",  select); break;
        case "g": navigate("graph", select); break;
        case "r": navigate("review", select); break;
        case "b": navigate("browse", select); break;
      }
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
    bindKeyboardShortcuts(select);
    if (typeof opts.onWorkspaceChange === "function" && select) {
      opts.onWorkspaceChange(select.value);
    }
  }

  window.AppHeader = { init, toast, routeFor, dbHeaders, jsonHeaders };
})();
