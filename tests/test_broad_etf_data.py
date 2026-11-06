import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from trademaster.e2e import broad_etf_data as data

DAYS = ("20251216", "20251217", "20251218")


def _source() -> dict[str, list[dict[str, Any]]]:
    fund = {"ts_code": "510050.SH"}
    quotes = [
        dict(fund, trade_date=day, open=p, high=p, low=p, close=p, pre_close=prior, vol=200.5)
        for day, p, prior in zip(DAYS, (2, 2.112, 2), (2, 1.92, 2.112), strict=True)
    ]
    dividend = dict(
        fund,
        ann_date="20251210",
        div_proc="实施",
        record_date=DAYS[0],
        ex_date=DAYS[1],
        pay_date=DAYS[2],
        div_cash=0.08,
    )
    return {
        "trade_cal": [
            {"exchange": "SSE", "cal_date": d, "is_open": 1} for d in (*DAYS, "20251219")
        ],
        "fund_daily": quotes,
        "index_daily": [
            {"ts_code": "000016.SH", "trade_date": d, "close": val}
            for d, val in zip(DAYS, (3000, 3154.765, 2999), strict=True)
        ],
        "fund_adj": [
            dict(fund, trade_date=d, adj_factor=a)
            for d, a in zip(DAYS, (1.2, 1.25, 1.25), strict=True)
        ],
        "etf_limit": [dict(fund, trade_date=DAYS[1], up_limit=2.11, down_limit=1.73)],
        "fund_div": [dividend, dividend.copy()],
    }


def _mock_source(monkeypatch: pytest.MonkeyPatch, source: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "test-only")
    monkeypatch.setattr(data, "START_DATE", date.fromisoformat(DAYS[0]))
    monkeypatch.setattr(data, "END_DATE", date.fromisoformat(DAYS[-1]))
    monkeypatch.setattr(data, "NEXT_SESSION", date(2025, 12, 19))

    def query(token: str, endpoint: str, params: dict[str, str], *, fields: str) -> Any:
        assert token == "test-only"
        if endpoint != "trade_cal":
            assert params["ts_code"] == ("000016.SH" if endpoint == "index_daily" else "510050.SH")
        return [{col: row[col] for col in fields.split(",")} for row in reversed(source[endpoint])]

    monkeypatch.setattr(data, "_query", query)


def test_signal_uses_index_points_while_the_account_trades_raw_etf_prices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_source(monkeypatch, _source())
    monkeypatch.setattr(
        data,
        "NORMALIZED_MARKET_SHA256",
        "28f8acfd8a5aaab01b9c182012d7b37044e374fe127b05ba0d67608a3e1ce1f5",
    )
    snapshot = data.prepare_snapshot(tmp_path)
    first, ex, last = (date.fromisoformat(d) for d in DAYS)
    assert snapshot.day(first).scores("sse50_close") == {"510050.SH": 3000}
    assert snapshot.day(ex).scores("sse50_close") == {"510050.SH": 3154.765}
    assert snapshot.day(last).closes("510050.SH") == (2, 2.112, 2)
    market = json.loads(snapshot.market_path.read_text())
    assert len(market["cash_dividends"]) == 1
    assert market["rows"][1]["buy_reason"] == "upper_limit"
    assert market["rows"][1]["buy_blocked_by_price_limit"] is True
    assert "sell_blocked_by_price_limit" not in market["rows"][1]
    assert market["rows"][1]["sell_at_open"] == "yes"
    monkeypatch.setattr(data, "NORMALIZED_MARKET_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="hash"):
        data.prepare_snapshot(tmp_path / "changed")


@pytest.mark.parametrize(
    ("endpoint", "key", "value", "error"),
    [
        ("fund_daily", "vol", 0, "vol"),
        ("fund_daily", "pre_close", 1.8, "previous close"),
        ("fund_adj", "adj_factor", 1.3, "factor"),
        ("etf_limit", "up_limit", 2.3, "limit"),
        ("index_daily", "ts_code", "000001.SH", "instrument"),
    ],
)
def test_source_inconsistencies_fail_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str, key: str, value: Any, error: str
) -> None:
    source = _source()
    source[endpoint][-1 if endpoint == "etf_limit" else 1][key] = value
    _mock_source(monkeypatch, source)
    with pytest.raises(ValueError, match=error):
        data.prepare_snapshot(tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_missing_session_and_conflicting_dividend_rows_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source()
    _mock_source(monkeypatch, source)
    index_row = source["index_daily"].pop()
    with pytest.raises(ValueError, match="calendar"):
        data.prepare_snapshot(tmp_path)
    source["index_daily"].append(index_row)
    source["fund_div"][0]["div_cash"] = 0.05
    with pytest.raises(ValueError, match="conflicting.*dividend"):
        data.prepare_snapshot(tmp_path)
