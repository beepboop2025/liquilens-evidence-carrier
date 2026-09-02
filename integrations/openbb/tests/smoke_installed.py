"""Assert an installed wheel exposes the bounded OpenBB router entry point."""

from importlib.metadata import entry_points, version

from openbb_core.app.router import Router


def main() -> None:
    matches = [
        item
        for item in entry_points(group="openbb_core_extension")
        if item.name == "liquilens"
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one liquilens router entry point, found {matches!r}")
    router = matches[0].load()
    if not isinstance(router, Router):
        raise SystemExit("liquilens entry point is not an OpenBB Router")
    paths = {
        route.path: sorted(route.methods or set()) for route in router._api_router.routes
    }
    if paths != {"/verify": ["POST"], "/verify_trade_safety": ["POST"]}:
        raise SystemExit(f"unexpected router surface: {paths!r}")
    print(
        "verified openbb-liquilens-evidence "
        f"with openbb-core {version('openbb-core')}: {paths}"
    )


if __name__ == "__main__":
    main()
