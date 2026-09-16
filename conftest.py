"""Pytest root hook.

The agent/LLM tests always run against the live app started by ``start.sh``;
there is no offline switch to turn them off.
"""

from __future__ import annotations

import os

os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
