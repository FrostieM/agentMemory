"""Live-transcript verification: the detector finds real corrections.

This test scans the most recent Claude Code session JSONL on the
current machine and asserts the heuristic finds at least one
correction. It complements the hermetic
``test_correction_detector_on_corpus.py`` (which uses inlined ground
truth) with a real-data smoke that prevents the heuristic from
silently degrading on actual chat data.

The test is **opt-in**: skipped when ``~/.claude/projects/`` is not
present (CI / fresh checkout / non-Claude-Code environments). It is
NOT a CI gate — it's a developer-machine sanity check that exercises
the detector against living data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def _claude_projects_root() -> Path | None:
    home = Path.home()
    candidates = [
        home / ".claude" / "projects",
        home / "AppData" / "Local" / "claude" / "projects",
    ]
    for path in candidates:
        if path.exists() and path.is_dir():
            return path
    return None


def _most_recent_jsonl(root: Path) -> Path | None:
    """Find the largest recent JSONL — proxy for "current chat"."""
    candidates: list[Path] = []
    for project_dir in root.iterdir():
        if project_dir.is_dir():
            candidates.extend(project_dir.glob("*.jsonl"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


@pytest.mark.integration
def test_detector_catches_corrections_in_live_transcript() -> None:
    """Walk a real Claude Code transcript and assert the detector
    matches at least one correction. Skips when no transcript is
    available (CI / fresh checkout)."""
    root = _claude_projects_root()
    if root is None:
        pytest.skip("~/.claude/projects/ not found; live-transcript test skipped")

    target = _most_recent_jsonl(root)
    if target is None:
        pytest.skip("no JSONL transcripts in ~/.claude/projects/")

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from agent_memory_lite.extraction.correction_patterns import (  # noqa: PLC0415
        match_correction,
    )

    hits = 0
    prev_assistant_len = 0
    with target.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            text = _extract_text(obj.get("message") or {})
            if not text:
                continue
            if kind == "assistant":
                prev_assistant_len = len(text)
            elif kind == "user" and prev_assistant_len >= 50:
                m = match_correction(text, min_user_len=30, min_confidence=0.5)
                if m.matched:
                    hits += 1
                prev_assistant_len = 0

    assert hits >= 1, (
        f"detector found 0 corrections in {target.name}; "
        "either the transcript has no real corrections or the heuristic regressed"
    )


def _extract_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    return t.strip()
    return ""
