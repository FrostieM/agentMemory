from __future__ import annotations

import re

import pytest

from agent_memory_lite.utils.ids import IdKind, new_id


def test_id_starts_with_kind_prefix() -> None:
    eid = new_id(IdKind.EPISODE)
    assert eid.startswith("ep_")


def test_id_has_expected_length() -> None:
    eid = new_id(IdKind.CHUNK, length=16)
    assert re.fullmatch(r"chk_[0-9a-f]{16}", eid)


def test_ids_are_unique_across_many_calls() -> None:
    ids = {new_id(IdKind.FACT) for _ in range(1000)}
    assert len(ids) == 1000


def test_zero_length_rejected() -> None:
    with pytest.raises(ValueError, match="length must be positive"):
        new_id(IdKind.EPISODE, length=0)
