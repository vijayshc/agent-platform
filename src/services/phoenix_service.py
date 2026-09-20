from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any

from src.phoenix_env import ensure_phoenix_workdir

logger = logging.getLogger("text2sql.services.phoenix")

# The trace database lives with the repository, not in the user's home
# directory; see ``src/phoenix_env.py`` for why this has to happen before the
# first ``phoenix`` import.
_PHOENIX_WORKING_DIR = ensure_phoenix_workdir()

_PHOENIX_SESSION: Any = None
_PHOENIX_LOCK = threading.Lock()
_DEFAULT_PORT = int(os.environ.get("PHOENIX_PORT", "6006"))
_DEFAULT_HOST = os.environ.get("PHOENIX_HOST", "127.0.0.1")


def get_phoenix_url() -> str:
    port = int(os.environ.get("PHOENIX_PORT", _DEFAULT_PORT))
    host = os.environ.get("PHOENIX_HOST", _DEFAULT_HOST)
    return f"http://{host}:{port}"


def is_phoenix_healthy() -> bool:
    url = get_phoenix_url()
    try:
        req = urllib.request.Request(
            f"{url}/",
            headers={"User-Agent": "HealthCheck", "X-Internal-Phoenix-Auth": "agent_platform_phoenix_secret_2026"},
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status in (200, 307)
    except Exception:
        return False


def start_phoenix_server() -> str:
    global _PHOENIX_SESSION
    with _PHOENIX_LOCK:
        if is_phoenix_healthy():
            logger.info("Phoenix server already healthy at %s", get_phoenix_url())
            return get_phoenix_url()

        try:
            import phoenix as px

            os.environ["PHOENIX_PORT"] = str(_DEFAULT_PORT)
            os.environ["PHOENIX_HOST"] = _DEFAULT_HOST
            _PHOENIX_SESSION = px.launch_app(host=_DEFAULT_HOST, port=_DEFAULT_PORT, use_temp_dir=False)
            logger.info("Launched Arize Phoenix server at %s", get_phoenix_url())
        except Exception as exc:
            logger.warning("Could not launch embedded Phoenix session: %s", exc)

        for _ in range(10):
            if is_phoenix_healthy():
                break
            time.sleep(0.5)

        return get_phoenix_url()


def stop_phoenix_server() -> None:
    global _PHOENIX_SESSION
    with _PHOENIX_LOCK:
        if _PHOENIX_SESSION is not None:
            try:
                import phoenix as px

                px.close_app()
                logger.info("Arize Phoenix server stopped.")
            except Exception as exc:
                logger.warning("Error stopping Phoenix: %s", exc)
            finally:
                _PHOENIX_SESSION = None


def list_phoenix_projects() -> list[dict[str, Any]]:
    url = get_phoenix_url()
    query = """
    {
      projects {
        edges {
          node {
            name
            id
            traceCount
          }
        }
      }
    }
    """
    try:
        req = urllib.request.Request(
            f"{url}/graphql",
            data=json.dumps({"query": query}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AgentPlatform",
                "X-Internal-Phoenix-Auth": "agent_platform_phoenix_secret_2026",
            },
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            edges = data.get("data", {}).get("projects", {}).get("edges", [])
            return [e["node"] for e in edges if e.get("node")]
    except Exception as exc:
        logger.debug("Failed to query Phoenix projects: %s", exc)
        return []
