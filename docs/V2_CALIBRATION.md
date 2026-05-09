# v2.0 — v2.1.x calibration notes

This document records the threshold defaults and design choices
made across the v1.4 → v2.1.x code-memory roadmap, plus the
empirical targets for each. Pair with
[`V1_1_0_CALIBRATION.md`](V1_1_0_CALIBRATION.md), which covers
the same exercise for the v1.0 — v1.10 retrieval / behavior
loops.

The numbers below are **starting defaults**. They are NOT yet
validated against a 1000+ symbol production workspace; the
v2.1.5 polish phase explicitly defers full calibration to "after
the substrate has accumulated real data." Treat this doc as the
target table; future commits will fill in actual measurements.

---

## v2.1.4 — `similar_signature` threshold

**Default**: `MEMORY_SIMILAR_SIG_THRESHOLD=0.7`.

**Why 0.7**: bag-of-tokens MinHash with 64 permutations on a
typical 10-30 token signature lands two parallel implementations
(``fetch_users(client) -> list[User]`` vs ``fetch_orders(client)
-> list[Order]``) at Jaccard ≈ 0.5. Closer parallels (e.g.
``handle_get(req)`` vs ``handle_post(req)``) hit 0.7+. Default
0.7 is calibrated for "very similar signatures only" — closer
to "duplicate detection" than "fuzzy match".

**Calibration target**:

* Healthy positive rate: **5-15% of new versions** produce ≥1
  similar_signature edge.
* If observed rate < 5%: lower threshold to 0.6.
* If observed rate > 25%: raise to 0.8.
* Per-workspace, not global — the operator can override via env.

**Test guard**: the e2e fixture lowers threshold to 0.4 because
6-token synthetic signatures don't naturally cross 0.7.

---

## v2.1.4 — `similar_signature` scan limit

**Default**: `MEMORY_SIMILAR_SIG_SCAN_LIMIT=200`.

**Why 200**: per-version cost is O(N) MinHash computations,
~0.1 ms each → 20 ms per version. On a typical re-ingest of 50
symbols that's 1s overhead — acceptable for foreground ingest.

**Calibration target**: `record_similar_signature_edges` should
add **< 50 ms** to the typical file-ingest pass. Above that, an
LSH index is justified (deferred to v2.2.x).

---

## v1.6.0 — `breaking_changes` window

**Default**: `since_days=7` (per request, not env-configurable).

**Why 7**: matches typical sprint cadence. "What signatures
changed this week?" is the natural question right before a
release.

**Calibration target**: typical pre-release scan should return
**5-30 changes** for a 1000-symbol workspace. > 100 means too
much churn; the operator should tighten review processes
upstream of memory.

---

## v1.7.0 — `active_edits` TTL

**Default**: 30 minutes (per `claim_edit` call, not env-default).

**Why 30 min**: long enough for a focused multi-step refactor;
short enough that a crashed agent doesn't lock the symbol all
day. Lazy expiry on every read so no background worker.

**Calibration target**: < 1% of all `claim_edit` calls should
hit a 409 conflict in normal operation. Higher means agents are
fighting for the same hot spots and the workspace should be
sharded or coordination loop tightened.

---

## v1.7.0 — `co_changed` weight increment

**Default**: `weight_increment=1.0` per pair, per ingest pass.

**Why 1.0**: easy to reason about — weight equals "how many
ingest passes saw these two symbols change together". A symbol
pair with weight 5 changes together 5x more often than weight
1.

**Calibration target**: typical ranks of `soft_neighbors`
should expose meaningful clusters (5-15 frequently-co-changing
neighbors) rather than every-pair-touched-once noise. If every
result has weight 1, the workspace is too young to draw
inferences from soft graph yet.

---

## v2.1.3 — LLM narrative timeout

**Default**: `MEMORY_LLM_NARRATIVE_TIMEOUT_SEC=10.0`.

**Why 10s**: a 7B Ollama model returns a 2-3 sentence summary
for a typical file in 2-6 seconds on a workstation GPU. 10s
covers worst-case (cold model load, large prompt) without
blocking ingest indefinitely.

**Calibration target**: < 5% of LLM narrative calls should hit
the timeout. If you're seeing more, switch to a smaller model
(`qwen2.5:3b-instruct`) or raise the timeout.

---

## v2.1.2 — D3 dashboard `max_nodes`

**Default**: 200 nodes per graph render.

**Why 200**: D3 force layout becomes sluggish above ~500 nodes
on a typical browser. 200 is the sweet spot between "see most
of a small workspace" and "render cleanly".

**Calibration target**: dashboard should render in < 1 second
for 200 nodes on commodity hardware. The `truncated` flag in
the response tells the operator when they're seeing a slice.

---

## Future calibration work

The following measurements are NOT yet captured. Each should
land as a follow-up with actual numbers from the
agent-memory-lite + copyBot workspaces:

1. **`similar_signature` actual positive rate** — replay
   v1.5–v2.1.4 ingest passes on copyBot's substrate; count
   resulting `similar_signature` edges; tune threshold.
2. **`code_overview` query latency on 1000+ symbols** — set up
   a benchmark harness that ingests a synthetic 1000-symbol
   workspace and measures wall time for `/memory/code_overview`,
   `/memory/code_graph`, `/memory/breaking_changes`.
3. **LLM narrative qualitative quality** — operator review of
   10 random files from copyBot with `MEMORY_LLM_NARRATIVE_ENABLED=true`.
   Score each on a 1-5 scale ("the heuristic was already fine"
   = 1, "the LLM made the file's purpose obvious" = 5). Median
   ≥ 3 is a pass.
4. **`active_edits` 409 rate** — count `409 conflict` responses
   from `claim_edit` over a 7-day window. Should be < 1%.
5. **MinHash perf budget** — wall-clock per-version overhead of
   the similarity accumulator on the agent-memory-lite ingest
   workload. Should be < 50 ms per version.

Once filled in, this document becomes the operator's source of
truth for threshold defaults vs. observed behavior. Until then,
the defaults above are educated starting points only.
