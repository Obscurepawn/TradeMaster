"""Managed value, quality, growth, and balance-sheet factor definitions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pyarrow as pa

from trademaster.contracts import FactorContext, FactorSpec

from . import FactorOutputError, factor_output_schema
from .cross_section import (
    CrossSectionCompositeFactor,
    CrossSectionTransformSpec,
    WeightedFactorComponent,
)
from .management import FactorRegistration


class FundamentalMetricFactor:
    """One transparent transformation from a PIT fundamental input field."""

    def __init__(
        self,
        *,
        factor_id: str,
        version: str,
        dataset: str,
        source_field: str,
        transform: Literal["identity", "positive_reciprocal", "negate"],
    ) -> None:
        self.source_field = source_field
        self.transform = transform
        self.spec = FactorSpec(
            factor_id=factor_id,
            version=version,
            dependencies=(f"{dataset}.{source_field}",),
            lookback_sessions=0,
        )

    def compute(self, context: FactorContext) -> pa.Table:
        required = {"instrument_id", "event_time", self.source_field}
        if not required <= set(context.inputs.column_names):
            raise FactorOutputError("fundamental factor input schema is incomplete")
        output: list[dict[str, object]] = []
        for row in sorted(
            context.inputs.select(tuple(sorted(required))).to_pylist(),
            key=lambda item: (item["event_time"], str(item["instrument_id"])),
        ):
            raw = row[self.source_field]
            value = float(raw) if raw is not None else 0.0
            valid = raw is not None and math.isfinite(value)
            if self.transform == "positive_reciprocal":
                valid = valid and value > 0
                value = 1.0 / value if valid else 0.0
            elif self.transform == "negate":
                value = -value if valid else 0.0
            elif not valid:
                value = 0.0
            output.append(
                {
                    "instrument_id": str(row["instrument_id"]),
                    "event_time": row["event_time"],
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                    "value": value,
                    "is_valid": valid,
                }
            )
        return pa.Table.from_pylist(output, schema=factor_output_schema())


@dataclass(frozen=True, slots=True)
class FundamentalFactorSuite:
    atomic: tuple[FactorRegistration, ...]
    industry_components: tuple[WeightedFactorComponent, ...]
    industry_composite: FactorRegistration
    global_composite: FactorRegistration

    @property
    def external_dataset_fields(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                {
                    ("fundamental_inputs", item.factor.source_field)
                    for item in self.atomic
                    if isinstance(item.factor, FundamentalMetricFactor)
                }
            )
        )

    @property
    def registrations(self) -> tuple[FactorRegistration, ...]:
        return (*self.atomic, self.industry_composite, self.global_composite)


def fundamental_factor_suite() -> FundamentalFactorSuite:
    definitions = (
        (
            "fundamental.earnings_yield",
            "daily_basic",
            "pe_ttm",
            "positive_reciprocal",
            Decimal("0.15"),
            True,
            "Positive reciprocal trailing earnings valuation",
        ),
        (
            "fundamental.book_yield",
            "daily_basic",
            "pb",
            "positive_reciprocal",
            Decimal("0.10"),
            True,
            "Positive reciprocal price-to-book valuation",
        ),
        (
            "fundamental.dividend_yield",
            "daily_basic",
            "dv_ttm",
            "identity",
            Decimal("0.10"),
            False,
            "Trailing dividend yield",
        ),
        (
            "fundamental.roe",
            "financial_indicators",
            "roe",
            "identity",
            Decimal("0.15"),
            False,
            "Return on equity",
        ),
        (
            "fundamental.gross_margin",
            "financial_indicators",
            "grossprofit_margin",
            "identity",
            Decimal("0.10"),
            False,
            "Gross profit margin",
        ),
        (
            "fundamental.ocf_to_revenue",
            "financial_indicators",
            "ocf_to_or",
            "identity",
            Decimal("0.10"),
            False,
            "Operating cash flow to revenue",
        ),
        (
            "fundamental.sales_growth",
            "financial_indicators",
            "q_sales_yoy",
            "identity",
            Decimal("0.10"),
            False,
            "Quarterly sales year-on-year growth",
        ),
        (
            "fundamental.profit_growth",
            "financial_indicators",
            "q_profit_yoy",
            "identity",
            Decimal("0.10"),
            False,
            "Quarterly profit year-on-year growth",
        ),
        (
            "fundamental.debt_safety",
            "financial_indicators",
            "debt_to_assets",
            "negate",
            Decimal("0.10"),
            False,
            "Negative debt-to-assets ratio",
        ),
    )
    atomic: list[FactorRegistration] = []
    components: list[WeightedFactorComponent] = []
    for factor_id, dataset, source_field, transform, weight, required, description in definitions:
        factor = FundamentalMetricFactor(
            factor_id=factor_id,
            version="1",
            dataset="fundamental_inputs",
            source_field=source_field,
            transform=transform,  # type: ignore[arg-type]
        )
        atomic.append(
            FactorRegistration.create(
                factor,
                family="fundamental",
                description=description,
                parameters={"source_field": source_field, "transform": transform},
                frequency="quarterly" if dataset == "financial_indicators" else "daily",
                scope="cross_sectional",
                unit="score_input",
                direction=1,
            )
        )
        components.append(
            WeightedFactorComponent(
                factor_id=factor_id,
                factor_version="1",
                weight=weight,
                required=required,
            )
        )
    component_tuple = tuple(components)

    def composite(group_field: str | None, factor_id: str) -> FactorRegistration:
        transform_spec = CrossSectionTransformSpec(
            group_field=group_field,
            winsor_lower=0.05,
            winsor_upper=0.95,
            minimum_valid_components=5,
            missing_policy="neutral_zero",
            weight_normalization="sum_to_one",
        )
        factor = CrossSectionCompositeFactor(
            factor_id=factor_id,
            version="1",
            components=component_tuple,
            transform=transform_spec,
        )
        return FactorRegistration.create(
            factor,
            family="fundamental_composite",
            description=(
                "Industry-relative nine-metric fundamental composite"
                if group_field is not None
                else "Global nine-metric fundamental composite"
            ),
            parameters={
                "components": [
                    {
                        "factor_id": item.factor_id,
                        "factor_version": item.factor_version,
                        "weight": item.weight,
                        "required": item.required,
                    }
                    for item in component_tuple
                ],
                "group_field": group_field,
                "winsor_lower": transform_spec.winsor_lower,
                "winsor_upper": transform_spec.winsor_upper,
                "minimum_valid_components": transform_spec.minimum_valid_components,
                "missing_policy": transform_spec.missing_policy,
                "weight_normalization": transform_spec.weight_normalization,
            },
            frequency="quarterly",
            scope=("grouped_cross_sectional" if group_field is not None else "cross_sectional"),
            unit="zscore",
            direction=1,
        )

    return FundamentalFactorSuite(
        atomic=tuple(atomic),
        industry_components=component_tuple,
        industry_composite=composite("industry_id", "fundamental.composite.industry_relative"),
        global_composite=composite(None, "fundamental.composite.global"),
    )


__all__ = [
    "FundamentalFactorSuite",
    "FundamentalMetricFactor",
    "fundamental_factor_suite",
]
