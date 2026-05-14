"""Build a memory-audit prompt when the agent did real work without recording.

Called by ``scripts/inject_memory_context.py`` (UserPromptSubmit hook) right
before the agent sees the operator's new prompt. The hook reads the Claude
Code transcript JSONL, classifies tool_use blocks in the previous assistant
turn via ``memory_audit_classify``, and — if the agent did file mutations
without any memory write — prepends a forced-language audit reminder to
the new turn's system context.

Why: pinned behavior_instructions are advisory; Claude triages and skips
them under load (copyBot: 0 episodes despite 5 commits/day). Promoting the
discipline reminder from background envelope to foreground system reminder
in the user turn closes the adoption gap.

Pure-analysis module. No network, no DB. Returns ``inject=False`` on any
error so the hook degrades to silent passthrough.
"""

from __future__ import annotations

import json
from pathlib import Path

# Re-export for backwards compat / convenience of test imports.
from agent_memory_lite.extraction.memory_audit_classify import (  # noqa: F401
    AuditDecision,
    TurnToolStats,
    classify_tool,
    count_tools_in_turn,
)

_DEFAULT_MIN_MUTATIONS = 2
_DEFAULT_TAIL_LINES = 600


def analyze_last_assistant_turn(
    transcript_path: str | Path, *, tail_lines: int = _DEFAULT_TAIL_LINES
) -> TurnToolStats | None:
    """Read transcript JSONL tail; classify tool_use blocks in last assistant turn."""
    p = Path(transcript_path)
    if not p.is_file():
        return None
    try:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(all_lines[-tail_lines:]):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = event.get("message") if isinstance(event, dict) else None
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        return count_tools_in_turn(content)
    return None


def _summarize(stats: TurnToolStats) -> str:
    parts: list[str] = []
    if stats.file_mutations:
        parts.append(f"{stats.file_mutations} file mutations (Edit/Write/Bash)")
    if stats.file_reads:
        parts.append(f"{stats.file_reads} file reads")
    if stats.memory_reads:
        parts.append(f"{stats.memory_reads} memory reads")
    if stats.other:
        parts.append(f"{stats.other} other tool calls")
    return ", ".join(parts) or "tool activity"


def decide_audit(
    stats: TurnToolStats | None, *, min_mutations: int = _DEFAULT_MIN_MUTATIONS
) -> AuditDecision:
    """Decide whether the previous turn warrants an audit-prompt injection."""
    if stats is None or stats.file_mutations < min_mutations or stats.memory_writes > 0:
        return AuditDecision(inject=False, prompt="")
    summary = _summarize(stats)
    prompt = (
        f"[memory-audit] Previous assistant turn: {summary}, 0 memory writes. "
        "Before responding to this user prompt, call memory_ingest_episode "
        "summarizing the work you just did. If any architectural choice was "
        "made, also call memory_write_decision (or memory_record_with_evidence "
        "for the atomic combo). Acknowledging this audit in your response is "
        "REQUIRED, not optional — the operator sees the audit text and treats "
        "absence of follow-through as a violation."
    )
    return AuditDecision(inject=True, prompt=prompt)
