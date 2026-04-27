"""Prefixed identifier generation.

Every domain object gets a short prefix so a raw ID is debuggable at a glance
(`ep_...` → episode, `chk_...` → chunk, `dec_...` → decision, etc.).
"""

from __future__ import annotations

import secrets
from enum import StrEnum


class IdKind(StrEnum):
    EPISODE = "ep"
    CHUNK = "chk"
    FILE = "file"
    DECISION = "dec"
    THEORY = "th"
    THEORY_EVIDENCE = "thev"
    MEMORY_SNAPSHOT = "snap"
    EXPERIMENT = "exp"
    EXPERIMENT_RESULT = "res"
    DOMAIN_CONCEPT = "concept"
    RESEARCH_INSIGHT = "insight"
    AGENT_ROLE = "role"
    AGENT_SKILL = "skill"
    AGENT_PLAYBOOK = "play"
    TASK_STATE = "task"
    CORE_MEMORY = "core"
    PROCEDURAL_RULE = "rule"
    ENTITY = "ent"
    FACT = "fact"
    AUDIT = "aud"
    EMBEDDING = "emb"


def new_id(kind: IdKind, *, length: int = 16) -> str:
    """Return a fresh ID like `ep_a1b2c3d4e5f60718`.

    The hex tail uses `secrets.token_hex` so collisions are negligible at the scale
    a single-user local service will see.
    """
    if length <= 0:
        raise ValueError("length must be positive")
    return f"{kind.value}_{secrets.token_hex(length // 2)}"
