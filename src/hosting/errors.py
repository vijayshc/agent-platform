"""Errors raised by the hosting package."""

from __future__ import annotations


class HostingError(RuntimeError):
    """An install or lifecycle action could not be completed."""
