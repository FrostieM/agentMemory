from __future__ import annotations

from agent_memory_lite.utils.text_encoding import repair_common_mojibake


def test_repairs_cp1252_mojibake() -> None:
    mojibake = "API process " + "\u00e2\u0080\u0094" + " Worker"
    expected = "API process " + "\u2014" + " Worker"
    assert repair_common_mojibake(mojibake) == expected


def test_repairs_cp1251_mojibake() -> None:
    expected = bytes.fromhex("d09fd180d0b8d0b2d0b5d182").decode("utf-8")
    mojibake = expected.encode("utf-8").decode("cp1251")
    assert repair_common_mojibake(mojibake) == expected


def test_leaves_normal_text_unchanged() -> None:
    assert repair_common_mojibake("normal UTF-8 text") == "normal UTF-8 text"
