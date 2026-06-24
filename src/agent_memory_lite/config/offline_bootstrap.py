"""Default the HuggingFace hub to offline mode once the embedding model is cached.

Why this exists
---------------
The sentence-transformers embedding model (and the optional cross-encoder
reranker) bootstrap once from ``huggingface.co`` on first use -- a documented,
one-time download analogous to ``ollama pull`` (see ``config/local_only_guard``).
After that, every load is served from the local HF cache.

The *primary* local-only control is the per-load ``local_files_only=True`` the
providers already pass (``embeddings/sentence_transformers_provider`` and
``retrieval/rerank``): a cached model loads with zero network traffic without
any env var. This module adds *defense-in-depth* on top of that: at startup,
once the embedding model is confirmed present in the local HF cache, it sets
``HF_HUB_OFFLINE`` and ``TRANSFORMERS_OFFLINE`` to ``1`` by default, so any
transitive ``huggingface_hub`` / ``transformers`` code path we do not call
directly (tokenizer fetches, snapshot-revision checks, ...) is also kept
offline. It does NOT replace ``local_files_only=True`` and does NOT, on its
own, make an absolute "never touches the network" guarantee -- see the scope
notes below.

Import-free cache probe
-----------------------
``huggingface_hub`` reads ``HF_HUB_OFFLINE`` into a module-level constant *at
import time*. If we imported the library to probe the cache, that constant
would freeze to its pre-call value before we set the env var, so late
``huggingface_hub`` code paths would ignore the flag. The cache probe is
therefore a plain filesystem check of the standard hub layout
(``<cache>/models--org--name/snapshots/<rev>/...``) that imports nothing. The
env var is then set before ``huggingface_hub`` is first imported -- which holds
as long as no module imports it eagerly; every current call site imports it
lazily (inside the provider / reranker load methods), and the HTTP and MCP
entrypoints call :func:`configure_offline_env` before any handler can trigger a
load.

Bootstrap detection
-------------------
When the model is NOT yet cached, the offline vars are left unset so the
one-time bootstrap download still works. The probe is conservative: it requires
a real model-weight file (``*.safetensors`` / ``*.bin`` / ``*.onnx`` / ...) in
a snapshot revision, so a partial or interrupted download (e.g. a lone
``config.json``) is treated as "not cached" and the bootstrap / self-heal
window stays open. Any error -- missing dirs, an unresolvable home directory,
a permission fault -- also reports "not cached", so enforce-offline is only
ever decided on a positive, on-disk weight hit.

Operator override
----------------
If either offline var is already present in the environment, the operator is
assumed to be managing offline mode explicitly; this module leaves both
untouched, in either direction (an explicit ``HF_HUB_OFFLINE=0`` is honored).
Auto-offline is also independent of ``allow_remote_providers``: an operator who
deliberately fronts a private, non-cloud HF mirror (``HF_ENDPOINT=...`` with
remote providers relaxed) and wants revision refreshes must opt out via
``MEMORY_HF_AUTO_OFFLINE=false`` or an explicit ``HF_HUB_OFFLINE=0``.

Interaction with the opt-in reranker
------------------------------------
Auto-offline is gated on the *embedding* model only -- the model the service
cannot run without. The cross-encoder reranker is opt-in, uses a *different*
model (``retrieval/rerank.DEFAULT_MODEL``), and is failure-soft: if offline mode
blocks its one-time bootstrap, ``rerank`` degrades to the original
(score-sorted) hit order rather than raising (see ``retrieval/rerank._load_model``). So during the
embedding-model bootstrap window (offline not yet enforced) a first reranked
search may still fetch the reranker model; operators who want the reranker in a
strictly offline deployment must pre-cache its model (like pre-pulling an Ollama
model).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path  # noqa: F401  re-exported: tests patch ``ob.Path.home``

# The import-free cache probe lives in a sibling module; re-exported here so the
# original module path keeps exporting these symbols (callers / tests use
# ``offline_bootstrap._model_in_hf_cache`` and friends).
from agent_memory_lite.config.offline_bootstrap_cache_probe import (
    _WEIGHT_SUFFIXES,  # noqa: F401  re-exported for callers/tests
    _expand,  # noqa: F401  re-exported for callers/tests
    _hf_cache_root,  # noqa: F401  re-exported for callers/tests
    _model_in_hf_cache,
)

# The settings-driven gate lives in a sibling module; re-exported here so the
# original module path keeps exporting these. The gate references this module
# only via TYPE_CHECKING + a lazy call-time import, so there is no import cycle.
from agent_memory_lite.config.offline_bootstrap_settings_gate import (
    SupportsOfflineConfig as SupportsOfflineConfig,  # noqa: PLC0414  explicit re-export
)
from agent_memory_lite.config.offline_bootstrap_settings_gate import (
    maybe_configure_offline as maybe_configure_offline,  # noqa: PLC0414  explicit re-export
)

_log = logging.getLogger("agent_memory_lite.offline_bootstrap")

# Both vars matter: ``huggingface_hub`` reads ``HF_HUB_OFFLINE``; the
# ``transformers`` library reads ``TRANSFORMERS_OFFLINE``. We set both so no
# layer of the stack is left online.
OFFLINE_ENV_VARS: tuple[str, ...] = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")

_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class OfflineReport:
    """Network-posture self-check produced at startup."""

    model_name: str
    model_cached: bool
    offline_enabled: bool
    operator_override: bool
    reason: str


def configure_offline_env(
    model_name: str,
    *,
    env: MutableMapping[str, str] | None = None,
) -> OfflineReport:
    """Enforce HF offline mode by default once ``model_name`` is cached.

    Returns an :class:`OfflineReport` describing the decision. Mutates ``env``
    (defaults to ``os.environ``) only when it enables offline mode, and never
    when the operator has set an offline var explicitly.
    """
    target = os.environ if env is None else env

    if any(var in target for var in OFFLINE_ENV_VARS):
        enabled = any(
            str(target.get(var, "")).strip().lower() in _TRUTHY for var in OFFLINE_ENV_VARS
        )
        report = OfflineReport(
            model_name=model_name,
            model_cached=_model_in_hf_cache(model_name, env=target),
            offline_enabled=enabled,
            operator_override=True,
            reason="operator set HF offline vars explicitly; left unchanged",
        )
    elif _model_in_hf_cache(model_name, env=target):
        for var in OFFLINE_ENV_VARS:
            target[var] = "1"
        report = OfflineReport(
            model_name=model_name,
            model_cached=True,
            offline_enabled=True,
            operator_override=False,
            reason="embedding model present in local HF cache; offline mode enforced",
        )
    else:
        report = OfflineReport(
            model_name=model_name,
            model_cached=False,
            offline_enabled=False,
            operator_override=False,
            reason="embedding model not in local HF cache; allowing one-time bootstrap download",
        )

    _log.info(
        "hf_offline_posture model=%s cached=%s offline=%s override=%s reason=%s",
        report.model_name,
        report.model_cached,
        report.offline_enabled,
        report.operator_override,
        report.reason,
    )
    return report


def hf_offline_active(env: Mapping[str, str] | None = None) -> bool:
    """True iff HF offline mode is currently enforced in the environment.

    Diagnostic helper (surfaced in ``memory_status``): reports whether
    ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE`` is truthy right now, however it
    got set (auto by :func:`configure_offline_env` or by the operator).
    """
    src = os.environ if env is None else env
    return any(str(src.get(var, "")).strip().lower() in _TRUTHY for var in OFFLINE_ENV_VARS)
