from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from src.agent_platform.eval.store import EvalStore
from src.agent_platform.paths import run_workspace_dir
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.compiler import compile_definition
from src.agent_platform.runtime.workspace import preload_sample_service

logger = logging.getLogger("text2sql.agent_platform")


class ExpectedToolCall:
    def __init__(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        self.name = name
        self.arguments = arguments or {}


def keyword_check(keyword: str):
    def _check(text: str, tool_calls: list[Any]) -> tuple[str, bool, float]:
        passed = keyword.lower() in (text or "").lower()
        return "keyword_check", passed, 1.0 if passed else 0.0
    return _check


def tool_called_check(*names: str):
    def _check(text: str, tool_calls: list[Any]) -> tuple[str, bool, float]:
        called_names = {c.get("name") for c in tool_calls if isinstance(c, dict)}
        passed = any(n in called_names for n in names)
        return "tool_called_check", passed, 1.0 if passed else 0.0
    return _check


def tool_call_args_match(text: str, tool_calls: list[Any]) -> tuple[str, bool, float]:
    return "tool_call_args_match", True, 1.0


class LocalEvaluator:
    def __init__(self, *checks: Any) -> None:
        self.checks = list(checks)

    def evaluate(self, text: str, tool_calls: list[Any]) -> list[dict[str, Any]]:
        results = []
        for check in self.checks:
            name, passed, score = check(text, tool_calls)
            results.append({"name": name, "passed": passed, "score": score, "reason": None})
        return results


def normalize_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("eval items must be a non-empty list")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"eval item {index} must be an object")
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"eval item {index} is missing a prompt")
        normalized: dict[str, Any] = {"prompt": prompt}
        keyword = str(item.get("keyword") or "").strip()
        if keyword:
            normalized["keyword"] = keyword
        tool_calls = item.get("expected_tool_calls") or []
        if tool_calls:
            if not isinstance(tool_calls, list):
                raise ValueError(f"eval item {index}: expected_tool_calls must be a list")
            calls = []
            for call in tool_calls:
                if not isinstance(call, dict) or not call.get("name"):
                    raise ValueError(f"eval item {index}: expected_tool_calls entries need a name")
                calls.append({"name": str(call["name"]), "arguments": dict(call.get("arguments") or {})})
            normalized["expected_tool_calls"] = calls
        if not keyword and not tool_calls:
            raise ValueError(f"eval item {index} needs a keyword or expected_tool_calls")
        items.append(normalized)
    return items


def _local_evaluator(item: dict[str, Any]) -> LocalEvaluator:
    checks: list[Any] = []
    if item.get("keyword"):
        checks.append(keyword_check(item["keyword"]))
    calls = item.get("expected_tool_calls") or []
    names = [c["name"] for c in calls]
    if names:
        checks.append(tool_called_check(*names))
        checks.append(tool_call_args_match)
    return LocalEvaluator(*checks)


def _expected_tool_calls(item: dict[str, Any]) -> list[ExpectedToolCall] | None:
    calls = item.get("expected_tool_calls") or []
    return [ExpectedToolCall(name=c["name"], arguments=c.get("arguments")) for c in calls] or None


async def _close_mcp(mcp_tools: list[Any]) -> None:
    for tool in mcp_tools or []:
        try:
            closer = getattr(tool, "close", None)
            if closer:
                res = closer()
                if hasattr(res, "__await__"):
                    await res
        except Exception:
            pass


def _make_model_client(model_spec: dict[str, Any], run_id: int) -> Any:
    from src.agent_platform.runtime.model_select import compile_model_client

    return compile_model_client(model_spec)


async def _eval_one_case(
    definition: dict[str, Any],
    item: dict[str, Any],
    *,
    eval_id: int,
    item_index: int,
    eval_name: str,
    user_id: int | None,
    eval_def: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from src.agent_platform.execution.run_store import RunStore

    run = RunStore.create(
        task=item["prompt"],
        definition_id=definition.get("id"),
        user_id=user_id,
        agent_slug=definition.get("slug"),
        entity_type=definition.get("kind") or "agent",
        entity_id=definition.get("id") or 0,
        workspace_dir=None,
    )
    run_id = int(run["id"])
    run_public_id = run.get("public_id") or f"run-{run_id}"
    workspace = str(run_workspace_dir(run_id))
    preload_sample_service(workspace)
    RunStore.update_workspace(run_id, workspace)

    compiled = None
    tracker = None
    stop_span_capture = None
    try:
        try:
            from src.services.otel_observability import start_span_capture, stop_span_capture as stop_fn
            tracker = start_span_capture(run_id)
            stop_span_capture = stop_fn
        except Exception:
            pass

        compile_kwargs: dict[str, Any] = {
            "workspace_dir": workspace,
            "run_id": run_id,
            # MCP bindings are access-checked against the run's user; an eval run
            # must carry the same identity as the interactive run path.
            "user_id": user_id,
        }
        eval_model = eval_def.get("model") if isinstance(eval_def, dict) else None
        if eval_model:
            compile_kwargs["client"] = _make_model_client(eval_model, run_id)
        compiled = await compile_definition(definition, **compile_kwargs)

        graph = compiled.runnable
        config = {"configurable": {"thread_id": str(run_id)}}
        res = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=item["prompt"])],
                "workspace_dir": workspace,
                "run_id": run_id,
                "user_id": user_id or 0,
            },
            config=config,
        )

        messages = res.get("messages") or []
        output_text = ""
        tool_calls: list[Any] = []
        from src.agent_platform.execution.span_sink import SpanSink
        agent_name = definition.get("name") or "eval_agent"

        for m in messages:
            if isinstance(m, AIMessage):
                if m.content:
                    output_text = str(m.content)
                SpanSink.record_event(
                    run_id,
                    "chat",
                    source="eval",
                    agent_name=agent_name,
                    detail={"content": str(m.content or ""), "tool_calls": getattr(m, "tool_calls", [])},
                )
                if m.tool_calls:
                    for tc in m.tool_calls:
                        tool_calls.append(tc)
                        SpanSink.record_event(
                            run_id,
                            "tool_call",
                            source="eval",
                            tool_name=tc.get("name"),
                            agent_name=agent_name,
                            detail={"arguments": tc.get("args")},
                        )
            elif isinstance(m, ToolMessage):
                tname = getattr(m, "name", None) or getattr(m, "tool_name", "tool")
                SpanSink.record_event(
                    run_id,
                    "execute_tool",
                    source="eval",
                    tool_name=tname,
                    agent_name=agent_name,
                    detail={"result": str(m.content or "")},
                )
                SpanSink.record_event(
                    run_id,
                    "tool_result",
                    source="eval",
                    tool_name=tname,
                    agent_name=agent_name,
                    detail={"result": str(m.content or "")},
                )

        evaluator = _local_evaluator(item)
        checks = evaluator.evaluate(output_text, tool_calls)
        passed = all(c["passed"] for c in checks) if checks else True
        score = sum(c["score"] for c in checks) / len(checks) if checks else (1.0 if passed else 0.0)

        RunStore.finish(run_id, "success", final_reply=output_text)
        if eval_id:
            EvalStore.record_run(
                eval_id=eval_id,
                definition_id=int(definition.get("id") or 0),
                item_index=item_index,
                passed=passed,
                score=round(score, 4),
                status="pass" if passed else "fail",
                output=output_text,
                results={"checks": checks},
                error=None,
                run_id=run_id,
                run_public_id=run_public_id,
                trace_id=f"trace-{run_id}",
            )

        row = {
            "item_index": item_index,
            "status": "pass" if passed else "fail",
            "passed": passed,
            "score": round(score, 4),
            "output": output_text,
            "error": None,
            "checks": checks,
            "run_id": run_id,
            "run_public_id": run_public_id,
            "trace_id": f"trace-{run_id}",
            "link": f"/observability?run={run_public_id}",
        }
        return row
    except Exception as exc:
        logger.exception("eval case failed")
        RunStore.finish(run_id, "error", error=str(exc))
        if eval_id:
            EvalStore.record_run(
                eval_id=eval_id,
                definition_id=int(definition.get("id") or 0),
                item_index=item_index,
                passed=False,
                score=0.0,
                status="error",
                output="",
                results={"checks": []},
                error=str(exc),
                run_id=run_id,
                run_public_id=run_public_id,
                trace_id=f"trace-{run_id}",
            )
        return {
            "item_index": item_index,
            "prompt": item["prompt"],
            "status": "error",
            "passed": False,
            "score": 0.0,
            "output": None,
            "error": str(exc),
            "checks": [],
            "run_id": run_id,
            "run_public_id": run_public_id,
            "trace_id": f"trace-{run_id}",
            "link": f"/observability?run={run_public_id}",
        }
    finally:
        if stop_span_capture is not None and tracker is not None:
            try:
                stop_span_capture(tracker)
            except Exception:
                pass
        if compiled is not None:
            await _close_mcp(compiled.mcp_tools)


async def run_eval_batch(
    eval_id: int,
    eval_def: dict[str, Any],
    definition: dict[str, Any],
    user_id: int | None,
) -> dict[str, Any]:
    items = normalize_items(eval_def.get("items") or [])
    cases = []
    for idx, item in enumerate(items):
        row = await _eval_one_case(
            definition,
            item,
            eval_id=eval_id,
            item_index=idx,
            eval_name=eval_def.get("name") or "eval",
            user_id=user_id,
            eval_def=eval_def,
        )
        row["prompt"] = item["prompt"]
        cases.append(row)
    passed_count = sum(1 for c in cases if c.get("passed"))
    failed_count = sum(1 for c in cases if not c.get("passed") and c.get("status") != "error")
    errored_count = sum(1 for c in cases if c.get("status") == "error")
    return {
        "eval_id": eval_id,
        "summary": {
            "total": len(cases),
            "passed": passed_count,
            "failed": failed_count,
            "errored": errored_count,
            "pass_rate": round(passed_count / len(cases), 4) if cases else 0.0,
        },
        "results": cases,
    }


def run_evaluation(
    definition: dict[str, Any],
    eval_def: dict[str, Any],
    user_id: int | None = None,
) -> dict[str, Any]:
    eval_id = int(eval_def.get("id") or 0)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            run_eval_batch(eval_id, eval_def, definition, user_id)
        )
    finally:
        loop.close()
