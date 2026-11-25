"""Streaming snapshot publication must reproduce Snapshot.create byte for byte."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.backtest import (
    CashDividend,
    DailyPrice,
    Feature,
    Instrument,
    Snapshot,
)

DAYS = tuple(date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9))
NEXT = date(2024, 1, 10)
SHANGHAI = timezone(timedelta(hours=8))


def rich_inputs() -> dict[str, Any]:
    """Rows covering suspensions, limit markers, unknown evidence, listings and dividends."""
    instruments = [
        Instrument("600000.SH", venue="sse"),
        Instrument("000001.SZ", venue="szse", listed_on=DAYS[1]),
        Instrument("510050.SH", venue="sse", asset_class="etf", delisted_on=DAYS[5]),
    ]
    daily = []
    for i, day in enumerate(DAYS):
        daily.append(
            DailyPrice(
                day,
                "600000.SH",
                Decimal("10.01") + i,
                Decimal("10.11") + i,
                "no" if i == 2 else "yes",
                "yes",
                "limit_up_at_open" if i == 2 else None,
                None,
                evidence="Tushare 日线与涨跌停",
                buy_blocked_by_price_limit=i == 2,
                adjusted_close=10.11 * (1.5 + i),
            )
        )
        if i >= 1:
            suspended = i == 3
            daily.append(
                DailyPrice(
                    day,
                    "000001.SZ",
                    None if suspended else 12,
                    None if suspended else Decimal("12.5"),
                    "no" if suspended else "unknown",
                    "no" if suspended else "unknown",
                    "suspended" if suspended else None,
                    "suspended" if suspended else None,
                    evidence="status" if suspended else "",
                    suspended=suspended,
                    adjusted_close=None if suspended else 25.0,
                )
            )
        if i < 5:
            daily.append(DailyPrice(day, "510050.SH", 3, 3, evidence="etf", adjusted_close=3.0))
    features = [
        Feature("600000.SH", "roe", 0.12, datetime.combine(day, time(20), SHANGHAI))
        for day in DAYS[::2]
    ] + [Feature("000001.SZ", "pb", None, datetime(2024, 1, 4, 13, tzinfo=UTC), "2")]
    return {
        "sessions": DAYS,
        "next_session": NEXT,
        "instruments": instruments,
        "daily": daily,
        "features": features,
        "cash_dividends": [CashDividend("600000.SH", DAYS[1], DAYS[2], DAYS[4], Decimal("0.25"))],
        "source_evidence": {"fixture": "rich"},
    }


def stream(root: Path, inputs: dict[str, Any], batch: int = 4) -> Snapshot:
    from trademaster.backtest import SnapshotWriter

    params = {k: v for k, v in inputs.items() if k != "daily"}
    rows = sorted(inputs["daily"], key=lambda r: (r.session, r.instrument_id))
    with SnapshotWriter(root, **params) as writer:
        for start in range(0, len(rows), batch):
            writer.add(rows[start : start + batch])
    return writer.snapshot


def files(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(root.iterdir())}


def test_create_snapshot_ids_are_unchanged(tmp_path: Path) -> None:
    from test_snapshot_backtest import weekly_snapshot

    rich = Snapshot.create(tmp_path / "rich", **rich_inputs())
    assert rich.snapshot_id == "f94b416bb782dfbdd1f8d6d1245719b570d03b2b344ed4aec8663dec71584ffb"
    weekly = weekly_snapshot(tmp_path / "weekly")
    assert weekly.snapshot_id == "0c4755d15ac5780041a08b673905c80c83065ad576a85c2e8537330b47bb6345"


def test_streamed_batches_match_create_byte_for_byte(tmp_path: Path) -> None:
    created = Snapshot.create(tmp_path / "create", **rich_inputs())
    streamed = stream(tmp_path / "stream", rich_inputs(), batch=3)
    assert streamed.snapshot_id == created.snapshot_id
    assert files(tmp_path / "stream") == files(tmp_path / "create")
    assert streamed.day(DAYS[5]).closes("600000.SH") == created.day(DAYS[5]).closes("600000.SH")


def test_feature_row_groups_match_a_single_parquet_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trademaster.backtest import snapshot as module

    monkeypatch.setattr(module, "_FEATURE_ROW_GROUP", 4)
    snap = stream(tmp_path, rich_inputs(), batch=5)
    path = next(tmp_path.glob("features-*.parquet"))
    assert pq.ParquetFile(path).metadata.num_row_groups > 1
    expected = pa.BufferOutputStream()
    pq.write_table(pq.read_table(path), expected, row_group_size=4)
    assert path.read_bytes() == expected.getvalue().to_pybytes()
    assert snap.day(DAYS[4]).closes("510050.SH", adjusted=True)[-1] == 3.0


def test_rows_must_arrive_in_session_and_instrument_order(tmp_path: Path) -> None:
    from trademaster.backtest import SnapshotWriter

    inputs = rich_inputs()
    params = {k: v for k, v in inputs.items() if k != "daily"}
    first, second = inputs["daily"][0], inputs["daily"][1]  # 600000.SH, then 510050.SH
    order = pytest.raises(ValueError, match="in \\(session, instrument_id\\) order")
    with order, SnapshotWriter(tmp_path / "order", **params) as writer:
        writer.add([first, second])
    duplicate = pytest.raises(ValueError, match="unknown or duplicate daily row")
    with duplicate, SnapshotWriter(tmp_path / "dup", **params) as writer:
        writer.add([first, first])


def test_failed_publication_leaves_no_partial_snapshot(tmp_path: Path) -> None:
    from trademaster.backtest import SnapshotWriter

    inputs = rich_inputs()
    params = {k: v for k, v in inputs.items() if k != "daily"}
    bad = DailyPrice(DAYS[0], "000001.SZ", 12, 12, evidence="before listing")
    rejected = pytest.raises(ValueError, match="outside listing interval")
    with rejected, SnapshotWriter(tmp_path, **params) as writer:
        writer.add([bad])
    assert list(tmp_path.iterdir()) == []


def test_publishing_does_not_open_the_snapshot(tmp_path: Path) -> None:
    from trademaster.backtest import SnapshotWriter

    inputs = rich_inputs()
    params = {k: v for k, v in inputs.items() if k != "daily"}
    with SnapshotWriter(tmp_path, **params) as writer:
        writer.add(sorted(inputs["daily"], key=lambda r: (r.session, r.instrument_id)))
    assert writer.snapshot_id == "f94b416bb782dfbdd1f8d6d1245719b570d03b2b344ed4aec8663dec71584ffb"
    assert writer._snapshot is None  # nothing loaded until the snapshot is requested
    assert writer.snapshot.snapshot_id == writer.snapshot_id
    assert writer.snapshot is writer.snapshot


def test_snapshot_is_unavailable_before_publication(tmp_path: Path) -> None:
    from trademaster.backtest import SnapshotWriter

    params = {k: v for k, v in rich_inputs().items() if k != "daily"}
    writer = SnapshotWriter(tmp_path / "pending", **params)
    with pytest.raises(RuntimeError, match="not been published"):
        writer.snapshot  # noqa: B018
    writer.abort()
    assert not (tmp_path / "pending").exists()
