"""Read-only, execution-neutral Trade Safety Gateway."""

from .app import app, create_app

__version__ = "0.1.0"

__all__ = ["app", "create_app"]
