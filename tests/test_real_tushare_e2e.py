from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from trademaster.e2e import E2ERunnerError
from trademaster.e2e.real_tushare import (
    DailyLimitRuleEvidence,
    _runner_bar,
    run_real_tushare_e2e,
)


def test_real_bar_requires_status_or_explicit_exchange_limit_rule() -> None:
    row: dict[str, object] = {
        "instrument_id": "510300.SH",
        "open": 4.0,
        "high": 4.1,
        "low": 3.9,
        "close": 4.05,
        "pre_close": 4.0,
        "volume": 1000.0,
    }
    with pytest.raises(E2ERunnerError, match="status or limit-rule evidence"):
        _runner_bar(
            row,
            at_open=True,
            status_row=None,
            limit_rule=None,
            evidence_prefix="snapshot",
        )

    bar = _runner_bar(
        row,
        at_open=True,
        status_row=None,
        limit_rule=DailyLimitRuleEvidence(
            rule_id="sse-etf-10pct-v1:510300.SH",
            limit_ratio_ppm=100_000,
            tick_size_scaled=100_000,
        ),
        evidence_prefix="snapshot",
    )
    assert bar.trading_status == "tradable"
    assert bar.down_limit_scaled == "360000000"
    assert bar.up_limit_scaled == "440000000"
    assert "limit-rule:sse-etf-10pct-v1" in bar.status_evidence_id


@pytest.mark.skipif(not os.environ.get("TUSHARE_TOKEN"), reason="TUSHARE_TOKEN not set")
def test_real_tushare_cross_sectional_and_etf_full_chain(tmp_path: Path) -> None:
    outcome = run_real_tushare_e2e(
        data_root=tmp_path / "data",
        output_root=tmp_path / "artifacts",
        as_of=datetime(2026, 8, 11, 12, tzinfo=UTC),
        runner_command=(
            "cargo",
            "run",
            "--quiet",
            "-p",
            "tm-runner",
            "--bin",
            "tm-runner",
        ),
    )

    cross = json.loads(outcome.cross_sectional.result_path.read_text(encoding="utf-8"))
    etf = json.loads(outcome.etf_timing.result_path.read_text(encoding="utf-8"))
    cross_report_input = json.loads(
        outcome.cross_sectional.report_input_path.read_text(encoding="utf-8")
    )
    etf_report_input = json.loads(outcome.etf_timing.report_input_path.read_text(encoding="utf-8"))
    summary = json.loads(outcome.summary_path.read_text(encoding="utf-8"))
    assert cross["accepted_order_count"] == 2
    assert cross["execution_count"] == 2
    assert len(cross["final_positions"]) == 2
    assert etf["accepted_order_count"] == 2
    assert etf["execution_count"] == 2
    assert etf["final_positions"] == []
    assert etf["fills"][1]["tax_scaled"] == "0"
    assert etf["fills"][1]["transfer_fee_scaled"] == "0"
    assert len(cross["orders"]) == len(cross["accepted_orders"]) == 2
    assert len(cross["executions"]) == 2
    assert len(cross["ledger_effects"]) == cross["ledger_effect_count"] == 5
    assert len(cross["account_snapshots"]) == cross["event_count"]
    cross_latest_industry = dict(cross_report_input["exposures"][-1]["industry_weights"])
    etf_holding_industry = dict(etf_report_input["exposures"][-2]["industry_weights"])
    assert Decimal(cross_latest_industry["cash"]) > Decimal("0.05")
    assert Decimal(etf_holding_industry["cash"]) > Decimal("0.90")
    assert outcome.provider_calls_before_cache_probe > 0
    assert outcome.provider_calls_after_cache_probe == outcome.provider_calls_before_cache_probe
    assert summary["raw_parquet_count"] > 0
    assert summary["staging_parquet_count"] > 0
    assert summary["canonical_parquet_count"] > 0
