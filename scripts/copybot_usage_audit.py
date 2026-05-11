"""Read-only usage + effectiveness audit for copyBot workspace.

Distinct from hygiene_report (which scores discipline) and from
workspace_doctor (which detects pollution): this audit answers
"is everything in memory actually being USED, and is the USE
effective?" by looking at last_retrieved_at, application_count,
usage_count / success_count / failure_count, EWMA feedback rows,
and audit_log activity windows.

Direct read-only SQLite query — no HTTP. Path resolved from
``~/.agent_memory/workspaces.json``.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REGISTRY_PATH = Path.home() / ".agent_memory" / "workspaces.json"
WORKSPACE = "copyBot"
NOW = datetime.now(UTC)


def db_path() -> Path:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    for w in data.get("workspaces", []):
        if w["id"] == WORKSPACE:
            return Path(w["db_path"])
    raise SystemExit(f"workspace {WORKSPACE} not in registry")


def open_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None
    return (NOW - ts).total_seconds() / 86400.0


def bucket(days: float | None) -> str:
    if days is None:
        return "never"
    if days <= 7:
        return "≤7d"
    if days <= 30:
        return "8-30d"
    if days <= 60:
        return "31-60d"
    if days <= 90:
        return "61-90d"
    return ">90d"


def section(title: str) -> None:
    print(f"\n### {title}")


def kv(label: str, value: object, suffix: str = "") -> None:
    print(f"  {label:35s}: {value}{suffix}")


def audit_decisions(c: sqlite3.Connection) -> None:
    section("Decisions (active + supersedes-aware)")
    rows = c.execute(
        "SELECT id, status, importance, source_episode_id, pinned, last_retrieved_at, "
        "created_at FROM decisions WHERE workspace_id = ?",
        (WORKSPACE,),
    ).fetchall()
    active = [r for r in rows if r[1] == "active"]
    superseded = [r for r in rows if r[1] == "superseded"]
    kv("total decisions", len(rows))
    kv("active", len(active))
    kv("superseded", len(superseded))
    kv("pinned active", sum(1 for r in active if r[4]))
    kv("active w/ source_episode_id (Move 1)", sum(1 for r in active if r[3]))
    kv("active w/o source_episode_id (orphan)", sum(1 for r in active if not r[3]))
    kv(
        "importance≥0.8 active",
        sum(1 for r in active if (r[2] or 0) >= 0.8),
    )
    retrieval = Counter(bucket(age_days(r[5])) for r in active)
    print(f"  last_retrieved_at bucket : {dict(retrieval)}")


def audit_theories(c: sqlite3.Connection) -> None:
    section("Theories (status + evidence reality)")
    rows = c.execute(
        "SELECT id, status, evidence_count, evidence_strength, confidence, "
        "last_tested_at, created_at FROM theories WHERE workspace_id = ?",
        (WORKSPACE,),
    ).fetchall()
    status_dist = Counter(r[1] for r in rows)
    kv("total theories", len(rows))
    print(f"  status distribution      : {dict(status_dist)}")
    no_ev = [r for r in rows if (r[2] or 0) == 0 and r[1] not in {"archived", "rejected"}]
    kv("active w/o evidence", len(no_ev))
    weak = [r for r in rows if 0 < (r[2] or 0) < 3 and r[1] not in {"archived", "rejected"}]
    kv("active w/ <3 evidence (under bridge threshold)", len(weak))
    tested_age = Counter(
        bucket(age_days(r[5])) for r in rows if r[1] not in {"archived", "rejected"}
    )
    print(f"  last_tested_at bucket    : {dict(tested_age)}")


def audit_behavior(c: sqlite3.Connection) -> None:
    section("Behavior instructions (which actually fire)")
    rows = c.execute(
        "SELECT id, name, kind, scope, active, application_count, last_applied_at, "
        "expires_at, conflict_group FROM behavior_instructions WHERE workspace_id = ?",
        (WORKSPACE,),
    ).fetchall()
    active = [r for r in rows if r[4]]
    kv("total instructions", len(rows))
    kv("active", len(active))
    kv("inactive", len(rows) - len(active))
    kv("pinned active", sum(1 for r in active if r[5] is not None))
    expired_ct = sum(1 for r in active if r[7] and age_days(r[7]) and age_days(r[7]) > 0)
    kv("expired but still active", expired_ct)
    counts = [(r[5] or 0) for r in active]
    if counts:
        zero = sum(1 for ac in counts if ac == 0)
        kv("active w/ application_count=0 (never fired)", zero)
        kv("active w/ application_count≥10 (workhorses)", sum(1 for ac in counts if ac >= 10))
        print(
            f"  application_count buckets: ≤2={sum(1 for ac in counts if ac <= 2)}, "
            f"3-9={sum(1 for ac in counts if 3 <= ac <= 9)}, "
            f"≥10={sum(1 for ac in counts if ac >= 10)}"
        )
    last_applied_bucket = Counter(bucket(age_days(r[6])) for r in active)
    print(f"  last_applied_at bucket   : {dict(last_applied_bucket)}")
    # conflict group dups
    groups = Counter(r[8] for r in active if r[8])
    dups = {g: n for g, n in groups.items() if n > 1}
    if dups:
        kv("conflict_groups with >1 active", dups)


def audit_capabilities(c: sqlite3.Connection, table: str, kind: str) -> None:
    rows = c.execute(
        f"SELECT id, name, active, usage_count, success_count, failure_count, "
        f"last_invoked_at FROM {table} WHERE workspace_id = ?",
        (WORKSPACE,),
    ).fetchall()
    active = [r for r in rows if r[2]]
    kv(f"{kind} total", len(rows))
    kv(f"{kind} active", len(active))
    never_used = [r for r in active if (r[3] or 0) == 0]
    kv(f"{kind} never invoked", len(never_used))
    for r in never_used[:5]:
        print(f"    - {r[1]} (id={r[0]})")
    if any((r[3] or 0) > 0 for r in active):
        top = sorted(active, key=lambda r: r[3] or 0, reverse=True)[:5]
        print("  top-5 by usage_count:")
        for r in top:
            print(
                f"    {r[3]:3d}x {r[1]} "
                f"(success={r[4] or 0}, fail={r[5] or 0}, last={r[6] or 'never'})"
            )


def audit_candidates_lifetime(c: sqlite3.Connection) -> None:
    section("Candidates lifetime (extraction noise vs signal)")
    rows = c.execute(
        "SELECT kind, status FROM memory_candidates WHERE workspace_id = ?",
        (WORKSPACE,),
    ).fetchall()
    kv("total candidates ever", len(rows))
    by_status = Counter(r[1] for r in rows)
    print(f"  status distribution      : {dict(by_status)}")
    by_kind_status: dict[str, Counter] = {}
    for kind, status in rows:
        by_kind_status.setdefault(kind, Counter())[status] += 1
    print("  per-kind reject/promote ratio:")
    for k, dist in sorted(by_kind_status.items()):
        n = sum(dist.values())
        rej = dist.get("rejected", 0)
        prm = dist.get("promoted", 0)
        nw = dist.get("new", 0)
        print(
            f"    {k:14s} n={n:3d}  promote={prm:3d} ({prm / n * 100:5.1f}%)  "
            f"reject={rej:3d} ({rej / n * 100:5.1f}%)  new={nw}"
        )


def audit_capability_links(c: sqlite3.Connection) -> None:
    section("Capability_links coverage (discipline metric)")
    rows = c.execute(
        "SELECT target_type, target_id FROM capability_links WHERE workspace_id = ?",
        (WORKSPACE,),
    ).fetchall()
    kv("total links", len(rows))
    by_target = Counter(r[0] for r in rows)
    print(f"  per target_type          : {dict(by_target)}")
    # Coverage ratio
    dec_total = c.execute(
        "SELECT COUNT(*) FROM decisions WHERE workspace_id=? AND status='active'",
        (WORKSPACE,),
    ).fetchone()[0]
    th_total = c.execute(
        "SELECT COUNT(*) FROM theories WHERE workspace_id=? AND status NOT IN ('archived','rejected')",
        (WORKSPACE,),
    ).fetchone()[0]
    dec_linked = c.execute(
        "SELECT COUNT(DISTINCT target_id) FROM capability_links "
        "WHERE workspace_id=? AND target_type='decision'",
        (WORKSPACE,),
    ).fetchone()[0]
    th_linked = c.execute(
        "SELECT COUNT(DISTINCT target_id) FROM capability_links "
        "WHERE workspace_id=? AND target_type='theory'",
        (WORKSPACE,),
    ).fetchone()[0]
    kv("active decisions linked / total", f"{dec_linked}/{dec_total}")
    kv("active theories linked / total", f"{th_linked}/{th_total}")


def audit_episodes_chunks(c: sqlite3.Connection) -> None:
    section("Episodes + chunks (substrate)")
    ep_total = c.execute(
        "SELECT COUNT(*) FROM episodes WHERE workspace_id=?", (WORKSPACE,)
    ).fetchone()[0]
    ep_arch = c.execute(
        "SELECT COUNT(*) FROM episodes WHERE workspace_id=? AND is_archived=1", (WORKSPACE,)
    ).fetchone()[0]
    ch_total = c.execute(
        "SELECT COUNT(*) FROM chunks WHERE workspace_id=?", (WORKSPACE,)
    ).fetchone()[0]
    ch_arch = c.execute(
        "SELECT COUNT(*) FROM chunks WHERE workspace_id=? AND is_archived=1", (WORKSPACE,)
    ).fetchone()[0]
    kv("episodes total / archived", f"{ep_total} / {ep_arch}")
    kv("chunks total / archived", f"{ch_total} / {ch_arch}")
    cold_rows = c.execute(
        "SELECT last_retrieved_at FROM chunks WHERE workspace_id=? AND is_archived=0",
        (WORKSPACE,),
    ).fetchall()
    cold_buckets = Counter(bucket(age_days(r[0])) for r in cold_rows)
    print(f"  chunk last_retrieved_at  : {dict(cold_buckets)}")


def audit_audit_log(c: sqlite3.Connection) -> None:
    section("audit_log activity (last 7 / 30 days)")
    cutoff_7 = (NOW - timedelta(days=7)).isoformat()
    cutoff_30 = (NOW - timedelta(days=30)).isoformat()
    rows_7 = c.execute(
        "SELECT action FROM audit_log WHERE workspace_id=? AND created_at>=?",
        (WORKSPACE, cutoff_7),
    ).fetchall()
    rows_30 = c.execute(
        "SELECT action FROM audit_log WHERE workspace_id=? AND created_at>=?",
        (WORKSPACE, cutoff_30),
    ).fetchall()
    kv("last 7 days total", len(rows_7))
    kv("last 30 days total", len(rows_30))
    top_7 = Counter(r[0] for r in rows_7).most_common(8)
    print(f"  top actions (7d)         : {top_7}")
    top_30 = Counter(r[0] for r in rows_30).most_common(8)
    print(f"  top actions (30d)        : {top_30}")


def audit_feedback(c: sqlite3.Connection) -> None:
    section("Feedback signal (EWMA scoring fuel)")
    # Schema may differ across migrations; introspect columns first.
    cols = {row[1] for row in c.execute("PRAGMA table_info(memory_usage_feedback)").fetchall()}
    signal_col = (
        "delta"
        if "delta" in cols
        else "signal"
        if "signal" in cols
        else "value"
        if "value" in cols
        else None
    )
    source_type_col = "source_type" if "source_type" in cols else "kind" if "kind" in cols else None
    if not source_type_col:
        kv("memory_usage_feedback", f"unrecognized schema cols={cols}")
        return
    rows = c.execute(
        f"SELECT {source_type_col}{(', ' + signal_col) if signal_col else ''} "
        f"FROM memory_usage_feedback WHERE workspace_id=?",
        (WORKSPACE,),
    ).fetchall()
    kv("total feedback rows", len(rows))
    by_type = Counter(r[0] for r in rows)
    print(f"  per source_type          : {dict(by_type)}")
    if signal_col and rows:
        signals = [r[1] for r in rows if r[1] is not None]
        if signals:
            pos = sum(1 for s in signals if s > 0)
            neg = sum(1 for s in signals if s < 0)
            print(f"  signal sign distribution : +={pos}, -={neg}")


def main() -> int:
    path = db_path()
    print("=== copyBot usage + effectiveness audit ===")
    print(f"db: {path}")
    print(f"as of: {NOW.isoformat()}")
    with open_ro(path) as c:
        audit_decisions(c)
        audit_theories(c)
        audit_behavior(c)
        section("Capabilities (roles / skills / playbooks)")
        audit_capabilities(c, "agent_roles", "role")
        audit_capabilities(c, "agent_skills", "skill")
        audit_capabilities(c, "agent_playbooks", "playbook")
        audit_candidates_lifetime(c)
        audit_capability_links(c)
        audit_episodes_chunks(c)
        audit_audit_log(c)
        audit_feedback(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
