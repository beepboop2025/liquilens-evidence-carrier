"""Configured ASGI singleton for Uvicorn and container entry points.

Keeping this construction outside :mod:`trade_safety_gateway.app` lets library
callers import the application factory without opening network clients,
telemetry files, or the x402 settlement journal.
"""

from __future__ import annotations

from .app import create_app

app = create_app()

__all__ = ["app"]
