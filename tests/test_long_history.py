import csv
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from trademaster.backtest import CashDividend, DailyPrice, Feature, Instrument, Snapshot
from trademaster.e2e.long_history import check_reference, run_benchmark
from trademaster.replay import replay


def test_quoted_close_signals_and_repeated_account_results(tmp_path: Path) -> None:
    symbol = "000702.SZ"
    flat = Decimal("10.01")
    prices = [flat] * 199 + [None, flat, 11, None, 12, 9, 9, flat, 15]
    sessions = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(prices))]
    market_dir = tmp_path / "snapshot"
    Snapshot.create(
        market_dir,
        sessions=sessions,
        next_session=sessions[-1] + timedelta(days=1),
        instruments=[Instrument(symbol, venue="szse")],
        daily=[
            DailyPrice(
                day,
                symbol,
                10 if price is not None else None,
                price,
                buy_at_open="yes" if price is not None else "no",
                sell_at_open="yes" if price is not None else "no",
                suspended=price is None,
                evidence="fixture",
            )
            for day, price in zip(sessions, prices, strict=True)
        ],
    )

    output = tmp_path / "report"
    report = run_benchmark(market_dir, output, repeats=2)
    assert json.loads((output / "benchmark.json").read_bytes()) == report
    for name, expected in (
        ("sma200", [(201, True), (204, False), (207, True)]),
        ("buy_and_hold", [(200, True)]),
    ):
        folder = output / name
        request = json.loads((folder / "request.json").read_bytes())
        assert [(t["decision_session"], t["weights"]) for t in request["run"]["targets"]] == [
            (sessions[index].isoformat(), {symbol: 100_000_000} if long else {})
            for index, long in expected
        ]
        result = json.loads((folder / "result.json").read_bytes())
        assert json.loads(replay(folder / "request.json")) == result
        assert (
            result["fills"][0]["session"] == sessions[203 if name == "sma200" else 201].isoformat()
        )
        measurements = report["strategies"][name]
        assert measurements["callbacks"] == measurements["valued_sessions"] == len(sessions)
        assert measurements["fill_count"] == len(result["fills"])
        assert len(measurements["samples_ms"]) == 2
        payload = json.dumps(
            [request["run"]["targets"], result], sort_keys=True, separators=(",", ":")
        )
        assert measurements["digest"] == hashlib.sha256(payload.encode()).hexdigest()
    with (output / "equity.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert list(rows[0]) == ["session", "sma200", "buy_and_hold"]
    assert [r["session"] for r in rows] == [s.isoformat() for s in sessions]


def test_index_signals_trade_etf_at_next_open_and_pay_only_commission(tmp_path: Path) -> None:
    symbol = "510050.SH"
    index = [100] * 199 + [101, 99, 101, 101, 101]
    sessions = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(index))]
    root = tmp_path / "snapshot"
    # The ETF falls as the index crosses above its average. Only the index signals a buy.
    prices = (
        [Decimal("2.000")] * 199 + [Decimal("1.999"), Decimal("2.001")] * 2 + [Decimal("2.000")]
    )
    Snapshot.create(
        root,
        sessions=sessions,
        next_session=sessions[-1] + timedelta(days=1),
        instruments=[Instrument(symbol, "sse", tick_size=Decimal("0.001"), asset_class="etf")],
        daily=[
            DailyPrice(day, symbol, price, price, evidence="fixture")
            for day, price in zip(sessions, prices, strict=True)
        ],
        cash_dividends=[
            CashDividend(symbol, sessions[200], sessions[201], sessions[202], Decimal("0.10")),
            CashDividend(
                symbol,
                sessions[202],
                sessions[203],
                sessions[203] + timedelta(days=1),
                Decimal("0.03"),
            ),
        ],
        features=[
            Feature(symbol, "sse50_close", points, datetime.combine(day, time(7), tzinfo=UTC))
            for day, points in zip(sessions, index, strict=True)
        ],
    )

    output = tmp_path / "report"
    report = run_benchmark(root, output, repeats=1)
    assert report["instrument"] == symbol
    assert report["signal_instrument"] == "000016.SH"
    assert (
        report["features_sha256"]
        == json.loads((root / "manifest.json").read_bytes())["features"]["sha256"]
    )
    for name, expected in [
        ("sma200", [(199, True), (200, False), (201, True)]),
        ("buy_and_hold", [(199, True)]),
    ]:
        request = json.loads((output / name / "request.json").read_bytes())
        assert [(t["decision_session"], t["weights"]) for t in request["run"]["targets"]] == [
            (sessions[i].isoformat(), {symbol: 100_000_000} if long else {}) for i, long in expected
        ]
        result = json.loads((output / name / "result.json").read_bytes())
        fills = result["fills"]
        assert fills[0]["session"] == sessions[200].isoformat()
        assert fills[0]["instrument_id"] == symbol
        assert fills[0]["price"] == 200_100_000
        assert all(f["commission"] >= 500_000_000 for f in fills)
        assert all(f["tax"] == f["transfer_fee"] == 0 for f in fills)
        assert Decimal(report["strategies"][name]["cash_dividends_paid_cny"]) == (
            fills[0]["quantity"] * Decimal("0.10")
        )
        held_on_second_record = sum(
            f["quantity"] * (1 if f["side"] == "buy" else -1)
            for f in fills
            if f["session"] <= sessions[202].isoformat()
        )
        assert Decimal(report["strategies"][name]["ending_dividends_receivable_cny"]) == (
            held_on_second_record * Decimal("0.03")
        )
    assert {
        f["side"] for f in json.loads((output / "sma200/result.json").read_bytes())["fills"]
    } == {"buy", "sell"}


def test_etf_window_requires_a_fresh_index_close_each_session(tmp_path: Path) -> None:
    symbol = "510050.SH"
    a, b, c = (date(2020, 1, n) for n in (6, 7, 8))
    root = tmp_path / "snapshot"
    Snapshot.create(
        root,
        sessions=[a, b],
        next_session=c,
        instruments=[Instrument(symbol)],
        daily=[DailyPrice(d, symbol, 2, 2, evidence="fixture") for d in (a, b)],
        features=[Feature(symbol, "sse50_close", 100, datetime.combine(a, time(7), tzinfo=UTC))],
    )
    with pytest.raises(ValueError, match="sse50_close"):
        run_benchmark(root, tmp_path / "report", repeats=1)


@pytest.mark.parametrize(
    "changed", ["snapshot_id", "market_sha256", "features_sha256", "sma200", "buy_and_hold"]
)
def test_reference_compares_market_and_both_ledgers_without_timing(
    tmp_path: Path, changed: str
) -> None:
    report: dict[str, Any] = {
        "snapshot_id": "market-v1",
        "market_sha256": "bars-v1",
        "features_sha256": "features-v1",
        "strategies": {
            "sma200": {"digest": "strategy-ledger", "median_ms": {"repeat": 20}},
            "buy_and_hold": {"digest": "hold-ledger", "median_ms": {"repeat": 10}},
        },
    }
    reference = tmp_path / "reference.json"
    expected = {
        **report,
        "strategies": {
            name: {"digest": row["digest"]} for name, row in report["strategies"].items()
        },
    }
    reference.write_text(json.dumps(expected))
    check_reference(report, reference)
    if changed in ("snapshot_id", "market_sha256", "features_sha256"):
        expected[changed] = "different"
    else:
        expected["strategies"][changed]["digest"] = "different"
    reference.write_text(json.dumps(expected))
    with pytest.raises(ValueError, match=changed):
        check_reference(report, reference)
