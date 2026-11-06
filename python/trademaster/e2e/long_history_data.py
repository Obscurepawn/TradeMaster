"""Prepare the fixed 2011–2025 Shenzhen data snapshot for the long-history example."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import duckdb
import pyarrow.parquet as pq

from trademaster.backtest import DailyPrice, Instrument, Snapshot
from trademaster.backtest.snapshot import canonical, scaled
from trademaster.research.full_a_panel import FactorResearchPanelManifest

SYMBOL = "000702.SZ"
START_DATE = date(2011, 1, 4)
END_DATE = date(2025, 12, 31)
NEXT_SESSION = date(2026, 1, 5)
NORMALIZED_MARKET_SHA256 = "3d169b69053a8ee459d310160f12b90768ffbe542e4a20a4db209d6d144a5679"

type Rows = dict[date, dict[str, Any]]
_PRICE_FIELDS = ("open", "close", "pre_close", "adj_factor", "up_limit", "down_limit")
_FIELDS = {
    "trade_cal": "exchange,cal_date,is_open",
    "daily": "ts_code,trade_date,open,close,pre_close",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "stk_limit": "ts_code,trade_date,up_limit,down_limit",
    "suspend_d": "ts_code,trade_date,suspend_type,suspend_timing",
    "stock_st": "ts_code,trade_date,type",
}


def _query(
    token: str, endpoint: str, params: dict[str, str], *, fields: str | None = None
) -> list[dict[str, Any]]:
    fields = fields or _FIELDS[endpoint]
    request = Request(
        "https://api.tushare.pro",
        data=canonical({"api_name": endpoint, "token": token, "params": params, "fields": fields}),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result["code"] != 0:
        # Provider messages may echo credentials. The endpoint and code identify the failure.
        raise ValueError(f"TuShare {endpoint} request failed (code {result['code']})")
    data = result["data"]
    if data.get("has_more") or sorted(data["fields"]) != sorted(fields.split(",")):
        raise ValueError(f"incomplete TuShare {endpoint} response")
    return [dict(zip(data["fields"], values, strict=True)) for values in data["items"]]


def _provider_rows() -> Rows:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise ValueError("set TUSHARE_TOKEN or supply the local panel manifest")
    rows: Rows = {}
    # Three calendar years contain fewer trading dates than the smallest endpoint row limit.
    for year in range(START_DATE.year, END_DATE.year + 1, 3):
        start = max(START_DATE, date(year, 1, 1))
        end = min(END_DATE, date(year + 2, 12, 31))
        span = {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
        last = NEXT_SESSION if end == END_DATE else end
        calendar = _query(
            token,
            "trade_cal",
            {
                **span,
                "end_date": last.strftime("%Y%m%d"),
                "exchange": "SZSE",
                "is_open": "1",
            },
        )
        dates = [date.fromisoformat(r["cal_date"]) for r in calendar]
        if (
            len(set(dates)) != len(dates)
            or not dates
            or any(not start <= d <= last for d in dates)
            or any(r["exchange"] != "SZSE" or str(r["is_open"]) != "1" for r in calendar)
        ):
            raise ValueError("invalid SZSE exchange calendar")
        if end == END_DATE and sorted(d for d in dates if d > end) != [NEXT_SESSION]:
            raise ValueError("unexpected next SZSE trading session")
        block = {
            d: {f: None for f in _PRICE_FIELDS} | {"suspended": False, "is_st": False}
            for d in dates
            if d <= end
        }
        for endpoint in ("daily", "adj_factor", "stk_limit", "suspend_d", "stock_st"):
            params = {**span, "ts_code": SYMBOL}
            if endpoint == "suspend_d":
                params["suspend_type"] = "S"
            seen = set()
            for observation in _query(token, endpoint, params):
                session = date.fromisoformat(observation["trade_date"])
                if observation["ts_code"] != SYMBOL or session not in block or session in seen:
                    raise ValueError(f"unexpected TuShare {endpoint} instrument/session")
                seen.add(session)
                if endpoint == "suspend_d":
                    if observation["suspend_type"] != "S" or observation["suspend_timing"]:
                        raise ValueError(f"unsupported intraday suspension on {session}")
                    block[session]["suspended"] = True
                elif endpoint == "stock_st":
                    block[session]["is_st"] = True
                else:
                    block[session].update(
                        {f: observation[f] for f in _FIELDS[endpoint].split(",")[2:]}
                    )
        rows.update(block)
    return rows


def _panel_rows(path: Path) -> Rows:
    manifest = FactorResearchPanelManifest.model_validate_json(path.read_bytes())
    ref = next(ref for ref in manifest.artifacts if ref.name == "dense_panel")
    # Accept either the manifests/<hash>.json copy or runs/<id>/manifest.json.
    root = path.parent.parent if path.parent.name == "manifests" else path.parent.parent.parent
    parquet = (root / ref.uri).resolve()
    if not parquet.is_relative_to(root.resolve()):
        raise ValueError("dense panel artifact escapes its root")
    with parquet.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != ref.content_sha256:
            raise ValueError("dense panel content hash mismatch")
    metadata = pq.read_metadata(parquet)
    schema = metadata.schema.to_arrow_schema().remove_metadata()
    if (
        ref.row_count != metadata.num_rows
        or hashlib.sha256(schema.serialize()).hexdigest() != ref.schema_sha256
    ):
        raise ValueError("dense panel row count or schema hash mismatch")
    with duckdb.connect() as db:
        selected = (
            db.execute(
                """
            SELECT session_date, session_index, open, close, pre_close, adj_factor,
                   up_limit, down_limit, explicitly_suspended AS suspended, is_st,
                   suspend_timing
            FROM read_parquet(?) WHERE instrument_id=? AND venue='SZSE'
              AND session_date BETWEEN ? AND ? ORDER BY session_date
        """,
                [str(parquet), SYMBOL, START_DATE, NEXT_SESSION],
            )
            .to_arrow_table()
            .to_pylist()
        )
    if not selected or selected[-1]["session_date"] != NEXT_SESSION:
        raise ValueError("missing next session in dense panel calendar")
    first_index = selected[0]["session_index"]
    rows: Rows = {}
    for offset, row in enumerate(selected):
        session = row.pop("session_date")
        if row.pop("session_index") != first_index + offset or session in rows:
            raise ValueError("dense panel calendar has missing or duplicate sessions")
        if session == NEXT_SESSION:
            continue
        if session > END_DATE:
            raise ValueError("unexpected next SZSE trading session")
        if row.pop("suspend_timing"):
            raise ValueError(f"unsupported intraday suspension on {session}")
        rows[session] = row
    return rows


def _normalize(rows: Mapping[date, Mapping[str, Any]]) -> tuple[list[DailyPrice], str]:
    prices = []
    normalized = []
    last_close = None
    last_factor = None
    for session, row in sorted(rows.items()):
        values = [Decimal(str(row[f])) if row[f] is not None else None for f in _PRICE_FIELDS]
        if any(value is not None and not value.is_finite() for value in values):
            raise ValueError(f"nonfinite TuShare value on {session}")
        opening, close, prior, factor, upper, lower = values
        suspended = row["suspended"]
        if factor is None or upper is None or lower is None or not 0 < lower < upper or factor <= 0:
            raise ValueError(f"missing or invalid adjustment factor or price limits on {session}")
        if last_factor is not None and last_factor != factor:
            raise ValueError(f"unsupported corporate action (adjustment factor) on {session}")
        last_factor = factor
        if suspended:
            if any(p is not None for p in (opening, close, prior)) or last_close is None:
                raise ValueError(f"reported suspension conflicts with the daily bar on {session}")
        else:
            if opening is None or close is None or prior is None:
                raise ValueError(f"missing daily bar without a reported suspension on {session}")
            if last_close is not None and prior != last_close:
                raise ValueError(f"unsupported corporate action (previous close) on {session}")
            if not lower <= opening <= upper or not lower <= close <= upper or prior <= 0:
                raise ValueError(f"invalid daily bar or price limits on {session}")
            last_close = close
        buy_reason = "suspended" if suspended else "upper_limit" if opening == upper else None
        sell_reason = "suspended" if suspended else "lower_limit" if opening == lower else None
        prices.append(
            DailyPrice(
                session,
                SYMBOL,
                opening,
                close,
                "no" if buy_reason else "yes",
                "no" if sell_reason else "yes",
                buy_reason,
                sell_reason,
                "tushare:stk_limit+suspend_d",
                suspended=suspended,
                adjusted_close=float(close * factor) if close is not None else None,
                buy_blocked_by_price_limit=not suspended and opening == upper,
                sell_blocked_by_price_limit=not suspended and opening == lower,
            )
        )
        normalized.append(
            [
                session.isoformat(),
                *(scaled(x, "provider value") if x is not None else None for x in values),
                suspended,
                row["is_st"],
            ]
        )
    digest = hashlib.sha256(canonical([SYMBOL, NEXT_SESSION.isoformat(), normalized])).hexdigest()
    return prices, digest


def prepare_snapshot(directory: Path, *, panel_manifest: Path | None = None) -> Snapshot:
    """Verify the historical input before publishing a daily SDK snapshot in directory."""
    rows = _panel_rows(panel_manifest) if panel_manifest is not None else _provider_rows()
    prices, digest = _normalize(rows)
    if digest != NORMALIZED_MARKET_SHA256:
        raise ValueError(f"TuShare normalized market hash differs from the reference: {digest}")
    return Snapshot.create(
        directory,
        sessions=[p.session for p in prices],
        next_session=NEXT_SESSION,
        instruments=[Instrument(SYMBOL, venue="szse")],
        daily=prices,
        source_evidence={"provider": "TuShare Pro", "normalized_market_sha256": digest},
    )
