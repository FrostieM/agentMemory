"""Walk a built context object and list each item used in the envelope.

Split out of ``explain.py`` so the orchestrator stays under the SLOC
ceiling. ``used_context_objects`` builds the audit trail the
explainer + ``trace_used_context_objects`` both consume.
"""

from __future__ import annotations

from agent_memory_lite.retrieval.explain_models import UsedContextObjectExplanation


def _object_label(item: object) -> str:
    for attr in ("title", "name", "summary", "goal", "path", "text", "claim"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    return str(getattr(item, "id", "memory object"))


def _object_time(item: object) -> str | None:
    for attr in ("updated_at", "created_at", "observed_at", "valid_from", "last_indexed_at"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    metadata = getattr(item, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("updated_at", "created_at", "observed_at"):
            if metadata.get(key):
                return str(metadata[key])
    return None


def _append_used_object(
    out: list[UsedContextObjectExplanation],
    *,
    table: str,
    item: object,
    relation: str,
) -> None:
    item_id = str(getattr(item, "id", "") or "")
    if not item_id:
        return
    out.append(
        UsedContextObjectExplanation(
            table=table,
            id=item_id,
            label=_object_label(item),
            relation=relation,
            updated_at=_object_time(item),
            rank=len(out),
        )
    )


def _append_research_objects(out: list[UsedContextObjectExplanation], context: object) -> None:
    for theory_context in getattr(context, "theories", []):
        theory = getattr(theory_context, "theory", None)
        if theory is not None:
            _append_used_object(out, table="theories", item=theory, relation="theory")
        for item in getattr(theory_context, "evidence", []):
            _append_used_object(out, table="theory_evidence", item=item, relation="evidence")
    agenda = getattr(context, "research_agenda", None)
    for table, attr, relation in (
        ("memory_snapshots", "snapshots", "snapshot"),
        ("research_experiments", "experiments", "experiment"),
        ("research_insights", "insights", "insight"),
        ("domain_concepts", "concepts", "concept"),
    ):
        for item in getattr(agenda, attr, []) if agenda else []:
            _append_used_object(out, table=table, item=item, relation=relation)


def _append_capability_objects(out: list[UsedContextObjectExplanation], context: object) -> None:
    capabilities = getattr(context, "agent_capabilities", None)
    for table, attr, relation in (
        ("agent_roles", "roles", "role"),
        ("agent_skills", "skills", "skill"),
        ("agent_playbooks", "playbooks", "playbook"),
    ):
        for item in getattr(capabilities, attr, []) if capabilities else []:
            _append_used_object(out, table=table, item=item, relation=relation)


def used_context_objects(context: object) -> list[UsedContextObjectExplanation]:
    out: list[UsedContextObjectExplanation] = []
    task = getattr(context, "task_state", None)
    if task is not None:
        _append_used_object(out, table="task_state", item=task, relation="task state")
    behavior = getattr(context, "behavior_instructions", None)
    for item in getattr(behavior, "instructions", []) if behavior else []:
        _append_used_object(out, table="behavior_instructions", item=item, relation="instruction")
    for item in getattr(context, "decisions", []):
        _append_used_object(out, table="decisions", item=item, relation="decision")
    _append_research_objects(out, context)
    _append_capability_objects(out, context)
    for item in getattr(context, "rules", []):
        _append_used_object(out, table="procedural_rules", item=item, relation="rule")
    for item in getattr(context, "facts", []):
        _append_used_object(out, table="retrieved_facts", item=item, relation="fact")
    for item in getattr(context, "hits", []):
        _append_used_object(out, table="chunks", item=item, relation="retrieved chunk")
    return out


def section_counts(context: object) -> dict[str, int]:
    decisions = getattr(context, "decisions", [])
    theories = getattr(context, "theories", [])
    agenda = getattr(context, "research_agenda", None)
    behavior = getattr(context, "behavior_instructions", None)
    capabilities = getattr(context, "agent_capabilities", None)
    return {
        "core": len(getattr(context, "core", [])),
        "behavior_instructions": len(getattr(behavior, "instructions", []) if behavior else []),
        "decisions": len(decisions),
        "theories": len(theories),
        "experiments": len(getattr(agenda, "experiments", []) if agenda else []),
        "insights": len(getattr(agenda, "insights", []) if agenda else []),
        "concepts": len(getattr(agenda, "concepts", []) if agenda else []),
        "snapshots": len(getattr(agenda, "snapshots", []) if agenda else []),
        "roles": len(getattr(capabilities, "roles", []) if capabilities else []),
        "skills": len(getattr(capabilities, "skills", []) if capabilities else []),
        "playbooks": len(getattr(capabilities, "playbooks", []) if capabilities else []),
        "rules": len(getattr(context, "rules", [])),
        "facts": len(getattr(context, "facts", [])),
        "chunks": len(getattr(context, "hits", [])),
    }
