#!/usr/bin/env python3
"""Recreate the local demo database from scratch (destructive, dev convenience).

    python scripts/demo_database.py                 # text2sql.db
    python scripts/demo_database.py --db temp/x.db

Deletes the target sqlite file (refusing while it is in use), creates the demo
business tables plus the full platform schema, the admin user, and the default
LLM connection. For an existing database use ``scripts/setup_platform.py`` and
``scripts/feed_samples.py`` instead -- neither of those deletes anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_DB = "text2sql.db"


def _in_use(path: Path) -> str | None:
    for sidecar in (f"{path}-wal", f"{path}-shm"):
        if os.path.exists(sidecar):
            return sidecar
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=os.environ.get("DATABASE_URI", f"sqlite:///{DEFAULT_DB}"))
    args = parser.parse_args(argv)

    db_path = args.db[len("sqlite:///"):] if args.db.startswith("sqlite:///") else args.db
    # Child processes (setup/feed) read DATABASE_URI, so pin it to this file.
    os.environ["DATABASE_URI"] = f"sqlite:///{db_path}"

    path = Path(db_path).resolve()
    busy = _in_use(path)
    if path.exists() and busy:
        print(f"Refusing to reinitialize {path}: {busy} exists (database is in use). Stop the app first.")
        return 1
    if path.exists():
        print(f"Removing {path}")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    from platform_samples.demo_dataset import ensure_demo_dataset

    inserted = ensure_demo_dataset(path)
    print("Demo data: " + ", ".join(f"{name}={n}" for name, n in inserted.items()))

    import setup_platform

    if setup_platform.main([]) != 0:
        return 1

    import feed_samples

    return feed_samples.main(["--no-demo-data"])


if __name__ == "__main__":
    raise SystemExit(main())
