"""End-to-end checks of the stock-selection example on a small synthetic market."""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from trademaster.backtest import DailyPrice, Instrument, Snapshot

SCRIPT = Path(__file__).resolve().parents[1] / "specs/examples/all_a_momentum.py"


def test_weekly_selection_uses_exact_adjusted_endpoints_and_ten_equal_slots(tmp_path: Path) -> None:
    start = date(2024, 6, 14)
    dates = [start + timedelta(days=i) for i in range(39)]
    days = [d for d in dates if d.weekday() < 5]
    symbols = [f"{i:06d}.SZ" for i in range(1, 17)]
    stocks = [
        Instrument(
            symbol,
            listed_on=days[1] if i == 15 else start,
            delisted_on=days[20] if i == 16 else None,
        )
        for i, symbol in enumerate(symbols, start=1)
    ]
    # On the first ranking date, symbols 1 and 2 tie for the last slot behind 3–11.
    ranking_closes = [11, 11] + [20] * 9 + [9] + [100] * 4
    rows = []
    for index, day in enumerate(days):
        for i, symbol in enumerate(symbols, start=1):
            if (i == 15 and index == 0) or (i == 16 and index >= 20):
                continue
            adjusted: float | None = ranking_closes[i - 1] if index == 20 else 10
            if (i, index) in ((13, 20), (14, 0), (3, 9)) or (index == 25 and i != 1):
                adjusted = None
            rows.append(DailyPrice(day, symbol, 10, 10, adjusted_close=adjusted, evidence="test"))

    snapshot = Snapshot.create(
        tmp_path / "snapshot",
        sessions=days,
        next_session=days[-1] + timedelta(days=1),
        instruments=stocks,
        daily=rows,
    )
    output = tmp_path / "run"
    subprocess.run([sys.executable, SCRIPT, snapshot.directory, "--output", output], check=True)
    targets = json.loads((output / "request.json").read_bytes())["run"]["targets"]
    assert [row["decision_session"] for row in targets] == [d.isoformat() for d in days[::5]]
    assert all(row["weights"] == {} for row in targets[:4])
    assert targets[4]["eligible_session"] == days[21].isoformat()
    assert targets[4]["weights"] == dict.fromkeys([symbols[0], *symbols[2:11]], 10_000_000)
    assert targets[5]["weights"] == {symbols[0]: 10_000_000}
    result = json.loads((output / "result.json").read_bytes())
    assert result["status"] == "completed"
    assert result["fills"][0]["session"] == days[21].isoformat()


@pytest.mark.parametrize(("asset_class", "currency"), [("etf", "CNY"), ("stock", "USD")])
def test_example_rejects_mixed_or_non_cny_inputs(
    tmp_path: Path, asset_class: str, currency: str
) -> None:
    snapshot = Snapshot.create(
        tmp_path / "snapshot",
        sessions=[date(2024, 6, 14)],
        next_session=date(2024, 6, 17),
        instruments=[
            Instrument("A", currency=currency),
            Instrument("B", asset_class=asset_class, currency=currency),
        ],
        daily=[],
    )
    run = subprocess.run(
        [sys.executable, SCRIPT, snapshot.directory, "--output", tmp_path / "run"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "CNY stocks" in run.stderr
