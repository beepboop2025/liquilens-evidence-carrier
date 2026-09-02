"""CLI entry point for the sandbox HTTP service."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Run the fixed-port service without request-body access logs."""

    uvicorn.run(
        "trade_safety_gateway.app:app",
        host="0.0.0.0",
        port=8080,
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    main()
