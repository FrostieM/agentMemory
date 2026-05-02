"""Aggregator facade for research-lab writers.

Snapshot writer lives in ``research_writer_snapshots.py``;
experiment + result writer in ``research_writer_experiments.py``;
insight writers in ``research_writer_insights.py``;
concept writer in ``research_writer_concepts.py``;
shared workspace validator in ``research_writer_shared.py``. This
module re-exports the public API.
"""

from __future__ import annotations

from agent_memory_lite.ingestion.research_writer_concepts import upsert_domain_concept
from agent_memory_lite.ingestion.research_writer_experiments import (
    add_experiment_result,
    write_experiment,
)
from agent_memory_lite.ingestion.research_writer_insights import (
    distill_insight,
    update_insight,
)
from agent_memory_lite.ingestion.research_writer_snapshots import register_snapshot

__all__ = [
    "add_experiment_result",
    "distill_insight",
    "register_snapshot",
    "update_insight",
    "upsert_domain_concept",
    "write_experiment",
]
