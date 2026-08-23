"""Immutable full-A factor-evaluation artifacts and self-contained reports."""

from __future__ import annotations

import hashlib
import io
import json
import math
import statistics
import tempfile
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import plotly.graph_objects as go  # type: ignore[import-untyped]
import plotly.io as pio  # type: ignore[import-untyped]
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.research.factor_evaluation import (
    FullAFactorDiagnostics,
    FullAFactorEvaluationResult,
    FullAHorizonDiagnostics,
    full_a_event_metric_schema,
    full_a_factor_correlation_schema,
    full_a_quantile_metric_schema,
)

_SHA256_LENGTH = 64
_PARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"
_REQUIRED_OBJECT_NAMES = (
    "correlation_metrics",
    "event_metrics",
    "quantile_metrics",
    "report_html",
    "report_markdown",
    "summary",
)
_OBJECT_CONTRACTS = {
    "correlation_metrics": (_PARQUET_MEDIA_TYPE, ".parquet", True),
    "event_metrics": (_PARQUET_MEDIA_TYPE, ".parquet", True),
    "quantile_metrics": (_PARQUET_MEDIA_TYPE, ".parquet", True),
    "report_html": ("text/html; charset=utf-8", ".html", False),
    "report_markdown": ("text/markdown; charset=utf-8", ".md", False),
    "summary": ("application/json", ".json", False),
}


class FactorReportIntegrityError(RuntimeError):
    """Raised when an immutable report artifact cannot be proven intact."""


def _json_safe(value: object) -> object:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def factor_correlation_metric_schema() -> pa.Schema:
    """Pairwise factor correlations, or an explicit empty table when unavailable."""

    return full_a_factor_correlation_schema()


class FullAFactorReportIdentity(BaseModel):
    """Provenance required to distinguish one formal research evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-report-identity/v3"] = (
        "trademaster.factor-report-identity/v3"
    )
    research_run_id: str
    data_snapshot_sha256: str
    evaluation_input_manifest_sha256s: tuple[str, ...]
    universe_policy_sha256: str
    label_policy_sha256: str
    evaluation_config_sha256: str
    factor_definition_sha256s: tuple[str, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> FullAFactorReportIdentity:
        hashes = (
            self.data_snapshot_sha256,
            *self.evaluation_input_manifest_sha256s,
            self.universe_policy_sha256,
            self.label_policy_sha256,
            self.evaluation_config_sha256,
            *self.factor_definition_sha256s,
        )
        if (
            not self.research_run_id.strip()
            or not self.evaluation_input_manifest_sha256s
            or self.evaluation_input_manifest_sha256s
            != tuple(sorted(set(self.evaluation_input_manifest_sha256s)))
            or not self.factor_definition_sha256s
            or self.factor_definition_sha256s != tuple(sorted(set(self.factor_definition_sha256s)))
            or any(not _is_sha256(value) for value in hashes)
        ):
            raise ValueError("factor report identity is not canonical")
        return self

    @property
    def identity_sha256(self) -> str:
        return _sha256(_canonical_json(self.model_dump(mode="json")))


FactorReportObjectName = Literal[
    "correlation_metrics",
    "event_metrics",
    "quantile_metrics",
    "report_html",
    "report_markdown",
    "summary",
]


class FactorReportObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: FactorReportObjectName
    uri: str
    sha256: str
    media_type: str
    row_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_object(self) -> FactorReportObject:
        if not _is_sha256(self.sha256) or not self.uri or not self.media_type:
            raise ValueError("factor report object is not canonical")
        return self


class FullAFactorReportManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-report/v3"] = "trademaster.factor-report/v3"
    report_id: str
    identity: FullAFactorReportIdentity
    limitations: tuple[str, ...]
    objects: tuple[FactorReportObject, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> FullAFactorReportManifest:
        if (
            not _is_sha256(self.report_id)
            or tuple(item.name for item in self.objects) != _REQUIRED_OBJECT_NAMES
            or self.limitations != tuple(sorted(set(self.limitations)))
            or any(not item.strip() for item in self.limitations)
        ):
            raise ValueError("factor report manifest is not canonical")
        for item in self.objects:
            media_type, suffix, is_table = _OBJECT_CONTRACTS[item.name]
            if (
                item.media_type != media_type
                or PurePosixPath(item.uri).suffix != suffix
                or (is_table and item.row_count is None)
                or (not is_table and item.row_count is not None)
            ):
                raise ValueError("factor report object contract is invalid")
        metric_objects = [
            item.model_dump(mode="json")
            for item in self.objects
            if item.name
            in {
                "correlation_metrics",
                "event_metrics",
                "quantile_metrics",
                "summary",
            }
        ]
        expected = _sha256(
            _canonical_json(
                {
                    "identity": self.identity.model_dump(mode="json"),
                    "limitations": self.limitations,
                    "metric_objects": metric_objects,
                }
            )
        )
        if self.report_id != expected:
            raise ValueError("factor report identity hash mismatch")
        return self


@dataclass(frozen=True, slots=True)
class FullAFactorReportArtifact:
    report_id: str
    manifest: FullAFactorReportManifest
    manifest_path: Path
    manifest_sha256: str


def _sanitize_table(table: pa.Table) -> pa.Table:
    rows = table.to_pylist()
    for row in rows:
        for field in table.schema:
            value = row[field.name]
            if pa.types.is_floating(field.type) and value is not None and not math.isfinite(value):
                row[field.name] = None
    return pa.Table.from_pylist(rows, schema=table.schema)


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue()


def _format_number(value: float | None, *, digits: int = 4) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:.{digits}f}"


def _format_percent(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"{value:.2%}"


def _plot_value(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _summary_rows(
    summaries: tuple[FullAFactorDiagnostics, ...],
) -> list[tuple[FullAFactorDiagnostics, FullAHorizonDiagnostics]]:
    return [
        (summary, horizon)
        for summary in sorted(summaries, key=lambda item: (item.factor_id, item.factor_version))
        for horizon in sorted(summary.horizons, key=lambda item: item.horizon_sessions)
    ]


def _direction_label(direction: int) -> str:
    return {
        -1: "-1（低原始值为Top）",
        0: "0（原始高值为Top）",
        1: "+1（高原始值为Top）",
    }[direction]


def _horizon_figure(summaries: tuple[FullAFactorDiagnostics, ...]) -> go.Figure:
    figure = go.Figure()
    for summary in sorted(summaries, key=lambda item: (item.factor_id, item.factor_version)):
        horizons = sorted(summary.horizons, key=lambda item: item.horizon_sessions)
        label = f"{summary.factor_id}@{summary.factor_version}"
        figure.add_scatter(
            x=[item.horizon_sessions for item in horizons],
            y=[_plot_value(item.mean_ic) for item in horizons],
            mode="lines+markers",
            name=f"{label} IC",
        )
        figure.add_scatter(
            x=[item.horizon_sessions for item in horizons],
            y=[_plot_value(item.mean_rank_ic) for item in horizons],
            mode="lines+markers",
            name=f"{label} RankIC",
            line={"dash": "dot"},
        )
    figure.update_layout(
        title="Horizon decay：IC / RankIC 随预测周期变化",
        xaxis_title="Forward horizon（交易日）",
        yaxis_title="相关系数",
        template="plotly_white",
    )
    return figure


def _quantile_figure(table: pa.Table) -> go.Figure:
    grouped: dict[tuple[str, str, int, int], list[float]] = {}
    for row in table.to_pylist():
        value = row["mean_forward_return"]
        if value is None or not math.isfinite(float(cast(Any, value))):
            continue
        key = (
            str(row["factor_id"]),
            str(row["factor_version"]),
            int(row["horizon_sessions"]),
            int(row["quantile"]),
        )
        grouped.setdefault(key, []).append(float(cast(Any, value)))
    series: dict[tuple[str, str, int], list[tuple[int, float]]] = {}
    for quantile_key, values in grouped.items():
        series.setdefault(quantile_key[:3], []).append(
            (quantile_key[3], math.fsum(values) / len(values))
        )
    figure = go.Figure()
    for series_key in sorted(series):
        points = sorted(series[series_key])
        figure.add_bar(
            x=[item[0] for item in points],
            y=[item[1] for item in points],
            name=f"{series_key[0]}@{series_key[1]} / {series_key[2]}D",
        )
    figure.update_layout(
        title="分位数组合平均 forward return",
        xaxis_title="Quantile（1=低分组）",
        yaxis_title="平均收益率",
        yaxis_tickformat=".2%",
        barmode="group",
        template="plotly_white",
    )
    return figure


def _spread_figure(table: pa.Table) -> go.Figure:
    grouped: dict[tuple[str, str, int], list[tuple[object, float]]] = {}
    for row in table.to_pylist():
        spread = row["long_short_spread"]
        if spread is None or not math.isfinite(float(cast(Any, spread))):
            continue
        series_key = (
            str(row["factor_id"]),
            str(row["factor_version"]),
            int(row["horizon_sessions"]),
        )
        grouped.setdefault(series_key, []).append((row["event_time"], float(cast(Any, spread))))
    figure = go.Figure()
    for key in sorted(grouped):
        events = sorted(grouped[key], key=lambda item: cast(Any, item[0]))
        cumulative = 1.0
        values: list[float] = []
        for _, spread in events:
            cumulative *= 1.0 + spread
            values.append(cumulative - 1.0)
        figure.add_scatter(
            x=[item[0] for item in events],
            y=values,
            mode="lines",
            name=f"{key[0]}@{key[1]} / {key[2]}D",
        )
    figure.update_layout(
        title="累计 Top-Bottom spread（未扣成本研究序列）",
        xaxis_title="因子观测时间",
        yaxis_title="累计 spread",
        yaxis_tickformat=".2%",
        template="plotly_white",
    )
    return figure


def _figure_html(figure: go.Figure, *, include_plotlyjs: bool, div_id: str) -> str:
    value: str = pio.to_html(
        figure,
        include_plotlyjs=include_plotlyjs,
        full_html=False,
        config={"responsive": True, "displaylogo": False},
        div_id=div_id,
    )
    return value


def _metric_table_html(summaries: tuple[FullAFactorDiagnostics, ...]) -> str:
    rows: list[str] = []
    for summary, horizon in _summary_rows(summaries):
        rows.append(
            "<tr>"
            f"<td>{escape(summary.factor_id)}@{escape(summary.factor_version)}</td>"
            f"<td>{escape(_direction_label(summary.factor_direction))}</td>"
            f"<td>{horizon.horizon_sessions}</td>"
            f"<td>{_format_percent(horizon.joint_observation_coverage)}</td>"
            f"<td>{_format_number(horizon.mean_ic)}</td>"
            f"<td>{_format_number(horizon.mean_rank_ic)}</td>"
            f"<td>{_format_number(horizon.mean_size_neutral_rank_ic)}</td>"
            f"<td>{_format_number(horizon.newey_west_ic_t_stat)}</td>"
            f"<td>{_format_percent(horizon.mean_long_short_spread)}</td>"
            f"<td>{_format_number(horizon.spread_sharpe)}</td>"
            f"<td>{_format_percent(horizon.spread_max_drawdown)}</td>"
            f"<td>{_format_number(horizon.spread_max_recovery_events)}</td>"
            f"<td>{_format_percent(horizon.mean_top_quantile_turnover)}</td>"
            f"<td>{_format_number(horizon.mean_industry_rank_ic)}</td>"
            f"<td>{_format_number(horizon.mean_factor_size_correlation)}</td>"
            f"<td>{_format_percent(horizon.mean_top_small_market_cap_share)}</td>"
            f"<td>{_format_percent(horizon.mean_top_mid_market_cap_share)}</td>"
            f"<td>{_format_percent(horizon.mean_top_large_market_cap_share)}</td>"
            f"<td>{_format_number(horizon.capacity_proxy_cny, digits=0)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='19'>N/A</td></tr>")
    return (
        "<table><thead><tr><th>因子</th><th>方向</th><th>Horizon</th><th>联合覆盖</th>"
        "<th>IC</th><th>RankIC</th><th>Size中性RankIC</th><th>HAC t-stat</th><th>Spread</th>"
        "<th>Spread Sharpe</th><th>最大回撤</th><th>修复事件数</th><th>换手</th>"
        "<th>行业内RankIC</th><th>Size相关</th><th>Top小市值占比</th>"
        "<th>Top中市值占比</th><th>Top大市值占比</th><th>容量proxy(CNY)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _coverage_table_html(summaries: tuple[FullAFactorDiagnostics, ...]) -> str:
    rows = [
        "<tr>"
        f"<td>{escape(item.factor_id)}@{escape(item.factor_version)}</td>"
        f"<td>{item.universe_observation_count:,}</td>"
        f"<td>{_format_percent(item.factor_observation_coverage)}</td>"
        f"<td>{_format_percent(item.label_observation_coverage)}</td>"
        f"<td>{_format_percent(item.joint_observation_coverage)}</td></tr>"
        for item in sorted(summaries, key=lambda value: (value.factor_id, value.factor_version))
    ]
    if not rows:
        rows.append("<tr><td colspan='5'>N/A</td></tr>")
    return (
        "<table><thead><tr><th>因子</th><th>PIT universe分母</th><th>因子覆盖</th>"
        "<th>标签覆盖</th><th>联合覆盖</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _horizon_coverage_table_html(
    summaries: tuple[FullAFactorDiagnostics, ...],
) -> str:
    rows = [
        "<tr>"
        f"<td>{escape(summary.factor_id)}@{escape(summary.factor_version)}</td>"
        f"<td>{horizon.horizon_sessions}</td>"
        f"<td>{_format_percent(horizon.label_observation_coverage)}</td>"
        f"<td>{_format_percent(horizon.joint_observation_coverage)}</td>"
        f"<td>{horizon.effective_newey_west_lag}</td>"
        f"<td>{'是' if horizon.overlapping_forward_returns else '否'}</td>"
        f"<td>{escape('; '.join(f'{name}={count}' for name, count in horizon.label_invalid_reason_counts) or 'N/A')}</td>"
        "</tr>"
        for summary, horizon in _summary_rows(summaries)
    ]
    if not rows:
        rows.append("<tr><td colspan='7'>N/A</td></tr>")
    return (
        "<table><thead><tr><th>因子</th><th>Horizon</th><th>标签覆盖</th>"
        "<th>联合覆盖</th><th>HAC lag</th><th>forward标签重叠</th>"
        "<th>标签失效原因计数</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _yearly_rows(
    table: pa.Table,
) -> list[tuple[str, str, int, int, int, float | None, float | None, float | None]]:
    grouped: dict[tuple[str, str, int, int], dict[str, list[float]]] = {}
    for row in table.to_pylist():
        event_time = cast(Any, row["event_time"])
        key = (
            str(row["factor_id"]),
            str(row["factor_version"]),
            int(row["horizon_sessions"]),
            int(event_time.year),
        )
        values = grouped.setdefault(key, {"ic": [], "rank_ic": [], "spread": []})
        for source, target in (
            ("pearson_ic", "ic"),
            ("rank_ic", "rank_ic"),
            ("long_short_spread", "spread"),
        ):
            raw = row[source]
            if raw is not None and math.isfinite(float(cast(Any, raw))):
                values[target].append(float(cast(Any, raw)))
    return [
        (
            *key,
            max(len(values["ic"]), len(values["rank_ic"]), len(values["spread"])),
            statistics.fmean(values["ic"]) if values["ic"] else None,
            statistics.fmean(values["rank_ic"]) if values["rank_ic"] else None,
            statistics.fmean(values["spread"]) if values["spread"] else None,
        )
        for key, values in sorted(grouped.items())
    ]


def _yearly_table_html(table: pa.Table) -> str:
    rows = [
        "<tr>"
        f"<td>{escape(factor_id)}@{escape(version)}</td><td>{horizon}</td>"
        f"<td>{year}</td><td>{count}</td><td>{_format_number(ic)}</td>"
        f"<td>{_format_number(rank_ic)}</td><td>{_format_percent(spread)}</td></tr>"
        for factor_id, version, horizon, year, count, ic, rank_ic, spread in _yearly_rows(table)
    ]
    if not rows:
        rows.append("<tr><td colspan='7'>N/A</td></tr>")
    return (
        "<table><thead><tr><th>因子</th><th>Horizon</th><th>年度</th><th>事件数</th>"
        "<th>平均IC</th><th>平均RankIC</th><th>平均Spread</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_html(
    result: FullAFactorEvaluationResult,
    *,
    identity: FullAFactorReportIdentity,
    limitations: tuple[str, ...],
    event_metrics: pa.Table,
    quantile_metrics: pa.Table,
    correlations: pa.Table,
) -> bytes:
    horizon = _horizon_figure(result.summaries)
    quantile = _quantile_figure(quantile_metrics)
    spread = _spread_figure(event_metrics)
    correlation_note = (
        f"已落盘 {correlations.num_rows:,} 条成对相关性记录。"
        if correlations.num_rows
        else "相关性表已显式落盘为空表；当前评估没有足够的共同样本时显示 N/A。"
    )
    limitation_items = "".join(f"<li>{escape(item)}</li>" for item in limitations)
    embedded_identity = _canonical_json(identity.model_dump(mode="json")).decode("utf-8")
    safe_identity = (
        embedded_identity.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    )
    html = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>TradeMaster 全A因子测评报告</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "max-width:1500px;margin:24px auto;padding:0 20px;color:#1f2937;line-height:1.6}"
        "section{margin:26px 0;padding:18px;border:1px solid #dbe3ec;border-radius:10px;overflow-x:auto}"
        "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d1d5db;"
        "padding:7px;text-align:right}th{background:#f3f6fa}th:first-child,td:first-child{text-align:left}"
        ".note{background:#fff8e6}.guide{background:#f7f9fc}code{word-break:break-all}</style>"
        "</head><body><h1>TradeMaster 全A因子测评报告</h1>"
        f"<p>研究运行：<code>{escape(identity.research_run_id)}</code>；数据快照："
        f"<code>{identity.data_snapshot_sha256}</code>；输入manifest："
        f"<code>{escape(', '.join(identity.evaluation_input_manifest_sha256s))}</code>。"
        "表格中的不可计算值统一显示 N/A。</p>"
        "<section><h2>覆盖率与缺失性</h2>"
        "<p>覆盖率分母来自当时点PIT历史股票池，而不是已有因子行。因子、forward-return标签"
        "和二者联合覆盖分别报告。缺失、无穷值会被排除；常数截面不能计算IC。总表的标签/"
        "联合覆盖是跨horizon平均值，逐horizon表才是诊断某一持有期的权威口径。</p>"
        f"{_coverage_table_html(result.summaries)}</section>"
        "<section><h2>逐Horizon标签覆盖</h2>"
        "<p>Quantile在查看未来标签前由当期有效因子池冻结；标签失效只减少收益测量样本，"
        "不会反向改变分组或换手。失效原因与HAC lag按horizon分别报告。</p>"
        f"{_horizon_coverage_table_html(result.summaries)}</section>"
        "<section><h2>IC、RankIC 与 HAC</h2>"
        "<p>IC是截面因子值与未来收益的Pearson相关；RankIC是秩相关，对极端值更稳健。ICIR"
        "是均值/时间序列标准差。普通t-stat假设较强；HAC/Newey-West t-stat修正时间序列"
        "异方差和自相关。样本不足或方差为零时显示 N/A。</p>"
        f"{_metric_table_html(result.summaries)}</section>"
        "<section><h2>Horizon decay</h2><p>比较不同forward horizon的IC与RankIC，观察预测"
        "能力随持有期衰减、反转或失稳；不同horizon的重叠标签会产生自相关，应结合HAC阅读。"
        "当forward持有期超过观察间隔时，报告不把重叠spread当作独立收益复利，Spread、"
        "Sharpe、回撤与修复时间统一显示N/A。</p>"
        + _figure_html(horizon, include_plotlyjs=True, div_id="tm-factor-horizon-decay")
        + "</section><section><h2>年度稳定性</h2>"
        "<p>按自然年汇总event级IC、RankIC与Spread，用于识别因子失效、方向翻转和样本期"
        "依赖；年度事件过少时应降低结论置信度。</p>"
        + _yearly_table_html(event_metrics)
        + "</section><section><h2>分位数组合、Spread 与换手</h2>"
        "<p>Quantile按definition方向排序：direction=-1时原始低值进入Top，direction=0或+1"
        "时原始高值进入Top；IC/RankIC仍保持raw符号。Top-Bottom spread为方向化Top减Bottom"
        "的未来收益。单调性衡量分组收益是否有序，换手越高，成本侵蚀通常越强。</p>"
        + _figure_html(quantile, include_plotlyjs=False, div_id="tm-factor-quantiles")
        + _figure_html(spread, include_plotlyjs=False, div_id="tm-factor-spread")
        + "</section><section><h2>最大回撤与修复时间</h2>"
        "<p>仅非重叠horizon的最大回撤基于未扣交易成本的累计spread研究序列；修复/水下事件数是离开"
        "高水位后连续处于水下的最长因子观测数，期末尚未修复的区间也会计入，并非自然日。"
        "没有足够spread序列时为 N/A。</p>"
        "</section><section><h2>行业与 Size 暴露</h2>"
        "<p>行业内RankIC衡量同业公司之间的排序能力；Size相关性是因子与对数总市值的截面"
        "相关。Size中性RankIC先在每个截面回归 raw factor ~ intercept + log(total_mv)，再"
        "计算残差与未来收益的RankIC。历史行业PIT证据不足时不能宣称行业中性。"
        f"{escape(correlation_note)}</p>"
        "<h3>Top市值三分位暴露</h3><p>Small/Mid/Large由完整PIT eligible universe按当期"
        "total_mv稳定切成三组；表中占比是direction-oriented Top成员的等权数量占比，"
        "不是用未来收益分组，也不是市值加权。</p>"
        "</section><section class='note'><h2>容量与成本限制</h2>"
        "<p>容量proxy来自Top分组成交额和配置的参与率，只是可交易性的下界诊断；它没有模拟"
        "盘口深度、冲击函数、佣金、印花税、滑点和涨跌停排队，因此不是可管理资金上限，也"
        "不是可实现策略收益。</p><ul>"
        f"{limitation_items}</ul></section>"
        "<section class='guide'><h2>结论阅读边界</h2><p>全A测评必须同时绑定历史universe、"
        "数据snapshot、因子definition、标签对齐和评估配置。高IC不自动等于可交易策略；"
        "仍需检验缺失机制、行业和市值暴露、换手、容量、成本及不同市场阶段稳定性。</p>"
        "</section>"
        f"<script id='trademaster-factor-report-identity' type='application/json'>{safe_identity}"
        "</script></body></html>"
    )
    return html.encode("utf-8")


def _markdown_table(summaries: tuple[FullAFactorDiagnostics, ...]) -> str:
    lines = [
        "| 因子 | 方向 | Horizon | 联合覆盖 | IC | RankIC | Size中性RankIC | HAC t-stat | Spread | 最大回撤 | 换手 | 行业内RankIC | Size相关 | Top小/中/大占比 | 容量proxy(CNY) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for summary, horizon in _summary_rows(summaries):
        lines.append(
            "| "
            + " | ".join(
                (
                    f"{summary.factor_id}@{summary.factor_version}",
                    _direction_label(summary.factor_direction),
                    str(horizon.horizon_sessions),
                    _format_percent(horizon.joint_observation_coverage),
                    _format_number(horizon.mean_ic),
                    _format_number(horizon.mean_rank_ic),
                    _format_number(horizon.mean_size_neutral_rank_ic),
                    _format_number(horizon.newey_west_ic_t_stat),
                    _format_percent(horizon.mean_long_short_spread),
                    _format_percent(horizon.spread_max_drawdown),
                    _format_percent(horizon.mean_top_quantile_turnover),
                    _format_number(horizon.mean_industry_rank_ic),
                    _format_number(horizon.mean_factor_size_correlation),
                    "/".join(
                        (
                            _format_percent(horizon.mean_top_small_market_cap_share),
                            _format_percent(horizon.mean_top_mid_market_cap_share),
                            _format_percent(horizon.mean_top_large_market_cap_share),
                        )
                    ),
                    _format_number(horizon.capacity_proxy_cny, digits=0),
                )
            )
            + " |"
        )
    if len(lines) == 2:
        lines.append(
            "| N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |"
        )
    return "\n".join(lines)


def _horizon_coverage_markdown(
    summaries: tuple[FullAFactorDiagnostics, ...],
) -> str:
    lines = [
        "| 因子 | Horizon | 标签覆盖 | 联合覆盖 | HAC lag | 标签重叠 | 标签失效原因计数 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for summary, horizon in _summary_rows(summaries):
        reasons = "; ".join(
            f"{name}={count}" for name, count in horizon.label_invalid_reason_counts
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    f"{summary.factor_id}@{summary.factor_version}",
                    str(horizon.horizon_sessions),
                    _format_percent(horizon.label_observation_coverage),
                    _format_percent(horizon.joint_observation_coverage),
                    str(horizon.effective_newey_west_lag),
                    "是" if horizon.overlapping_forward_returns else "否",
                    reasons or "N/A",
                )
            )
            + " |"
        )
    if len(lines) == 2:
        lines.append("| N/A | N/A | N/A | N/A | N/A | N/A | N/A |")
    return "\n".join(lines)


def _yearly_markdown(table: pa.Table) -> str:
    lines = [
        "| 因子 | Horizon | 年度 | 事件数 | 平均IC | 平均RankIC | 平均Spread |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                f"{factor_id}@{version}",
                str(horizon),
                str(year),
                str(count),
                _format_number(ic),
                _format_number(rank_ic),
                _format_percent(spread),
            )
        )
        + " |"
        for factor_id, version, horizon, year, count, ic, rank_ic, spread in _yearly_rows(table)
    )
    if len(lines) == 2:
        lines.append("| N/A | N/A | N/A | N/A | N/A | N/A | N/A |")
    return "\n".join(lines)


def _render_markdown(
    result: FullAFactorEvaluationResult,
    *,
    identity: FullAFactorReportIdentity,
    limitations: tuple[str, ...],
    correlations: pa.Table,
) -> bytes:
    limitations_text = "\n".join(f"- {item}" for item in limitations)
    text = f"""# TradeMaster 全A因子测评报告

研究运行：`{identity.research_run_id}`<br>
数据快照：`{identity.data_snapshot_sha256}`
输入manifest：`{", ".join(identity.evaluation_input_manifest_sha256s)}`

所有不可计算数值统一写为 N/A；缺失不能解释为零。

## 覆盖率与缺失性

覆盖率以PIT历史股票池为分母，分别记录因子、forward-return标签和联合覆盖。缺失、无穷值会
被排除，常数截面不能计算IC。总表标签覆盖是跨horizon平均值，不能替代逐horizon诊断。

## 逐Horizon标签覆盖

Quantile在查看未来标签前由当期因子池冻结；标签失效只影响收益测量样本，不会反向改变分组
或换手。每个horizon分别列出覆盖、invalid reason、HAC lag与标签是否重叠。

{_horizon_coverage_markdown(result.summaries)}

## IC、RankIC 与 HAC

IC是截面Pearson相关；RankIC是秩相关；ICIR是IC均值除以时间序列标准差。HAC/Newey-West
t-stat修正异方差和序列相关。样本不足或方差为零时为 N/A。

{_markdown_table(result.summaries)}

## Horizon decay

比较不同forward horizon的IC/RankIC，观察衰减、反转和稳定性。重叠标签会产生自相关，需与
HAC t-stat联合阅读。重叠horizon不把spread当作独立收益复利，因此Spread、Sharpe、回撤和
修复时间统一为N/A。

## 年度稳定性

按自然年汇总event级IC、RankIC和Spread，用于检查方向翻转、阶段失效与样本期依赖。年度事件
太少时应降低置信度。

{_yearly_markdown(result.event_metrics)}

## 分位数组合、Spread 与换手

Quantile按definition方向排序：direction=-1时原始低值为Top，direction=0/+1时原始高值为
Top；IC/RankIC保留raw符号。Spread是方向化Top减Bottom的未来收益。Top换手越高，实际成本
侵蚀通常越强。

## 最大回撤与修复时间

非重叠horizon的最大回撤来自未扣成本的累计spread研究序列；修复/水下时间是离开高水位后
连续处于水下的最长因子观测数，并非自然日。重叠或序列不足时为 N/A。

## 行业与 Size 暴露

行业内RankIC衡量同业排序能力；Size相关性是因子与对数总市值的截面相关。Size中性RankIC
在每个截面回归 `raw factor ~ intercept + log(total_mv)`，再计算残差与未来收益的RankIC。
历史行业PIT证据不足时不能宣称行业中性。成对相关性记录数：{correlations.num_rows:,}。

### Top市值三分位暴露

Small/Mid/Large由完整PIT eligible universe按当期total_mv稳定切成三组；表中是方向化Top成员
的等权数量占比，不使用未来收益，也不是市值加权。

## 容量与成本限制

容量proxy由Top分组成交额和参与率估算，没有模拟盘口、冲击、佣金、印花税、滑点和涨跌停
排队；它不是可管理资金上限，也不是收益承诺。

{limitations_text}

## 结论阅读边界

全A结论必须同时绑定历史universe、数据snapshot、因子definition、标签对齐和完整评估配置。
高IC不自动等于可交易策略，还要检查缺失机制、行业和市值暴露、换手、容量、成本与市场阶段。
"""
    return text.encode("utf-8")


class FullAFactorReportStore:
    """Persist and verify immutable v3 evaluation/report bundles."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.objects = self.root / "objects"
        self.manifests = self.root / "manifests"
        self.temporary = self.root / ".tmp"
        for directory in (self.objects, self.manifests, self.temporary):
            directory.mkdir(parents=True, exist_ok=True)

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as error:
            raise FactorReportIntegrityError("factor report path escapes root") from error

    def _resolve_uri(self, uri: str) -> Path:
        pure = PurePosixPath(uri)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise FactorReportIntegrityError("factor report object URI is unsafe")
        path = (self.root / Path(*pure.parts)).resolve()
        self._relative(path)
        return path

    def _put(self, payload: bytes, *, suffix: str) -> tuple[Path, str]:
        digest = _sha256(payload)
        path = self.objects / digest[:2] / f"{digest}.{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise FactorReportIntegrityError("content-addressed factor report object conflicts")
            return (path, digest)
        with tempfile.NamedTemporaryFile(
            dir=self.temporary,
            prefix="factor-report-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        try:
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return (path, digest)

    def _descriptor(
        self,
        *,
        name: FactorReportObjectName,
        payload: bytes,
        suffix: str,
        media_type: str,
        row_count: int | None,
    ) -> FactorReportObject:
        path, digest = self._put(payload, suffix=suffix)
        return FactorReportObject(
            name=name,
            uri=self._relative(path),
            sha256=digest,
            media_type=media_type,
            row_count=row_count,
        )

    def persist(
        self,
        result: FullAFactorEvaluationResult,
        *,
        identity: FullAFactorReportIdentity,
        correlation_metrics: pa.Table | None = None,
        limitations: tuple[str, ...] = (),
    ) -> FullAFactorReportArtifact:
        if not result.summaries:
            raise ValueError("factor report requires at least one evaluation summary")
        if result.event_metrics.schema.remove_metadata() != full_a_event_metric_schema():
            raise ValueError("factor report event metric schema drift")
        if result.quantile_metrics.schema.remove_metadata() != full_a_quantile_metric_schema():
            raise ValueError("factor report quantile metric schema drift")
        correlations = (
            result.factor_correlations if correlation_metrics is None else correlation_metrics
        )
        if correlations.schema.remove_metadata() != factor_correlation_metric_schema():
            raise ValueError("factor report correlation metric schema drift")
        event_metrics = _sanitize_table(result.event_metrics)
        quantile_metrics = _sanitize_table(result.quantile_metrics)
        correlations = _sanitize_table(correlations)
        canonical_limitations = tuple(sorted(set(limitations)))
        summary_document = {
            "schema_id": "trademaster.factor-report-summary/v3",
            "identity": identity.model_dump(mode="json"),
            "limitations": canonical_limitations,
            "summaries": [item.model_dump(mode="json") for item in result.summaries],
            "tables": {
                "event_metrics": event_metrics.num_rows,
                "quantile_metrics": quantile_metrics.num_rows,
                "correlation_metrics": correlations.num_rows,
            },
        }
        metric_descriptors = [
            self._descriptor(
                name="correlation_metrics",
                payload=_parquet_bytes(correlations),
                suffix="parquet",
                media_type=_PARQUET_MEDIA_TYPE,
                row_count=correlations.num_rows,
            ),
            self._descriptor(
                name="event_metrics",
                payload=_parquet_bytes(event_metrics),
                suffix="parquet",
                media_type=_PARQUET_MEDIA_TYPE,
                row_count=event_metrics.num_rows,
            ),
            self._descriptor(
                name="quantile_metrics",
                payload=_parquet_bytes(quantile_metrics),
                suffix="parquet",
                media_type=_PARQUET_MEDIA_TYPE,
                row_count=quantile_metrics.num_rows,
            ),
            self._descriptor(
                name="summary",
                payload=_canonical_json(summary_document),
                suffix="json",
                media_type="application/json",
                row_count=None,
            ),
        ]
        report_id = _sha256(
            _canonical_json(
                {
                    "identity": identity.model_dump(mode="json"),
                    "limitations": canonical_limitations,
                    "metric_objects": [item.model_dump(mode="json") for item in metric_descriptors],
                }
            )
        )
        report_descriptors = [
            self._descriptor(
                name="report_html",
                payload=_render_html(
                    result,
                    identity=identity,
                    limitations=canonical_limitations,
                    event_metrics=event_metrics,
                    quantile_metrics=quantile_metrics,
                    correlations=correlations,
                ),
                suffix="html",
                media_type="text/html; charset=utf-8",
                row_count=None,
            ),
            self._descriptor(
                name="report_markdown",
                payload=_render_markdown(
                    result,
                    identity=identity,
                    limitations=canonical_limitations,
                    correlations=correlations,
                ),
                suffix="md",
                media_type="text/markdown; charset=utf-8",
                row_count=None,
            ),
        ]
        manifest = FullAFactorReportManifest(
            report_id=report_id,
            identity=identity,
            limitations=canonical_limitations,
            objects=tuple(
                sorted((*metric_descriptors, *report_descriptors), key=lambda item: item.name)
            ),
        )
        manifest_payload = _canonical_json(manifest.model_dump(mode="json"))
        manifest_sha256 = _sha256(manifest_payload)
        manifest_path = self.manifests / f"{manifest_sha256}.json"
        if manifest_path.exists():
            if manifest_path.read_bytes() != manifest_payload:
                raise FactorReportIntegrityError(
                    "content-addressed factor report manifest conflicts"
                )
        else:
            with tempfile.NamedTemporaryFile(
                dir=self.temporary,
                prefix="factor-report-manifest-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(manifest_payload)
            try:
                temporary.replace(manifest_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return FullAFactorReportArtifact(
            report_id=report_id,
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
        )

    def verify(self, manifest_path: Path) -> FullAFactorReportManifest:
        path = manifest_path.resolve()
        self._relative(path)
        if not path.is_file() or path.parent != self.manifests:
            raise FactorReportIntegrityError("factor report manifest path is invalid")
        payload = path.read_bytes()
        digest = _sha256(payload)
        if path.name != f"{digest}.json":
            raise FactorReportIntegrityError("factor report manifest hash mismatch")
        try:
            manifest = FullAFactorReportManifest.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise FactorReportIntegrityError("factor report manifest is invalid") from error
        expected_schemas = {
            "correlation_metrics": factor_correlation_metric_schema(),
            "event_metrics": full_a_event_metric_schema(),
            "quantile_metrics": full_a_quantile_metric_schema(),
        }
        for item in manifest.objects:
            object_path = self._resolve_uri(item.uri)
            if object_path.name.split(".", 1)[0] != item.sha256:
                raise FactorReportIntegrityError(
                    "factor report object path is not content addressed"
                )
            if not object_path.is_file() or _sha256(object_path.read_bytes()) != item.sha256:
                raise FactorReportIntegrityError("factor report object hash mismatch")
            if item.name in expected_schemas:
                try:
                    table = pq.read_table(object_path)
                except (OSError, pa.ArrowException) as error:
                    raise FactorReportIntegrityError(
                        "factor report parquet is unreadable"
                    ) from error
                if (
                    table.schema.remove_metadata() != expected_schemas[item.name]
                    or table.num_rows != item.row_count
                ):
                    raise FactorReportIntegrityError("factor report parquet contract mismatch")
            elif item.row_count is not None:
                raise FactorReportIntegrityError("factor report non-table row count is invalid")
        summary_object = next(item for item in manifest.objects if item.name == "summary")
        try:
            summary = json.loads(
                self._resolve_uri(summary_object.uri).read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
            parsed_summaries = tuple(
                FullAFactorDiagnostics.model_validate_json(_canonical_json(item), strict=True)
                for item in summary["summaries"]
            )
        except (KeyError, TypeError, UnicodeDecodeError, ValueError) as error:
            raise FactorReportIntegrityError("factor report summary is invalid") from error
        table_counts = {
            item.name: item.row_count for item in manifest.objects if item.name in expected_schemas
        }
        if (
            not isinstance(summary, dict)
            or set(summary) != {"schema_id", "identity", "limitations", "summaries", "tables"}
            or summary["schema_id"] != "trademaster.factor-report-summary/v3"
            or _canonical_json(summary["identity"])
            != _canonical_json(manifest.identity.model_dump(mode="json"))
            or tuple(summary["limitations"]) != manifest.limitations
            or not parsed_summaries
            or summary["tables"] != table_counts
        ):
            raise FactorReportIntegrityError("factor report summary contract mismatch")
        return manifest


__all__ = [
    "FactorReportIntegrityError",
    "FactorReportObject",
    "FullAFactorReportArtifact",
    "FullAFactorReportIdentity",
    "FullAFactorReportManifest",
    "FullAFactorReportStore",
    "factor_correlation_metric_schema",
]
