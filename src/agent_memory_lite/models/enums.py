"""Domain enums.

All string-valued so they round-trip cleanly through SQLite TEXT columns and JSON.
"""

from __future__ import annotations

from enum import StrEnum


class TrustLevel(StrEnum):
    USER_ASSERTED = "user_asserted"
    EXPLICIT_DECISION = "explicit_decision"
    VERIFIED_BY_TOOL = "verified_by_tool"
    AGENT_OBSERVED = "agent_observed"
    AGENT_INFERRED = "agent_inferred"
    UNTRUSTED_DOC = "untrusted_doc"
    UNKNOWN = "unknown"


class EpisodeSource(StrEnum):
    USER_MESSAGE = "user_message"
    AGENT_ACTION = "agent_action"
    AGENT_REPLY = "agent_reply"
    TOOL_RESULT = "tool_result"
    COMMAND_OUTPUT = "command_output"
    FILE_INDEXED = "file_indexed"
    FILE_CHANGED = "file_changed"
    SYSTEM = "system"
    SUMMARY = "summary"


class ChunkKind(StrEnum):
    EPISODE = "episode"
    CODE = "code"
    DOC = "doc"
    LOG = "log"
    SUMMARY = "summary"


class DecisionStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class MemoryCandidateKind(StrEnum):
    CONSTRAINT = "constraint"
    PROJECT_FACT = "project_fact"
    DECISION = "decision"
    TASK_STATE = "task_state"
    RELATIONSHIP = "relationship"
    PROCEDURAL_RULE = "procedural_rule"
    CORRECTION = "correction"
    BUG = "bug"
    FIX = "fix"
