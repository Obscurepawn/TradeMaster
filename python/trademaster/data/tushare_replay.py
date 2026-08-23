"""Independent replay of raw Tushare rows into canonical staging semantics."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

import pyarrow as pa

from trademaster.contracts import DatasetRequest


def _revision(row: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _compact_date(value: object) -> date:
    raw = str(value)
    if len(raw) != 8 or not raw.isdigit():
        raise ValueError("raw date is not canonical YYYYMMDD")
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))


def _venue(instrument_id: str) -> str:
    try:
        return {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[
            instrument_id.rsplit(".", 1)[1]
        ]
    except (IndexError, KeyError) as error:
        raise ValueError("raw instrument has an unsupported suffix") from error


def _session_key(request: DatasetRequest) -> tuple[str, date, str]:
    if len(request.coverage_keys) != 1:
        raise ValueError("staging replay requires exactly one coverage key")
    coverage_key = request.coverage_keys[0]
    venue, raw_date = coverage_key.split(":", 1)
    return venue, date.fromisoformat(raw_date), coverage_key


def _rows(
    raw_by_endpoint: dict[str, list[dict[str, object]]], endpoint: str
) -> list[dict[str, object]]:
    return raw_by_endpoint.get(endpoint, [])


def validate_tushare_raw_request_scope(
    *,
    request: DatasetRequest,
    raw_requests_by_endpoint: dict[str, list[dict[str, object]]],
    etf_instruments: frozenset[str],
) -> None:
    """Bind every referenced provider request to the partial canonical request."""

    if (
        set(request.instruments) & etf_instruments
        and request.dataset not in {"instrument_master", "daily_bars"}
    ):
        raise ValueError(f"unsupported ETF dataset: {request.dataset}")

    expected_fields = {
        "trade_cal": "exchange,cal_date,is_open,pretrade_date",
        "daily": "ts_code,trade_date,open,high,low,close,vol,amount,pre_close",
        "fund_daily": "ts_code,trade_date,open,high,low,close,vol,amount,pre_close",
        "stk_limit": "ts_code,trade_date,up_limit,down_limit",
        "suspend_d": "ts_code,trade_date,suspend_type",
        "stock_st": "ts_code,trade_date,type",
        "adj_factor": "ts_code,trade_date,adj_factor",
        "daily_basic": "ts_code,trade_date,turnover_rate,total_mv,circ_mv",
        "index_daily": "ts_code,trade_date,open,high,low,close,vol,amount",
        "stock_basic": "ts_code,exchange,market,curr_type,list_date,delist_date",
        "etf_basic": "ts_code,exchange,list_date,list_status",
        "index_weight": "index_code,con_code,trade_date,weight",
        "index_member_all": "l1_code,l2_code,l3_code,ts_code,in_date,out_date",
        "fina_indicator": "",
    }
    for endpoint, payloads in raw_requests_by_endpoint.items():
        for payload in payloads:
            if payload.get("fields") != expected_fields[endpoint]:
                raise ValueError("raw provider fields do not match endpoint contract")
            params = payload.get("params")
            if not isinstance(params, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in params.items()
            ):
                raise ValueError("raw provider params are not canonical strings")
            if request.dataset in {
                "trade_calendar",
                "daily_bars",
                "daily_limits_status",
                "adj_factors",
                "daily_basic",
                "index_bars",
            }:
                venue, session_date, _ = _session_key(request)
                raw_date = session_date.strftime("%Y%m%d")
            if endpoint == "trade_cal":
                expected = {
                    "exchange": venue,
                    "start_date": raw_date,
                    "end_date": raw_date,
                }
                valid = params == expected
            elif endpoint in {"daily", "stk_limit", "stock_st", "adj_factor", "daily_basic"}:
                valid = params == {"trade_date": raw_date}
            elif endpoint == "suspend_d":
                valid = params == {"trade_date": raw_date, "suspend_type": "S"}
            elif endpoint in {"fund_daily", "index_daily"}:
                valid = (
                    params.get("ts_code") in request.instruments
                    and params == {"ts_code": params.get("ts_code"), "trade_date": raw_date}
                )
                if endpoint == "fund_daily":
                    valid = valid and params.get("ts_code") in etf_instruments
            elif endpoint in {"stock_basic", "etf_basic"}:
                instrument_id = request.coverage_keys[0].rsplit(":", 1)[0]
                if endpoint == "stock_basic":
                    valid = params == {
                        "ts_code": instrument_id,
                        "list_status": params.get("list_status"),
                    } and params.get("list_status") in {"L", "D", "P", "G"}
                    valid = valid and instrument_id not in etf_instruments
                else:
                    valid = params == {"ts_code": instrument_id}
                    valid = valid and instrument_id in etf_instruments
            elif endpoint == "index_weight":
                index_id, raw_effective = request.coverage_keys[0].rsplit(":", 1)
                valid = params == {
                    "index_code": index_id,
                    "trade_date": date.fromisoformat(raw_effective).strftime("%Y%m%d"),
                }
            elif endpoint == "index_member_all":
                valid = params == {"ts_code": params.get("ts_code")} and params.get(
                    "ts_code"
                ) in request.instruments
            elif endpoint == "fina_indicator":
                instrument_id, raw_period, _ = request.coverage_keys[0].split(":")
                valid = params == {
                    "ts_code": instrument_id,
                    "period": date.fromisoformat(raw_period).strftime("%Y%m%d"),
                }
            else:
                valid = False
            if not valid:
                raise ValueError("raw provider request scope differs from canonical request")


def _replay_trade_calendar(
    request: DatasetRequest,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
    normalized_at: datetime,
) -> list[dict[str, object]]:
    venue, session_date, _ = _session_key(request)
    raw_date = session_date.strftime("%Y%m%d")
    matches = [
        row
        for row in _rows(raw_by_endpoint, "trade_cal")
        if str(row.get("exchange")) == venue
        and str(row.get("cal_date")) == raw_date
    ]
    if len(matches) != 1:
        raise ValueError("raw trade calendar does not uniquely cover staging")
    row = matches[0]
    return [
        {
            "venue": venue,
            "session_date": session_date,
            "is_open": bool(int(cast(Any, row["is_open"]))),
            "open_at": datetime(
                session_date.year, session_date.month, session_date.day, 1, 30, tzinfo=UTC
            ),
            "close_at": datetime(
                session_date.year, session_date.month, session_date.day, 7, tzinfo=UTC
            ),
            "event_time": datetime(
                session_date.year, session_date.month, session_date.day, 7, tzinfo=UTC
            ),
            "known_at": normalized_at,
            "source_revision": _revision(row),
        }
    ]


def _replay_daily_bars(
    request: DatasetRequest,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
    etf_instruments: frozenset[str],
) -> list[dict[str, object]]:
    venue, trade_date, _ = _session_key(request)
    raw_date = trade_date.strftime("%Y%m%d")
    result: list[dict[str, object]] = []
    for endpoint in ("daily", "fund_daily"):
        for row in _rows(raw_by_endpoint, endpoint):
            instrument_id = str(row.get("ts_code"))
            if (endpoint == "daily") == (instrument_id in etf_instruments):
                raise ValueError("raw daily bar endpoint conflicts with ETF identity")
            if (
                str(row.get("trade_date")) != raw_date
                or _venue(instrument_id) != venue
                or (request.instruments and instrument_id not in request.instruments)
            ):
                continue
            result.append(
                {
                    "instrument_id": instrument_id,
                    "trade_date": trade_date,
                    "open": float(cast(Any, row["open"])),
                    "high": float(cast(Any, row["high"])),
                    "low": float(cast(Any, row["low"])),
                    "close": float(cast(Any, row["close"])),
                    "volume": float(cast(Any, row["vol"])),
                    "amount": float(cast(Any, row["amount"])),
                    "pre_close": float(cast(Any, row["pre_close"])),
                    "venue": venue,
                    "event_time": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        7,
                        tzinfo=UTC,
                    ),
                    "known_at": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        8,
                        tzinfo=UTC,
                    ),
                    "source_revision": _revision(row),
                }
            )
    return result


def _replay_simple_session(
    request: DatasetRequest,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    venue, trade_date, _ = _session_key(request)
    raw_date = trade_date.strftime("%Y%m%d")
    endpoint = {
        "adj_factors": "adj_factor",
        "daily_basic": "daily_basic",
        "index_bars": "index_daily",
    }[request.dataset]
    result: list[dict[str, object]] = []
    for row in _rows(raw_by_endpoint, endpoint):
        instrument_id = str(row.get("ts_code"))
        if (
            str(row.get("trade_date")) != raw_date
            or _venue(instrument_id) != venue
            or (request.instruments and instrument_id not in request.instruments)
        ):
            continue
        event_hour = 1 if request.dataset == "adj_factors" else 7
        event_minute = 20 if request.dataset == "adj_factors" else 0
        known_hour = {"adj_factors": 1, "daily_basic": 12, "index_bars": 8}[
            request.dataset
        ]
        known_minute = 20 if request.dataset == "adj_factors" else 0
        normalized: dict[str, object] = {
            "instrument_id": instrument_id,
            "trade_date": trade_date,
            "venue": venue,
            "event_time": datetime(
                trade_date.year,
                trade_date.month,
                trade_date.day,
                event_hour,
                event_minute,
                tzinfo=UTC,
            ),
            "known_at": datetime(
                trade_date.year,
                trade_date.month,
                trade_date.day,
                known_hour,
                known_minute,
                tzinfo=UTC,
            ),
            "source_revision": _revision(row),
        }
        if request.dataset == "adj_factors":
            normalized["adj_factor"] = float(cast(Any, row["adj_factor"]))
        elif request.dataset == "daily_basic":
            normalized.update(
                float_market_value=float(cast(Any, row["circ_mv"])),
                total_market_value=float(cast(Any, row["total_mv"])),
                turnover_rate=float(cast(Any, row["turnover_rate"])),
            )
        else:
            normalized.update(
                open=float(cast(Any, row["open"])),
                high=float(cast(Any, row["high"])),
                low=float(cast(Any, row["low"])),
                close=float(cast(Any, row["close"])),
                volume=float(cast(Any, row["vol"])),
                amount=float(cast(Any, row["amount"])),
            )
        result.append(normalized)
    return result


def _replay_status(
    request: DatasetRequest,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    venue, trade_date, _ = _session_key(request)
    raw_date = trade_date.strftime("%Y%m%d")
    suspended = {
        str(row.get("ts_code"))
        for row in _rows(raw_by_endpoint, "suspend_d")
        if str(row.get("suspend_type")) == "S"
        and str(row.get("trade_date")) == raw_date
    }
    st_instruments = {
        str(row.get("ts_code"))
        for row in _rows(raw_by_endpoint, "stock_st")
        if str(row.get("trade_date")) == raw_date
    }
    result: list[dict[str, object]] = []
    for row in _rows(raw_by_endpoint, "stk_limit"):
        instrument_id = str(row.get("ts_code"))
        if (
            str(row.get("trade_date")) != raw_date
            or _venue(instrument_id) != venue
            or (request.instruments and instrument_id not in request.instruments)
        ):
            continue
        status_payload = dict(row)
        status_payload.update(
            suspended=instrument_id in suspended,
            is_st=instrument_id in st_instruments,
        )
        result.append(
            {
                "instrument_id": instrument_id,
                "trade_date": trade_date,
                "up_limit": float(cast(Any, row["up_limit"])),
                "down_limit": float(cast(Any, row["down_limit"])),
                "suspended": instrument_id in suspended,
                "is_st": instrument_id in st_instruments,
                "venue": venue,
                "event_time": datetime(
                    trade_date.year,
                    trade_date.month,
                    trade_date.day,
                    1,
                    20,
                    tzinfo=UTC,
                ),
                "known_at": datetime(
                    trade_date.year,
                    trade_date.month,
                    trade_date.day,
                    1,
                    20,
                    tzinfo=UTC,
                ),
                "source_revision": _revision(status_payload),
            }
        )
    return result


def _replay_instrument_master(
    request: DatasetRequest,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
    etf_instruments: frozenset[str],
) -> list[dict[str, object]]:
    coverage_key = request.coverage_keys[0]
    instrument_id, raw_effective = coverage_key.rsplit(":", 1)
    effective_from = date.fromisoformat(raw_effective)
    event_time = datetime(
        effective_from.year, effective_from.month, effective_from.day, tzinfo=UTC
    )
    is_etf = instrument_id in etf_instruments
    if is_etf != bool(_rows(raw_by_endpoint, "etf_basic")):
        raise ValueError("raw instrument endpoint conflicts with ETF identity")
    if is_etf and _rows(raw_by_endpoint, "stock_basic"):
        raise ValueError("ETF master cannot use stock endpoint evidence")
    if not is_etf and _rows(raw_by_endpoint, "etf_basic"):
        raise ValueError("stock master cannot use ETF endpoint evidence")
    if _rows(raw_by_endpoint, "etf_basic"):
        matches = [
            row
            for row in _rows(raw_by_endpoint, "etf_basic")
            if str(row.get("ts_code")) == instrument_id
            and str(row.get("list_date")) == effective_from.strftime("%Y%m%d")
        ]
        if len(matches) != 1:
            raise ValueError("raw ETF master does not uniquely cover staging")
        row = matches[0]
        raw_venue = str(row.get("exchange"))
        venue = {"SH": "SSE", "SZ": "SZSE"}.get(raw_venue)
        if venue is None or venue != _venue(instrument_id) or row.get("list_status") != "L":
            raise ValueError("raw ETF master venue/lifecycle is invalid")
        return [
            {
                "instrument_id": instrument_id,
                "effective_from": effective_from,
                "venue": venue,
                "asset_class": "etf",
                "list_date": effective_from,
                "delist_date": date(9999, 12, 31),
                "event_time": event_time,
                "known_at": event_time,
                "source_revision": _revision(row),
            }
        ]
    unique = {
        json.dumps(row, sort_keys=True, default=str): row
        for row in _rows(raw_by_endpoint, "stock_basic")
    }
    matches = [
        row
        for row in unique.values()
        if str(row.get("ts_code")) == instrument_id
        and (
            str(row.get("list_date")) == effective_from.strftime("%Y%m%d")
            or str(row.get("delist_date")) == effective_from.strftime("%Y%m%d")
        )
    ]
    if len(matches) != 1:
        raise ValueError("raw stock master does not uniquely cover staging")
    row = matches[0]
    list_date = _compact_date(row["list_date"])
    raw_delist = row.get("delist_date")
    announced_delist = (
        _compact_date(raw_delist) if raw_delist not in (None, "") else None
    )
    if effective_from == list_date:
        delist_date = date(9999, 12, 31)
        revision_payload = {key: value for key, value in row.items() if key != "delist_date"}
    elif announced_delist == effective_from:
        delist_date = effective_from
        revision_payload = row
    else:
        raise ValueError("raw stock master event does not match staging")
    return [
        {
            "instrument_id": instrument_id,
            "effective_from": effective_from,
            "venue": str(row.get("exchange")) or _venue(instrument_id),
            "asset_class": "stock",
            "list_date": list_date,
            "delist_date": delist_date,
            "event_time": event_time,
            "known_at": event_time,
            "source_revision": _revision(revision_payload),
        }
    ]


def _replay_membership(
    request: DatasetRequest,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
    normalized_at: datetime,
) -> list[dict[str, object]]:
    identity, raw_effective = request.coverage_keys[0].rsplit(":", 1)
    effective_from = date.fromisoformat(raw_effective)
    raw_date = effective_from.strftime("%Y%m%d")
    event_time = datetime(
        effective_from.year, effective_from.month, effective_from.day, tzinfo=UTC
    )
    if request.dataset == "index_membership":
        return [
            {
                "index_id": identity,
                "instrument_id": str(row.get("con_code")),
                "effective_from": effective_from,
                "effective_to": effective_from,
                "weight": float(cast(Any, row["weight"])),
                "event_time": event_time,
                "known_at": normalized_at,
                "source_revision": _revision(row),
            }
            for row in _rows(raw_by_endpoint, "index_weight")
            if str(row.get("index_code")) == identity
            and str(row.get("trade_date")) == raw_date
        ]
    if identity != "SW2021":
        raise ValueError("raw industry taxonomy is unsupported")
    return [
        {
            "taxonomy": identity,
            "instrument_id": str(row.get("ts_code")),
            "effective_from": effective_from,
            "industry_id": str(row.get("l3_code")),
            "effective_to": (
                _compact_date(row["out_date"])
                if row.get("out_date") not in (None, "")
                else date(9999, 12, 31)
            ),
            "event_time": event_time,
            "known_at": normalized_at,
            "source_revision": _revision(row),
        }
        for row in _rows(raw_by_endpoint, "index_member_all")
        if str(row.get("ts_code")) in request.instruments
        and str(row.get("in_date")) == raw_date
        and row.get("l3_code") not in (None, "")
    ]


def _replay_financial(
    request: DatasetRequest,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    instrument_id, raw_period, raw_announcement = request.coverage_keys[0].split(":")
    report_period = date.fromisoformat(raw_period)
    announcement_date = date.fromisoformat(raw_announcement)
    result: list[dict[str, object]] = []
    for row in _rows(raw_by_endpoint, "fina_indicator"):
        if (
            str(row.get("ts_code")) != instrument_id
            or str(row.get("end_date")) != report_period.strftime("%Y%m%d")
            or str(row.get("ann_date")) != announcement_date.strftime("%Y%m%d")
        ):
            continue
        values = {
            key: value
            for key, value in row.items()
            if key not in {"ts_code", "end_date", "ann_date", "update_flag"}
        }
        result.append(
            {
                "instrument_id": instrument_id,
                "report_period": report_period,
                "announcement_id": announcement_date.isoformat(),
                "revision": str(row.get("update_flag") or _revision(row)),
                "report_values_json": json.dumps(
                    values,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
                "event_time": datetime(
                    report_period.year,
                    report_period.month,
                    report_period.day,
                    23,
                    59,
                    tzinfo=UTC,
                ),
                "known_at": datetime(
                    announcement_date.year,
                    announcement_date.month,
                    announcement_date.day,
                    16,
                    tzinfo=UTC,
                ),
                "source_revision": _revision(row),
            }
        )
    return result


def validate_tushare_staging_replay(
    *,
    request: DatasetRequest,
    staging_table: pa.Table,
    raw_by_endpoint: dict[str, list[dict[str, object]]],
    normalized_at: datetime,
    schema: pa.Schema,
    primary_key: tuple[str, ...],
    etf_instruments: frozenset[str],
) -> None:
    """Recompute canonical rows from referenced raw rows and compare exactly."""

    if request.dataset == "trade_calendar":
        rows = _replay_trade_calendar(request, raw_by_endpoint, normalized_at)
    elif request.dataset == "daily_bars":
        rows = _replay_daily_bars(request, raw_by_endpoint, etf_instruments)
    elif request.dataset in {"adj_factors", "daily_basic", "index_bars"}:
        rows = _replay_simple_session(request, raw_by_endpoint)
    elif request.dataset == "daily_limits_status":
        rows = _replay_status(request, raw_by_endpoint)
    elif request.dataset == "instrument_master":
        rows = _replay_instrument_master(request, raw_by_endpoint, etf_instruments)
    elif request.dataset in {"index_membership", "industry_membership"}:
        rows = _replay_membership(request, raw_by_endpoint, normalized_at)
    elif request.dataset == "financial_indicators":
        rows = _replay_financial(request, raw_by_endpoint)
    else:
        raise ValueError("dataset has no Tushare normalization replay")
    expected = pa.Table.from_pylist(rows, schema=schema).combine_chunks()
    actual = staging_table.replace_schema_metadata(None).combine_chunks()
    sort_keys: list[tuple[str, Literal["ascending", "descending"]]] = [
        (name, "ascending") for name in primary_key + ("source_revision",)
    ]
    if expected.num_rows:
        expected = expected.sort_by(sort_keys)
        actual = actual.sort_by(sort_keys)
    if not expected.equals(actual, check_metadata=False):
        raise ValueError("raw normalization does not reproduce staging content")
