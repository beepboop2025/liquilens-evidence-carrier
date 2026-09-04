"""Shared HTTP client invariants for fixed financial-data dependencies."""

from __future__ import annotations

from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from typing import Any


class _RejectAllCookiePolicy(DefaultCookiePolicy):
    """Reject response cookies and forbid sending any stored cookie."""

    def set_ok(self, cookie: Cookie, request: Any) -> bool:
        return False

    def return_ok(self, cookie: Cookie, request: Any) -> bool:
        return False

    def domain_return_ok(self, domain: str, request: Any) -> bool:
        return False

    def path_return_ok(self, path: str, request: Any) -> bool:
        return False


def cookie_free_jar() -> CookieJar:
    """Return a fresh jar that cannot persist cross-request identity state."""

    return CookieJar(policy=_RejectAllCookiePolicy())


__all__ = ["cookie_free_jar"]
