"""Unit tests for role-activation prompt builder."""

from __future__ import annotations

from agent_memory_lite.extraction.role_activation_prompt import (
    decide_role_activation,
)


def _role(name: str, purpose: str, confidence: float = 0.9, active: bool = True) -> dict:
    return {"name": name, "purpose": purpose, "confidence": confidence, "active": active}


def _skill(name: str, summary: str, confidence: float = 0.9, active: bool = True) -> dict:
    return {"name": name, "summary": summary, "confidence": confidence, "active": active}


def test_short_prompt_no_inject() -> None:
    caps = {"roles": [_role("Quant", "Evaluate strategy quality with replay")]}
    d = decide_role_activation(user_prompt="ok", capabilities=caps)
    assert d.inject is False


def test_empty_prompt_no_inject() -> None:
    d = decide_role_activation(user_prompt="", capabilities={"roles": [_role("X", "Y")]})
    assert d.inject is False


def test_none_capabilities_no_inject() -> None:
    d = decide_role_activation(
        user_prompt="explain how copyBot calibrates tier ladders today",
        capabilities=None,
    )
    assert d.inject is False


def test_no_roles_no_inject() -> None:
    d = decide_role_activation(
        user_prompt="explain how copyBot calibrates tier ladders today",
        capabilities={"roles": [], "skills": []},
    )
    assert d.inject is False


def test_low_confidence_role_no_inject() -> None:
    d = decide_role_activation(
        user_prompt="explain how copyBot calibrates tier ladders today",
        capabilities={"roles": [_role("Weak", "p", confidence=0.5)]},
    )
    assert d.inject is False


def test_inactive_role_skipped() -> None:
    d = decide_role_activation(
        user_prompt="explain how copyBot calibrates tier ladders today",
        capabilities={"roles": [_role("Old", "p", active=False)]},
    )
    assert d.inject is False


def test_valid_role_injects_with_required_sections() -> None:
    caps = {
        "roles": [_role("Quant Research Engineer", "Evaluate strategy quality with replay")],
        "skills": [_skill("Replay and backtest design", "Design explicit baselines + acceptance")],
    }
    d = decide_role_activation(
        user_prompt="should we deploy tier2/tier3 calibration today?",
        capabilities=caps,
    )
    assert d.inject is True
    assert "[role-activation]" in d.prompt
    assert "Quant Research Engineer" in d.prompt
    assert "Replay and backtest design" in d.prompt
    # Single role => verbatim Acting-as opener
    assert "Acting as <role>." in d.prompt
    assert "discipline violation" in d.prompt


def test_multi_role_stack_injection_when_top_k_available() -> None:
    """Multiple confident roles => PRIMARY + SECONDARY stack reminder."""
    caps = {
        "roles": [
            _role("Senior Frontend UX Architect", "Design operator interfaces"),
            _role("Quant Research Engineer", "Evaluate strategy quality with replay"),
            _role("Production Reliability Engineer", "Diagnose runtime issues"),
        ],
        "skills": [_skill("Replay and backtest design", "Explicit baselines")],
    }
    d = decide_role_activation(
        user_prompt="should we deploy tier2 calibration today without backtest?",
        capabilities=caps,
    )
    assert d.inject is True
    assert "ROLE PRIMARY:" in d.prompt
    assert "ROLE SECONDARY:" in d.prompt
    assert "Senior Frontend UX Architect" in d.prompt
    assert "Quant Research Engineer" in d.prompt
    # 3rd role NOT present at default top_k=2
    assert "Production Reliability Engineer" not in d.prompt
    # Acting-as line uses role stack form
    assert "Acting as role stack:" in d.prompt
    assert "Senior Frontend UX Architect + Quant Research Engineer" in d.prompt


def test_top_k_roles_configurable() -> None:
    caps = {
        "roles": [
            _role("R1", "p1"),
            _role("R2", "p2"),
            _role("R3", "p3"),
        ],
    }
    d_k3 = decide_role_activation(
        user_prompt="design a backtest for the source-flip edge analysis",
        capabilities=caps,
        top_k_roles=3,
    )
    assert d_k3.inject is True
    assert "ROLE PRIMARY:" in d_k3.prompt
    assert "ROLE SECONDARY:" in d_k3.prompt
    assert "ROLE TERTIARY:" in d_k3.prompt
    assert "R1 + R2 + R3" in d_k3.prompt

    d_k1 = decide_role_activation(
        user_prompt="design a backtest for the source-flip edge analysis",
        capabilities=caps,
        top_k_roles=1,
    )
    assert d_k1.inject is True
    # Single-role form when top_k=1
    assert "ROLE PRIMARY:" not in d_k1.prompt
    assert "ROLE:" in d_k1.prompt
    assert "Acting as <role>." in d_k1.prompt


def test_role_without_skill_still_injects() -> None:
    caps = {
        "roles": [
            _role("Production Reliability Engineer", "Diagnose runtime issues evidence-first")
        ],
        "skills": [],
    }
    d = decide_role_activation(
        user_prompt="why is the maker bot worker stalled on port 8080?",
        capabilities=caps,
    )
    assert d.inject is True
    assert "Production Reliability Engineer" in d.prompt
    assert "SKILL:" not in d.prompt, "no skill block when no qualifying skill"


def test_first_confident_role_chosen_over_low_confidence() -> None:
    caps = {
        "roles": [
            _role("Weak", "p", confidence=0.4),
            _role("Strong", "Evaluate strategy quality", confidence=0.95),
        ],
    }
    d = decide_role_activation(
        user_prompt="should we deploy tier2/tier3 calibration today?",
        capabilities=caps,
    )
    assert d.inject is True
    assert "Strong" in d.prompt


def test_long_purpose_trimmed() -> None:
    long_purpose = "Evaluate " + ("strategy edge " * 200)
    caps = {"roles": [_role("Quant", long_purpose)]}
    d = decide_role_activation(
        user_prompt="design a backtest for the source-flip edge on tennis favorites",
        capabilities=caps,
    )
    assert d.inject is True
    assert "..." in d.prompt, "long purpose should be soft-trimmed"
    # The injected prompt should fit comfortably under 2 KB.
    assert len(d.prompt) < 2000


def test_confidence_threshold_configurable() -> None:
    caps = {"roles": [_role("Mid", "p", confidence=0.65)]}
    d_default = decide_role_activation(
        user_prompt="explain how copyBot calibrates tier ladders today",
        capabilities=caps,
    )
    assert d_default.inject is False, "default min_confidence=0.7, 0.65 should not trigger"
    d_lower = decide_role_activation(
        user_prompt="explain how copyBot calibrates tier ladders today",
        capabilities=caps,
        min_confidence=0.6,
    )
    assert d_lower.inject is True


def test_prompt_length_threshold_configurable() -> None:
    caps = {"roles": [_role("Quant", "Evaluate strategy quality")]}
    d_default = decide_role_activation(user_prompt="short ask", capabilities=caps)
    assert d_default.inject is False
    d_lower = decide_role_activation(user_prompt="short ask", capabilities=caps, min_prompt_len=5)
    assert d_lower.inject is True
