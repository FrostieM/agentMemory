"""HF offline-by-default with bootstrap detection (release plan step 10.2).

``configure_offline_env`` flips ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE``
to ``1`` once the embedding model is confirmed in the local HF cache, leaves
them unset while the model still needs its one-time bootstrap download, and
never fights an explicit operator setting. The cache probe is an import-free
filesystem check so the env var is set before huggingface_hub is imported.

These tests pin each branch with an injected ``env`` dict / a fake cache layout
under ``tmp_path`` so the real process environment is never mutated.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent_memory_lite.config import offline_bootstrap as ob


def _make_cache(root: Path, model: str) -> None:
    """Build a minimal but valid HF hub cache layout for ``model`` under root.

    Includes a weight file, since the probe requires one to count as cached.
    """
    folder = "models--" + model.replace("/", "--")
    rev = root / folder / "snapshots" / "abc123"
    rev.mkdir(parents=True)
    (rev / "config.json").write_text("{}", encoding="utf-8")
    (rev / "model.safetensors").write_text("", encoding="utf-8")


# ---- configure_offline_env branches (cache probe stubbed) ----------------


def test_enforces_offline_when_model_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ob, "_model_in_hf_cache", lambda _name, **_kw: True)
    env: dict[str, str] = {}
    report = ob.configure_offline_env("intfloat/multilingual-e5-small", env=env)
    assert env == {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    assert report.offline_enabled is True
    assert report.model_cached is True
    assert report.operator_override is False


def test_leaves_env_unset_when_model_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ob, "_model_in_hf_cache", lambda _name, **_kw: False)
    env: dict[str, str] = {}
    report = ob.configure_offline_env("intfloat/multilingual-e5-small", env=env)
    assert env == {}  # bootstrap window stays open
    assert report.offline_enabled is False
    assert report.model_cached is False
    assert "bootstrap" in report.reason


def test_operator_override_on_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Operator set HF_HUB_OFFLINE explicitly: we report it but do not touch env,
    # so TRANSFORMERS_OFFLINE is NOT silently added.
    monkeypatch.setattr(ob, "_model_in_hf_cache", lambda _name, **_kw: False)
    env = {"HF_HUB_OFFLINE": "1"}
    report = ob.configure_offline_env("m", env=env)
    assert env == {"HF_HUB_OFFLINE": "1"}
    assert report.operator_override is True
    assert report.offline_enabled is True


def test_operator_override_off_is_respected_even_when_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Critical safety case: a cached model would normally enforce offline, but
    # an explicit HF_HUB_OFFLINE=0 must win -- we must NOT force it back to "1".
    monkeypatch.setattr(ob, "_model_in_hf_cache", lambda _name, **_kw: True)
    env = {"HF_HUB_OFFLINE": "0"}
    report = ob.configure_offline_env("m", env=env)
    assert env == {"HF_HUB_OFFLINE": "0"}  # unchanged
    assert report.operator_override is True
    assert report.offline_enabled is False


@pytest.mark.parametrize("truthy", ["true", "yes", "on", "1"])
def test_override_truthiness_variants(monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
    monkeypatch.setattr(ob, "_model_in_hf_cache", lambda _name, **_kw: False)
    env = {"TRANSFORMERS_OFFLINE": truthy}
    report = ob.configure_offline_env("m", env=env)
    assert report.operator_override is True
    assert report.offline_enabled is True


# ---- _model_in_hf_cache filesystem probe (import-free) -------------------


def test_filesystem_cache_hit_via_hf_hub_cache(tmp_path: Path) -> None:
    _make_cache(tmp_path, "intfloat/multilingual-e5-small")
    assert (
        ob._model_in_hf_cache("intfloat/multilingual-e5-small", env={"HF_HUB_CACHE": str(tmp_path)})
        is True
    )


def test_filesystem_cache_miss_for_unknown_model(tmp_path: Path) -> None:
    # Empty cache root -> not cached. Filesystem-only: no network, no import.
    assert ob._model_in_hf_cache("nope/not-real", env={"HF_HUB_CACHE": str(tmp_path)}) is False


def test_hf_home_precedence_appends_hub(tmp_path: Path) -> None:
    # HF_HOME resolves to <HF_HOME>/hub as the cache root.
    _make_cache(tmp_path / "hub", "org/model")
    assert ob._model_in_hf_cache("org/model", env={"HF_HOME": str(tmp_path)}) is True


def test_empty_snapshot_dir_is_conservatively_uncached(tmp_path: Path) -> None:
    # The model folder + snapshots dir exist but hold no revision files: a
    # partial / interrupted download must NOT be treated as a usable cache hit.
    (tmp_path / "models--org--model" / "snapshots").mkdir(parents=True)
    assert ob._model_in_hf_cache("org/model", env={"HF_HUB_CACHE": str(tmp_path)}) is False


def test_stray_file_in_snapshots_is_ignored(tmp_path: Path) -> None:
    # A stray non-directory entry in snapshots/ (e.g. .DS_Store, a lockfile)
    # must be skipped, not mistaken for a revision; a valid revision with
    # weights still reads as cached.
    _make_cache(tmp_path, "org/model")
    (tmp_path / "models--org--model" / "snapshots" / ".DS_Store").write_text("", encoding="utf-8")
    assert ob._model_in_hf_cache("org/model", env={"HF_HUB_CACHE": str(tmp_path)}) is True


def test_empty_model_name_is_never_cached() -> None:
    assert ob._model_in_hf_cache("") is False
    env: dict[str, str] = {}
    report = ob.configure_offline_env("", env=env)
    assert env == {}
    assert report.offline_enabled is False


def test_config_only_partial_cache_is_uncached(tmp_path: Path) -> None:
    # Interrupted download: config.json present but NO weight file. Must report
    # not-cached so the bootstrap / self-heal window stays open (otherwise
    # offline gets enforced and the half-downloaded model can never complete).
    rev = tmp_path / "models--org--model" / "snapshots" / "rev1"
    rev.mkdir(parents=True)
    (rev / "config.json").write_text("{}", encoding="utf-8")
    assert ob._model_in_hf_cache("org/model", env={"HF_HUB_CACHE": str(tmp_path)}) is False


def test_xdg_cache_home_precedence(tmp_path: Path) -> None:
    # With HF_HUB_CACHE / HF_HOME unset, XDG_CACHE_HOME/huggingface/hub is root.
    _make_cache(tmp_path / "huggingface" / "hub", "org/model")
    assert ob._model_in_hf_cache("org/model", env={"XDG_CACHE_HOME": str(tmp_path)}) is True


def test_unresolvable_home_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # Path.home() raises RuntimeError when the home dir is unresolvable (stripped
    # containers). With no cache env var set we fall to the home() branch; the
    # probe must swallow the error and report not-cached, never crash startup.
    def _boom() -> Path:
        raise RuntimeError("no home directory")

    monkeypatch.setattr(ob.Path, "home", _boom)
    assert ob._model_in_hf_cache("org/model", env={}) is False


def test_entrypoints_do_not_eagerly_import_hf() -> None:
    # Ordering invariant: configure_offline_env must run before huggingface_hub
    # is first imported. Guard it -- importing the HTTP + MCP entrypoint modules
    # must NOT transitively pull in any HF lib (they import lazily in load
    # methods), so the env var is genuinely set before the first import.
    code = (
        "import sys, agent_memory_lite.api.app, agent_memory_lite.mcp.stdio_server\n"
        "bad = [m for m in "
        "('huggingface_hub', 'transformers', 'sentence_transformers', 'torch') "
        "if m in sys.modules]\n"
        "print(','.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"eager HF import detected: {result.stdout.strip()}"


def test_hf_offline_active_reports_env_truthiness() -> None:
    assert ob.hf_offline_active(env={"HF_HUB_OFFLINE": "1"}) is True
    assert ob.hf_offline_active(env={"TRANSFORMERS_OFFLINE": "true"}) is True
    assert ob.hf_offline_active(env={}) is False
    assert ob.hf_offline_active(env={"HF_HUB_OFFLINE": "0"}) is False


# ---- gate helper + startup wiring ----------------------------------------


def test_maybe_configure_offline_skips_when_disabled(settings_factory) -> None:
    settings = settings_factory(MEMORY_HF_AUTO_OFFLINE="false")
    assert ob.maybe_configure_offline(settings) is None


def test_maybe_configure_offline_calls_when_enabled(
    settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def _spy(model: str) -> ob.OfflineReport:
        seen.append(model)
        return ob.OfflineReport(model, False, False, False, "stub")

    monkeypatch.setattr(ob, "configure_offline_env", _spy)
    settings = settings_factory(MEMORY_HF_AUTO_OFFLINE="true")
    report = ob.maybe_configure_offline(settings)
    assert seen == [settings.embedding_model]
    assert report is not None


def test_bootstrap_invokes_maybe_configure_offline(
    settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The HTTP entrypoint seam: _bootstrap must call maybe_configure_offline
    # (the gate lives inside the helper, which is unit-tested above).
    import agent_memory_lite.api.app as app_mod  # noqa: PLC0415 — lazy: avoid app import at collection

    seen: list[object] = []

    def _spy(s: object) -> None:
        seen.append(s)

    monkeypatch.setattr(app_mod, "maybe_configure_offline", _spy)
    monkeypatch.setattr(app_mod, "assert_local_only", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "open_connection", lambda *a, **k: object())
    monkeypatch.setattr(app_mod, "apply_migrations", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "ensure_workspace_manifest", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "record_foreign_key_violations", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "close_connection", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "probe_ollama", lambda *a, **k: None)
    settings = settings_factory()
    app_mod._bootstrap(settings)
    assert seen == [settings]


def test_huggingface_hub_cache_legacy_alias(tmp_path: Path) -> None:
    # The legacy HUGGINGFACE_HUB_CACHE alias is honored when HF_HUB_CACHE unset.
    _make_cache(tmp_path, "org/model")
    assert ob._model_in_hf_cache("org/model", env={"HUGGINGFACE_HUB_CACHE": str(tmp_path)}) is True


def test_hf_hub_cache_wins_over_legacy_alias(tmp_path: Path) -> None:
    # When both are set, HF_HUB_CACHE wins: weights only under the legacy dir
    # must therefore read as a miss.
    legacy = tmp_path / "legacy"
    primary = tmp_path / "primary"
    _make_cache(legacy, "org/model")  # weights only in the legacy location
    primary.mkdir()
    env = {"HF_HUB_CACHE": str(primary), "HUGGINGFACE_HUB_CACHE": str(legacy)}
    assert ob._model_in_hf_cache("org/model", env=env) is False


def test_configure_offline_end_to_end_real_filesystem(tmp_path: Path) -> None:
    # Probe -> decision -> mutation through the REAL filesystem path (not the
    # stubbed branch): a cached model under HF_HUB_CACHE enables offline. Also
    # exercises the env-threading fix (the probe reads the injected env).
    _make_cache(tmp_path, "org/model")
    env = {"HF_HUB_CACHE": str(tmp_path)}
    report = ob.configure_offline_env("org/model", env=env)
    assert report.offline_enabled is True
    assert report.model_cached is True
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
