"""Settings-driven gate that applies auto-offline from the startup paths.

Extracted sibling of ``config/offline_bootstrap``. Holds the structural
``Settings`` protocol and the single gate function the HTTP and MCP entrypoints
share, so the two entrypoints cannot drift on the ``hf_auto_offline`` decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_memory_lite.config.offline_bootstrap import OfflineReport


class SupportsOfflineConfig(Protocol):
    """Minimal Settings shape needed to decide HF offline mode.

    Read-only properties (not bare attributes) so a pydantic ``Settings`` whose
    fields are read-only still satisfies the protocol structurally.
    """

    @property
    def hf_auto_offline(self) -> bool: ...

    @property
    def embedding_model(self) -> str: ...


def maybe_configure_offline(settings: SupportsOfflineConfig) -> OfflineReport | None:
    """Gate + apply auto-offline from a startup path (HTTP ``_bootstrap`` / MCP ``_run``).

    Single source for the ``hf_auto_offline`` gate, so the two entrypoints
    cannot drift. Returns the :class:`OfflineReport` when enabled, else ``None``.

    ``configure_offline_env`` is resolved off the ``offline_bootstrap`` module at
    call time (not bound at import) so it stays the single canonical attribute --
    monkeypatching ``offline_bootstrap.configure_offline_env`` is honored here.
    """
    if not settings.hf_auto_offline:
        return None
    # Imported lazily to avoid a circular import at module load and to resolve
    # the (possibly monkeypatched) attribute from the canonical module.
    from agent_memory_lite.config import offline_bootstrap  # noqa: PLC0415

    return offline_bootstrap.configure_offline_env(settings.embedding_model)
