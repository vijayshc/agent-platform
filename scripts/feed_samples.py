#!/usr/bin/env python3
"""Feed optional sample content into the platform: MCP servers, skills, agents.

Run after ``scripts/setup_platform.py``::

    python scripts/feed_samples.py                 # servers, skills, agents, evals, demo data
    python scripts/feed_samples.py --no-demo-data  # skip the demo business tables
    python scripts/feed_samples.py --scripted      # + deterministic agents for UI e2e
    python scripts/feed_samples.py --legacy-import # + import legacy teams/workflows

Everything here is content, not platform code: the app boots and serves without
any of it. Registering the bundled servers and agents is what makes the samples
usable, and every step is idempotent (upsert by name/slug), so re-running this
is safe -- including as a way to refresh moved module paths.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from platform_samples.feed import (  # noqa: E402
    feed_agents,
    feed_demo_data,
    feed_evals,
    feed_legacy,
    feed_mcp_servers,
    feed_scripted,
    feed_skills,
    migrate_mcp_module_paths,
)
from src.agent_platform.paths import SAMPLE_SERVICE_DIR, SAMPLES_DIR  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--http-port", type=int, help="port for the local HTTP MCP server row")
    parser.add_argument("--no-demo-data", action="store_true", help="skip the demo business dataset")
    parser.add_argument(
        "--force", action="store_true", help="overwrite sample agents that already exist"
    )
    parser.add_argument("--scripted", action="store_true", help="also add scripted e2e agents")
    parser.add_argument("--legacy-import", action="store_true", help="also import legacy teams/workflows")
    args = parser.parse_args(argv)

    print(f"Sample content directory: {SAMPLES_DIR}")
    if not SAMPLE_SERVICE_DIR.is_dir():
        print("  warning: sample service fixture missing (the 'sample-service' seed will be empty)")

    print(f"  mcp:    registered {', '.join(feed_mcp_servers(http_port=args.http_port))}")
    moved = migrate_mcp_module_paths()
    if moved:
        print(f"  mcp:    repointed rows that referenced the retired seeds package: {', '.join(moved)}")
    print(f"  skills: registered {', '.join(feed_skills()) or 'none'}")
    written = feed_agents(force=args.force)
    print(f"  agents: wrote {', '.join(written) or 'nothing'}"
          + ("" if args.force else " (existing definitions kept)"))
    print(f"  evals:  {feed_evals(force=args.force)}")
    if not args.no_demo_data:
        print(f"  data:   {feed_demo_data()}")
    if args.scripted:
        print(f"  e2e:    {feed_scripted(force=args.force)}")
    if args.legacy_import:
        print(f"  legacy: {feed_legacy()}")
    print("Samples fed. Start the app, or run `python scripts/mcp_http_service.py --autostart`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
