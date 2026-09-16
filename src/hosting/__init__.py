"""Hosted apps: import an externally-built Flask app, run it confined, serve it.

The public entry points are:

``capabilities``  probe what isolation this host can provide, and pick a tier
``settings``      environment-driven configuration (``.env``)
``supervisor``    install, start, stop and observe hosted apps
``confine``       the seccomp / Landlock / rlimit layers applied to an app
``launcher``      the process that applies a tier and becomes the app

An app is served under ``/apps/<slug>/`` by the parent application; it never
listens on a port of its own. See ``docs/hosted-apps-isolation.md`` for why each
mechanism was chosen and what it cannot do.
"""

from __future__ import annotations

__all__ = ["capabilities", "confine", "enter", "launcher", "settings", "supervisor"]
