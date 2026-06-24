"""Import-free filesystem probe of the local HuggingFace hub cache.

Extracted sibling of ``config/offline_bootstrap`` holding the cache-locating
helpers. These deliberately import nothing from ``huggingface_hub`` -- importing
it would freeze its module-level ``HF_HUB_OFFLINE`` constant before
``configure_offline_env`` sets the env var, so the probe is a plain filesystem
check of the standard hub layout.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# Model-weight file extensions. A snapshot holding only config / tokenizer
# files (an interrupted download) must NOT count as cached.
_WEIGHT_SUFFIXES: frozenset[str] = frozenset(
    {".safetensors", ".bin", ".onnx", ".gguf", ".pt", ".h5", ".ckpt"}
)


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
