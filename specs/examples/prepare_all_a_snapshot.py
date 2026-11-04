"""Build the July 3–August 13, 2026 all-A example from a verified research panel.

All listed Shanghai, Shenzhen and Beijing stocks enter the ranking. STAR and
Beijing order rules and intraday suspensions have unknown opening permissions
in this example. The panel has no distribution schedule. A changed adjustment
factor or a reset of the raw previous close flags an unresolved corporate action.
"""

import argparse
import hashlib
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
from trademaster.backtest import DailyPrice, Instrument, Snapshot
from trademaster.backtest.snapshot import Permission
from trademaster.research.full_a_panel import FactorResearchPanelManifest

PANEL = Path("artifacts/research/full-a-20y/panel/manifests") / (
    "9f163dd4c7b3fe958c4beb363303e767dbe40c00523e5d2a928ef24feb1d4c89.json"
)
START, END, FOLLOWING = date(2026, 7, 3), date(2026, 8, 13), date(2026, 8, 14)


def _price(
    row: dict[str, Any], previous_close: float | None, previous_factor: float | None
) -> DailyPrice:
    symbol, opening, close = row["instrument_id"], row["open"], row["close"]
    factor, upper, lower = row["adj_factor"], row["up_limit"], row["down_limit"]
    action_gap = (
        factor is None
        or factor <= 0
        or (previous_factor is not None and factor != previous_factor)
        or (close is not None and previous_close is not None and row["pre_close"] != previous_close)
    )
    suspended = bool(
        row["explicitly_suspended"]
        and row["suspend_type"] == "S"
        and not row["suspend_timing"]
        and opening is None
        and close is None
    )
    buy: Permission = "unknown"
    sell: Permission = "unknown"
    buy_reason = sell_reason = None
    if action_gap:
        buy_reason = sell_reason = "corporate_action_gap"
    elif suspended:
        buy = sell = "no"
        buy_reason = sell_reason = "suspended"
    elif row["venue"] == "BSE" or symbol.startswith(("688", "689")):
        buy_reason = sell_reason = "unmodeled_board_order_rules"
    elif (
        not row["explicitly_suspended"]
        and opening is not None
        and upper is not None
        and isfinite(upper)
        and lower is not None
        and 0 < lower <= opening <= upper
        and lower < upper
    ):
        buy = "yes" if opening < upper else "no"
        sell = "yes" if opening > lower else "no"
        buy_reason = None if buy == "yes" else "limit_up_at_open"
        sell_reason = None if sell == "yes" else "limit_down_at_open"
    else:
        buy_reason = sell_reason = "missing_or_ambiguous_open_evidence"
    return DailyPrice(
        row["session_date"],
        symbol,
        opening,
        close,
        buy,
        sell,
        buy_reason,
        sell_reason,
        evidence="TuShare full-A dense panel",
        suspended=suspended,
        corporate_action_gap=action_gap,
        adjusted_close=close * factor if close and factor and factor > 0 else None,
    )


def _snapshot(
    parquet: Path,
    directory: Path,
    evidence: dict[str, str],
    start: date,
    end: date,
    following: date,
) -> Snapshot:
    # The dense panel contains one row per listed instrument/session, even on suspensions.
    with duckdb.connect() as db:
        db.from_parquet(str(parquet)).create_view("panel")
        calendar = db.execute(
            """
            SELECT DISTINCT session_date, session_index FROM panel
            WHERE session_date BETWEEN ? AND ? ORDER BY session_date
        """,
            [start, following],
        ).fetchall()
        if (
            len(calendar) < 2
            or calendar[0][0] != start
            or calendar[-2][0] != end
            or calendar[-1][0] != following
            or any(index != calendar[0][1] + i for i, (_, index) in enumerate(calendar))
        ):
            raise ValueError(
                "full-A panel does not cover the example calendar and following session"
            )
        sessions = [day for day, _ in calendar[:-1]]
        rows = db.execute(
            """
            SELECT session_date, session_index, instrument_id, venue, open, close, pre_close,
                   adj_factor, up_limit, down_limit, explicitly_suspended, suspend_type, suspend_timing
            FROM panel WHERE session_date BETWEEN ? AND ? ORDER BY session_date, instrument_id
        """,
            [start, end],
        ).to_arrow_reader(batch_size=16384)
        first: dict[str, date] = {}
        last: dict[str, tuple[int, date, str]] = {}
        closes: dict[str, float] = {}
        factors: dict[str, float] = {}
        daily = []
        for batch in rows:
            for row in batch.to_pylist():
                symbol, day, index = row["instrument_id"], row["session_date"], row["session_index"]
                if symbol in last and index != last[symbol][0] + 1:
                    raise ValueError(f"non-contiguous full-A membership for {symbol}")
                first.setdefault(symbol, day)
                last[symbol] = (index, day, row["venue"])
                daily.append(_price(row, closes.get(symbol), factors.get(symbol)))
                if row["close"] is not None:
                    closes[symbol] = row["close"]
                if row["adj_factor"] is not None:
                    factors[symbol] = row["adj_factor"]
    instruments = [
        Instrument(
            symbol,
            venue.lower(),
            listed_on=first[symbol],
            delisted_on=day + timedelta(days=1) if day < end else None,
        )
        for symbol, (_, day, venue) in last.items()
    ]
    return Snapshot.create(
        directory,
        sessions=sessions,
        next_session=following,
        instruments=instruments,
        daily=daily,
        source_evidence=evidence,
    )


def prepare(manifest_path: Path, directory: Path) -> Snapshot:
    manifest = FactorResearchPanelManifest.model_validate_json(manifest_path.read_bytes())
    ref = next(a for a in manifest.artifacts if a.name == "dense_panel")
    root = manifest_path.resolve().parent
    root = root.parent if root.name == "manifests" else root.parent.parent
    parquet = (root / ref.uri).resolve()
    if not parquet.is_relative_to(root):
        raise ValueError("dense panel outside the artifact root")
    with parquet.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != ref.content_sha256:
            raise ValueError("dense panel content hash mismatch")
    metadata = pq.read_metadata(parquet)
    schema = metadata.schema.to_arrow_schema().remove_metadata()
    if (
        metadata.num_rows != ref.row_count
        or hashlib.sha256(schema.serialize()).hexdigest() != ref.schema_sha256
    ):
        raise ValueError("dense panel row count or schema hash mismatch")
    return _snapshot(
        parquet,
        directory,
        {
            "panel_manifest_sha256": manifest.manifest_sha256,
            "dense_panel_sha256": ref.content_sha256,
        },
        START,
        END,
        FOLLOWING,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-manifest", type=Path, default=PANEL)
    parser.add_argument("--output", type=Path, default=Path("artifacts/all-a-momentum/snapshot"))
    args = parser.parse_args()
    if not args.panel_manifest.is_file():
        parser.error(
            "materialize the full-A research panel and pass its path with --panel-manifest"
        )
    snapshot = prepare(args.panel_manifest, args.output)
    print(
        f"{len(snapshot.sessions)} sessions, {len(snapshot.instruments)} stocks, {snapshot.directory}"
    )
