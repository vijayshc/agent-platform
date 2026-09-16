"""Model selection: pin > run-level pick > operator default, and agent options.

The chat model picker is a run-level default. An agent that names a connection
is pinned to it; ``client: "default"`` (or no client at all) follows whatever
the caller picked, and falls back to the operator's default connection.
"""

from __future__ import annotations

import pytest

from src.agent_platform.catalog.validate import validate_definition
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.compiler import compile_definition_sync
from src.agent_platform.runtime.model_select import generation_options
from src.agent_platform.runtime.model_select import compile_model_client
from src.agent_platform.runtime.scripted_client import ScriptedChatClient
from src.utils import llm_connection_manager as mgr


def _agent(name: str, **config):
    base = {"kind": "agent", "name": name, "instructions": f"You are {name}."}
    base.update(config)
    return {"kind": "agent", "name": name, "config": base}


@pytest.fixture()
def connections(temp_db):
    pinned = mgr.save_connection(
        {
            "name": "Pinned",
            "base_url": "https://pinned.example/v1",
            "api_key": "k",
            "model_name": "pinned-model",
            "extra_body": {"temperature": 0.9, "max_tokens": 4321},
        }
    )
    default = mgr.save_connection(
        {
            "name": "Fallback",
            "base_url": "https://fallback.example/v1",
            "api_key": "k",
            "model_name": "fallback-model",
            "is_default": True,
        }
    )
    return pinned, default


def test_pinned_connection_beats_the_run_level_client(connections):
    register_builtin_plugins()
    pinned, _ = connections
    run_client = ScriptedChatClient(responses=["run-level"])

    compiled = compile_definition_sync(
        _agent("Pinned", model={"client": str(pinned.id)}), client=run_client
    )
    assert compiled.runnable.client is not run_client
    assert compiled.runnable.client.model_name == "pinned-model"


def test_default_and_missing_clients_follow_the_run_level_pick(connections):
    register_builtin_plugins()
    run_client = ScriptedChatClient(responses=["run-level"])

    for spec in ({"client": "default", "name": None}, {}, {"client": ""}):
        compiled = compile_definition_sync(_agent("Follows", model=spec), client=run_client)
        assert compiled.runnable.client is run_client, spec


def test_default_connection_used_when_no_run_level_pick(connections):
    register_builtin_plugins()
    _, default = connections
    compiled = compile_definition_sync(_agent("Solo", model={"client": "default"}))
    assert compiled.runnable.client.model_name == "fallback-model"
    assert getattr(compiled.runnable.client, "_llm_connection", None).id == default.id


def test_agent_generation_options_override_connection_parameters(connections):
    register_builtin_plugins()
    pinned, _ = connections
    compiled = compile_definition_sync(
        _agent(
            "Tuned",
            model={"client": str(pinned.id)},
            default_options={"temperature": 0.05, "max_tokens": 77},
        )
    )
    assert compiled.runnable.client.extra_body == {"temperature": 0.05, "max_tokens": 77}


def test_max_output_tokens_wins_over_default_options(connections):
    register_builtin_plugins()
    pinned, _ = connections
    compiled = compile_definition_sync(
        _agent(
            "Capped",
            model={"client": str(pinned.id)},
            default_options={"max_tokens": 77},
            max_output_tokens=12,
        )
    )
    assert compiled.runnable.client.extra_body["max_tokens"] == 12


def test_generation_options_ignores_empty_values():
    assert generation_options({}) == {}
    # explicit zeros are values (temperature 0 is meaningful), only None is "unset"
    assert generation_options({"default_options": {"temperature": None, "max_tokens": 0}}) == {
        "max_tokens": 0
    }
    assert generation_options({"max_output_tokens": 0}) == {}
    assert generation_options({"default_options": {"max_tokens": 9}, "max_output_tokens": 5}) == {
        "max_tokens": 5
    }


def test_generation_options_refuse_app_owned_request_keys():
    """default_options share the connection's contract: no model/messages/stream."""
    from src.utils.llm_connection_manager import LLMConnectionError

    for key in ("model", "messages", "stream", "extra_body"):
        with pytest.raises(LLMConnectionError) as exc:
            generation_options({"default_options": {key: "x"}})
        assert key in str(exc.value)

    # ...and the studio/API surface reports it before the definition is saved.
    report = validate_definition(
        {
            "kind": "agent",
            "name": "Owned Keys",
            "config": {
                "instructions": "x",
                "model": {"client": "default"},
                "default_options": {"messages": [{"role": "user", "content": "hi"}]},
            },
        }
    )
    assert not report["ok"]
    assert [e["code"] for e in report["errors"]] == ["bad_default_options"]
    assert "messages" in report["errors"][0]["message"]

    workflow = validate_definition(
        {
            "kind": "workflow",
            "name": "Wf",
            "config": {
                "kind": "workflow",
                "pattern": "graph",
                "participants": [
                    {"name": "A", "instructions": "a", "default_options": {"stream": True}}
                ],
            },
        }
    )
    assert not workflow["ok"]
    assert "stream" in workflow["errors"][0]["message"]


def test_llm_manager_plugin_and_selector_agree(connections):
    """The inspector's model plugin must not re-implement precedence."""
    register_builtin_plugins()
    from src.agent_platform.plugins.registry import get_registry

    pinned, _ = connections
    plugin = get_registry().get("llm_connection")
    run_client = ScriptedChatClient(responses=["run-level"])

    direct = compile_model_client({"client": str(pinned.id)}, None)
    via_plugin = plugin.compile({"client": str(pinned.id)}, None)
    assert via_plugin.model_name == direct.model_name == "pinned-model"
    assert via_plugin.extra_body == direct.extra_body
    assert plugin.compile({"client": "default"}, type("Ctx", (), {"client": run_client})()) is run_client


def test_validation_rejects_unusable_pinned_connections(connections):
    pinned, _ = connections
    mgr.save_connection({**mgr._to_dict(pinned), "id": pinned.id, "enabled": False})

    unknown = validate_definition(_agent("Bad", model={"client": "no-such-connection"}))
    assert not unknown["ok"]
    assert [e["code"] for e in unknown["errors"]] == ["bad_model_connection"]
    assert "Unknown LLM connection" in unknown["errors"][0]["message"]

    disabled = validate_definition(_agent("Off", model={"client": str(pinned.id)}))
    assert not disabled["ok"]
    assert disabled["errors"][0]["code"] == "bad_model_connection"
    assert "disabled" in disabled["errors"][0]["message"]

    # "default" follows the operator's choice, so it is never a stale pin.
    assert validate_definition(_agent("Ok", model={"client": "default"}))["ok"]


def test_validation_accepts_both_context_window_spellings():
    camel = validate_definition(_agent("Camel", maxContextWindowTokens=0))
    assert [e["code"] for e in camel["errors"]] == ["bad_max_context_window_tokens"]
    assert "maxContextWindowTokens" in camel["errors"][0]["message"]

    snake = validate_definition(_agent("Snake", max_context_window_tokens=-5))
    assert [e["code"] for e in snake["errors"]] == ["bad_max_context_window_tokens"]
    assert "max_context_window_tokens" in snake["errors"][0]["message"]

    assert validate_definition(_agent("Good", maxContextWindowTokens=32000, max_output_tokens=800))["ok"]
    assert not validate_definition(_agent("Zero", max_output_tokens=0))["ok"]
    assert not validate_definition(
        _agent("Inverted", maxContextWindowTokens=500, max_output_tokens=900)
    )["ok"]
