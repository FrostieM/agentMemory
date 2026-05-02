from __future__ import annotations

from pathlib import Path

from agent_memory_lite.maintenance.memory_diff import diff_memory_payloads, load_memory_payload


def test_memory_diff_detects_regression() -> None:
    before = {
        "status": "ok",
        "counts": {"chunks": 10, "vectors": 10},
        "checks": {"vector": {"status": "ok", "details": {}}},
        "failures": [],
    }
    after = {
        "status": "degraded",
        "counts": {"chunks": 10, "vectors": 9},
        "checks": {"vector": {"status": "degraded", "details": {}}},
        "failures": ["vector"],
    }

    report = diff_memory_payloads(before, after)

    assert report.status == "degraded"
    assert report.count_deltas["vectors"] == -1
    assert report.component_changes["root"] == {"before": "ok", "after": "degraded"}
    assert report.component_changes["check.vector"] == {"before": "ok", "after": "degraded"}
    assert report.failures_added == ["vector"]


def test_memory_diff_reports_resolved_warning_as_ok() -> None:
    before = {"status": "warning", "warnings": ["hygiene"]}
    after = {"status": "ok", "warnings": []}

    report = diff_memory_payloads(before, after)

    assert report.status == "ok"
    assert report.warnings_resolved == ["hygiene"]


def test_memory_diff_loads_utf8_bom_json(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text('\ufeff{"status":"ok"}', encoding="utf-8")

    assert load_memory_payload(path)["status"] == "ok"
