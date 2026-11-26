"""How ``Snapshot.open`` verifies and loads published market and feature objects."""

import hashlib
import io
import json
import random
from datetime import UTC, date, datetime
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
    TargetBatch,
    backtest,
)
from trademaster.backtest.snapshot import canonical

DAYS = (date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9))
NEXT = date(2024, 1, 10)
ADJUSTED = "__adjusted_close__"
# Default A-share closes are 15:00 UTC+8.
CLOSE = {d: datetime(d.year, d.month, d.day, 7, tzinfo=UTC) for d in DAYS}


def publish(root: Path) -> Snapshot:
    rows = []
    for i, day in enumerate(DAYS):
        price = 10 + i
        rows.append(
            DailyPrice(
                day, "A", max(price - 1, 10), price, evidence="fixture", adjusted_close=2.0 * price
            )
        )
        if i < 2:
            rows.append(
                DailyPrice(
                    day,
                    "B",
                    20,
                    None if i else 20,
                    suspended=bool(i),
                    evidence="fixture",
                    adjusted_close=None if i else 40.0,
                )
            )
    return Snapshot.create(
        root,
        sessions=DAYS,
        next_session=NEXT,
        instruments=[Instrument("A"), Instrument("B", delisted_on=DAYS[2])],
        daily=rows,
        features=[
            Feature("A", "score", 1.0, CLOSE[DAYS[0]]),
            Feature("B", "score", 2.0, CLOSE[DAYS[0]]),
            Feature("A", "score", 3.0, CLOSE[DAYS[1]]),
        ],
        cash_dividends=[CashDividend("A", DAYS[0], DAYS[1], DAYS[2], Decimal("0.05"))],
    )


def manifest(root: Path) -> dict[str, Any]:
    return json.loads((root / "manifest.json").read_bytes())  # type: ignore[no-any-return]


def market(root: Path) -> dict[str, Any]:
    return json.loads((root / manifest(root)["market"]["file"]).read_bytes())  # type: ignore[no-any-return]


def features(root: Path) -> pa.Table:
    return pq.read_table(root / manifest(root)["features"]["file"])


def parquet(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def republish(
    root: Path,
    *,
    market: bytes | None = None,
    features: bytes | None = None,
    market_digest: str | None = None,
    features_digest: str | None = None,
) -> None:
    """Replace objects and rewrite a self-consistent manifest, optionally recording a digest."""
    record = manifest(root)
    record.pop("snapshot_id")
    for kind, extension, payload, digest in (
        ("market", "json", market, market_digest),
        ("features", "parquet", features, features_digest),
    ):
        if payload is not None:
            actual = hashlib.sha256(payload).hexdigest()
            name = f"{kind}-{actual}.{extension}"
            (root / name).write_bytes(payload)
            record[kind] = {"file": name, "sha256": digest or actual}
    record["snapshot_id"] = hashlib.sha256(canonical(record)).hexdigest()
    (root / "manifest.json").write_bytes(canonical(record))


def error(root: Path) -> BaseException:
    with pytest.raises(Exception) as raised:
        Snapshot.open(root)
    return raised.value


def test_market_errors_keep_their_type_and_text(tmp_path: Path) -> None:
    publish(tmp_path)
    valid = canonical(market(tmp_path))
    rows = valid.index(b'"rows"')
    unknown = market(tmp_path)
    unknown["rows"][1]["volume"] = 1
    duplicate = market(tmp_path)
    duplicate["rows"][3]["instrument_id"] = "A"
    zero = market(tmp_path)
    zero["rows"][4]["close"] = 0
    outside = market(tmp_path)
    outside["rows"][4]["session"] = NEXT.isoformat()
    cases: list[tuple[str, bytes, type[BaseException], str]] = [
        (
            "truncated",
            valid[: rows + 300],
            ValueError,
            "invalid market snapshot: EOF while parsing a string at line 1 column 764",
        ),
        (
            "invalid UTF-8",
            valid.replace(b"fixture", b"fi\xffture", 1),
            ValueError,
            "invalid market snapshot: invalid unicode code point at line 1 column 579",
        ),
        (
            "unknown field",
            canonical(unknown),
            ValueError,
            (
                "invalid market snapshot: unknown field `volume`, expected one of `session`, "
                "`instrument_id`, `open`, `close`, `buy_at_open`, `sell_at_open`, `buy_reason`, "
                "`sell_reason`, `evidence`, `suspended`, `corporate_action_gap`, "
                "`buy_blocked_by_price_limit`, `sell_blocked_by_price_limit` at line 1 column 935"
            ),
        ),
        (
            "trailing data",
            valid + b"\n{}",
            ValueError,
            "invalid market snapshot: trailing characters at line 2 column 1",
        ),
        (
            "duplicate row",
            canonical(duplicate),
            RuntimeError,
            "invalid snapshot run: invalid or duplicate row: 2024-01-08 A",
        ),
        (
            "non-positive close",
            canonical(zero),
            RuntimeError,
            "invalid snapshot run: invalid or duplicate row: 2024-01-09 A",
        ),
        (
            "row outside sessions",
            canonical(outside),
            RuntimeError,
            "invalid snapshot run: row outside sampled sessions",
        ),
    ]
    for name, payload, kind, message in cases:
        republish(tmp_path, market=payload)
        raised = error(tmp_path)
        assert (type(raised), str(raised)) == (kind, message), (name, str(raised))


def test_market_digest_is_checked_before_features_and_content(tmp_path: Path) -> None:
    publish(tmp_path)
    valid = canonical(market(tmp_path))
    truncated = valid[:-10]
    other = hashlib.sha256(b"other").hexdigest()
    # A damaged market recorded under another digest reports the digest, not the parse error.
    republish(tmp_path, market=truncated, market_digest=other)
    name = manifest(tmp_path)["market"]["file"]
    raised = error(tmp_path)
    assert (type(raised), str(raised)) == (ValueError, f"snapshot object hash mismatch: {name}")
    # The market digest is reported before a features digest mismatch.
    republish(tmp_path, features=b"not parquet", features_digest=other)
    raised = error(tmp_path)
    assert str(raised) == f"snapshot object hash mismatch: {name}"
    # Both digests are verified before market content is parsed.
    republish(tmp_path, market=truncated)
    features_name = manifest(tmp_path)["features"]["file"]
    raised = error(tmp_path)
    assert str(raised) == f"snapshot object hash mismatch: {features_name}"


def test_a_missing_market_object_raises_the_os_error(tmp_path: Path) -> None:
    publish(tmp_path)
    (tmp_path / manifest(tmp_path)["market"]["file"]).unlink()
    assert type(error(tmp_path)) is FileNotFoundError


def test_market_object_must_stay_inside_the_snapshot(tmp_path: Path) -> None:
    publish(tmp_path / "snap")
    root = tmp_path / "snap"
    record = manifest(root)
    record.pop("snapshot_id")
    record["market"]["file"] = "../market.json"
    record["snapshot_id"] = hashlib.sha256(canonical(record)).hexdigest()
    (root / "manifest.json").write_bytes(canonical(record))
    raised = error(root)
    assert str(raised) == "snapshot object must be inside the snapshot directory"


def adjusted(instrument: str | None, at: datetime | None, value: float | None) -> dict[str, Any]:
    return {
        "instrument_id": instrument,
        "name": ADJUSTED,
        "version": "1",
        "available_at": at,
        "value": value,
    }


def with_rows(table: pa.Table, rows: list[dict[str, Any]], *, first: bool = False) -> bytes:
    extra = pa.Table.from_pylist(rows, schema=table.schema)
    return parquet(pa.concat_tables([extra, table] if first else [table, extra]))


def test_feature_errors_keep_their_type_text_and_priority(tmp_path: Path) -> None:
    publish(tmp_path)
    table = features(tmp_path)
    late = datetime(2024, 1, 9, 8, tzinfo=UTC)
    early = datetime(2024, 1, 5, 8, tzinfo=UTC)
    cases: list[tuple[str, list[dict[str, Any]], bool, type[BaseException], tuple[Any, ...]]] = [
        ("unknown time", [adjusted("A", late, 1.0)], False, KeyError, (late,)),
        ("unknown instrument", [adjusted("Z", CLOSE[DAYS[1]], 1.0)], False, KeyError, ("Z",)),
        (
            "outside listing",
            [adjusted("B", CLOSE[DAYS[2]], 1.0)],
            False,
            ValueError,
            ("adjusted close outside listing interval: (datetime.date(2024, 1, 9), 'B')",),
        ),
        (
            "earliest unknown time wins regardless of file order",
            [adjusted("A", late, 1.0), adjusted("A", early, 1.0)],
            False,
            KeyError,
            (early,),
        ),
        (
            "unknown times precede unknown instruments",
            [adjusted("Z", CLOSE[DAYS[0]], 1.0), adjusted("A", late, 1.0)],
            True,
            KeyError,
            (late,),
        ),
        (
            "unknown instruments precede listing errors",
            [adjusted("B", CLOSE[DAYS[2]], 1.0), adjusted("Y", CLOSE[DAYS[2]], 1.0)],
            False,
            KeyError,
            ("Y",),
        ),
        (
            "earliest unknown instrument by time, then file order",
            [
                adjusted("Y", CLOSE[DAYS[1]], 1.0),
                adjusted("X", CLOSE[DAYS[0]], 1.0),
                adjusted("W", CLOSE[DAYS[0]], 1.0),
            ],
            False,
            KeyError,
            ("X",),
        ),
        ("null instrument", [adjusted(None, CLOSE[DAYS[1]], 1.0)], False, KeyError, (None,)),
        (
            "null time sorts after known times",
            [adjusted("A", None, 1.0), adjusted("A", late, 1.0)],
            True,
            KeyError,
            (late,),
        ),
        ("null time alone", [adjusted("A", None, 1.0)], False, KeyError, (None,)),
    ]
    for name, rows, first, kind, args in cases:
        republish(tmp_path, features=with_rows(table, rows, first=first))
        raised = error(tmp_path)
        assert (type(raised), raised.args) == (kind, args), name


def test_feature_schema_must_match(tmp_path: Path) -> None:
    publish(tmp_path)
    table = features(tmp_path)
    for name, changed in (
        ("extra column", table.append_column("x", pa.array([1] * table.num_rows))),
        ("float32 values", table.set_column(4, "value", table["value"].cast(pa.float32()))),
        ("dictionary names", table.set_column(1, "name", table["name"].dictionary_encode())),
    ):
        republish(tmp_path, features=parquet(changed))
        raised = error(tmp_path)
        assert (type(raised), str(raised)) == (ValueError, "snapshot feature schema mismatch"), name


def test_duplicate_feature_observations_resolve_by_file_order(tmp_path: Path) -> None:
    publish(tmp_path)
    rows = [
        adjusted("A", CLOSE[DAYS[1]], 99.0),
        adjusted("A", CLOSE[DAYS[1]], 98.0),
        adjusted("A", CLOSE[DAYS[0]], None),
        {
            "instrument_id": "B",
            "name": "score",
            "version": "1",
            "available_at": CLOSE[DAYS[0]],
            "value": 7.0,
        },
    ]
    republish(tmp_path, features=with_rows(features(tmp_path), rows))
    snap = Snapshot.open(tmp_path)
    assert snap.day(DAYS[2]).closes("A", adjusted=True) == (None, 98.0, 24.0)
    assert snap.day(DAYS[1]).closes("B", adjusted=True) == (40.0, None)
    assert dict(snap.day(DAYS[0]).scores("score")) == {"A": 1.0, "B": 7.0}
    assert dict(snap.day(DAYS[1]).scores("score")) == {"A": 3.0, "B": 7.0}


def test_market_row_order_does_not_change_reads_or_results(tmp_path: Path) -> None:
    root = tmp_path / "snap"
    original = publish(root)

    def outcome(snap: Snapshot) -> tuple[Any, ...]:
        result = backtest(
            snap,
            targets=[
                TargetBatch(DAYS[0], {"A": Decimal("0.5"), "B": Decimal("0.3")}, batch_id="t")
            ],
            initial_cash=Decimal(100_000),
            detail=True,
        )
        reads = [
            snap.day(DAYS[1]).closes(s, adjusted=adjusted)
            for s in ("A", "B")
            for adjusted in (False, True)
        ]
        return reads, canonical(result.raw)

    expected = outcome(original)
    assert b'"fill_count":1' in expected[1] and b'"missing_open_row"' in expected[1]
    content = market(root)
    for seed in range(4):
        random.Random(seed).shuffle(content["rows"])
        republish(root, market=canonical(content))
        assert outcome(Snapshot.open(root)) == expected, seed
