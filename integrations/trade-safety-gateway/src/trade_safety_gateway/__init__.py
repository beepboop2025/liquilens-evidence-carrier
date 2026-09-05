"""Read-only, execution-neutral Trade Safety Gateway.

Web application construction is lazy so importing an operator or protocol
submodule cannot open network clients, telemetry files, or the x402 journal.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.2.2"

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    """Load the compatibility web exports only when explicitly requested."""

    if name not in __all__:
        raise AttributeError(name)
    if name == "app":
        from .asgi import app as application

        globals()[name] = application
        return application
    from .app import create_app as application_factory

    globals()[name] = application_factory
    return application_factory


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
