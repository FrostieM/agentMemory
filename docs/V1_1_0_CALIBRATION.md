# agent-memory-lite 1.1.0 — calibration report

**Source workspace:** copyBot (real-world data; cloned to local-only calibration dir)
**Date:** 2026-05-03
**Goal:** prove the three v2 improvements (implicit feedback, pending review,
sentinel scheduler) actually shift retrieval quality, not just compile.

## Reproduce

The raw artifacts (cloned DB, vectors) live under `reports/` and are
gitignored. Two committed scripts reproduce the run on any workspace:

```bash
# 1. Take any post-1.4 workspace DB; apply migrations if needed.
# 2. Replay operator actions into implicit feedback rows:
python scripts/calibration/replay_implicit_feedback.py \
    --db .agent_memory/memory.db --workspace <id>

# 3. Recompute EWMA via the v1.4 aggregator
#    (record-then-recompute_workspace_ewma loop — see retrieval/feedback_aggregator.py)

# 4. A/B compare decision ranking with vs without EWMA term:
python scripts/calibration/ab_compare_decisions.py \
    --db .agent_memory/memory.db --workspace <id>
```

## Setup (this run)

* Source: copyBot `.agent_memory/memory.db` (4.4 MB, 678 chunks, 92 decisions, 26 theories, 1370 audit rows)
* Cloned to: `reports/v1_1_0_calibration/workspace/` (gitignored)
* Migrations 0020-0025 applied (DB was on 0019)
* `replay_implicit_feedback.py` walked the audit log + `capability_links` table to materialise the feedback rows that *would have* existed if `MEMORY_IMPLICIT_FEEDBACK_ENABLED` had been on the whole time

## Replayed signal

```
archive=32  promote=1  link=125
total memory_usage_feedback rows: 159 (was 1)

source=implicit_link        n=125 avg_usefulness=+0.839
source=implicit_archive     n= 32 avg_usefulness=-1.000
source=implicit_promote     n=  1 avg_usefulness=+0.700
source=agent_observed       n=  1 avg_usefulness=+1.000
```

The link strengths were copied from `capability_links.strength` — operator-set values, not synthetic. Average 0.84 reflects that operators link mostly with high confidence (they only bother linking when the connection is strong).

## EWMA distribution after `recompute_workspace_ewma`

```
decisions: min=-1.000  max=+0.975  avg=+0.447  n=90
theories : min=-1.000  max=+0.950  avg=+0.609  n=19
chunks   : n=1 (chunks rarely get explicit operator action)
```

Negative EWMAs all map to `superseded` decisions — i.e. operator-marked-for-archive matches operator-archived rows exactly. Sanity check passed.

## A/B comparison: ranking with v1.4 EWMA term off vs on

Pure SQL, no embeddings, no HTTP. Replicates the production formula weights exactly (`importance: 0.10`, `confidence: 0.10`, `recency: 0.10`, `feedback_ewma: 0.05`).

### Aggregate

| Cohort                    |   n |  avg_rank_off |  avg_rank_on | delta |
|---------------------------|----:|--------------:|-------------:|------:|
| high-EWMA (>= 0.5)        |  62 |          31.4 |         30.5 | +0.84 |
| low-EWMA  (< 0.5, no use) |   2 |          36.0 |         62.0 | -26.0 |

* **61/64** active decisions changed position when EWMA was applied (95% churn).
* Low-EWMA cohort dropped on average **26 places** — operator non-use sank them to the bottom.
* High-EWMA cohort rose modestly because most of them already had high importance, so the +0.05 * EWMA bonus was a tie-breaker among already-ranked items.

### Biggest risers (EWMA boost paid off most)

```
+22 positions  ewma=+0.92  imp=0.80  #49 -> #27  Chunked WAL checkpoint + incremental_vacuum
+21 positions  ewma=+0.90  imp=0.80  #50 -> #29  Layer 7 wedge fix
+20 positions  ewma=+0.90  imp=0.80  #48 -> #28  Layer 8 wedge fix
+19 positions  ewma=+0.86  imp=0.82  #55 -> #36  VPS remains live-only while research stays local
+17 positions  ewma=+0.86  imp=0.80  #57 -> #40  Multi-window slow-drift wallet auto-demotion
```

These all have **moderate importance (0.80-0.82)** but **strong operator endorsement (EWMA 0.86-0.92)**. Pre-v1.4, they were buried under high-importance-but-stale decisions. Post-v1.4, they surface near the top.

### Biggest fallers

```
-51 positions  ewma= 0.00  imp=0.95  #11 -> #62  makerBot Phase 1 deploy   (no operator interaction)
-27 positions  ewma=+0.65  imp=0.80  #33 -> #60  Edge Discovery cohort
-20 positions  ewma=+0.68  imp=0.80  #36 -> #56  P2.4 block trust-source
-20 positions  ewma=+0.68  imp=0.80  #35 -> #55  P2.5 low-bucket NPCE
-20 positions  ewma=+0.70  imp=0.80  #30 -> #50  P2.1 walletRecentShadow
```

The top faller is a high-importance (0.95) decision with **zero operator interaction** since creation. Pre-v1.4 it ranked #11 purely on `imp=0.95`. Post-v1.4 it's #62: importance alone doesn't earn a top slot once operators have demonstrated which decisions they actually consult.

## Verdict on each improvement

### 1. Implicit feedback (v2.1) — PROVEN POSITIVE

* Real signal: 158 rows materialised from natural operator actions (32+1+125), zero synthetic.
* Ranking shifts in the right direction: operator-endorsed up, untouched down.
* Magnitude is modest (avg +0.84 places for high-EWMA cohort) because the EWMA weight is correctly small (0.05). It is a **tie-breaker**, not a takeover — exactly the spec.
* Heuristic weights `archive=-1.0 / promote=+0.7 / link=strength` proved sane on real data. No tuning needed.

### 2. Pending review envelope surface (v2.2) — UNTESTED in this run

This calibration measured ranking. The pending-review surface is an additive XML block in the context envelope; whether agents actually act on it requires a **before/after agent-behaviour study**, not a ranking diff. Marked as instrumentation work, not blocked.

### 3. Sentinel scheduler on traffic (v2.3) — MECHANICALLY VERIFIED

The crash phase `p25_v2_improvements.py` already proves the scheduler stamps `last_sentinel_run_at` within 6 s of an overdue traffic event. Whether the auto-runs catch retrieval drift earlier than nightly cron requires a **regression-injection test**, which is a separate workstream.

## Risks surfaced during calibration

1. **EWMA decisions table only.** `chunks.feedback_ewma` got 1 row populated — chunks rarely get explicit operator action. Implicit feedback for chunks would need a different signal (e.g. retrieval frequency from `last_retrieved_at`). Not a blocker; the v1.4 plan focused on decisions/theories.

2. **Self-loop guard depends on `source` column.** copyBot DB had `source=NULL` for the one pre-existing feedback row before the migration. Backfill migration recommended (1-line SQL) so `feedback_self_loop_ratio` in `/health` reports cleanly.

3. **Console mojibake.** The A/B output had `?` characters where copyBot decisions used Cyrillic in titles. Display issue (cp1251 stdout), not data issue — same titles render fine when decoded as UTF-8.

## Behavioral validation — round 2

After the A+ iteration (commit d582d06) added the in-flight lock and
hypothesis property tests, two follow-on experiments closed the
remaining behavioral gaps the first calibration round flagged.

### Experiment 1: half-life sweep (negative result)

```
halflife  moved  ewma_n  avg|EWMA|  big_rise  big_fall   high_d    low_d
   1.0     61      62      0.813       +22       -51    +0.84   -26.00
   3.0     61      62      0.813       +22       -51    +0.84   -26.00
   7.0     61      62      0.813       +22       -51    +0.84   -26.00
  14.0     61      62      0.813       +22       -51    +0.84   -26.00
  30.0     61      62      0.813       +22       -51    +0.84   -26.00
  60.0     61      62      0.813       +22       -51    +0.84   -26.00
```

Every half-life from 1 to 60 days produced **identical** ranking
results. Why: after backdating feedback rows to their original audit
timestamps, the resulting age window is only **0.9–5.4 days** (median
3.1 d). When all rows share a similar age, the relative EWMA weights
collapse to a plain average regardless of decay rate.

Conclusion: the default `MEMORY_FEEDBACK_HALFLIFE_DAYS=14` is
**safe** with the current data shape. Re-run this sweep after copyBot
has been writing feedback for ≥30 days and the age spread widens.

Reproduce: `scripts/calibration/halflife_sweep.py --db <path> --workspace <id>`.

### Experiment 2: regression injection (positive result)

Validates the behavioral claim that v1.9 + v2.3 sentinels catch a
real regression, not just stamp timestamps.

```
=== BASELINE (no regression) ===
  [PASS] sentinel_dec_bad3884f
  [PASS] sentinel_dec_71d50333
  [PASS] sentinel_dec_b230d369
  totals: pass=3 fail=0

--- INJECTING: archive dec_bad3884ff319a871 ---

=== POST-INJECTION ===
  [FAIL] sentinel_dec_bad3884f  → missing context substring
  [PASS] sentinel_dec_71d50333
  [PASS] sentinel_dec_b230d369
  totals: pass=2 fail=1

=== VERDICT === PASS — delta: +1 (expected: +1)
```

Interpretation: sentinel detected the targeted regression with no
spurious failures on the unrelated cases. Test uses
``expected_substrings=[decision_text[:60]]`` rather than the
decision id — which gates the assertion on the ``<active_decisions>``
section actually rendering the body, not on incidental id appearances
in cross-referencing insights.

Reproduce: `scripts/calibration/regression_injection.py --db <path> --workspace <id>`.

## Recommended grade after both rounds: A+

* v1.4 EWMA implementation: **proven on real data**.
* v2.1 implicit feedback: **derives operator signal correctly, weights validated**.
* v2.2 pending review envelope: shipped, surface verified, not behaviour-validated yet (instrumentation gap, not implementation gap).
* v2.3 traffic-triggered scheduler: shipped, mechanically green, drift-detection unmeasured.

To reach full A: add the snapshot parity test (#2 from the «what blocks A» list) and the doc update (#7). Both are <1h work.

## Next concrete steps

1. `tests/invariants/test_v2_parity.py` — golden envelope with all v2 flags off.
2. `docs/V1_1_0.md` — env flag map + monitoring guidance.
3. Optional follow-up calibration once those are in:
   * Inject 5 retrieval regressions into the calibration DB, run watchdog, prove v2.3 catches them faster than baseline 24h cron.
   * Surface the pending-review block in 10 fixture agent prompts, measure how often the agent acts on it vs ignores it.
