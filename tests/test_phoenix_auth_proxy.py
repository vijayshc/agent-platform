from __future__ import annotations

import json
import pytest

from app import app
from src.agent_platform.api.phoenix_proxy import _rewrite_phoenix_html


@pytest.fixture
def auth_client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "admin"
            sess["role"] = "admin"
        yield client


@pytest.fixture
def unauth_client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client


def test_phoenix_html_rewrite():
    sample_html = """
    <!DOCTYPE html>
    <html>
      <head>
        <link rel="icon" href="/favicon.ico">
        <link rel="stylesheet" href="/assets/index.css">
        <script src="/modernizr.js"></script>
      </head>
      <body>
        <script>
          window.Config = { basename: "", platformVersion: "20.4.0" };
        </script>
        <script type="module" src="/assets/index.js"></script>
      </body>
    </html>
    """
    rewritten = _rewrite_phoenix_html(sample_html, "/api/v1/phoenix/proxy")
    assert '<base href="/api/v1/phoenix/proxy/">' in rewritten
    assert 'href="/api/v1/phoenix/proxy/assets/index.css"' in rewritten
    assert 'src="/api/v1/phoenix/proxy/modernizr.js"' in rewritten
    assert 'href="/api/v1/phoenix/proxy/favicon.ico"' in rewritten
    assert 'basename: "/api/v1/phoenix/proxy"' in rewritten
    assert 'src="/api/v1/phoenix/proxy/assets/index.js"' in rewritten
    assert "data-text2sql-phoenix-theme" in rewritten
    assert "arize-phoenix-theme" in rewritten
    assert "selectedTheme" in rewritten
    # Idempotent: a second rewrite must not duplicate the theme-sync script.
    twice = _rewrite_phoenix_html(rewritten, "/api/v1/phoenix/proxy")
    assert twice.count("data-text2sql-phoenix-theme") == 1


def test_phoenix_proxy_unauthenticated_blocked(unauth_client):
    resp = unauth_client.get("/api/v1/phoenix/proxy/")
    assert resp.status_code in (401, 302)


def test_phoenix_proxy_authenticated_page(auth_client):
    resp = auth_client.get("/api/v1/phoenix/proxy/")
    assert resp.status_code == 200
    assert "Phoenix" in resp.text or "<!DOCTYPE html>" in resp.text
    assert "/api/v1/phoenix/proxy" in resp.text


def test_phoenix_proxy_graphql_authenticated(auth_client):
    query = {"query": "{ projects { edges { node { name id traceCount } } } }"}
    resp = auth_client.post(
        "/api/v1/phoenix/proxy/graphql",
        data=json.dumps(query),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "data" in data
    assert "projects" in data["data"]


def test_phoenix_proxy_static_asset(auth_client):
    resp = auth_client.get("/api/v1/phoenix/proxy/modernizr.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("Content-Type", "")


def test_direct_phoenix_port_access_redirects():
    import requests
    from src.services.phoenix_service import get_phoenix_url
    url = get_phoenix_url()
    resp = requests.get(f"{url}/projects", allow_redirects=False)
    assert resp.status_code == 307
    assert "5000/agent-runs" in resp.headers.get("Location", "")


def test_direct_phoenix_port_graphql_blocked():
    import requests
    from src.services.phoenix_service import get_phoenix_url
    url = get_phoenix_url()
    resp = requests.post(f"{url}/graphql", json={"query": "{ projects { edges { node { name } } } }"}, allow_redirects=False)
    assert resp.status_code == 401


