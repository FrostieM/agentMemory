"""Close open facts by stamping `valid_to` and `invalidated_by_fact_id`."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from agent_memory_lite.repositories.facts_repo import close_fact


def invalidate_facts(
    conn: sqlite3.Connection,
    *,
    fact_ids: Iterable[str],
    valid_to: str,
    invalidated_by_fact_id: str,
) -> None:
    for fact_id in fact_ids:
        if fact_id == invalidated_by_fact_id:
            continue
        close_fact(
            conn,
            fact_id=fact_id,
            valid_to=valid_to,
            invalidated_by_fact_id=invalidated_by_fact_id,
        )
