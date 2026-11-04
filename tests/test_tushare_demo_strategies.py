"""Exercise the published demo command with real Tushare observations and the Rust runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import urllib3
from urllib3.util import Retry, Timeout

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("TRADEMASTER_DEMO_DATA_ROOT", ROOT / "data/research/full-a-20y"))
RECORDED_PLAN = "6ce15c56c759a79f635af3ac489bbeb624620670e4f4cd323eb80935ed17a633"
PLAN = os.environ.get("TRADEMASTER_DEMO_PLAN", RECORDED_PLAN)
UNIT = 10**8


@pytest.fixture(scope="module")
def real_runs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not (DATA / "plans" / PLAN / "manifest.json").is_file():
        pytest.skip("the real Tushare demo plan is absent")
    output = tmp_path_factory.mktemp("real-demo")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "trademaster.e2e.demo_strategies",
            "--data-root",
            str(DATA),
            "--plan",
            PLAN,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
    )
    return output


def _runs(output: Path) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    for name in ("cross-sectional-momentum", "moving-average", "macd"):
        run = output / name
        assert (run / "report.html").stat().st_size > 0
        yield (
            name,
            json.loads((run / "runner-request.json").read_bytes()),
            json.loads((run / "runner-result.json").read_bytes()),
        )


def test_three_real_strategies_reconcile_fills_and_cash(real_runs: Path) -> None:
    summary = json.loads((real_runs / "summary.json").read_bytes())
    traded: dict[str, list[tuple[str, str]]] = {}
    for name, request, result in _runs(real_runs):
        instruments = {row["instrument_id"] for row in request["instruments"]}
        assert len(instruments) == (5 if name == "cross-sectional-momentum" else 1)
        by_day = [e for e in request["events"] if e["kind"] == "bar_close"]
        opens = [e for e in request["events"] if e["kind"] == "session_open"]
        next_open = {
            close["event_time"]: opening["event_time"] for close, opening in zip(by_day, opens)
        }
        assert all(
            next_open[s["signal_time"]] == s["eligible_execution_time"] for s in request["signals"]
        )
        prices = {
            (e["event_time"], b["instrument_id"]): b["open_scaled"]
            for e in opens
            for b in e["bars"]
        }
        eligible = {s["signal_id"]: s["eligible_execution_time"] for s in request["signals"]}
        order_time = {o["order_id"]: eligible[o["signal_id"]] for o in result["orders"]}
        fills = result["fills"]
        assert all(f["event_time"] == order_time[f["order_id"]] for f in fills)
        assert {f["side"] for f in fills} == {"buy", "sell"}
        assert all(f["price_scaled"] == prices[f["event_time"], f["instrument_id"]] for f in fills)
        traded[name] = [(f["side"], f["event_time"]) for f in fills]
        cash = int(request["initial_cash_scaled"])
        for f in fills:
            sign = 1 if f["side"] == "sell" else -1
            costs = sum(
                int(f[k])
                for k in (
                    "commission_scaled",
                    "tax_scaled",
                    "transfer_fee_scaled",
                    "slippage_scaled",
                )
            )
            cash += sign * int(f["quantity"]) * int(f["price_scaled"]) - costs
        final = result["nav"][-1]
        assert int(final["cash_scaled"]) == cash
        assert Decimal(summary["strategies"][name]["ending_equity"]) * UNIT == int(
            final["net_asset_value_scaled"]
        )
    assert traded["moving-average"] != traded["macd"]
    if PLAN == RECORDED_PLAN:
        published = {
            "cross-sectional-momentum": (16, 15, "104666.94"),
            "moving-average": (1, 1, "107115.45"),
            "macd": (2, 2, "106825.69"),
        }
        assert {
            name: (row["buys"], row["sells"], row["ending_equity"])
            for name, row in summary["strategies"].items()
        } == published


@pytest.mark.skipif(not os.getenv("TUSHARE_TOKEN"), reason="TUSHARE_TOKEN is absent")
def test_daily_bars_match_live_tushare(real_runs: Path) -> None:
    # Query the official TLS endpoint. Only market fields reach an assertion or report.
    _, request, _ = next(_runs(real_runs))
    closes = [event for event in request["events"] if event["kind"] == "bar_close"]
    fields = ("trade_date", "open", "high", "low", "close", "vol")
    # These are read-only queries. Retry temporary gateway and rate-limit responses.
    retry = Retry(
        total=3,
        allowed_methods={"POST"},
        status_forcelist=(429, 502, 503, 504),
        backoff_factor=1,
        raise_on_status=False,
    )
    with urllib3.PoolManager(retries=retry, timeout=Timeout(connect=5, read=30)) as http:
        for instrument in request["instruments"]:
            symbol = instrument["instrument_id"]
            response = http.request(
                "POST",
                "https://api.tushare.pro/",
                json={
                    "api_name": "daily",
                    "token": os.environ["TUSHARE_TOKEN"],
                    "fields": ",".join(fields),
                    "params": {"ts_code": symbol, "start_date": "20240102", "end_date": "20240531"},
                },
                redirect=False,
            )
            if response.status != 200:
                pytest.fail(
                    f"Tushare daily returned HTTP {response.status} for {symbol}", pytrace=False
                )
            reply = json.loads(response.data, parse_float=Decimal)
            if reply.get("code") != 0:
                pytest.fail(
                    f"Tushare daily returned code {reply.get('code')} for {symbol}", pytrace=False
                )
            data = reply["data"]
            observed = {row[0]: list(row[1:]) for row in data["items"]}
            assert tuple(data["fields"]) == fields
            expected = {
                event["event_time"][:10].replace("-", ""): [
                    Decimal(b[key + "_scaled"]) / UNIT for key in fields[1:5]
                ]
                + [Decimal(b["volume_units"]) / 100]
                for event in closes
                for b in event["bars"]
                if b["instrument_id"] == symbol
            }
            assert observed == expected
