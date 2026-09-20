"""Point Phoenix at this checkout instead of the user's home directory.

``phoenix`` resolves its working directory exactly once — when the module is
first imported — from ``PHOENIX_WORKING_DIR``, and otherwise creates
``~/.phoenix``. That means *whichever module happens to import ``phoenix``
first* decides where the trace database lives, which is not something an import
order should decide: the app kept traces in the checkout while a pytest plugin
importing ``phoenix`` at startup tried to create ``~/.phoenix``.

Every entry point (``app.py`` and the pytest root ``conftest.py``) calls
:func:`ensure_phoenix_workdir` before anything can import ``phoenix``, so the
trace database always lives next to the code that produced it.
"""

from __future__ import annotations

import os
from pathlib import Path

#: ``src/phoenix_env.py`` -> ``src`` -> repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Default home of the Phoenix trace database: inside the checkout.
DEFAULT_WORKING_DIR = PROJECT_ROOT / ".phoenix"


def ensure_phoenix_workdir() -> str:
    """Default ``PHOENIX_WORKING_DIR`` to the checkout; respect an explicit one."""
    return os.environ.setdefault("PHOENIX_WORKING_DIR", str(DEFAULT_WORKING_DIR))
