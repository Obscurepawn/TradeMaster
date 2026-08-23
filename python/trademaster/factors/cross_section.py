"""Configurable cross-sectional factor transforms and composite scoring."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

import pyarrow as pa

from trademaster.contracts import FactorContext, FactorSpec

from . import FactorOutputError, factor_output_schema


@dataclass(frozen=True, slots=True)
class WeightedFactorComponent:
    factor_id: str
    factor_version: str
    weight: Decimal
    required: bool = False

    def __post_init__(self) -> None:
        if (
            not self.factor_id
            or not self.factor_version
            or self.weight <= 0
            or not self.weight.is_finite()
        ):
            raise ValueError("weighted factor component is invalid")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.factor_id, self.factor_version)


@dataclass(frozen=True, slots=True)
class CrossSectionTransformSpec:
    group_field: str | None
    winsor_lower: float
    winsor_upper: float
    minimum_valid_components: int
    missing_policy: Literal["neutral_zero"]
    weight_normalization: Literal["sum_to_one", "none"] = "sum_to_one"

    def __post_init__(self) -> None:
        if (
            (self.group_field is not None and not self.group_field)
            or not 0 <= self.winsor_lower < self.winsor_upper <= 1
            or self.minimum_valid_components < 1
        ):
            raise ValueError("cross-section transform spec is invalid")


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    fraction = location - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def assemble_grouped_factor_inputs(
    factor_tables: tuple[pa.Table, ...],
    *,
    dimensions: pa.Table,
    group_field: str | None,
) -> pa.Table:
    """Attach one canonical grouping dimension to snapshot-aligned factor rows."""

    if not factor_tables:
        raise ValueError("grouped factor assembly requires factor tables")
    snapshot_metadata = {
        key: value
        for key, value in (factor_tables[0].schema.metadata or {}).items()
        if key.startswith(b"trademaster.snapshot_")
    }
    if any(
        table.schema.remove_metadata() != factor_output_schema()
        or {
            key: value
            for key, value in (table.schema.metadata or {}).items()
            if key.startswith(b"trademaster.snapshot_")
        }
        != snapshot_metadata
        for table in factor_tables
    ):
        raise FactorOutputError("atomic factor tables are not schema/provenance aligned")
    required_dimensions = {"instrument_id", "event_time"}
    if group_field is not None:
        required_dimensions.add(group_field)
    if not required_dimensions <= set(dimensions.column_names):
        raise FactorOutputError("factor dimensions are incomplete")
    dimension_rows = dimensions.select(tuple(sorted(required_dimensions))).to_pylist()
    by_key: dict[tuple[str, datetime], dict[str, object]] = {}
    for row in dimension_rows:
        key = (str(row["instrument_id"]), cast(datetime, row["event_time"]))
        if key in by_key:
            raise FactorOutputError("factor dimensions contain duplicate observations")
        by_key[key] = row
    expected_keys = set(by_key)
    output: list[dict[str, object]] = []
    for table in factor_tables:
        rows = table.to_pylist()
        keys = {(str(row["instrument_id"]), cast(datetime, row["event_time"])) for row in rows}
        if keys != expected_keys or len(keys) != len(rows):
            raise FactorOutputError("atomic factor coverage differs from grouping dimensions")
        for row in rows:
            key = (str(row["instrument_id"]), cast(datetime, row["event_time"]))
            value = dict(row)
            if group_field is not None:
                group = by_key[key][group_field]
                if not isinstance(group, str) or not group:
                    raise FactorOutputError("factor grouping value must be a nonempty string")
                value[group_field] = group
            output.append(value)
    fields = list(factor_output_schema())
    if group_field is not None:
        fields.append(pa.field(group_field, pa.string(), nullable=False))
    return pa.Table.from_pylist(output, schema=pa.schema(fields)).replace_schema_metadata(
        snapshot_metadata
    )


def composite_valid_counts(
    inputs: pa.Table,
    components: tuple[WeightedFactorComponent, ...],
) -> dict[tuple[datetime, str], int]:
    requested = {item.identity for item in components}
    counts: dict[tuple[datetime, str], int] = {}
    seen: set[tuple[datetime, str, tuple[str, str]]] = set()
    for row in inputs.to_pylist():
        identity = (str(row["factor_id"]), str(row["factor_version"]))
        if identity not in requested:
            continue
        key = (cast(datetime, row["event_time"]), str(row["instrument_id"]))
        observation = (*key, identity)
        if observation in seen:
            raise FactorOutputError("duplicate composite component observation")
        seen.add(observation)
        if bool(row["is_valid"]) and math.isfinite(float(row["value"])):
            counts[key] = counts.get(key, 0) + 1
        else:
            counts.setdefault(key, 0)
    return counts


class CrossSectionCompositeFactor:
    """Winsorize raw components, standardize per event/group, and combine."""

    def __init__(
        self,
        *,
        factor_id: str,
        version: str,
        components: tuple[WeightedFactorComponent, ...],
        transform: CrossSectionTransformSpec,
    ) -> None:
        identities = tuple(item.identity for item in components)
        if (
            not components
            or len(identities) != len(set(identities))
            or transform.minimum_valid_components > len(components)
        ):
            raise ValueError("composite factor components are invalid")
        self.components = components
        self.transform = transform
        self.spec = FactorSpec(
            factor_id=factor_id,
            version=version,
            dependencies=tuple(
                sorted(f"factor:{item.factor_id}@{item.factor_version}" for item in components)
            ),
            lookback_sessions=0,
        )

    def compute(self, context: FactorContext) -> pa.Table:
        required = set(factor_output_schema().names)
        if self.transform.group_field is not None:
            required.add(self.transform.group_field)
        if not required <= set(context.inputs.column_names):
            raise FactorOutputError("composite factor input schema is incomplete")
        requested = {item.identity: item for item in self.components}
        observations: dict[tuple[datetime, str], dict[tuple[str, str], tuple[float, bool]]] = {}
        group_by_observation: dict[tuple[datetime, str], str] = {}
        for row in context.inputs.to_pylist():
            identity = (str(row["factor_id"]), str(row["factor_version"]))
            if identity not in requested:
                continue
            key = (cast(datetime, row["event_time"]), str(row["instrument_id"]))
            by_component = observations.setdefault(key, {})
            if identity in by_component:
                raise FactorOutputError("duplicate composite component observation")
            by_component[identity] = (float(row["value"]), bool(row["is_valid"]))
            group = (
                "__all__"
                if self.transform.group_field is None
                else str(row[self.transform.group_field])
            )
            previous_group = group_by_observation.setdefault(key, group)
            if previous_group != group or not group:
                raise FactorOutputError("composite grouping identity is inconsistent")

        total_weight = sum((item.weight for item in self.components), Decimal(0))
        groups = sorted(
            {
                (event_time, group_by_observation[(event_time, instrument)])
                for event_time, instrument in observations
            }
        )
        output: list[dict[str, object]] = []
        for event_time, group in groups:
            instruments = sorted(
                instrument
                for candidate_time, instrument in observations
                if candidate_time == event_time
                and group_by_observation[(candidate_time, instrument)] == group
            )
            eligible: set[str] = set()
            for instrument in instruments:
                component_values = observations[(event_time, instrument)]
                valid_identities = {
                    identity
                    for identity, (value, valid) in component_values.items()
                    if valid and math.isfinite(value)
                }
                if len(valid_identities) >= self.transform.minimum_valid_components and all(
                    not component.required or component.identity in valid_identities
                    for component in self.components
                ):
                    eligible.add(instrument)
            standardized: dict[tuple[str, str], dict[str, float]] = {}
            for identity in requested:
                values = {
                    instrument: observations[(event_time, instrument)][identity][0]
                    for instrument in instruments
                    if instrument in eligible
                    if identity in observations[(event_time, instrument)]
                    and observations[(event_time, instrument)][identity][1]
                    and math.isfinite(observations[(event_time, instrument)][identity][0])
                }
                if not values:
                    standardized[identity] = {}
                    continue
                lower = _quantile(list(values.values()), self.transform.winsor_lower)
                upper = _quantile(list(values.values()), self.transform.winsor_upper)
                clipped = {
                    instrument: min(max(value, lower), upper)
                    for instrument, value in values.items()
                }
                mean = statistics.fmean(clipped.values())
                deviation = statistics.pstdev(clipped.values())
                standardized[identity] = {
                    instrument: 0.0 if deviation == 0 else (value - mean) / deviation
                    for instrument, value in clipped.items()
                }
            for instrument in instruments:
                valid = instrument in eligible
                value = 0.0
                if valid:
                    value = sum(
                        float(
                            component.weight
                            / (
                                total_weight
                                if self.transform.weight_normalization == "sum_to_one"
                                else Decimal(1)
                            )
                        )
                        * standardized[component.identity].get(instrument, 0.0)
                        for component in self.components
                    )
                output.append(
                    {
                        "instrument_id": instrument,
                        "event_time": event_time,
                        "factor_id": self.spec.factor_id,
                        "factor_version": self.spec.version,
                        "value": value,
                        "is_valid": valid,
                    }
                )
        return pa.Table.from_pylist(output, schema=factor_output_schema())


__all__ = [
    "CrossSectionCompositeFactor",
    "CrossSectionTransformSpec",
    "WeightedFactorComponent",
    "assemble_grouped_factor_inputs",
    "composite_valid_counts",
]
