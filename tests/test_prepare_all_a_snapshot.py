"""Opening evidence and dated membership of the full-market README example."""

import json
import runpy
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def test_full_a_panel_preserves_listing_auction_limits_and_action_gaps(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "specs/examples/prepare_all_a_snapshot.py"
    build_snapshot = runpy.run_path(str(script))["_snapshot"]
    days = [date(2026, 7, 6) + timedelta(days=i) for i in range(4)]
    rows = []
    for i, day in enumerate(days):
        for symbol in ("600001.SH", "600002.SH", "600003.SH", "688001.SH", "920001.BJ"):
            if symbol == "600002.SH" and i != 1:
                continue
            upper = {"600002.SH": None, "600003.SH": float("inf")}.get(symbol, 11.0)
            rows.append(
                {
                    "session_date": day,
                    "session_index": i,
                    "instrument_id": symbol,
                    "venue": "BSE" if symbol.endswith(".BJ") else "SSE",
                    "open": 11.0 if i == 0 else 9.0,
                    "close": 10.0,
                    "pre_close": 10.0,
                    "adj_factor": 1.1 if i >= 2 else 1.0,
                    "up_limit": upper,
                    "down_limit": 9.0,
                    "explicitly_suspended": False,
                    "suspend_type": None,
                    "suspend_timing": None,
                }
            )
    panel = tmp_path / "panel.parquet"
    pq.write_table(pa.Table.from_pylist(rows), panel)
    snapshot = build_snapshot(panel, tmp_path / "market", {}, days[0], days[2], days[3])
    # Pinned from the list-based preparer: streaming publication must not change a byte.
    assert (
        snapshot.snapshot_id == "adacba11b17b28c6997eb8cedf0c8554ab8eb6d71c183630e3482b54ddd3ecf6"
    )
    assert "600002.SH" not in snapshot.day(days[0]).instruments
    assert "600002.SH" in snapshot.day(days[1]).instruments
    assert "600002.SH" not in snapshot.day(days[2]).instruments
    assert "920001.BJ" in snapshot.day(days[0]).instruments
    market = json.loads(snapshot.market_path.read_bytes())
    prices = {(r["session"], r["instrument_id"]): r for r in market["rows"]}

    def permissions(i: int, symbol: str) -> tuple[str, str]:
        row = prices[(days[i].isoformat(), symbol)]
        return row["buy_at_open"], row["sell_at_open"]

    assert permissions(0, "600001.SH") == ("no", "yes")
    assert permissions(1, "600001.SH") == ("yes", "no")
    assert prices[(days[0].isoformat(), "600001.SH")]["buy_blocked_by_price_limit"]
    assert prices[(days[1].isoformat(), "600001.SH")]["sell_blocked_by_price_limit"]
    assert permissions(1, "600002.SH") == permissions(1, "600003.SH") == ("unknown", "unknown")
    assert permissions(0, "688001.SH") == permissions(0, "920001.BJ") == ("unknown", "unknown")
    assert prices[(days[2].isoformat(), "600001.SH")]["corporate_action_gap"]
    assert permissions(2, "600001.SH") == ("unknown", "unknown")
    for row in prices.values():
        if row["buy_at_open"] == "unknown":
            assert "buy_blocked_by_price_limit" not in row
            assert "sell_blocked_by_price_limit" not in row
