from __future__ import annotations

from pathlib import Path

from scripts.v3_surface_check import run


def test_v3_surface_check_catches_relative_research_list_routes(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE skills (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    routes_dir = tmp_path / "src" / "agent_memory_lite" / "api" / "routes"
    routes_dir.mkdir(parents=True)
    (routes_dir / "research.py").write_text(
        '@router.post("/list_research_agenda")\ndef route():\n    pass\n',
        encoding="utf-8",
    )

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "forbidden_active_surface"
        and Path(item["path"]).name == "research.py"
        and item["match"] == '@router.post("/list_research_agenda"'
        for item in payload["findings"]
    )


def test_v3_surface_check_catches_theory_list_remount(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE skills (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "agent_memory_lite" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "app_routes.py").write_text(
        "from agent_memory_lite.api.routes import theory_list\n",
        encoding="utf-8",
    )

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "deleted_module_reference"
        and Path(item["path"]).name == "app_routes.py"
        and "theory_list" in item["match"]
        for item in payload["findings"]
    )


def test_v3_surface_check_catches_removed_bootstrap_capability_tool(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE skills (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    bootstrap_dir = tmp_path / "src" / "agent_memory_lite" / "bootstrap"
    bootstrap_dir.mkdir(parents=True)
    (bootstrap_dir / "project_memory_seed_behavior_writes.py").write_text(
        'RULE = "call memory_link_capability after writing"\n',
        encoding="utf-8",
    )

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "forbidden_bootstrap_contract"
        and Path(item["path"]).name == "project_memory_seed_behavior_writes.py"
        and item["match"] == "memory_link_capability"
        for item in payload["findings"]
    )


def test_v3_surface_check_catches_removed_code_memory_routes(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE skills (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "agent_memory_lite" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "app_routes.py").write_text(
        "from agent_memory_lite.api.routes import find_symbols, code_graph\n",
        encoding="utf-8",
    )

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "deleted_module_reference"
        and Path(item["path"]).name == "app_routes.py"
        and "find_symbols" in item["match"]
        for item in payload["findings"]
    )


def test_v3_surface_check_catches_removed_active_edits_table(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE active_edits (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "legacy_v2_table_in_init_schema" and item["match"] == "active_edits"
        for item in payload["findings"]
    )


def test_v3_surface_check_catches_legacy_status_field_names(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE skills (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    schema_dir = tmp_path / "src" / "agent_memory_lite" / "api" / "schemas"
    schema_dir.mkdir(parents=True)
    (schema_dir / "memory_status.py").write_text(
        "behavior_instructions_active = 1\n",
        encoding="utf-8",
    )

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "forbidden_status_contract"
        and item["match"] == "behavior_instructions_active"
        for item in payload["findings"]
    )


def test_v3_surface_check_catches_deleted_hook_config_reference(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE skills (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '"scripts/inject_memory_context.py" = ["PLR0911"]\n',
        encoding="utf-8",
    )

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "forbidden_config_contract" and item["match"] == "inject_memory_context.py"
        for item in payload["findings"]
    )


def test_v3_surface_check_catches_removed_core_memory_module(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_init.sql").write_text(
        "CREATE TABLE skills (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    module = tmp_path / "src" / "agent_memory_lite" / "repositories" / "core_memory_repo.py"
    module.parent.mkdir(parents=True)
    module.write_text("pass\n", encoding="utf-8")

    payload = run(tmp_path)

    assert payload["status"] == "failed"
    assert any(
        item["kind"] == "forbidden_active_legacy_module" and item["match"] == "core_memory_repo.py"
        for item in payload["findings"]
    )
