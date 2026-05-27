from __future__ import annotations

from scripts import run_evals as run_evals_script

from agent_memory_lite.evals.metrics import EvalReport


def test_run_evals_cli_returns_nonzero_when_cases_fail(monkeypatch) -> None:
    def fake_run_evals(*_args, **_kwargs) -> EvalReport:
        report = EvalReport(cases_run=1, cases_passed=0)
        report.failures.append("case failed")
        return report

    monkeypatch.setattr(run_evals_script, "run_evals", fake_run_evals)

    assert run_evals_script.main(["--workspace", "default", "--no-vector"]) == 1
