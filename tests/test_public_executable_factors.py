from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest
from trademaster.contracts import (
    AvailabilityPolicy,
    FactorContext,
    SnapshotManifest,
    SnapshotRequest,
    bind_snapshot_provenance,
)
from trademaster.factors.public_factors import public_executable_factor_suite

NOW = datetime(2025, 1, 31, 8, tzinfo=UTC)


def _snapshot() -> SnapshotManifest:
    return SnapshotManifest.build(
        request=SnapshotRequest(datasets=(), as_of=NOW),
        availability_policy=AvailabilityPolicy(
            policy_id="test/v1",
            known_at_field="known_at",
            publication_lag_policy_id="none/v1",
        ),
        objects=(),
        coverages=(),
    )


def test_public_executable_suite_registers_one_huatai_and_ten_reviewed_gtja_factors() -> None:
    suite = public_executable_factor_suite()

    assert tuple(item.definition.factor_id for item in suite.registrations) == (
        "huatai53.size.log_total_market_value",
        "gtja191.alpha014",
        "gtja191.alpha015",
        "gtja191.alpha018",
        "gtja191.alpha020",
        "gtja191.alpha031",
        "gtja191.alpha034",
        "gtja191.alpha046",
        "gtja191.alpha053",
        "gtja191.alpha058",
        "gtja191.alpha088",
    )
    assert suite.external_dataset_fields == (("fundamental_inputs", "total_market_value"),)
    units = {item.definition.factor_id: item.definition.unit for item in suite.registrations}
    assert units["gtja191.alpha014"] == "hfq_price_difference"
    assert units["gtja191.alpha015"] == "ratio"
    assert units["gtja191.alpha020"] == "percent"
    alpha14 = suite.registrations[1].definition
    assert tuple(
        (item.dataset, item.fields) for item in alpha14.dependencies if item.kind == "dataset"
    ) == (
        ("adj_factors", ("adj_factor",)),
        ("daily_bars", ("close",)),
    )
    parameters = dict(alpha14.parameters)
    assert parameters["price_basis"] == '"hfq"'
    assert parameters["adjustment_formula"] == '"raw_price * adj_factor"'


def test_huatai_size_uses_tushare_total_mv_unit_and_rejects_nonpositive_values() -> None:
    factor = public_executable_factor_suite().registrations[0].factor
    inputs = pa.table(
        {
            "instrument_id": ["000001.SZ", "600000.SH"],
            "event_time": pa.array([NOW, NOW], type=pa.timestamp("us", tz="UTC")),
            "total_market_value": [123.0, 0.0],
        }
    )

    snapshot = _snapshot()
    output = factor.compute(
        FactorContext(
            as_of=NOW,
            snapshot=snapshot,
            inputs=bind_snapshot_provenance(inputs, snapshot),
        )
    )

    rows = output.to_pylist()
    assert rows[0]["instrument_id"] == "000001.SZ"
    assert rows[0]["value"] == pytest.approx(math.log(123.0 * 10_000.0))
    assert rows[0]["is_valid"] is True
    assert rows[1]["value"] == 0.0
    assert rows[1]["is_valid"] is False


def test_reviewed_gtja_formulas_match_hand_calculated_golden_values() -> None:
    suite = public_executable_factor_suite()
    event_times = [NOW - timedelta(days=24 - index) for index in range(25)]
    inputs = pa.table(
        {
            "instrument_id": ["000001.SZ"] * 25,
            "event_time": pa.array(event_times, type=pa.timestamp("us", tz="UTC")),
            "close": [float(value) for value in range(1, 26)],
            "open": [float(value) - 0.5 for value in range(1, 26)],
            "adj_factor": [1.0] * 25,
        }
    )
    snapshot = _snapshot()
    context = FactorContext(
        as_of=NOW,
        snapshot=snapshot,
        inputs=bind_snapshot_provenance(inputs, snapshot),
    )
    expected = {
        "gtja191.alpha014": 5.0,
        "gtja191.alpha015": 24.5 / 24.0 - 1.0,
        "gtja191.alpha018": 25.0 / 20.0,
        "gtja191.alpha020": 100.0 * 6.0 / 19.0,
        "gtja191.alpha031": 100.0 * (25.0 - 19.5) / 19.5,
        "gtja191.alpha034": 19.5 / 25.0,
        "gtja191.alpha046": (24.0 + 22.5 + 19.5 + 13.5) / (4.0 * 25.0),
        "gtja191.alpha053": 100.0,
        "gtja191.alpha058": 100.0,
        "gtja191.alpha088": 400.0,
    }

    for registration in suite.registrations[1:]:
        rows = registration.factor.compute(context).to_pylist()
        assert rows[-1]["is_valid"] is True
        assert rows[-1]["value"] == pytest.approx(expected[registration.definition.factor_id])
        assert rows[0]["is_valid"] is False
        assert rows[0]["value"] == 0.0


def test_gtja_alpha015_only_requires_current_open_and_previous_close() -> None:
    factor = public_executable_factor_suite().registrations[2].factor
    snapshot = _snapshot()
    inputs = pa.table(
        {
            "instrument_id": ["000001.SZ", "000001.SZ"],
            "event_time": pa.array(
                [NOW - timedelta(days=1), NOW], type=pa.timestamp("us", tz="UTC")
            ),
            "close": [10.0, math.nan],
            "open": [9.0, 11.0],
            "adj_factor": [1.0, 1.0],
        }
    )
    output = factor.compute(
        FactorContext(
            as_of=NOW,
            snapshot=snapshot,
            inputs=bind_snapshot_provenance(inputs, snapshot),
        )
    )

    assert output.to_pylist()[-1]["is_valid"] is True
    assert output.to_pylist()[-1]["value"] == pytest.approx(0.1)
