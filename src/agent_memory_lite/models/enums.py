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
    # v3.0.0-final: legacy data path. Pre-v3 workspaces wrote
    # status='archived' from auto-promote → behavior pipeline.
    # Enum stays tolerant rather than 500ing on browse.
    ARCHIVED = "archived"


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
    # v3.1 Vector 1: proto-theory proposal emitted by the heuristic
    # experiment-proposal scanner (find_proposal_candidates). Operator
    # promotes via the existing review queue to a real ``theory`` row.
    THEORY_PROPOSAL = "theory_proposal"
    # v3.1 Vector 5: predictive-failure warning. The scanner surfaces a
    # new candidate decision that token-overlaps a known low-outcome
    # decision; operator reviews to either pin the warning as a
    # behavior instruction or reject the false positive.
    PREDICTIVE_WARNING = "predictive_warning"


class MemoryCandidateStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class MaintenanceEventStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class MaintenanceActionStatus(StrEnum):
    """v3.4 #6 — operator-side triage state. Orthogonal to
    ``MaintenanceEventStatus``: ``status`` tracks the substrate-level
    truth (does the drift still exist?), ``action_status`` tracks the
    operator workflow (is anyone working on this?). The queue page at
    ``/ui/queue`` filters by this dimension."""

    OPEN = "open"
    CLAIMED = "claimed"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


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


class BehaviorInstructionKind(StrEnum):
    COMMUNICATION_STYLE = "communication_style"
    OPERATING_RULE = "operating_rule"
    PROJECT_CONVENTION = "project_convention"
    WORKFLOW_PREFERENCE = "workflow_preference"
    ROLE_GUIDANCE = "role_guidance"


class BehaviorInstructionScope(StrEnum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    PROJECT = "project"
    TASK = "task"
    ROLE = "role"


class BehaviorInstructionPriority(StrEnum):
    SYSTEM_BOUND = "system_bound"
    USER_PREFERENCE = "user_preference"
    PROJECT_CONVENTION = "project_convention"
    SUGGESTION = "suggestion"


class BehaviorConflictPolicy(StrEnum):
    SYSTEM_WINS = "system_wins"
    CURRENT_USER_WINS = "current_user_wins"
    HIGHER_PRIORITY_WINS = "higher_priority_wins"
    MOST_SPECIFIC_WINS = "most_specific_wins"
    LATEST_WINS = "latest_wins"
