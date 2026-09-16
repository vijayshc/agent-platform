"""The output-cap guard: a truncated model call must become an error.

The check reads the provider's own finish reason, so it is a pure function and
needs no model. Live coverage of the same behaviour is in
``tests/test_live_output_limit.py``.
"""

from __future__ import annotations

from src.agent_platform.runtime.output_budget import truncated_message


def _usage(output: int, reasoning: int) -> dict:
    return {"output_tokens": output, "output_token_details": {"reasoning": reasoning}}


def test_reasoning_ate_the_whole_budget():
    text = truncated_message(
        content="",
        finish_reason="length",
        usage=_usage(8000, 8000),
        model_name="deepseek/deepseek-v4-flash",
    )
    assert text is not None
    assert "8000-token output limit" in text
    assert "reasoning" in text
    assert "no answer" in text


def test_cut_off_while_writing_the_answer():
    text = truncated_message(
        content="Here is a detailed design document, structured to be",
        finish_reason="length",
        usage=_usage(400, 334),
        model_name="model-x",
    )
    assert text is not None
    assert "cut off mid-answer" in text
    assert "400-token output limit" in text


def test_a_tool_call_turn_is_not_a_truncation():
    assert truncated_message(content="", finish_reason="tool_calls", tool_calls=2) is None


def test_a_finished_answer_is_not_a_truncation():
    assert truncated_message(content="done", finish_reason="stop", usage=_usage(12, 3)) is None


def test_max_tokens_finish_reason_is_also_a_truncation():
    assert truncated_message(content="", finish_reason="max_tokens") is not None
