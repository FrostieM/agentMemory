"""Round-2 audit #4: lock-protected brief cache under concurrent writes.

The brief cache uses Python's dict insertion order for LRU eviction:

    _BRIEF_CACHE.pop(key, None)
    _BRIEF_CACHE[key] = value
    while len(_BRIEF_CACHE) > MAX:
        oldest = next(iter(_BRIEF_CACHE))
        del _BRIEF_CACHE[oldest]

Two threads in the eviction loop together can:
1. Both mutate insertion order between ``next(iter(...))`` calls,
   raising ``RuntimeError: dictionary changed size during iteration``.
2. Both think they're the one to evict the oldest entry, breaking
   the bound (size temporarily exceeds MAX, or wrong entry evicted).

Post-fix every read+update goes through ``_BRIEF_CACHE_LOCK`` so the
critical section is serialised. This test pummels the cache from
many threads at once. Pre-fix it raises RuntimeError or violates
``len(_BRIEF_CACHE) <= _BRIEF_CACHE_MAX`` within a few hundred iters.
Post-fix neither happens, no matter how hard we hammer.
"""

from __future__ import annotations

import threading

from agent_memory_lite.cognition.brief import (
    _BRIEF_CACHE,
    _BRIEF_CACHE_MAX,
    Brief,
    _cache_remember,
)


def test_cache_remember_survives_concurrent_inserts() -> None:
    """Hammer ``_cache_remember`` from many threads. The critical
    section (pop + insert + eviction loop) must be atomic — any race
    would either raise RuntimeError from dict iteration or break the
    size invariant ``len(_BRIEF_CACHE) <= _BRIEF_CACHE_MAX``."""
    # Start clean.
    _BRIEF_CACHE.clear()

    errors: list[BaseException] = []
    barrier = threading.Barrier(8)
    iters_per_thread = 300

    def hammer(tid: int) -> None:
        try:
            barrier.wait()  # release all threads at once for max contention
            for i in range(iters_per_thread):
                # Use overlapping but not-quite-identical keys so we exercise
                # both the LRU-promote and the eviction paths.
                key = (f"ws{tid % 3}", 500 + (i % 7), f"fp-{i % 5}")
                _cache_remember(
                    key,
                    Brief(
                        body_md=f"thread {tid} iter {i}",
                        token_count=i,
                        sections=(),
                        cache_hit=False,
                    ),
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent inserts raised: {errors[:3]}"
    # Size invariant — eviction loop kept us at or below the cap.
    assert len(_BRIEF_CACHE) <= _BRIEF_CACHE_MAX, (
        f"cache overflowed: {len(_BRIEF_CACHE)} > {_BRIEF_CACHE_MAX}"
    )
    _BRIEF_CACHE.clear()
