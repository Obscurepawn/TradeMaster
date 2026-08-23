from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from trademaster.factors.public_factors import public_executable_factor_suite
from trademaster.research.__main__ import (
    _managed_factor_directions,
    _minimum_spacing_from_dense_panel,
)
from trademaster.research.factor_evaluation import (
    FullAFactorDiagnostics,
    FullAFactorEvaluationResult,
    FullAHorizonDiagnostics,
    full_a_event_metric_schema,
    full_a_quantile_metric_schema,
)
from trademaster.research.factor_report import (
    FactorReportIntegrityError,
    FullAFactorReportArtifact,
    FullAFactorReportIdentity,
    FullAFactorReportStore,
    factor_correlation_metric_schema,
)

EVENT_TIME = datetime(2025, 1, 2, 7, tzinfo=UTC)


def test_cli_binds_definition_directions_and_rejects_definition_drift() -> None:
    registrations = public_executable_factor_suite().registrations
    bindings = tuple(
        (
            item.definition.factor_id,
            item.definition.version,
            item.definition.definition_sha256,
        )
        for item in registrations
    )

    directions = _managed_factor_directions(bindings, registrations)

    assert directions[0] == ("gtja191.alpha014", "1", 0)
    assert ("huatai53.size.log_total_market_value", "1", -1) in directions
    with pytest.raises(ValueError, match="definition binding"):
        _managed_factor_directions(
            ((*bindings[0][:2], "0" * 64), *bindings[1:]),
            registrations,
        )


def test_cli_derives_the_conservative_observation_spacing_from_dense_sessions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dense.parquet"
    pq.write_table(
        pa.table(
            {
                "session_index": [0, 1, 14, 15, 37],
                "is_observation": [True, False, True, False, True],
            }
        ),
        path,
    )

    assert _minimum_spacing_from_dense_panel(path) == 14


def _identity() -> FullAFactorReportIdentity:
    return FullAFactorReportIdentity(
        research_run_id="full-a-20y-20260823",
        data_snapshot_sha256="a" * 64,
        evaluation_input_manifest_sha256s=("f" * 64,),
        universe_policy_sha256="b" * 64,
        label_policy_sha256="c" * 64,
        evaluation_config_sha256="d" * 64,
        factor_definition_sha256s=("e" * 64,),
    )


def test_report_identity_binds_the_exact_evaluation_input_manifests(
    tmp_path: Path,
) -> None:
    store = FullAFactorReportStore(tmp_path)
    first_identity = _identity()
    second_identity = first_identity.model_copy(
        update={"evaluation_input_manifest_sha256s": ("0" * 64,)},
    )

    first = store.persist(_result(), identity=first_identity)
    second = store.persist(_result(), identity=second_identity)

    assert first.report_id != second.report_id
    assert first.manifest.identity.evaluation_input_manifest_sha256s == ("f" * 64,)
    assert store.verify(first.manifest_path).identity == first_identity


def _result(*, non_finite: bool = False) -> FullAFactorEvaluationResult:
    horizon = FullAHorizonDiagnostics(
        horizon_sessions=5,
        event_count=1,
        label_observation_coverage=0.85,
        joint_observation_coverage=0.8,
        label_invalid_reason_counts=(("exit_price_unavailable", 15),),
        effective_newey_west_lag=5,
        overlapping_forward_returns=False,
        mean_ic=0.08,
        ic_standard_deviation=None,
        icir=None,
        ic_t_stat=None,
        newey_west_ic_t_stat=None,
        mean_rank_ic=0.1,
        rank_ic_standard_deviation=None,
        rank_icir=None,
        positive_ic_ratio=1.0,
        mean_long_short_spread=0.02,
        spread_standard_deviation=None,
        spread_sharpe=None,
        spread_hit_ratio=1.0,
        spread_max_drawdown=-0.04,
        spread_max_recovery_events=3,
        quantile_monotonicity=0.9,
        mean_top_quantile_turnover=0.25,
        mean_factor_size_correlation=0.12,
        mean_industry_rank_ic=0.07,
        capacity_proxy_cny=800_000.0,
        mean_size_neutral_rank_ic=0.11,
        mean_top_small_market_cap_share=0.6,
        mean_top_mid_market_cap_share=0.3,
        mean_top_large_market_cap_share=0.1,
    )
    summary = FullAFactorDiagnostics(
        factor_id="gtja.alpha014",
        factor_version="1",
        factor_direction=-1,
        universe_observation_count=100,
        factor_observation_coverage=0.92,
        label_observation_coverage=0.88,
        joint_observation_coverage=0.82,
        horizons=(horizon,),
    )
    event = pa.Table.from_pylist(
        [
            {
                "factor_id": "gtja.alpha014",
                "factor_version": "1",
                "event_time": EVENT_TIME,
                "horizon_sessions": 5,
                "observation_count": 82,
                "pearson_ic": float("nan") if non_finite else 0.08,
                "rank_ic": 0.1,
                "factor_size_correlation": 0.12,
                "industry_rank_ic": 0.07,
                "long_short_spread": 0.02,
                "quantile_monotonicity": 0.9,
                "top_quantile_turnover": 0.25,
                "capacity_proxy_cny": 800_000.0,
            }
        ],
        schema=full_a_event_metric_schema(),
    )
    quantile = pa.Table.from_pylist(
        [
            {
                "factor_id": "gtja.alpha014",
                "factor_version": "1",
                "event_time": EVENT_TIME,
                "horizon_sessions": 5,
                "quantile": quantile_id,
                "mean_forward_return": value,
                "observation_count": 41,
            }
            for quantile_id, value in ((1, -0.01), (2, 0.01))
        ],
        schema=full_a_quantile_metric_schema(),
    )
    return FullAFactorEvaluationResult(
        summaries=(summary,),
        event_metrics=event,
        quantile_metrics=quantile,
        factor_correlations=pa.Table.from_pylist([], schema=factor_correlation_metric_schema()),
    )


def _object_path(root: Path, artifact: FullAFactorReportArtifact, name: str) -> Path:
    descriptor = next(item for item in artifact.manifest.objects if item.name == name)
    return root / descriptor.uri


def test_report_store_persists_content_addressed_self_contained_bundle(
    tmp_path: Path,
) -> None:
    store = FullAFactorReportStore(tmp_path)

    artifact = store.persist(
        _result(non_finite=True),
        identity=_identity(),
        limitations=(
            "SW2021历史行业覆盖不足时不输出伪造的历史行业中性结果。",
            "容量仅为成交额参与率proxy，不等于可成交资金上限。",
        ),
    )

    assert artifact.manifest_path.name == f"{artifact.manifest_sha256}.json"
    assert artifact.manifest.report_id == artifact.report_id
    assert [item.name for item in artifact.manifest.objects] == [
        "correlation_metrics",
        "event_metrics",
        "quantile_metrics",
        "report_html",
        "report_markdown",
        "summary",
    ]
    correlations = pq.read_table(_object_path(tmp_path, artifact, "correlation_metrics"))
    assert correlations.schema.remove_metadata() == factor_correlation_metric_schema()
    assert correlations.num_rows == 0

    event = pq.read_table(_object_path(tmp_path, artifact, "event_metrics"))
    assert event["pearson_ic"].null_count == 1
    summary_text = _object_path(tmp_path, artifact, "summary").read_text(encoding="utf-8")
    json.loads(summary_text)
    assert "NaN" not in summary_text and "Infinity" not in summary_text

    html = _object_path(tmp_path, artifact, "report_html").read_text(encoding="utf-8")
    markdown = _object_path(tmp_path, artifact, "report_markdown").read_text(encoding="utf-8")
    assert "plotly.js" in html.lower()
    assert re.search(r"<script[^>]+src=['\"]https?://", html, re.IGNORECASE) is None
    for phrase in (
        "覆盖率与缺失性",
        "IC、RankIC 与 HAC",
        "Horizon decay",
        "年度稳定性",
        "分位数组合、Spread 与换手",
        "最大回撤与修复时间",
        "行业与 Size 暴露",
        "容量与成本限制",
        "逐Horizon标签覆盖",
        "Size中性RankIC",
        "Top市值三分位暴露",
        "方向",
        "N/A",
    ):
        assert phrase in html
        assert phrase in markdown
    assert re.search(r">\s*(?:nan|[-+]?inf(?:inity)?)\s*<", html, re.IGNORECASE) is None
    assert re.search(r"\|\s*(?:nan|[-+]?inf(?:inity)?)\s*\|", markdown, re.IGNORECASE) is None
    assert store.verify(artifact.manifest_path).report_id == artifact.report_id


def test_report_store_is_deterministic_and_persists_optional_correlations(
    tmp_path: Path,
) -> None:
    correlations = pa.Table.from_pylist(
        [
            {
                "left_factor_id": "gtja.alpha014",
                "left_factor_version": "1",
                "right_factor_id": "huatai.style.size",
                "right_factor_version": "1",
                "pearson_correlation": 0.3,
                "rank_correlation": 0.25,
                "observation_count": 82,
            }
        ],
        schema=factor_correlation_metric_schema(),
    )
    store = FullAFactorReportStore(tmp_path)

    first = store.persist(_result(), identity=_identity(), correlation_metrics=correlations)
    second = store.persist(_result(), identity=_identity(), correlation_metrics=correlations)

    assert first.report_id == second.report_id
    assert first.manifest_sha256 == second.manifest_sha256
    assert (
        pq.read_table(_object_path(tmp_path, first, "correlation_metrics")).to_pylist()
        == correlations.to_pylist()
    )


def test_report_store_fails_closed_when_an_object_or_manifest_is_tampered(
    tmp_path: Path,
) -> None:
    store = FullAFactorReportStore(tmp_path)
    artifact = store.persist(_result(), identity=_identity())
    event_path = _object_path(tmp_path, artifact, "event_metrics")
    event_path.write_bytes(event_path.read_bytes() + b"tampered")

    with pytest.raises(FactorReportIntegrityError, match="object hash mismatch"):
        store.verify(artifact.manifest_path)

    second_root = tmp_path / "second"
    second_store = FullAFactorReportStore(second_root)
    second = second_store.persist(_result(), identity=_identity())
    second.manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(FactorReportIntegrityError, match="manifest hash mismatch"):
        second_store.verify(second.manifest_path)
