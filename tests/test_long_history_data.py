import io
import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest
from trademaster.e2e import long_history_data as history


def test_only_confirmed_full_day_suspensions_can_be_carried() -> None:
    first, halt, resume = (date(2022, 6, d) for d in (10, 13, 14))
    quote: dict[str, Any] = {
        "open": 5.6,
        "close": 5.6,
        "pre_close": 5.6,
        "adj_factor": 2.553,
        "up_limit": 6.16,
        "down_limit": 5.04,
        "suspended": False,
        "is_st": False,
    }
    no_bar = dict(quote, open=None, close=None, pre_close=None)
    rows: history.Rows = {
        first: quote,
        halt: dict(no_bar, suspended=True),
        resume: dict(quote, open=5.04),
    }
    prices, _ = history._normalize(rows)
    assert [(p.open, p.close, p.suspended) for p in prices][1] == (None, None, True)
    assert prices[1].adjusted_close is None
    assert prices[1].buy_reason == prices[1].sell_reason == "suspended"
    assert prices[2].buy_at_open == "yes"
    assert prices[2].sell_reason == "lower_limit"
    with pytest.raises(ValueError, match="missing.*bar"):
        history._normalize({first: quote, halt: no_bar})
    for key, value in (("adj_factor", 2.554), ("pre_close", 5.5)):
        with pytest.raises(ValueError, match="corporate action"):
            history._normalize(
                {first: quote, halt: rows[halt], resume: dict(quote, **{key: value})}
            )


def test_provider_path_requires_credential_only_when_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
        history.prepare_snapshot(tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_online_requests_are_scoped_and_pin_the_same_market_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, halt, resume, after = (date(2022, 6, d) for d in (10, 13, 14, 15))
    monkeypatch.setattr(history, "START_DATE", first)
    monkeypatch.setattr(history, "END_DATE", resume)
    monkeypatch.setattr(history, "NEXT_SESSION", after)
    monkeypatch.setenv("TUSHARE_TOKEN", "test-provider-credential")
    market_days = (first, halt, resume)
    raw: dict[str, list[dict[str, Any]]] = {
        "trade_cal": [
            {"exchange": "SZSE", "cal_date": d.strftime("%Y%m%d"), "is_open": 1}
            for d in (*market_days, after)
        ],
        "daily": [
            {
                "ts_code": history.SYMBOL,
                "trade_date": d.strftime("%Y%m%d"),
                "open": 5.6,
                "close": 5.6,
                "pre_close": 5.6,
            }
            for d in (first, resume)
        ],
        "suspend_d": [
            {
                "ts_code": history.SYMBOL,
                "trade_date": "20220613",
                "suspend_type": "S",
                "suspend_timing": None,
            }
        ],
        "stock_st": [],
    }
    for endpoint, values in (
        ("adj_factor", {"adj_factor": 2.553}),
        ("stk_limit", {"up_limit": 6.16, "down_limit": 5.04}),
    ):
        raw[endpoint] = [
            {"ts_code": history.SYMBOL, "trade_date": d.strftime("%Y%m%d")} | values
            for d in market_days
        ]

    calls = []

    def respond(request: Request, *, timeout: int) -> io.BytesIO:
        assert request.full_url == "https://api.tushare.pro" and isinstance(request.data, bytes)
        data = json.loads(request.data)
        name, fields, params = data["api_name"], data["fields"].split(","), data["params"]
        assert data["token"] == "test-provider-credential" and timeout > 0
        assert params["start_date"] == "20220610"
        if name == "trade_cal":
            assert params["exchange"] == "SZSE" and params["end_date"] == "20220615"
        else:
            assert params["ts_code"] == history.SYMBOL and params["end_date"] == "20220614"
        calls.append(name)
        # TuShare names its response columns and may return rows in descending order.
        fields.reverse()
        items = [[r[f] for f in fields] for r in reversed(raw[name])]
        return io.BytesIO(
            json.dumps({"code": 0, "data": {"fields": fields, "items": items}}).encode()
        )

    monkeypatch.setattr(history, "urlopen", respond)
    raw_quote: dict[str, Any] = {
        "open": 5.6,
        "close": 5.6,
        "pre_close": 5.6,
        "adj_factor": 2.553,
        "up_limit": 6.16,
        "down_limit": 5.04,
        "suspended": False,
        "is_st": False,
    }
    independent = dict(raw_quote, open=None, close=None, pre_close=None, suspended=True)
    _, expected_digest = history._normalize(
        {first: raw_quote, halt: independent, resume: raw_quote}
    )
    monkeypatch.setattr(history, "NORMALIZED_MARKET_SHA256", expected_digest)
    snapshot = history.prepare_snapshot(tmp_path)
    assert set(calls) == set(raw) and len(calls) == len(raw)
    assert snapshot.sessions == market_days and snapshot.next_session == after
    assert snapshot.day(resume).closes(history.SYMBOL) == (5.6, None, 5.6)
    assert snapshot.day(resume).closes(history.SYMBOL, adjusted=True) == (14.2968, None, 14.2968)
    manifest = (tmp_path / "manifest.json").read_text()
    assert expected_digest in manifest and "test-provider-credential" not in manifest
    raw["stk_limit"][0]["up_limit"] = 6.17
    with pytest.raises(ValueError, match="normalized market hash"):
        history.prepare_snapshot(tmp_path / "changed")
