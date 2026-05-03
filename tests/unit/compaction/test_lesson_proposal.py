"""Pure parser for lesson proposals — golden-input tests, no LLM needed."""

from __future__ import annotations

from agent_memory_lite.compaction.lesson_proposal import (
    EpisodeWindowItem,
    build_prompt,
    parse_lessons_response,
)


def _pool() -> set[str]:
    return {"ep_a", "ep_b", "ep_c", "ep_d", "ep_e"}


def test_empty_string_returns_empty_list() -> None:
    assert (
        parse_lessons_response(
            "", episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=10
        )
        == []
    )


def test_invalid_json_returns_empty_list() -> None:
    assert (
        parse_lessons_response(
            "not json at all",
            episode_ids_in_pool=_pool(),
            min_support_episodes=2,
            max_per_run=10,
        )
        == []
    )


def test_non_list_root_returns_empty_list() -> None:
    assert (
        parse_lessons_response(
            '{"oops": true}',
            episode_ids_in_pool=_pool(),
            min_support_episodes=2,
            max_per_run=10,
        )
        == []
    )


def test_well_formed_proposal_parses() -> None:
    raw = (
        '[{"insight_type": "lesson_learned",'
        ' "summary": "X happens whenever Y is missed",'
        ' "source_episode_ids": ["ep_a", "ep_b"],'
        ' "proposed_action": "Track Y explicitly",'
        ' "confidence": 0.78}]'
    )
    out = parse_lessons_response(
        raw, episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=10
    )
    assert len(out) == 1
    assert out[0].insight_type == "lesson_learned"
    assert out[0].source_episode_ids == ("ep_a", "ep_b")
    assert out[0].confidence == 0.78


def test_below_min_support_drops_proposal() -> None:
    raw = (
        '[{"insight_type": "lesson_learned",'
        ' "summary": "narrow lesson",'
        ' "source_episode_ids": ["ep_a"]}]'
    )
    assert (
        parse_lessons_response(
            raw, episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=10
        )
        == []
    )


def test_episode_ids_not_in_pool_filtered_out() -> None:
    raw = (
        '[{"insight_type": "lesson_learned",'
        ' "summary": "fabricated lesson",'
        ' "source_episode_ids": ["ep_unknown_1", "ep_unknown_2"]}]'
    )
    out = parse_lessons_response(
        raw, episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=10
    )
    assert out == []  # All ids filtered → not enough support → drop.


def test_unknown_insight_type_falls_back_to_default() -> None:
    raw = (
        '[{"insight_type": "magic_unicorn",'
        ' "summary": "valid summary",'
        ' "source_episode_ids": ["ep_a", "ep_b"]}]'
    )
    out = parse_lessons_response(
        raw, episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=10
    )
    assert len(out) == 1
    assert out[0].insight_type == "open_question"


def test_max_per_run_limits_output() -> None:
    items = [
        '{"insight_type": "lesson_learned", "summary": "lesson '
        + str(i)
        + '", "source_episode_ids": ["ep_a", "ep_b"]}'
        for i in range(5)
    ]
    raw = "[" + ",".join(items) + "]"
    out = parse_lessons_response(
        raw, episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=2
    )
    assert len(out) == 2


def test_confidence_clamped_to_zero_one() -> None:
    raw = (
        '[{"insight_type": "lesson_learned",'
        ' "summary": "x",'
        ' "source_episode_ids": ["ep_a", "ep_b"],'
        ' "confidence": 99.5}]'
    )
    out = parse_lessons_response(
        raw, episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=10
    )
    assert out[0].confidence == 1.0


def test_fenced_code_block_is_stripped() -> None:
    raw = '```json\n[{"insight_type": "lesson_learned", "summary": "ok",'
    raw += ' "source_episode_ids": ["ep_a", "ep_b"]}]\n```'
    out = parse_lessons_response(
        raw, episode_ids_in_pool=_pool(), min_support_episodes=2, max_per_run=10
    )
    assert len(out) == 1


def test_build_prompt_mentions_min_support_and_max_per_run() -> None:
    window = [EpisodeWindowItem(id="ep_a", summary="x"), EpisodeWindowItem(id="ep_b", summary="y")]
    prompt = build_prompt(window, max_per_run=7, min_support_episodes=4)
    assert "7" in prompt
    assert "4" in prompt
    assert "ep_a" in prompt
    assert "ep_b" in prompt


def test_build_prompt_handles_empty_window() -> None:
    prompt = build_prompt([], max_per_run=3, min_support_episodes=2)
    assert "(empty)" in prompt
