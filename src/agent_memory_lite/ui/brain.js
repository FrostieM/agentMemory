/**
 * v3.0.0-final brain data renderer.
 *
 * Single shared module used by Observatory (`/ui`) + Reflexes / Recall /
 * Metrics pages. Provides:
 *
 *   BrainUI.fetchState(workspaceId)            -- one-shot snapshot
 *   BrainUI.renderSelfModel(el, data)          -- identity card
 *   BrainUI.renderWatchOuts(el, items)         -- negative-outcome rows
 *   BrainUI.renderRecentInsights(el, items)    -- candidate insights
 *   BrainUI.renderOutcomeBar(el, dist)         -- segmented bar
 *   BrainUI.outcomePill(score)                 -- HTML string for a pill
 *   BrainUI.refreshSelfModel(workspaceId)      -- POST + refetch
 *
 * Every renderer is failure-soft: missing data => empty-state node;
 * no exceptions bubble up to break the host page.
 */
(function () {
  const BRAIN_STATE_URL = "/memory/ui/brain_state";

  function escapeHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function outcomeClass(score) {
    if (score >= 0.05) return "is-pos";
    if (score <= -0.05) return "is-neg";
    return "is-neu";
  }

  function outcomePill(score) {
    const n = Number(score) || 0;
    const cls = outcomeClass(n);
    const sign = n >= 0 ? "+" : "";
    return `<span class="outcome-pill ${cls}">${sign}${n.toFixed(2)}</span>`;
  }

  async function fetchState(workspaceId) {
    const url = `${BRAIN_STATE_URL}?workspace_id=${encodeURIComponent(workspaceId)}`;
    try {
      const r = await fetch(url, {
        headers: window.AppHeader ? window.AppHeader.dbHeaders(workspaceId) : {},
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      console.error("brain_state fetch failed", e);
      return null;
    }
  }

  function renderSelfModel(el, sm) {
    if (!el) return;
    if (!sm || !sm.identity_text) {
      el.innerHTML = `<div class="brain-empty">No self-model yet — click <strong>refresh</strong> to generate the first one.</div>`;
      return;
    }
    const refreshedShort = sm.updated_at ? sm.updated_at.slice(0, 16).replace("T", " ") : "—";
    el.innerHTML = `
      <div class="brain-card-head">
        <span class="brain-card-kicker">self-model · identity</span>
        <div class="brain-card-actions">
          <button class="btn-tiny" data-action="refresh-self-model">refresh</button>
        </div>
      </div>
      <div class="self-model-narrative">${escapeHtml(sm.identity_text)}</div>
      <div class="self-model-meta">
        <span>via <strong>${escapeHtml(sm.refreshed_via || "—")}</strong></span>
        <span><strong>${sm.word_count || 0}</strong> words</span>
        <span><strong>${sm.invariants_count || 0}</strong> invariants</span>
        <span><strong>${sm.uncertainties_count || 0}</strong> uncertainties</span>
        <span class="foot-mute">${refreshedShort}</span>
      </div>
    `;
  }

  function renderWatchOuts(el, items) {
    if (!el) return;
    if (!items || !items.length) {
      el.innerHTML = `<div class="brain-empty">No watch-outs — every row has outcome ≥ -0.3.</div>`;
      return;
    }
    const rows = items.map(it => `
      <div class="brain-row" data-kind="${escapeHtml(it.kind)}" data-id="${escapeHtml(it.id)}">
        <span class="row-kind">${escapeHtml(it.kind)}</span>
        <span class="row-gist" title="${escapeHtml(it.gist || "")}">${escapeHtml(it.gist || "—")}</span>
        ${outcomePill(it.outcome_score)}
        <span class="row-id">${escapeHtml((it.id || "").slice(0, 18))}</span>
      </div>`).join("");
    el.innerHTML = `<div class="brain-list">${rows}</div>`;
  }

  function renderRecentInsights(el, items) {
    if (!el) return;
    if (!items || !items.length) {
      el.innerHTML = `<div class="brain-empty">No candidate insights right now.</div>`;
      return;
    }
    const rows = items.map(it => {
      const conf = Number(it.confidence) || 0;
      const surfaceBadge = it.surface_count >= 2
        ? `<span class="row-meta" style="color:var(--accent)">surfaced×${it.surface_count}</span>`
        : `<span class="row-meta">surfaced×${it.surface_count || 0}</span>`;
      return `
        <div class="brain-row" data-kind="insight" data-id="${escapeHtml(it.id)}">
          <span class="row-kind">${escapeHtml(it.insight_type || "insight")}</span>
          <span class="row-gist" title="${escapeHtml(it.summary || "")}">${escapeHtml(it.gist || it.summary || "—")}</span>
          <span class="row-meta">conf ${conf.toFixed(2)}</span>
          ${surfaceBadge}
        </div>`;
    }).join("");
    el.innerHTML = `<div class="brain-list">${rows}</div>`;
  }

  function renderOutcomeBar(el, dist) {
    if (!el) return;
    if (!dist || !dist.total) {
      el.innerHTML = `<div class="brain-empty">No data.</div>`;
      return;
    }
    const t = dist.total;
    const p = (dist.positive / t) * 100;
    const n = (dist.negative / t) * 100;
    const m = 100 - p - n;
    el.innerHTML = `
      <div class="outcome-bar" title="pos ${dist.positive} · neu ${dist.neutral} · neg ${dist.negative}">
        <div class="outcome-bar-seg-pos" style="width:${p}%"></div>
        <div class="outcome-bar-seg-neu" style="width:${m}%"></div>
        <div class="outcome-bar-seg-neg" style="width:${n}%"></div>
      </div>
      <div class="metric-tile-sub">
        <strong>${dist.positive}</strong>+ · ${dist.neutral}· · <strong>${dist.negative}</strong>−
        of ${t} (mean ${dist.mean >= 0 ? "+" : ""}${(dist.mean || 0).toFixed(3)})
      </div>
    `;
  }

  async function refreshSelfModel(workspaceId) {
    try {
      const r = await fetch("/memory/self_model/refresh", {
        method: "POST",
        headers: window.AppHeader ? window.AppHeader.jsonHeaders(workspaceId) : { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId, use_ollama: false }),
      });
      const j = await r.json();
      if (window.AppHeader && j.ok) window.AppHeader.toast("self-model refreshed", "success");
      else if (window.AppHeader) window.AppHeader.toast("refresh failed: " + (j.error?.message || "?"), "error");
      return j.ok ? j.data : null;
    } catch (e) {
      if (window.AppHeader) window.AppHeader.toast("refresh failed: " + e.message, "error");
      return null;
    }
  }

  window.BrainUI = {
    fetchState,
    renderSelfModel,
    renderWatchOuts,
    renderRecentInsights,
    renderOutcomeBar,
    outcomePill,
    outcomeClass,
    refreshSelfModel,
    escapeHtml,
  };
})();
