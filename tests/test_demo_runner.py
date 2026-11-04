import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest
from trademaster.e2e import E2ERunnerError, verify_e2e_bundle
from trademaster.e2e.demo_data import DemoMarket
from trademaster.e2e.demo_runner import run_demo_strategy


def test_stock_targets_replay_at_the_next_open_with_archived_inputs(tmp_path: Path) -> None:
    days = (date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9))
    stock = "600036.SH"
    bars = pa.Table.from_pylist(
        [
            {
                "instrument_id": stock,
                "trade_date": day,
                "open": 11.8,
                "high": 12.5,
                "low": 11.5,
                "close": 12.0,
                "pre_close": 12.0,
                "volume": 1400.0,
                "adj_factor": 1.0,
            }
            for day in days
        ]
    )
    status = pa.Table.from_pylist(
        [
            {
                "instrument_id": stock,
                "trade_date": day,
                "up_limit": 13.2,
                "down_limit": 10.8,
                "suspended": False,
                "source_revision": "b" * 64,
            }
            for day in days
        ]
    )
    market = DemoMarket(
        days=days,
        next_session=date(2024, 1, 10),
        bars=bars,
        status=status,
        source_evidence={"daily-test": "a" * 64},
    )
    config: dict[str, object] = {"rule": "buy then exit", "plan_sha256": "c" * 64}
    bundle = run_demo_strategy(
        strategy_id="test-stock",
        market=market,
        instruments=(stock,),
        decisions=[{stock: Decimal(1)}, {stock: Decimal(0)}, {stock: Decimal(1)}],
        data_root=tmp_path / "derived",
        output_dir=tmp_path / "report",
        strategy_config=config,
    )
    result = json.loads(bundle.result_path.read_bytes())
    assert [(f["side"], f["event_time"], f["price_scaled"]) for f in result["fills"]] == [
        ("buy", "2024-01-08T01:30:00Z", "1180000000"),
        ("sell", "2024-01-09T01:30:00Z", "1180000000"),
    ]
    assert result["final_positions"] == []
    assert result["signal_count"] == 2
    assert all(int(fill["commission_scaled"]) > 0 for fill in result["fills"])
    request = json.loads(bundle.request_path.read_bytes())
    opening = next(e for e in request["events"] if e["kind"] == "session_open")["bars"][0]
    assert opening["open_scaled"] == opening["high_scaled"] == opening["close_scaled"]
    manifest = json.loads(bundle.manifest_path.read_bytes())
    descriptor = manifest["additional_artifacts"]["strategy"]
    strategy_path = bundle.manifest_path.parent / descriptor["path"]
    strategy = json.loads(strategy_path.read_bytes())
    assert strategy["config"] == config
    assert strategy["source_evidence"] == market.source_evidence
    assert strategy["decisions"][-1]["execution_session"] is None
    assert strategy["decisions"][-1]["weights"] == {stock: "1"}
    assert manifest["snapshot_objects"]
    strategy_path.write_text("{}", encoding="utf-8")
    with pytest.raises(E2ERunnerError, match="additional bundle artifact hash mismatch"):
        verify_e2e_bundle(bundle.manifest_path)
