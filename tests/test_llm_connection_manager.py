"""Tests for the admin-configurable LLM connection manager."""

from __future__ import annotations

import json

import pytest

from src.models.secrets import MASK, SecretString
from src.models.llm_connection import LLMConnection
import src.models.llm_connection as llm_connection_model
import src.utils.llm_connection_manager as mgr


def test_create_table_is_idempotent(temp_db):
    LLMConnection.create_table()
    LLMConnection.create_table()  # second call must not error


def test_crud_save_get_delete(temp_db):
    conn = mgr.save_connection(
        {
            "name": "Acme",
            "base_url": "https://api.acme.example/v1",
            "api_key": "sk-test-123",
            "model_name": "acme-mini",
            "system_instruction": "Be helpful.",
            "extra_body": {"temperature": 0.3, "max_tokens": 512, "top_p": 0.9, "frequency_penalty": 0.2},
            "http_headers": {"X-Tenant": "acme"},
            "verify_ssl": False,
            "enabled": True,
            "is_default": True,
        }
    )
    assert conn.id is not None

    fetched = LLMConnection.get_by_id(conn.id)
    assert fetched is not None
    assert fetched.name == "Acme"
    assert fetched.api_key == "sk-test-123"
    assert fetched.extra_body.get("top_p") == 0.9
    assert fetched.http_headers.get("X-Tenant") == "acme"
    assert fetched.verify_ssl == 0
    assert fetched.is_default == 1
    assert fetched.enabled == 1

    listed = {c["id"]: c for c in mgr.list_connections()}
    assert listed[conn.id]["name"] == "Acme"
    assert listed[conn.id]["api_key_masked"] is True
    assert listed[conn.id]["api_key"] == "********"
    assert listed[conn.id]["verify_ssl"] is False
    assert listed[conn.id]["extra_body"] == {
        "temperature": 0.3,
        "max_tokens": 512,
        "top_p": 0.9,
        "frequency_penalty": 0.2,
    }

    assert mgr.delete_connection(conn.id) is True
    assert LLMConnection.get_by_id(conn.id) is None


def test_verify_ssl_defaults_on_and_roundtrips(temp_db):
    conn = mgr.save_connection(
        {"name": "Tls", "base_url": "https://tls", "api_key": "k", "model_name": "m"}
    )
    assert conn.verify_ssl == 1

    # An edit that leaves the checkbox alone must keep it enabled.
    mgr.save_connection({"id": conn.id, "name": "Tls", "base_url": "https://tls", "model_name": "m"})
    assert LLMConnection.get_by_id(conn.id).verify_ssl == 1

    mgr.save_connection(
        {"id": conn.id, "name": "Tls", "base_url": "https://tls", "model_name": "m", "verify_ssl": False}
    )
    assert LLMConnection.get_by_id(conn.id).verify_ssl == 0


def test_save_rejects_malformed_extra_body(temp_db):
    import pytest

    with pytest.raises(ValueError):
        mgr.save_connection(
            {"name": "Bad", "base_url": "https://b", "model_name": "m", "extra_body": "not-a-dict"}
        )
    with pytest.raises(ValueError):
        mgr.save_connection(
            {"name": "Bad", "base_url": "https://b", "model_name": "m", "extra_body": "{oops"}
        )


def test_save_rejects_app_owned_body_keys(temp_db):
    """model/messages/stream/extra_body are set per call, not by a connection."""
    import pytest

    for key in ("model", "messages", "stream", "extra_body"):
        with pytest.raises(ValueError, match=key):
            mgr.save_connection(
                {
                    "name": f"Bad-{key}",
                    "base_url": "https://b",
                    "model_name": "m",
                    "extra_body": {key: "x"},
                }
            )


def test_legacy_columns_are_folded_into_extra_body(temp_db):
    """A pre-extra_body database keeps its provider arguments after migration."""
    # Attribute lookup (not a module-level import) so the fixture's patched
    # connection is the one used: the legacy schema must be built in the temp DB.
    conn = llm_connection_model.get_db_connection()
    conn.execute(
        """
        CREATE TABLE llm_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            base_url TEXT,
            api_key TEXT,
            model_name TEXT,
            temperature REAL,
            max_tokens INTEGER,
            top_k INTEGER,
            system_instruction TEXT,
            extra_options TEXT,
            http_headers TEXT,
            is_default INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO llm_connections (name, model_name, temperature, max_tokens, top_k, extra_options) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "Legacy",
            "legacy-model",
            0.2,
            8000,
            40,
            json.dumps({"top_p": 0.7, "custom": {"nested": True}}),
        ),
    )
    conn.execute(
        "INSERT INTO llm_connections (name, model_name, temperature) VALUES (?, ?, ?)",
        ("Legacy-Unset", "legacy-model", None),
    )
    conn.commit()
    conn.close()

    loaded = LLMConnection.get_by_name("Legacy")
    assert loaded is not None
    assert loaded.extra_body["temperature"] == 0.2
    assert loaded.extra_body["max_tokens"] == 8000
    assert loaded.extra_body["top_k"] == 40
    assert loaded.extra_body["top_p"] == 0.7
    assert loaded.extra_body["custom"] == {"nested": True}
    assert loaded.verify_ssl == 1

    # The fold is one-time and idempotent: an operator who clears the parameters
    # must not get the legacy values resurrected on the next read.
    mgr.save_connection({**loaded.config_dict_masked(), "extra_body": {}})
    assert LLMConnection.get_by_id(loaded.id).extra_body == {}
    assert LLMConnection.get_by_id(loaded.id).extra_body == {}

    # A connection with no legacy values keeps an empty parameter object.
    unset = LLMConnection.get_by_name("Legacy-Unset")
    assert unset.extra_body == {}


def test_save_roundtrip_keeps_masked_http_headers(temp_db):
    """Saving a listed payload must restore MASK headers, not persist the mask."""
    secret_key = "sk-roundtrip-secret"
    secret_auth = "Bearer SECRET-roundtrip"
    conn = mgr.save_connection(
        {
            "name": "Roundtrip",
            "base_url": "https://rt.example/v1",
            "api_key": secret_key,
            "model_name": "m",
            "http_headers": {"Authorization": secret_auth, "X-Tenant": "acme"},
        }
    )

    listed = mgr.list_connections()
    dumped = json.dumps(listed)
    assert secret_key not in dumped
    assert "SECRET-roundtrip" not in dumped
    payload = next(item for item in listed if item["id"] == conn.id)
    assert payload["http_headers"]["Authorization"] == MASK
    assert payload["api_key"] == MASK

    payload["http_headers"] = dict(payload["http_headers"])
    payload["http_headers"]["X-Tenant"] = "other"
    mgr.save_connection(payload)

    loaded = LLMConnection.get_by_id(conn.id)
    assert loaded is not None
    assert loaded.http_headers["Authorization"] == secret_auth
    assert isinstance(loaded.http_headers["Authorization"], SecretString)
    assert loaded.http_headers["X-Tenant"] == "other"
    assert loaded.api_key == secret_key
    assert isinstance(loaded.api_key, SecretString)


def test_set_default_only_one(temp_db):
    a = mgr.save_connection({"name": "A", "base_url": "https://a", "api_key": "k", "model_name": "m", "is_default": True})
    b = mgr.save_connection({"name": "B", "base_url": "https://b", "api_key": "k", "model_name": "m"})
    mgr.set_default(b.id)

    conns = LLMConnection.get_all()
    defaults = [c for c in conns if c.is_default]
    assert len(defaults) == 1
    assert defaults[0].id == b.id
    # A still exists but is no longer default
    assert LLMConnection.get_by_id(a.id).is_default == 0


def test_resolve_by_id_name_and_default(temp_db):
    mgr.save_connection(
        {"name": "Primary", "base_url": "https://p", "api_key": "k", "model_name": "m", "is_default": True}
    )
    mgr.save_connection({"name": "Backup", "base_url": "https://b", "api_key": "k", "model_name": "m"})

    by_name = mgr.get_connection("Backup")
    assert by_name.name == "Backup"

    by_slug = mgr.get_connection("primary")  # slugified name also matches
    assert by_slug.name == "Primary"

    # "default" maps to the default connection
    assert mgr.resolve("default").name == "Primary"
    assert mgr.resolve().name == "Primary"
    assert mgr.resolve("").name == "Primary"

    # resolving a specific id
    backup = mgr.get_connection("Backup")
    assert mgr.resolve(backup.id).name == "Backup"


def test_resolve_rejects_unknown_and_disabled_connections(temp_db):
    """An explicitly referenced connection is never silently swapped for another."""
    primary = mgr.save_connection(
        {"name": "Primary", "base_url": "https://p", "api_key": "k", "model_name": "m", "is_default": True}
    )
    mgr.save_connection(
        {"name": "Off", "base_url": "https://o", "api_key": "k", "model_name": "m", "enabled": False}
    )

    with pytest.raises(mgr.LLMConnectionError) as unknown:
        mgr.resolve("definitely-not-a-connection")
    assert "Unknown LLM connection" in str(unknown.value)
    assert "definitely-not-a-connection" in str(unknown.value)

    with pytest.raises(mgr.LLMConnectionError) as disabled:
        mgr.resolve("Off")
    assert "disabled" in str(disabled.value)

    # The default itself is still usable.
    assert mgr.resolve("default").id == primary.id


def test_disabled_default_connection_is_reported(temp_db):
    """Disabling the default is a configuration error, not "no connection"."""
    conn = mgr.save_connection(
        {"name": "OnlyOne", "base_url": "https://p", "api_key": "k", "model_name": "m", "is_default": True}
    )
    payload = {**mgr._to_dict(conn), "id": conn.id, "enabled": False}
    mgr.save_connection(payload)

    assert LLMConnection.get_by_id(conn.id).enabled == 0
    with pytest.raises(mgr.LLMConnectionError) as exc:
        mgr.get_default()
    assert "default LLM connection 'OnlyOne' is disabled" in str(exc.value)
    with pytest.raises(mgr.LLMConnectionError):
        mgr.resolve("default")


def test_partial_save_keeps_flags_it_does_not_mention(temp_db):
    """A payload without `enabled`/`is_default` must not flip them."""
    conn = mgr.save_connection(
        {"name": "Quiet", "base_url": "https://q", "api_key": "k", "model_name": "m", "enabled": True}
    )
    mgr.save_connection({**mgr._to_dict(conn), "id": conn.id, "enabled": False})
    # Integration update: rename only.
    mgr.save_connection({"id": conn.id, "name": "Quiet 2", "base_url": "https://q", "model_name": "m2"})

    reloaded = LLMConnection.get_by_id(conn.id)
    assert reloaded.name == "Quiet 2"
    assert reloaded.enabled == 0, "a partial save silently re-enabled a disabled connection"

    mgr.set_default(conn.id)
    mgr.save_connection({"id": conn.id, "name": "Quiet 3", "base_url": "https://q", "model_name": "m2"})
    assert LLMConnection.get_by_id(conn.id).is_default == 1, "a partial save dropped the default flag"


def test_apply_model_options_overrides_connection_parameters(temp_db):
    """Agent options win key by key; the operator's other parameters survive."""
    conn = mgr.save_connection(
        {
            "name": "Merge",
            "base_url": "https://merge.example/v1",
            "api_key": "k",
            "model_name": "m",
            "extra_body": {"temperature": 0.9, "max_tokens": 4321, "top_k": 20},
        }
    )
    base = mgr.build_client(conn)
    derived = mgr.apply_model_options(base, {"temperature": 0.05, "max_tokens": 77, "top_p": None})

    assert derived is not base
    assert derived.extra_body == {"temperature": 0.05, "max_tokens": 77, "top_k": 20}
    assert base.extra_body == {"temperature": 0.9, "max_tokens": 4321, "top_k": 20}
    # Same connection: the derived client shares the underlying transport.
    assert derived.root_client is base.root_client
    payload = derived._get_request_payload([{"role": "user", "content": "hi"}])
    assert payload["extra_body"] == {"temperature": 0.05, "max_tokens": 77, "top_k": 20}

    # Nothing to override is a no-op, and non-OpenAI clients are left alone.
    assert mgr.apply_model_options(base, {}) is base
    assert mgr.apply_model_options(object(), {"temperature": 1}) is not None


def test_verify_ssl_off_uses_the_sdk_transport(temp_db):
    """The TLS override drives the same HTTP library the OpenAI SDK drives."""
    import sys

    from openai import DefaultHttpxClient

    conn = mgr.save_connection(
        {
            "name": "Tls",
            "base_url": "https://tls.example/v1",
            "api_key": "k",
            "model_name": "m",
            "verify_ssl": False,
        }
    )
    transport = sys.modules[DefaultHttpxClient.__mro__[1].__module__.split(".")[0]]
    client = mgr.build_openai_client(conn)
    assert isinstance(client._client, transport.Client)
    assert client._client._transport._pool._ssl_context.check_hostname is False

    lc = mgr.build_client(conn)
    assert isinstance(lc.http_client, transport.Client)
    assert isinstance(lc.http_async_client, transport.AsyncClient)


def test_options_forward_model_parameters_verbatim(temp_db):
    """Every operator argument reaches the request body, nesting included."""
    extra_body = {
        "temperature": 1.0,
        "max_tokens": 81920,
        "top_p": 0.95,
        "presence_penalty": 0.0,
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
        "provider": {"order": ["deepseek"], "allow_fallbacks": False},
    }
    conn = mgr.save_connection(
        {
            "name": "Options",
            "base_url": "https://o",
            "api_key": "k",
            "model_name": "m",
            "extra_body": extra_body,
        }
    )
    opts = mgr.options(conn)
    # The free-form object is forwarded untouched: no key count limit, no
    # top-level/extra_body reshuffling, nested dicts intact.
    assert opts == {"extra_body": extra_body}
    assert opts["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_options_without_model_parameters_is_empty(temp_db):
    conn = mgr.save_connection(
        {"name": "Plain", "base_url": "https://p", "api_key": "k", "model_name": "m"}
    )
    assert mgr.options(conn) == {}
    assert mgr.options(conn) == {}


def test_build_openai_client_applies_headers_extra_body_and_tls(temp_db):
    """The provider client carries the connection's headers, extra_body and TLS mode."""
    conn = mgr.save_connection(
        {
            "name": "Wire",
            "base_url": "https://wire.example/v1",
            "api_key": "sk-wire",
            "model_name": "wire-model",
            "extra_body": {"top_k": 5, "chat_template_kwargs": {"enable_thinking": False}},
            "http_headers": {"X-Tenant": "acme", "Authorization": "Bearer tok-wire"},
            "verify_ssl": False,
        }
    )
    client = mgr.build_openai_client(conn, model_name="wire-model")
    assert client.default_headers["X-Tenant"] == "acme"
    assert client.default_headers["Authorization"] == "Bearer tok-wire"
    assert client.default_headers["Accept-Encoding"] == "gzip, deflate"
    assert client._llm_defaults["extra_body"] == {
        "top_k": 5,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # Verification off => explicit httpx clients with TLS verification disabled.
    assert client._client._transport._pool._ssl_context.check_hostname is False


def test_build_client_langchain_carries_extra_body_and_headers(temp_db):
    conn = mgr.save_connection(
        {
            "name": "LcWire",
            "base_url": "https://lc.example/v1",
            "api_key": "sk-lc",
            "model_name": "lc-model",
            "extra_body": {"top_k": 7, "chat_template_kwargs": {"enable_thinking": False}},
            "http_headers": {"X-Tenant": "acme"},
        }
    )
    client = mgr.build_client(conn)
    from langchain_openai import ChatOpenAI

    assert isinstance(client, ChatOpenAI)
    assert client.extra_body == {"top_k": 7, "chat_template_kwargs": {"enable_thinking": False}}
    assert client.default_headers["X-Tenant"] == "acme"
    # Parameters are part of every request the agent graph makes, not just metadata.
    payload = client._get_request_payload([{"role": "user", "content": "hi"}])
    assert payload["extra_body"] == {"top_k": 7, "chat_template_kwargs": {"enable_thinking": False}}
    assert client.http_client is None  # verification stays on unless disabled


def test_build_client_keeps_verification_on_by_default(temp_db):
    conn = mgr.save_connection(
        {"name": "SecureLc", "base_url": "https://s.example/v1", "api_key": "k", "model_name": "m"}
    )
    client = mgr.build_client(conn)
    assert client.http_client is None
    assert client.http_async_client is None
    assert "extra_body" not in client._get_request_payload([{"role": "user", "content": "hi"}])


def test_build_client_returns_langchain_client(temp_db):
    conn = mgr.save_connection(
        {
            "name": "Client",
            "api_key": "k",
            "model_name": "m",
        }
    )
    client = mgr.build_client(conn, model_name="override-model")
    from langchain_openai import ChatOpenAI

    assert isinstance(client, ChatOpenAI)
    assert getattr(client, "_llm_defaults", None) is not None
    assert getattr(client, "_llm_system_instruction", None) is None

def test_test_connection_validation(temp_db):
    result = mgr.test_connection({"name": "X", "api_key": "k", "base_url": "https://x"})
    assert result["ok"] is False
    assert "model" in result["error"].lower()

    # Missing api key should fail cleanly.
    result = mgr.test_connection({"name": "X", "model_name": "m", "base_url": "https://x"})
    assert result["ok"] is False
    assert "api key" in result["error"].lower()


def _closed_port() -> int:
    """A local port nothing listens on: the connection is refused for real."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_test_connection_reports_why_the_endpoint_is_unreachable(temp_db):
    """A dead endpoint must report the real transport cause, not "Connection error."."""
    base_url = f"http://127.0.0.1:{_closed_port()}/v1"
    result = mgr.test_connection(
        {"name": "Dead", "base_url": base_url, "api_key": "sk-test", "model_name": "gpt-x"}
    )

    assert result["ok"] is False
    assert result["endpoint"] == base_url
    assert result["model"] == "gpt-x"
    assert base_url in result["error"]
    assert result["error"] != "Connection error."
    assert "refused" in result["error"].lower()


def test_test_connection_reports_an_unknown_host(temp_db):
    """DNS failures name the host the app could not resolve."""
    base_url = "https://llm-manager-does-not-exist.invalid/v1"
    result = mgr.test_connection(
        {"name": "Nx", "base_url": base_url, "api_key": "sk-test", "model_name": "gpt-x"}
    )

    assert result["ok"] is False
    assert base_url in result["error"]
    assert result["error"] != "Connection error."


@pytest.fixture()
def broken_provider():
    """A real HTTP endpoint that answers like a provider which is not an LLM API.

    Nothing about the app is mocked: the test drives real sockets so the
    manager's own request/response handling is what is being exercised.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    completion = {
        "id": "chatcmpl-broken",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "pong"},
                "finish_reason": "stop",
            }
        ],
    }
    empty = json.loads(json.dumps(completion))
    empty["choices"][0]["message"]["content"] = ""
    empty["choices"][0]["finish_reason"] = "length"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            self.rfile.read(int(self.headers.get("content-length") or 0))
            mode = self.path.split("/v1/")[0].strip("/")
            if mode == "echo":
                body, content_type = json.dumps({"args": {}, "json": {"model": "stub"}}), "application/json"
            elif mode == "empty":
                body, content_type = json.dumps(empty), "application/json"
            elif mode == "html":
                body, content_type = "<html><title>Gateway</title><body>nope</body></html>", "text/html"
            elif mode == "leak":
                quoted = self.headers.get("Authorization") or ""
                body = json.dumps({"error": {"message": f"upstream rejected {quoted}"}})
                content_type = "application/json"
            else:
                body, content_type = json.dumps(completion), "application/json"
            payload = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):  # keep the test output clean
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_test_connection_rejects_a_200_that_is_not_a_chat_completion(temp_db, broken_provider):
    """HTTP 200 with an unrelated JSON body is a failure, not "(empty response)"."""
    result = mgr.test_connection(
        {
            "name": "Echo",
            "base_url": f"{broken_provider}/echo/v1",
            "api_key": "sk-test",
            "model_name": "stub-model",
        }
    )

    assert result["ok"] is False
    assert "without a chat completion" in result["error"]


def test_test_connection_rejects_an_empty_assistant_message(temp_db, broken_provider):
    """A completion whose assistant text is empty cannot be called a success."""
    result = mgr.test_connection(
        {
            "name": "Empty",
            "base_url": f"{broken_provider}/empty/v1",
            "api_key": "sk-test",
            "model_name": "stub-model",
        }
    )

    assert result["ok"] is False
    assert "returned no text" in result["error"]
    assert "length" in result["error"]


def test_test_connection_never_echoes_the_credential(temp_db, broken_provider):
    """A provider that quotes the request back must not leak the API key."""
    result = mgr.test_connection(
        {
            "name": "Leaky",
            "base_url": f"{broken_provider}/leak/v1",
            "api_key": "sk-live-secret",
            "http_headers": {"X-Tenant": "acme-secret-tenant"},
            "model_name": "stub-model",
        }
    )

    assert result["ok"] is False
    assert "sk-live-secret" not in result["error"]
    assert "acme-secret-tenant" not in result["error"]
    assert MASK in result["error"]


def test_test_connection_accepts_a_real_reply(temp_db, broken_provider):
    """A completion that does carry assistant text is reported as success."""
    result = mgr.test_connection(
        {
            "name": "Ok",
            "base_url": f"{broken_provider}/ok/v1",
            "api_key": "sk-test",
            "model_name": "stub-model",
        }
    )

    assert result["ok"] is True
    assert result["reply"] == "pong"
    assert result["endpoint"] == f"{broken_provider}/ok/v1"
    assert result["model"] == "stub-model"


def test_seed_default_from_config(monkeypatch, temp_db):
    import config.config as cfg

    monkeypatch.setattr(cfg, "OPENROUTER_BASE_URL", "https://seed.example/v1")
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "sk-seed")
    monkeypatch.setattr(cfg, "OPENROUTER_MODEL", "seed-model")
    monkeypatch.setattr(cfg, "TEMPERATURE", 0.5)
    monkeypatch.setattr(cfg, "MAX_TOKENS", 999)

    assert LLMConnection.get_default() is None
    mgr.seed_default_from_config()
    conn = LLMConnection.get_default()
    assert conn is not None
    assert conn.name == "Default"
    assert conn.base_url == "https://seed.example/v1"
    assert conn.api_key == "sk-seed"
    assert conn.model_name == "seed-model"
    assert conn.extra_body == {"temperature": 0.5, "max_tokens": 999}


def test_llm_engine_uses_llm_connection_manager_default(temp_db):
    from src.utils.llm_engine import LLMEngine

    mgr.save_connection(
        {
            "name": "ProdEngineConn",
            "base_url": "https://llm.example/v1",
            "api_key": "sk-engine-key",
            "model_name": "custom-engine-model",
            "is_default": True,
        }
    )
    engine = LLMEngine()
    assert engine.client is not None
    assert engine.model_name == "custom-engine-model"


def test_llm_engine_no_default_connection_does_not_use_config_key(temp_db, monkeypatch):
    import pytest
    import config.config as cfg
    from src.utils.llm_engine import LLMEngine

    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "should-never-be-used")
    assert LLMConnection.get_default() is None

    engine = LLMEngine()
    assert engine.client is None
    with pytest.raises(RuntimeError, match="No LLM connection configured"):
        engine._ensure_client()


def test_llm_engine_reset_picks_up_new_connection(temp_db):
    from src.utils.common_llm import get_llm_engine, reset_llm_engine

    reset_llm_engine()
    mgr.save_connection(
        {
            "name": "FirstDefault",
            "base_url": "https://first.example/v1",
            "api_key": "sk-first",
            "model_name": "model-one",
            "is_default": True,
        }
    )
    reset_llm_engine()
    engine = get_llm_engine()
    assert engine.client is not None
    assert engine.model_name == "model-one"

    second = mgr.save_connection(
        {
            "name": "SecondDefault",
            "base_url": "https://second.example/v1",
            "api_key": "sk-second",
            "model_name": "model-two",
            "is_default": True,
        }
    )
    reset_llm_engine()
    engine2 = get_llm_engine()
    assert engine2.client is not None
    assert engine2.model_name == "model-two"
    reset_llm_engine()
