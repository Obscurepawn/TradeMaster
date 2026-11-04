"""Regression checks for the same-data comparison."""

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest
from trademaster.e2e.demo_strategies import DEFAULT_PLAN


def test_real_comparison_uses_identical_targets_and_reports_each_session(tmp_path: Path) -> None:
    from trademaster.e2e.compare_rebalance import compare

    root = Path(__file__).resolve().parents[1]
    market = root / "data/research/full-a-20y"
    previous = root / "artifacts/demo-strategies"
    if (
        not (market / "plans" / DEFAULT_PLAN / "manifest.json").is_file()
        or not (previous / "summary.json").is_file()
    ):
        pytest.skip("the saved Tushare plan and original demo bundles are required")

    out = compare(
        data_root=market,
        plan=DEFAULT_PLAN,
        legacy_root=previous,
        output=tmp_path,
        repeats=1,
    )
    result = json.loads(out.read_bytes())
    assert set(result["strategies"]) == {"cross-sectional-momentum", "moving-average", "macd"}
    for name, entry in result["strategies"].items():
        assert entry["same_decisions"]
        assert entry["before"]["fills"] in (2, 4, 31)
        assert entry["after"]["fills"] > 0
        with (tmp_path / name / "equity.csv").open() as handle:
            daily = list(csv.DictReader(handle))
        assert len(daily) == 98
        assert daily[-1]["after_equity"] == entry["after"]["ending_equity"]
        assert entry["timing_ms"]["before"] > 0
        assert entry["timing_ms"]["after"] > 0

    with (tmp_path / "macd" / "trades.csv").open() as handle:
        trades = {row["session"]: row for row in csv.DictReader(handle)}
    purchase = trades["2024-03-28"]
    assert (purchase["instrument"], purchase["side"]) == ("600036.SH", "buy")
    for path in ("before", "after"):
        assert int(purchase[f"{path}_shares"]) == 3_000
        assert Decimal(purchase[f"{path}_fill_price"]) == Decimal("32.34")
        assert Decimal(purchase[f"{path}_costs"]) == Decimal("30.09")
    retry = trades["2024-04-17"]
    assert retry["before_fill_price"] == ""
    assert Decimal(retry["before_costs"]) == 0


def test_trade_csv_weights_multiple_fills_by_quantity(tmp_path: Path) -> None:
    from trademaster.e2e.compare_rebalance import UNIT, _compare_paths

    day = "2024-03-28"
    base = {"session": day, "instrument_id": "A", "side": "buy", "tax": 0, "transfer_fee": 0}
    fills = [
        {**base, "quantity": 100, "price": 11 * UNIT, "commission": UNIT, "slippage": 1},
        {**base, "quantity": 300, "price": 13 * UNIT, "commission": 2 * UNIT, "slippage": 0},
    ]
    current = {
        "status": "completed",
        "first_issue": None,
        "daily": [{"session": day, "cash": 0, "equity": 100 * UNIT}],
        "fills": fills,
        "total_costs": 3 * UNIT + 1,
        "batches": [],
    }
    legacy = {
        "nav": [
            {
                "event_time": day,
                "event_kind": "bar_close",
                "cash_scaled": "0",
                "net_asset_value_scaled": str(100 * UNIT),
            }
        ],
        "fills": [],
    }
    _compare_paths(legacy, current, tmp_path)
    with (tmp_path / "trades.csv").open() as handle:
        (row,) = csv.DictReader(handle)
    assert Decimal(row["after_fill_price"]) == Decimal("12.50")
    assert Decimal(row["after_costs"]) == Decimal("3.00000001")
    assert row["before_fill_price"] == ""
