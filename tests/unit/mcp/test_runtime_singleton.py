"""_Runtime.provider()/store() are single-instance under concurrency (plan 10.3).

The MCP warm-up thread and a concurrent first handler both call
``_runtime.provider()``; without a lock the check-then-set could build two
provider instances (each loading its own model). This pins the double-checked
init lock with a counting factory (no real model/store is built).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import agent_memory_lite.mcp.stdio_runtime as rt


def test_provider_is_single_instance_under_concurrency(monkeypatch) -> None:
    runtime = rt._Runtime()
    builds: list[int] = []

    def _factory(_settings: Any) -> Any:
        builds.append(1)
        time.sleep(0.02)  # widen the race so a missing lock double-builds
        return object()

    monkeypatch.setattr(rt, "get_embedding_provider", _factory)

    results: list[Any] = []

    def _call() -> None:
        results.append(runtime.provider())

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(builds) == 1  # one provider built despite 8 concurrent callers
    assert len({id(r) for r in results}) == 1  # all callers got the same instance


def test_store_is_single_instance_under_concurrency(monkeypatch) -> None:
    runtime = rt._Runtime()
    builds: list[int] = []

    def _factory(_settings: Any) -> Any:
        builds.append(1)
        time.sleep(0.02)
        return object()

    monkeypatch.setattr(rt, "get_vector_store", _factory)

    threads = [threading.Thread(target=runtime.store) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(builds) == 1
