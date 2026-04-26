"""Vector store contract.

A backend implements `upsert`, `query`, `delete`, `drop_namespace`, and `count`
for two namespaces (`chunks`, `entities`). Vectors are float32 ndarrays;
metadata is a flat dict of LanceDB-friendly scalar types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np


class VectorStoreUnavailableError(RuntimeError):
    """Raised when the configured backend cannot be opened or used."""


@dataclass(frozen=True, slots=True)
class VectorRow:
    id: str
    workspace_id: str
    vector: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorHit:
    id: str
    workspace_id: str
    score: float  # cosine similarity in [-1, 1]; bigger = closer
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    backend: str

    def open(self) -> None: ...

    def close(self) -> None: ...

    def upsert(self, namespace: str, rows: list[VectorRow]) -> None: ...

    def query(
        self,
        namespace: str,
        vector: np.ndarray,
        *,
        workspace_id: str,
        k: int = 10,
    ) -> list[VectorHit]: ...

    def delete(self, namespace: str, ids: list[str]) -> int: ...

    def drop_namespace(self, namespace: str) -> None: ...

    def count(self, namespace: str, *, workspace_id: str | None = None) -> int: ...
