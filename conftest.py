"""Pytest root hook.

The agent/LLM tests always run against the live app started by ``start.sh``;
there is no offline switch to turn them off.
"""

from __future__ import annotations

import os

# Before any plugin is imported: a ``pytest11`` entry point pulls in ``phoenix``,
# which would otherwise create its trace database in ``~/.phoenix``. Initial
# conftests load ahead of plugin autoload, so this wins the race.
from src.phoenix_env import ensure_phoenix_workdir

ensure_phoenix_workdir()

os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
