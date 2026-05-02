"""Sanity checks for the FakeVectorStore in conftest — exercises the protocol."""

from __future__ import annotations

import numpy as np

from agent_memory_lite.vector_store.base import VectorRow, VectorStore


def test_fake_store_satisfies_protocol(fake_vector_store) -> None:
    assert isinstance(fake_vector_store, VectorStore)


def test_fake_store_upsert_and_query(fake_vector_store) -> None:
    rows = [
        VectorRow(
            id="a",
            workspace_id="default",
            vector=np.array([1.0, 0.0, 0.0], dtype=np.float32),
        ),
        VectorRow(
            id="b",
            workspace_id="default",
            vector=np.array([0.0, 1.0, 0.0], dtype=np.float32),
        ),
    ]
    fake_vector_store.upsert("chunks", rows)
    hits = fake_vector_store.query(
        "chunks",
        np.array([1.0, 0.0, 0.0], dtype=np.float32),
        workspace_id="default",
        k=2,
    )
    assert hits[0].id == "a"
    assert hits[0].score == 1.0


def test_fake_store_delete_and_count(fake_vector_store) -> None:
    fake_vector_store.upsert(
        "chunks",
        [
            VectorRow(
                id="a",
                workspace_id="default",
                vector=np.array([1.0, 0.0], dtype=np.float32),
            )
        ],
    )
    assert fake_vector_store.count("chunks", workspace_id="default") == 1
    assert fake_vector_store.delete("chunks", ["a"]) == 1
    assert fake_vector_store.count("chunks", workspace_id="default") == 0


def test_fake_store_filters_by_workspace(fake_vector_store) -> None:
    fake_vector_store.upsert(
        "chunks",
        [
            VectorRow(
                id="a",
                workspace_id="default",
                vector=np.array([1.0, 0.0], dtype=np.float32),
            ),
            VectorRow(
                id="b",
                workspace_id="other",
                vector=np.array([1.0, 0.0], dtype=np.float32),
            ),
        ],
    )
    hits = fake_vector_store.query(
        "chunks",
        np.array([1.0, 0.0], dtype=np.float32),
        workspace_id="default",
        k=10,
    )
    assert [h.id for h in hits] == ["a"]
