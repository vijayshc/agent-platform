"""Sample content for the agent platform: demo agents, bundled MCP tool
servers, example skills, and the fixtures they use.

Nothing in ``src/agent_platform`` imports this package, and the app boots
without it. Content here is registered into a database by
``scripts/feed_samples.py``; the HTTP tool servers are served by
``scripts/mcp_http_service.py``.
"""

from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent
