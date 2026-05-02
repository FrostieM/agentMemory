"""Shared helper for research-lab writers."""

from __future__ import annotations

from agent_memory_lite.api.errors import ValidationError


def validate_workspace(
    *,
    item_workspace_id: str,
    payload_workspace_id: str,
    field_name: str,
) -> None:
    if item_workspace_id != payload_workspace_id:
        raise ValidationError(f"{field_name} must belong to the same workspace")
