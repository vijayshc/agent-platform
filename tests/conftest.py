from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

# Modules that bind ``get_db_connection`` at *import* time must be imported here,
# before any test patches ``src.utils.database.get_db_connection``.  If such a
# module is first imported while ``temp_db`` is active, it binds the temp
# ``_connect``; ``monkeypatch`` then records that temp function as the "original"
# and restores it at teardown, permanently pointing the module at a torn-down
# temp database and breaking every later test in the process.  Importing them up
# front makes the bound name the real helper, so patching always rebinds and
# restores it deterministically.
import src.agent_platform.catalog.skills_store  # noqa: E402,F401
import src.agent_platform.catalog.store  # noqa: E402,F401
import src.models.agent_team  # noqa: E402,F401
import src.models.agent_workflow  # noqa: E402,F401
import src.models.hosted_app  # noqa: E402,F401
import src.models.llm_connection  # noqa: E402,F401
import src.models.mcp_server  # noqa: E402,F401
import src.models.skill  # noqa: E402,F401
import src.routes.admin_db_routes  # noqa: E402,F401
import src.utils.dashboard_analytics  # noqa: E402,F401
import src.utils.knowledge_access  # noqa: E402,F401
import src.utils.knowledge_manager  # noqa: E402,F401


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "platform.db"

    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("src.agent_platform.db.get_db_connection", _connect)
    monkeypatch.setattr("src.utils.database.get_db_connection", _connect)
    monkeypatch.setattr("src.models.mcp_server.get_db_connection", _connect)
    monkeypatch.setattr("src.models.llm_connection.get_db_connection", _connect)
    # KnowledgeManager imported the helper by name at import time, so patching
    # src.utils.database alone does not redirect it; without this its writes
    # (document rows, chunk metadata) would land in the real text2sql.db.
    monkeypatch.setattr("src.utils.knowledge_manager.get_db_connection", _connect)

    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.agent_platform.paths.uploads_dir", lambda: uploads)

    # The stores cache a one-time DDL guard; reset it so the fresh DB file gets
    # its schema (and re-created tables) for each test.
    from src.agent_platform.execution.run_store import RunStore
    from src.agent_platform.conversations.store import ConversationStore

    RunStore._reset_schema_guard()
    ConversationStore._reset_schema_guard()

    # SpanSink keeps a per-run in-memory buffer (traces now live in Phoenix);
    # clear it so run ids reused by a fresh DB do not replay a prior test.
    from src.agent_platform.execution.span_sink import SpanSink

    SpanSink._reset()

    return db_path


@pytest.fixture
def app_client(temp_db):
    """Flask test client on the shipped /api/v1 blueprint, isolated temp DB."""
    from flask import Flask

    from src.agent_platform.api.blueprint import create_blueprint
    from src.agent_platform.execution.api_keys import ApiKeyStore
    from src.agent_platform.plugins import register_builtin_plugins

    register_builtin_plugins()
    ApiKeyStore.ensure_tables()
    key = ApiKeyStore.create(
        user_id=1,
        name="test-key",
        scopes=["agents:read", "agents:write", "runs:read", "runs:write"],
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(create_blueprint())
    client = app.test_client()
    client.environ_base["HTTP_X_API_KEY"] = key["key"]
    return client


@pytest.fixture(scope="module")
def api():
    """Module-scoped live client; always DELETE-s what it created, including on failure."""
    from livehelpers import require_live_app

    client = require_live_app()
    try:
        yield client
    finally:
        client.teardown()
