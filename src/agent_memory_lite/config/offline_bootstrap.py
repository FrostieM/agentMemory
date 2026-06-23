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
from pathlib import Path
from typing import Protocol

_log = logging.getLogger("agent_memory_lite.offline_bootstrap")

# Both vars matter: ``huggingface_hub`` reads ``HF_HUB_OFFLINE``; the
# ``transformers`` library reads ``TRANSFORMERS_OFFLINE``. We set both so no
# layer of the stack is left online.
OFFLINE_ENV_VARS: tuple[str, ...] = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")

_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})

# Model-weight file extensions. A snapshot holding only config / tokenizer
# files (an interrupted download) must NOT count as cached.
_WEIGHT_SUFFIXES: frozenset[str] = frozenset(
    {".safetensors", ".bin", ".onnx", ".gguf", ".pt", ".h5", ".ckpt"}
)


@dataclass(frozen=True)
class OfflineReport:
    """Network-posture self-check produced at startup."""

    model_name: str
    model_cached: bool
    offline_enabled: bool
    operator_override: bool
    reason: str


def _expand(value: str) -> Path:
    """Expand ``~`` and ``$VAR`` like huggingface_hub does for cache paths."""
    return Path(os.path.expandvars(os.path.expanduser(value)))


def _hf_cache_root(env: Mapping[str, str]) -> Path:
    """Resolve the HF hub cache dir, mirroring huggingface_hub's precedence.

    Deliberately does NOT import ``huggingface_hub`` (that would freeze its
    ``HF_HUB_OFFLINE`` constant before :func:`configure_offline_env` sets the
    env var). Precedence: ``HF_HUB_CACHE`` -> legacy ``HUGGINGFACE_HUB_CACHE``
    -> ``HF_HOME/hub`` -> ``XDG_CACHE_HOME/huggingface/hub`` ->
    ``~/.cache/huggingface/hub``.
    """
    explicit = env.get("HF_HUB_CACHE") or env.get("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return _expand(explicit)
    hf_home = env.get("HF_HOME")
    if hf_home:
        return _expand(hf_home) / "hub"
    xdg = env.get("XDG_CACHE_HOME")
    if xdg:
        return _expand(xdg) / "huggingface" / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _model_in_hf_cache(model_name: str, *, env: Mapping[str, str] | None = None) -> bool:
    """Return True iff ``model_name`` has weights on disk in the local HF cache.

    Filesystem-only and conservative -- a snapshot revision must contain at
    least one model-weight file (see ``_WEIGHT_SUFFIXES``) to count as cached.
    Returns False -- never raises -- on ANY error (missing dirs, an
    unresolvable home directory, permission faults), so an enforce-offline
    decision is only ever made on a positive, on-disk weight hit.
    """
    if not model_name:
        return False
    src = os.environ if env is None else env
    folder = "models--" + model_name.replace("/", "--")
    try:
        snapshots = _hf_cache_root(src) / folder / "snapshots"
        if not snapshots.is_dir():
            return False
        for rev in snapshots.iterdir():
            if not rev.is_dir():
                continue
            for path in rev.rglob("*"):
                if path.is_file() and path.suffix.lower() in _WEIGHT_SUFFIXES:
                    return True
        return False
    except Exception:
        # A best-effort cache probe must never break startup -- Path.home()
        # raises RuntimeError when the home dir is unresolvable (stripped
        # containers), iterdir/rglob can raise OSError on odd mounts, etc.
        return False


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
    """
    if not settings.hf_auto_offline:
        return None
    return configure_offline_env(settings.embedding_model)


def hf_offline_active(env: Mapping[str, str] | None = None) -> bool:
    """True iff HF offline mode is currently enforced in the environment.

    Diagnostic helper (surfaced in ``memory_status``): reports whether
    ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE`` is truthy right now, however it
    got set (auto by :func:`configure_offline_env` or by the operator).
    """
    src = os.environ if env is None else env
    return any(str(src.get(var, "")).strip().lower() in _TRUTHY for var in OFFLINE_ENV_VARS)
