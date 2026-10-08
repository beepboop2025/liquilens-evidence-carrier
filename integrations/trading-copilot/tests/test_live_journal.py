"""Recovery metadata remains readable without credentials or database writes."""

import json
import sqlite3
from pathlib import Path

import pytest
from test_live_connector import Broker, bundle, lane

from liquilens_trading_copilot import live_cli
from liquilens_trading_copilot.live_connector import LiveExecutionBlocked
from liquilens_trading_copilot.live_journal import local_orders, local_status
from liquilens_trading_copilot.state import operator_lock


def saved_order(tmp_path):
    request, receipt, binding = bundle()
    broker = Broker()
    client = lane(tmp_path, binding, broker)
    record = client.submit(request, receipt)
    client.broker.close()
    return record


def fingerprints(directory):
    return {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in directory.iterdir()
    }


def test_local_recovery_matches_saved_observation_without_writes_or_secrets(tmp_path):
    record = saved_order(tmp_path)
    before = fingerprints(tmp_path)
    assert local_status(tmp_path.resolve(), record["request_hash"]) == record
    report = local_orders(tmp_path.resolve())
    assert report["orders"] == [record]
    assert report["unresolved_count"] == 1
    assert report["network_accessed"] is False
    assert report["truncated"] is False
    assert fingerprints(tmp_path) == before


@pytest.mark.parametrize("operation", ["status", "orders", "export"])
def test_cli_local_recovery_never_loads_credentials_or_constructs_broker(
    tmp_path, monkeypatch, capsys, operation
):
    record = saved_order(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("local recovery accessed operational credentials or broker")

    monkeypatch.setattr(live_cli, "AlpacaLiveTransport", forbidden)
    monkeypatch.setattr(live_cli, "private_json", forbidden)
    argv = ["liquilens-live", operation, "--state-dir", str(tmp_path)]
    if operation == "status":
        argv += ["--request-hash", record["request_hash"]]
    monkeypatch.setattr("sys.argv", argv)
    before = fingerprints(tmp_path)
    assert live_cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert (output if operation == "status" else output["orders"][0]) == record
    if operation == "export":
        assert output["export_scope"] == "bounded_sanitized_metadata_page"
    assert fingerprints(tmp_path) == before


def test_missing_journal_is_not_created(tmp_path):
    tmp_path.chmod(0o700)
    before = fingerprints(tmp_path)
    with pytest.raises(LiveExecutionBlocked, match="unavailable"):
        local_orders(tmp_path.resolve())
    assert fingerprints(tmp_path) == before


@pytest.mark.parametrize("failure", ["symlink", "permissions", "schema", "recovery"])
def test_unsafe_or_unsupported_journal_fails_without_repair(tmp_path, failure):
    saved_order(tmp_path)
    path = tmp_path / "live-orders.sqlite3"
    if failure == "symlink":
        target = tmp_path / "real.sqlite3"
        path.rename(target)
        path.symlink_to(target)
    elif failure == "permissions":
        path.chmod(0o644)
    elif failure == "schema":
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE orders RENAME TO other_orders")
    else:
        Path(str(path) + "-journal").write_bytes(b"pending recovery")
    before = fingerprints(tmp_path)
    with pytest.raises(LiveExecutionBlocked):
        local_orders(tmp_path.resolve())
    assert fingerprints(tmp_path) == before


def test_reader_respects_existing_writer_lock(tmp_path):
    saved_order(tmp_path)
    with (
        operator_lock(tmp_path.resolve()),
        pytest.raises(LiveExecutionBlocked, match="busy"),
    ):
        local_orders(tmp_path.resolve())


def test_listing_paginates_and_export_filters_private_fields(tmp_path):
    saved_order(tmp_path)
    path = tmp_path / "live-orders.sqlite3"
    with sqlite3.connect(path) as db:
        original = db.execute("SELECT * FROM orders").fetchone()
        observed = json.loads(original[7])
        observed["provider_secret"] = "PRIVATE_PROVIDER_VALUE"
        db.execute("UPDATE orders SET observation=?", (json.dumps(observed),))
        for index in range(4):
            digest = f"{index:064x}"
            db.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,'uncertain',?,NULL,0)",
                (
                    digest,
                    f"private-id-{index}",
                    f"private-receipt-{index}",
                    "2026-10-08",
                    "2026-10-08T00:00:00+00:00",
                    "PRIVATE_REQUEST_WITH_ACCOUNT_AND_MANDATE",
                ),
            )
    first = local_orders(tmp_path.resolve(), limit=2)
    second = local_orders(tmp_path.resolve(), limit=2, after=first["next_after"])
    final = local_orders(tmp_path.resolve(), limit=2, after=second["next_after"])
    records = first["orders"] + second["orders"] + final["orders"]
    assert len({r["request_hash"] for r in records}) == 5
    assert final["truncated"] is False and final["next_after"] is None
    assert first["counts"] == {"uncertain": 4, "pending": 1, "terminal": 0}
    assert "PRIVATE" not in json.dumps(records)
    assert "private-id" not in json.dumps(records)
    assert "private-receipt" not in json.dumps(records)


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_listing_rejects_unbounded_limits_without_opening_state(tmp_path, limit):
    with pytest.raises(LiveExecutionBlocked, match="invalid_journal_limit"):
        local_orders(tmp_path.resolve(), limit=limit)
    assert list(tmp_path.iterdir()) == []


def test_unresolved_filter_preserves_terminal_records_in_complete_listing(tmp_path):
    request, receipt, binding = bundle()
    broker = Broker()
    client = lane(tmp_path, binding, broker)
    record = client.submit(request, receipt)
    broker.order.update(status="canceled")
    client.inspect(record["request_hash"], reconcile=True)
    client.broker.close()
    filtered = local_orders(tmp_path.resolve(), unresolved_only=True)
    assert filtered["orders"] == []
    assert filtered["counts"]["terminal"] == 1
    assert filtered["unresolved_count"] == 0
    assert local_orders(tmp_path.resolve())["orders"][0]["state"] == "terminal"


def test_unknown_hash_never_returns_an_unrelated_order(tmp_path):
    saved_order(tmp_path)
    with pytest.raises(LiveExecutionBlocked, match="unknown_order"):
        local_status(tmp_path.resolve(), "0" * 64)
