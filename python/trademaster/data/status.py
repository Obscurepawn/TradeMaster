"""Shared revision semantics for canonical daily trading status rows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

import pyarrow as pa

from trademaster.contracts import _require_utc


def latest_status_rows(
    table: pa.Table, *, as_of: datetime | None = None
) -> dict[tuple[str, str], dict[str, object]]:
    """Return one unambiguous latest row per instrument/session pair."""

    if as_of is not None:
        _require_utc(as_of)
    latest: dict[
        tuple[str, str], tuple[datetime, str, dict[str, object]]
    ] = {}
    for raw_row in table.to_pylist():
        row = cast(dict[str, object], raw_row)
        known_at = cast(datetime, row["known_at"])
        _require_utc(known_at)
        if as_of is not None and known_at > as_of:
            continue
        trade_date = cast(Any, row["trade_date"])
        key = (
            str(row["instrument_id"]),
            f'{row["venue"]}:{trade_date.isoformat()}',
        )
        identity = json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        previous = latest.get(key)
        if previous is None or known_at > previous[0]:
            latest[key] = (known_at, identity, row)
        elif known_at == previous[0] and identity != previous[1]:
            raise ValueError("status latest revision is ambiguous")
    return {key: item[2] for key, item in latest.items()}
