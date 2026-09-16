"""Unit tests for Phoenix span normalization (pure functions, no I/O).

The shapes below are copied from real Phoenix spans emitted by this app's
LangChain/LangGraph instrumentation, which is why they are so specific: the
parser exists precisely because those payloads are provider-shaped.
"""

from __future__ import annotations

from src.services.phoenix_span_format import (
    extract_turns,
    flatten,
    normalize_span,
    summarize,
)


def test_flatten_nests_dotted_keys():
    flat = flatten({"llm": {"model_name": "gpt", "token_count": {"prompt": 3}}, "session": {"id": "s"}})
    assert flat["llm.model_name"] == "gpt"
    assert flat["llm.token_count.prompt"] == 3
    assert flat["session.id"] == "s"


def test_extract_turns_langchain_serialized_messages():
    payload = (
        '{"messages": [[{"lc": 1, "type": "constructor", '
        '"id": ["langchain", "schema", "messages", "SystemMessage"], '
        '"kwargs": {"content": "You are helpful"}}, '
        '{"lc": 1, "id": ["langchain", "schema", "messages", "HumanMessage"], '
        '"kwargs": {"content": "hi"}}]]}'
    )
    turns = extract_turns(payload)
    assert [t["role"] for t in turns] == ["system", "user"]
    assert turns[0]["text"] == "You are helpful"
    assert turns[1]["text"] == "hi"


def test_extract_turns_aimessage_with_reasoning_and_tool_calls():
    payload = (
        '{"messages": [{"type": "ai", "data": {"content": "", '
        '"additional_kwargs": {"reasoning": "let me check"}, '
        '"tool_calls": [{"name": "list_tables", "args": {"schema": "main"}}]}}]}'
    )
    turns = extract_turns(payload)
    assert len(turns) == 1
    assert turns[0]["role"] == "assistant"
    assert turns[0]["reasoning"] == "let me check"
    assert turns[0]["tool_calls"] == [{"name": "list_tables", "args": {"schema": "main"}}]


def test_extract_turns_langgraph_update_wrapper():
    payload = '[{"graph": null, "update": {"messages": [{"type": "ai", "data": {"content": "done"}}]}}]'
    turns = extract_turns(payload)
    assert turns == [{"role": "assistant", "text": "done", "tool_calls": [], "reasoning": ""}]


def test_extract_turns_generations_text():
    turns = extract_turns('{"generations": [[{"text": "final answer"}]]}')
    assert turns[0]["role"] == "assistant"
    assert turns[0]["text"] == "final answer"


def test_extract_turns_tool_result_content_parts():
    payload = '{"type": "tool", "data": {"content": [{"type": "text", "text": "| a |\\n| - |"}]}}'
    turns = extract_turns(payload)
    assert turns[0]["role"] == "tool"
    assert "| a |" in turns[0]["text"]


def test_extract_turns_opaque_string_is_text():
    turns = extract_turns("SELECT 1")
    assert turns == [{"role": "text", "text": "SELECT 1", "tool_calls": [], "reasoning": ""}]


def test_extract_turns_dedupes_repeated_turn():
    payload = (
        '[{"type": "ai", "data": {"content": "same"}}, '
        '{"type": "ai", "data": {"content": "same"}}]'
    )
    assert len(extract_turns(payload)) == 1


def test_normalize_span_reads_nested_attributes_and_tokens():
    node = {
        "spanId": "abc",
        "parentId": None,
        "name": "ReasoningChatOpenAI",
        "spanKind": "llm",
        "startTime": "2026-09-16T14:06:23.132599+00:00",
        "endTime": "2026-09-16T14:06:33.000000+00:00",
        "latencyMs": 9867.9,
        "statusCode": "OK",
        "statusMessage": "",
        "tokenCountPrompt": 1720,
        "tokenCountCompletion": 88,
        "attributes": (
            '{"llm": {"model_name": "mimo-v2.5"}, "metadata": {"agent_name": "Text-to-SQL Agent",'
            ' "langgraph_node": "model"}, "tool": {"name": "execute_sql_query"}}'
        ),
        "input": {"value": '{"messages": [{"role": "user", "content": "hi"}]}', "mimeType": "application/json"},
        "output": {"value": "plain text answer", "mimeType": "text/plain"},
        "events": [{"name": "exception", "message": "boom", "timestamp": "t", "attributes": {"a": 1}}],
    }
    span = normalize_span(node)
    assert span["id"] == "abc"
    assert span["kind"] == "LLM"
    assert span["model"] == "mimo-v2.5"
    assert span["tool_name"] == "execute_sql_query"
    assert span["agent_name"] == "Text-to-SQL Agent"
    assert span["node"] == "model"
    assert span["prompt_tokens"] == 1720
    assert span["completion_tokens"] == 88
    assert span["total_tokens"] == 1808
    assert span["error"] is False
    assert span["input"]["turns"][0]["role"] == "user"
    assert span["attributes"]["llm.model_name"] == "mimo-v2.5"
    assert span["events"][0]["name"] == "exception"


def test_normalize_span_flags_error_status_and_exception():
    node = {
        "spanId": "x",
        "name": "execute_sql_query",
        "spanKind": "TOOL",
        "statusCode": "ERROR",
        "statusMessage": "syntax error",
        "attributes": "{}",
    }
    span = normalize_span(node)
    assert span["error"] is True
    assert span["status_message"] == "syntax error"


def test_summarize_aggregates_tokens_models_and_kinds():
    spans = [
        normalize_span({"spanId": "1", "name": "a", "spanKind": "LLM", "tokenCountPrompt": 10,
                        "tokenCountCompletion": 5, "attributes": '{"llm": {"model_name": "m"}}'}),
        normalize_span({"spanId": "2", "name": "t", "spanKind": "TOOL", "attributes": "{}"}),
        normalize_span({"spanId": "3", "name": "e", "spanKind": "TOOL", "statusCode": "ERROR", "attributes": "{}"}),
    ]
    summary = summarize(spans, {"latencyMs": 100.0, "costSummary": {"total": {"cost": 0.5}}})
    assert summary["span_count"] == 3
    assert summary["error_count"] == 1
    assert summary["prompt_tokens"] == 10
    assert summary["completion_tokens"] == 5
    assert summary["total_tokens"] == 15
    assert summary["models"] == ["m"]
    assert summary["kinds"] == {"llm": 1, "tool": 2}
    assert summary["llm_calls"] == 1
    assert summary["tool_calls"] == 2
    assert summary["cost"] == 0.5
