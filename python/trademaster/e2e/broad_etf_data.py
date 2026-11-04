"""Prepare the 2011–2025 SSE 50 index signal and ChinaAMC 50 ETF market snapshot."""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from trademaster.backtest import CashDividend, DailyPrice, Feature, Instrument, Snapshot
from trademaster.backtest.snapshot import canonical, scaled
from trademaster.e2e.long_history_data import END_DATE, NEXT_SESSION, START_DATE, _query

SYMBOL = "510050.SH"
INDEX = "000016.SH"
INDEX_FEATURE = "sse50_close"
NORMALIZED_MARKET_SHA256 = "338ca9b7be774622c5cde9c2d8b341bd7fdca8ccd7f1700cbc60e2eeda43bec7"

_TICK = Decimal("0.001")
_CENT = Decimal("0.01")
_LIMIT_RULES = (
    "https://www.sse.com.cn/disclosure/fund/announcement/c/2021-01-22/510050_20210123_6.pdf"
)
_FIELDS = {
    "trade_cal": "exchange,cal_date,is_open",
    "fund_daily": "ts_code,trade_date,open,high,low,close,pre_close,vol",
    "fund_adj": "ts_code,trade_date,adj_factor",
    "index_daily": "ts_code,trade_date,close",
    "etf_limit": "ts_code,trade_date,up_limit,down_limit",
    "fund_div": "ts_code,ann_date,div_proc,record_date,ex_date,pay_date,div_cash",
}

type Row = dict[str, Any]
type Quotes = dict[date, Row]


def _positive(row: Row, key: str) -> Decimal:
    try:
        value = Decimal(str(row[key]))
    except (InvalidOperation, KeyError) as exc:
        raise ValueError(f"missing or invalid {key}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{key} must be positive and finite")
    return value


def _provider_rows(token: str) -> tuple[list[date], dict[str, Quotes], list[Row]]:
    sessions: list[date] = []
    series: dict[str, Quotes] = {
        ep: {} for ep in ("fund_daily", "index_daily", "fund_adj", "etf_limit")
    }
    # Three trading years fit in one response from each daily endpoint.
    for year in range(START_DATE.year, END_DATE.year + 1, 3):
        start = max(START_DATE, date(year, 1, 1))
        end = min(END_DATE, date(year + 2, 12, 31))
        span = {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
        last = NEXT_SESSION if end == END_DATE else end
        calendar = _query(
            token,
            "trade_cal",
            {**span, "end_date": last.strftime("%Y%m%d"), "exchange": "SSE", "is_open": "1"},
            fields=_FIELDS["trade_cal"],
        )
        days = set()
        for row in calendar:
            day = date.fromisoformat(row["cal_date"])
            if (
                row["exchange"] != "SSE"
                or str(row["is_open"]) != "1"
                or day in days
                or not start <= day <= last
            ):
                raise ValueError("unexpected SSE calendar observation")
            days.add(day)
        if end == END_DATE:
            if sorted(d for d in days if d > end) != [NEXT_SESSION]:
                raise ValueError("missing or unexpected next SSE calendar session")
            days.remove(NEXT_SESSION)
        sessions.extend(sorted(days))

        for endpoint, quotes in series.items():
            code = INDEX if endpoint == "index_daily" else SYMBOL
            records = _query(token, endpoint, {**span, "ts_code": code}, fields=_FIELDS[endpoint])
            for row in records:
                day = date.fromisoformat(row["trade_date"])
                if row["ts_code"] != code or day not in days or day in quotes:
                    raise ValueError(
                        f"unexpected {endpoint} instrument or calendar session on {day}"
                    )
                quotes[day] = row
            if endpoint != "etf_limit" and len(records) != len(days):
                raise ValueError(f"{endpoint} does not cover the SSE calendar")
    if not sessions or sessions[0] != START_DATE or sessions[-1] != END_DATE:
        raise ValueError("SSE calendar is missing a sample boundary")
    distributions = _query(token, "fund_div", {"ts_code": SYMBOL}, fields=_FIELDS["fund_div"])
    return sessions, series, distributions


def _cash_dividends(rows: list[Row], sessions: list[date]) -> dict[date, CashDividend]:
    previous_session = dict(zip(sessions[1:], sessions[:-1], strict=True))
    dividends: dict[date, CashDividend] = {}
    calendar = set(sessions)
    for row in rows:
        if row["ts_code"] != SYMBOL:
            raise ValueError("unexpected dividend instrument")
        if row["div_proc"] != "实施":
            continue
        ex = date.fromisoformat(row["ex_date"])
        if not START_DATE <= ex <= END_DATE:
            continue
        record, paid = (date.fromisoformat(row[f]) for f in ("record_date", "pay_date"))
        announced = date.fromisoformat(row["ann_date"])
        if (
            announced > record
            or previous_session.get(ex) != record
            or paid not in calendar
            or paid < ex
        ):
            raise ValueError(f"invalid dividend schedule on {ex}")
        event = CashDividend(SYMBOL, record, ex, paid, _positive(row, "div_cash"))
        # fund_div can publish duplicate rows for one on-exchange payment.
        if dividends.setdefault(ex, event) != event:
            raise ValueError(f"conflicting cash dividend on {ex}")
    return dividends


def _limits(ref: Decimal, reported: Row | None) -> tuple[Decimal, Decimal, str]:
    upper, lower = (
        (ref * ratio).quantize(_TICK, rounding=ROUND_HALF_UP)
        for ratio in (Decimal("1.1"), Decimal("0.9"))
    )
    if reported is None:
        return upper, lower, "sse_rule"
    values = (_positive(reported, "up_limit"), _positive(reported, "down_limit"))
    if values == (upper, lower):
        return upper, lower, "etf_limit"
    # Early provider limits are rounded to cents. Recover the finer exchange tick
    # only if both observations corroborate the rule at their published precision.
    if all(
        v % _CENT == 0 and abs(v - expected) <= _CENT / 2
        for v, expected in zip(values, (upper, lower), strict=True)
    ):
        return upper, lower, "etf_limit_cent+sse_rule"
    raise ValueError(f"ETF limits conflict with the exchange rule on {reported['trade_date']}")


def prepare_snapshot(directory: Path) -> Snapshot:
    """Fetch and verify the reference inputs, then publish a reusable daily snapshot."""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise ValueError("set TUSHARE_TOKEN to prepare the ETF snapshot")
    sessions, series, records = _provider_rows(token)
    dividends = _cash_dividends(records, sessions)
    prices = []
    features = []
    normalized = []
    previous_close = previous_factor = None
    for day in sessions:
        row = series["fund_daily"][day]
        opening, high, low, close, ref, volume = (
            _positive(row, key) for key in ("open", "high", "low", "close", "pre_close", "vol")
        )
        factor = _positive(series["fund_adj"][day], "adj_factor")
        index = _positive(series["index_daily"][day], "close")
        dividend = dividends.get(day)
        if previous_close is not None and previous_factor is not None:
            cash = Decimal(str(dividend.amount_per_share)) if dividend else Decimal(0)
            if (previous_close - cash).quantize(_TICK, rounding=ROUND_HALF_UP) != ref:
                raise ValueError(f"ETF previous close disagrees with the cash dividend on {day}")
            # TuShare publishes fund_adj to four fractional digits. Both rounded
            # factors must allow the same continuous value across the ex-date.
            tolerance = (previous_close + ref) * Decimal("0.00005")
            if (not dividend and previous_factor != factor) or abs(
                ref * factor - previous_close * previous_factor
            ) > tolerance:
                raise ValueError(f"unexplained ETF adjustment factor on {day}")
        previous_close, previous_factor = close, factor
        upper, lower, evidence = _limits(ref, series["etf_limit"].get(day))
        if not lower <= low <= min(opening, close) <= max(opening, close) <= high <= upper or any(
            p % _TICK for p in (low, high, opening, close, ref)
        ):
            raise ValueError(f"invalid ETF bar or tick on {day}")
        prices.append(
            DailyPrice(
                day,
                SYMBOL,
                opening,
                close,
                buy_at_open="no" if opening == upper else "yes",
                sell_at_open="no" if opening == lower else "yes",
                buy_reason="upper_limit" if opening == upper else None,
                sell_reason="lower_limit" if opening == lower else None,
                evidence=f"tushare:fund_daily+{evidence}",
            )
        )
        when = datetime.combine(day, time(15), timezone(timedelta(hours=8)))
        features.append(Feature(SYMBOL, INDEX_FEATURE, float(index), when))
        normalized.append(
            [
                day.isoformat(),
                *[
                    scaled(v, "source observation")
                    for v in (opening, high, low, close, ref, volume, factor, index, upper, lower)
                ],
                evidence,
            ]
        )

    distributions = [dividends[d] for d in sorted(dividends)]
    dividend_rows = [
        [
            d.record_session.isoformat(),
            d.ex_session.isoformat(),
            d.pay_session.isoformat(),
            scaled(d.amount_per_share, "dividend"),
        ]
        for d in distributions
    ]
    digest = hashlib.sha256(
        canonical([SYMBOL, INDEX, NEXT_SESSION.isoformat(), normalized, dividend_rows])
    ).hexdigest()
    if digest != NORMALIZED_MARKET_SHA256:
        raise ValueError(
            f"TuShare normalized ETF/index market hash differs from the reference: {digest}"
        )
    return Snapshot.create(
        directory,
        sessions=sessions,
        next_session=NEXT_SESSION,
        instruments=[
            Instrument(
                SYMBOL, venue="sse", asset_class="etf", tick_size=_TICK, listed_on=date(2005, 2, 23)
            )
        ],
        daily=prices,
        features=features,
        cash_dividends=distributions,
        source_evidence={
            "provider": "TuShare Pro: SSE trade_cal, fund_daily, fund_adj, fund_div, etf_limit, index_daily",
            "signal": f"{INDEX} closing index points at 15:00 Asia/Shanghai",
            "execution": f"{SYMBOL} raw exchange prices in CNY per ETF unit",
            "opening_limits": "10% of the ex-adjusted reference close rounded half up to CNY 0.001; etf_limit checked where available",
            "opening_limit_rule": _LIMIT_RULES,
            "normalized_market_sha256": digest,
        },
    )
