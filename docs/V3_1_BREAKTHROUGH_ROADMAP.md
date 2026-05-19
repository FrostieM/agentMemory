# v3.1+ Breakthrough Roadmap — Active Memory

**Status:** Not started. Recorded for future planning. v3.0.0-final must ship first.

## Context

v3.0.0-final landed an organ-shaped memory: outcome-weighted ranking, Hebbian
co-retrieval, sleep consolidation, PreToolUse reflexes, per-workspace self-
model, bi-temporal facts, spreading-activation recall. All seven phases ship
local-first, flag-gated, with empirical validation on copyBot (3 real bugs
found and fixed via emergency scenario testing).

This is **good engineering + thoughtful biology defenses**. It is **not a
research breakthrough**. Every individual phase has precedent (Reflexion,
Zep, Mem0, generative agents, SPeCtrum, PreFlect, HeLa-Mem).

The breakthrough vector is **activity**: memory that does more than store +
retrieve. v3.1+ pursues six concrete capabilities that no shipped competitor
has, in order of implementation feasibility.

---

## Six vectors of active memory

### Vector 1 — Memory proposes experiments (priority: high; effort: 2-3w)

**What:** Self-driven hypothesis generation. Memory scans uncertain insights
(confidence 0.4-0.7) and unmeasured concepts, produces proto-theories with
validation_criteria pre-filled, writes them with `status='proposed'`,
`source='memory_self_proposal'`.

**Concrete first step:**

```python
def propose_experiments(conn, ws):
    uncertain = fetch_uncertain_insights(conn, ws, conf_range=(0.4, 0.7))
    for ins in uncertain:
        unmeasured = find_unmeasured_concepts_near(conn, ws, ins.tags)
        if not unmeasured:
            continue
        proposal = ollama_generate_theory(ins, unmeasured, schema=THEORY_SCHEMA)
        write_theory(proposal, source='memory_self_proposal')
```

**Hard part:** LLM proposal quality. **Mitigation:** tight schema + ≥3
similar episode-cluster evidence requirement before proposal.

**Why this matters:** Memory shifts from **reactive** to **initiating**.
First step toward Reflexion-like learning at the memory layer.

---

### Vector 2 — Adaptive retrieval (priority: medium; effort: 3-5w)

**What:** Retrieval hyperparameters (`recall_depth`, `outcome_floor`,
`spreading_min_weight`) tune themselves based on agent-outcome correlation.

**Concrete first step:** Bayesian-style A/B loop. Every N agent sessions,
50% of retrievals use current best config, 50% use a perturbation; measure
"post-retrieval outcome delta" (next 10 episodes); keep the winner.

```python
class RetrievalConfig:
    recall_depth: int
    outcome_floor: float
    spreading_min_weight: float

def adaptive_loop(conn, ws):
    current = load_best_config(ws)
    candidate = perturb(current)
    score_a = measure_outcome_delta(conn, ws, config=current)
    score_b = measure_outcome_delta(conn, ws, config=candidate)
    if score_b > score_a + EPSILON:
        save_best_config(ws, candidate)
```

**Hard part:** noise in "outcome delta" proxy without ground-truth labels.
**Mitigation:** require multi-sample agreement before promotion; floor on
EPSILON.

**Risk:** parameter drift compounds errors. Guard with hard reset to
defaults if performance regresses.

---

### Vector 3 — Blindspot detection (priority: high; effort: 1-2d)

**What:** Structural asymmetry — what's frequently mentioned but never
becomes a documented decision.

**Concrete first step:**

```python
def detect_blindspots(conn, ws, lookback_days=90):
    ep_tokens = tokenize_corpus(fetch_episodes(conn, ws, days=lookback_days))
    dec_tokens = tokenize_corpus(fetch_decisions(conn, ws, days=lookback_days))
    return [
        {"token": t, "episode_count": c, "decision_count": dec_tokens[t]}
        for t, c in ep_tokens.most_common(100)
        if c >= 5 and dec_tokens[t] == 0
    ]
```

**Hard part:** token noise (stopwords, generic words). Use bigrams + named
entity extraction + workspace-specific stopword learning.

**This is quick win.** Surfaces immediately in the brief's identity section
("STRUCTURAL: you've mentioned X 24 times in episodes without ever writing
a decision about it").

---

### Vector 4 — Learned causality (priority: medium-low; effort: 4-6w)

**What:** Move `causal_links` from structural extraction (supersedes pointer,
JSON evidence ids) to **learned** inference.

**Three approaches, all noisy:**

(a) **Difference-in-differences on supersede events.** For each `dec_new
supersedes dec_old`, compare outcome metrics 7d before vs 7d after the
write. If significant delta, emit `caused(weight=|delta|)`.

(b) **Granger causality on episode-token time series.** Token frequency
matrix over time; test which topics predict which others' onset.

(c) **Counterfactual via embedding similarity.** Find decisions with similar
pre-state but different content; compare downstream outcome.

**Hard part:** confounding (deploy + supersede + outcome up — which caused?).
Without randomization, all causality is correlation-with-strong-prior.

**Mitigation:** emit causal links with confidence band; consumers (recall,
brief) weight by confidence. Multi-method agreement boosts confidence.

---

### Vector 5 — Predictive failure detection (priority: medium; effort: 3-4w)

**What:** Per-workspace classifier on `audit_log` history. Predicts whether
the next 10 episodes' outcomes will trend negative given the current tool
call.

**Concrete first step:**

```python
def collect_training(conn, ws):
    samples = []
    for evt in audit_events(ws):
        features = extract_features(evt)  # tool_kind, payload tokens,
                                          # trail length, recent outcome trend
        post = subsequent_outcome_trend(conn, ws, evt.ts, window=10)
        label = 1 if post < -0.2 else 0
        samples.append((features, label))
    return samples

clf = LogisticRegression()
clf.fit(X, y)

# In lint:
prob = clf.predict_proba(current_features)[0][1]
if prob > 0.6:
    surface_warning(prob)
```

**Hard part:** cold-start (need ~500 tool events with outcome labels per
workspace), feature engineering, label noise (outcome trend has many
confounders).

**Why this matters:** Closes the loop from `prior_failures` (keyword-match
lookup) to `predicted_failure` (learned). Real PreFlect implementation.

---

### Vector 6 — Inter-agent memory negotiation (priority: low; effort: 6m+)

**What:** Multi-writer memory where two agents (potentially different model
families) can disagree, propose disputes, and reach consensus.

**Concrete first step (operator-in-the-loop):**

```sql
CREATE TABLE memory_disputes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    claimant_agent_id TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'open',
    resolution TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
```

MCP tool: `memory_propose_dispute(target_id, claim, evidence)`. Operator
resolves via `/ui/disputes`. Long-term: agent reputation derived from
historical dispute-resolution outcomes drives auto-resolution.

**This is research territory.** Full automation requires solving alignment
+ trust between agents, which is an open problem.

---

## Implementation order

If we pursue this:

1. **Blindspot detection** — 1-2 days, immediate value, low risk
2. **Memory proposes experiments** — 2-3 weeks, first step into active memory
3. **Predictive failure detection** — 3-4 weeks (after some adoption data
   accumulates from prod use of v3.0.0-final)
4. **Adaptive retrieval** — 3-5 weeks (depends on 3 for data + safety
   net)
5. **Learned causality** — 4-6 weeks (research-grade, accept noise)
6. **Inter-agent negotiation** — defer to v4.0+

Realistic 3-4 month single-developer track gets us through 1-3 + start of 4.

## What we do NOT do in v3.1+

- Multi-tenant SaaS — staying local-first
- Cloud model providers — staying Ollama-only
- Embedding-on-everything — selective, only where it pays
- Replacing CLAUDE.md / AGENTS.md with dynamic agent contract — keeping
  human-readable, agent-derived narrative separate

## Reality check

This roadmap is **ambitious**. Every vector has research-grade uncertainty.
If v3.1 ships only vectors 1+3 (proposes experiments + blindspot detection)
in 4 weeks, that's already a meaningful step toward "active memory."

We do not need to ship all six. We need each one to **work and not regress
the v3.0.0-final baseline**. Quality gates from v3.0.0 (organ_pass,
organ_metrics, organ_crash_test, organ_scenario_test) must stay green
through every v3.1 commit.
