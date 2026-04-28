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


class TheoryStatus(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    VALIDATED = "validated"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class TheoryEvidenceKind(StrEnum):
    SUPPORTING = "supporting"
    REFUTING = "refuting"
    MIXED = "mixed"
    NEUTRAL = "neutral"
    EXPERIMENT = "experiment"


class ExperimentStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ConceptKind(StrEnum):
    TERM = "term"
    METRIC = "metric"
    GATE = "gate"
    COHORT = "cohort"
    ARTIFACT = "artifact"


class InsightType(StrEnum):
    THEORY_CANDIDATE = "theory_candidate"
    EVIDENCE_CANDIDATE = "evidence_candidate"
    DECISION_CANDIDATE = "decision_candidate"
    RULE_CANDIDATE = "rule_candidate"
    OPEN_QUESTION = "open_question"
    CONTRADICTION = "contradiction"
    BOTTLENECK = "bottleneck"
    LESSON = "lesson"
    RISK = "risk"
    OPPORTUNITY = "opportunity"


class InsightStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ARCHIVED = "archived"


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


class MemoryCandidateStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class MaintenanceEventStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class MaintenanceSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class CapabilityType(StrEnum):
    ROLE = "role"
    SKILL = "skill"
    PLAYBOOK = "playbook"


class CapabilityLinkTargetType(StrEnum):
    THEORY = "theory"
    THEORY_EVIDENCE = "theory_evidence"
    EXPERIMENT = "experiment"
    EXPERIMENT_RESULT = "experiment_result"
    RESEARCH_INSIGHT = "research_insight"
    MEMORY_CANDIDATE = "memory_candidate"
    DECISION = "decision"


class CapabilityLinkRelation(StrEnum):
    OWNER = "owner"
    REVIEWER = "reviewer"
    CRITIQUE_LENS = "critique_lens"
    METHOD = "method"
    REQUIRED_SKILL = "required_skill"
    VALIDATION_PLAYBOOK = "validation_playbook"
    EVIDENCE_METHOD = "evidence_method"
    IMPLEMENTATION_ROLE = "implementation_role"
