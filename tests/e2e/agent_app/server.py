"""Flask server for Agent Chat / Runs Playwright tests.

Uses a temp DB, the shipped setup + sample-feed scripts (so the UI tests also
exercise the documented bootstrap path), and deterministic scripted agents.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PORT = int(os.environ.get("AGENT_E2E_PORT", "5055"))
DB_PATH = os.environ.get("AGENT_E2E_DB") or str(Path(tempfile.mkdtemp(prefix="agent-e2e-")) / "e2e.db")
os.environ["DATABASE_URI"] = f"sqlite:///{DB_PATH}"
os.environ["AGENT_PLATFORM_E2E"] = "1"
os.environ["DEBUG"] = "false"
os.environ["FLASK_DEBUG"] = "0"
os.environ.setdefault("AUTH_PROVIDER", "local")

import setup_platform  # noqa: E402

if setup_platform.main([]) != 0:
    raise SystemExit("e2e: platform setup failed")

import feed_samples  # noqa: E402

if feed_samples.main(["--scripted", "--no-demo-data"]) != 0:
    raise SystemExit("e2e: sample feed failed")

from app import app  # noqa: E402

if __name__ == "__main__":
    print(f"E2E_READY db={DB_PATH} port={PORT}", flush=True)
    app.run(host="127.0.0.1", port=PORT, use_reloader=False, threaded=True)
