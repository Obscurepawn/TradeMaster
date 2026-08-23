"""Versioned factor registry and provenance-preserving execution."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

import pyarrow as pa

from trademaster.contracts import (
    Factor,
    FactorContext,
    FactorSpec,
    bind_snapshot_provenance,
)
from trademaster.data.registry import (
    DatasetRegistry,
    default_dataset_registry,
)

from .management import (
    DatasetFieldDependency,
    FactorDefinition,
    FactorDependency,
    FactorRegistration,
    ManagedFactorRegistry,
)
from .public_cn import builtin_public_factor_library
from .public_factors import (
    HuataiLogMarketCapFactor,
    PublicExecutableFactorSuite,
    ReviewedGtjaPriceFactor,
    public_executable_factor_suite,
)
from .public_library import (
    FactorCandidateRecord,
    FactorCollectionDefinition,
    FactorSourceRecord,
    PublicFactorIntegrityError,
    PublicFactorLibrary,
    PublicFactorLibraryManifest,
    PublicFactorObjectRef,
    PublicFactorStore,
)
from .storage import (
    FactorArtifact,
    FactorCatalog,
    FactorIntegrityError,
    FactorManager,
    FactorMaterialization,
    FactorScope,
    ParentMaterializationRef,
)


class FactorOutputError(RuntimeError):
    """A factor violated its registered output contract."""


def factor_output_schema() -> pa.Schema:
    """Return the frozen M2 canonical factor-output schema."""

    fields: Any = [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("factor_id", pa.string(), nullable=False),
        pa.field("factor_version", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=False),
        pa.field("is_valid", pa.bool_(), nullable=False),
    ]
    return pa.schema(fields)


class FactorRegistry:
    """Immutable registry keyed by stable factor ID and version."""

    def __init__(
        self,
        factors: Iterable[Factor],
        *,
        dataset_registry: DatasetRegistry | None = None,
        external_factor_identities: tuple[tuple[str, str], ...] = (),
        external_dataset_fields: tuple[tuple[str, str], ...] = (),
    ) -> None:
        if external_factor_identities != tuple(sorted(set(external_factor_identities))):
            raise ValueError("external factor identities must be unique and sorted")
        if external_dataset_fields != tuple(sorted(set(external_dataset_fields))):
            raise ValueError("external dataset fields must be unique and sorted")
        by_identity: dict[tuple[str, str], Factor] = {}
        for factor in factors:
            identity = (factor.spec.factor_id, factor.spec.version)
            if any(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is None for value in identity
            ):
                raise ValueError("factor identity is invalid")
            if identity in by_identity:
                raise ValueError("duplicate factor identity")
            if factor.spec.dependencies != tuple(sorted(set(factor.spec.dependencies))):
                raise ValueError("factor dependencies must be unique and sorted")
            by_identity[identity] = factor
        known_factor_identities = set(by_identity) | set(external_factor_identities)
        registry = dataset_registry or default_dataset_registry()
        for identity, factor in by_identity.items():
            for dependency in factor.spec.dependencies:
                factor_match = re.fullmatch(
                    r"factor:([A-Za-z0-9][A-Za-z0-9_.-]*)@"
                    r"([A-Za-z0-9][A-Za-z0-9_.-]*)",
                    dependency,
                )
                if factor_match is not None:
                    referenced = (factor_match.group(1), factor_match.group(2))
                    if referenced == identity or referenced not in known_factor_identities:
                        raise ValueError(f"unknown factor dependency: {dependency}")
                    continue
                dataset_match = re.fullmatch(r"([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)", dependency)
                if dataset_match is None:
                    raise ValueError(f"unknown factor dependency: {dependency}")
                dataset, field = dataset_match.groups()
                if (dataset, field) in external_dataset_fields:
                    continue
                try:
                    spec = registry[dataset]
                except KeyError as error:
                    raise ValueError(f"unknown factor dependency: {dependency}") from error
                if field not in spec.query_fields:
                    raise ValueError(f"unknown factor dependency: {dependency}")
        self._by_identity = by_identity

    @property
    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._by_identity))

    def get(self, factor_id: str, version: str) -> Factor:
        try:
            return self._by_identity[(factor_id, version)]
        except KeyError as error:
            raise KeyError(f"unknown factor: {factor_id}@{version}") from error


class FactorExecutor:
    """Execute a registered factor and enforce the canonical output boundary."""

    def __init__(self, registry: FactorRegistry) -> None:
        self.registry = registry

    def compute(
        self,
        factor_id: str,
        version: str,
        context: FactorContext,
    ) -> pa.Table:
        factor = self.registry.get(factor_id, version)
        table = factor.compute(context)
        if table.schema.remove_metadata() != factor_output_schema():
            raise FactorOutputError("factor output schema drift")
        rows = table.to_pylist()
        if any(
            row["factor_id"] != factor.spec.factor_id
            or row["factor_version"] != factor.spec.version
            for row in rows
        ):
            raise FactorOutputError("factor output identity drift")
        if any(row["event_time"] > context.as_of for row in rows):
            raise FactorOutputError("factor output contains future event time")
        keys = [(row["instrument_id"], row["event_time"]) for row in rows]
        if len(keys) != len(set(keys)):
            raise FactorOutputError("factor output contains duplicate observations")
        if any(row["is_valid"] and not math.isfinite(float(row["value"])) for row in rows):
            raise FactorOutputError("valid factor output must be finite")
        return bind_snapshot_provenance(table.replace_schema_metadata(None), context.snapshot)


class MomentumFactor:
    """Per-instrument close-to-close momentum over an exact session lag."""

    def __init__(self, *, lookback_sessions: int, version: str = "1") -> None:
        if lookback_sessions < 1:
            raise ValueError("momentum lookback must be positive")
        self.lookback_sessions = lookback_sessions
        self.spec = FactorSpec(
            factor_id=f"momentum_{lookback_sessions}",
            version=version,
            dependencies=("daily_bars.close",),
            lookback_sessions=lookback_sessions,
        )

    def compute(self, context: FactorContext) -> pa.Table:
        required = {"instrument_id", "event_time", "close"}
        if not required <= set(context.inputs.column_names):
            raise FactorOutputError("momentum input schema is incomplete")
        ordered = sorted(
            context.inputs.select(tuple(sorted(required))).to_pylist(),
            key=lambda row: (str(row["instrument_id"]), row["event_time"]),
        )
        histories: dict[str, list[float]] = {}
        output: list[dict[str, object]] = []
        for row in ordered:
            instrument_id = str(row["instrument_id"])
            close = float(row["close"])
            history = histories.setdefault(instrument_id, [])
            valid = (
                len(history) >= self.lookback_sessions
                and math.isfinite(close)
                and close > 0
                and math.isfinite(history[-self.lookback_sessions])
                and history[-self.lookback_sessions] > 0
            )
            value = close / history[-self.lookback_sessions] - 1.0 if valid else 0.0
            output.append(
                {
                    "instrument_id": instrument_id,
                    "event_time": row["event_time"],
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                    "value": value,
                    "is_valid": valid,
                }
            )
            history.append(close)
        return pa.Table.from_pylist(output, schema=factor_output_schema())


class ValueFactor:
    """Cross-sectional size/value proxy using the negative log market value."""

    def __init__(
        self,
        *,
        market_value_field: str = "float_market_value",
        version: str = "1",
    ) -> None:
        if market_value_field not in {"float_market_value", "total_market_value"}:
            raise ValueError("unsupported market value field")
        self.market_value_field = market_value_field
        self.spec = FactorSpec(
            factor_id=f"value_{market_value_field}",
            version=version,
            dependencies=(f"daily_basic.{market_value_field}",),
            lookback_sessions=0,
        )

    def compute(self, context: FactorContext) -> pa.Table:
        required = {"instrument_id", "event_time", self.market_value_field}
        if not required <= set(context.inputs.column_names):
            raise FactorOutputError("value input schema is incomplete")
        output: list[dict[str, object]] = []
        for row in sorted(
            context.inputs.select(tuple(sorted(required))).to_pylist(),
            key=lambda item: (str(item["instrument_id"]), item["event_time"]),
        ):
            market_value = float(row[self.market_value_field])
            valid = math.isfinite(market_value) and market_value > 0
            output.append(
                {
                    "instrument_id": str(row["instrument_id"]),
                    "event_time": row["event_time"],
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                    "value": -math.log(market_value) if valid else 0.0,
                    "is_valid": valid,
                }
            )
        return pa.Table.from_pylist(output, schema=factor_output_schema())


class CloseLevelFactor:
    """Explicit unadjusted close level for price-threshold timing rules."""

    def __init__(self, *, version: str = "1") -> None:
        self.spec = FactorSpec(
            factor_id="raw_close_level",
            version=version,
            dependencies=("daily_bars.close",),
            lookback_sessions=0,
        )

    def compute(self, context: FactorContext) -> pa.Table:
        required = {"instrument_id", "event_time", "close"}
        if not required <= set(context.inputs.column_names):
            raise FactorOutputError("close-level input schema is incomplete")
        output: list[dict[str, object]] = []
        for row in sorted(
            context.inputs.select(tuple(sorted(required))).to_pylist(),
            key=lambda item: (str(item["instrument_id"]), item["event_time"]),
        ):
            close = float(row["close"])
            valid = math.isfinite(close) and close > 0
            output.append(
                {
                    "instrument_id": str(row["instrument_id"]),
                    "event_time": row["event_time"],
                    "factor_id": self.spec.factor_id,
                    "factor_version": self.spec.version,
                    "value": close if valid else 0.0,
                    "is_valid": valid,
                }
            )
        return pa.Table.from_pylist(output, schema=factor_output_schema())


@dataclass(frozen=True, slots=True)
class CompositeComponent:
    factor_id: str
    factor_version: str
    weight: Decimal
    direction: int = 1

    def __post_init__(self) -> None:
        if not self.factor_id or not self.factor_version:
            raise ValueError("composite component identity cannot be empty")
        if self.weight <= 0 or not self.weight.is_finite():
            raise ValueError("composite component weight must be positive")
        if self.direction not in {-1, 1}:
            raise ValueError("composite component direction must be -1 or 1")


class CompositeFactor:
    """Cross-sectional winsorized z-score combination of factor observations."""

    def __init__(
        self,
        *,
        factor_id: str,
        components: tuple[CompositeComponent, ...],
        winsor_z: float,
        version: str = "1",
    ) -> None:
        if not factor_id:
            raise ValueError("composite factor ID cannot be empty")
        identities = [(component.factor_id, component.factor_version) for component in components]
        if not components or len(identities) != len(set(identities)):
            raise ValueError("composite components must be nonempty and unique")
        if not math.isfinite(winsor_z) or winsor_z <= 0:
            raise ValueError("winsor_z must be positive")
        self.components = components
        self.winsor_z = winsor_z
        self.spec = FactorSpec(
            factor_id=factor_id,
            version=version,
            dependencies=tuple(
                sorted(
                    f"factor:{component.factor_id}@{component.factor_version}"
                    for component in components
                )
            ),
            lookback_sessions=0,
        )

    @staticmethod
    def _zscores(values: dict[str, float], winsor_z: float) -> dict[str, float]:
        mean = sum(values.values()) / len(values)
        variance = sum((value - mean) ** 2 for value in values.values()) / len(values)
        standard_deviation = math.sqrt(variance)
        if standard_deviation == 0:
            return {instrument_id: 0.0 for instrument_id in values}
        return {
            instrument_id: max(-winsor_z, min(winsor_z, (value - mean) / standard_deviation))
            for instrument_id, value in values.items()
        }

    def compute(self, context: FactorContext) -> pa.Table:
        if context.inputs.schema.remove_metadata() != factor_output_schema():
            raise FactorOutputError("composite input schema drift")
        requested = {
            (component.factor_id, component.factor_version): component
            for component in self.components
        }
        observations: dict[tuple[datetime, str], dict[tuple[str, str], tuple[float, bool]]] = {}
        for row in context.inputs.to_pylist():
            identity = (str(row["factor_id"]), str(row["factor_version"]))
            if identity not in requested:
                continue
            key = (
                cast(datetime, row["event_time"]),
                str(row["instrument_id"]),
            )
            by_component = observations.setdefault(key, {})
            if identity in by_component:
                raise FactorOutputError("duplicate composite component observation")
            by_component[identity] = (float(row["value"]), bool(row["is_valid"]))

        event_times = sorted({key[0] for key in observations})
        output: list[dict[str, object]] = []
        total_weight = sum((item.weight for item in self.components), Decimal(0))
        for event_time in event_times:
            instruments = sorted(
                instrument
                for candidate_time, instrument in observations
                if candidate_time == event_time
            )
            eligible = {
                instrument
                for instrument in instruments
                if len(observations[(event_time, instrument)]) == len(requested)
                and all(
                    valid and math.isfinite(value)
                    for value, valid in observations[(event_time, instrument)].values()
                )
            }
            component_zscores: dict[tuple[str, str], dict[str, float]] = {}
            for identity in requested:
                values = {
                    instrument: observations[(event_time, instrument)][identity][0]
                    for instrument in eligible
                }
                if values:
                    component_zscores[identity] = self._zscores(values, self.winsor_z)
            for instrument in instruments:
                valid = instrument in eligible
                value = 0.0
                if valid:
                    value = sum(
                        float(component.weight / total_weight)
                        * component.direction
                        * component_zscores[(component.factor_id, component.factor_version)][
                            instrument
                        ]
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
    "CloseLevelFactor",
    "CompositeComponent",
    "CompositeFactor",
    "DatasetFieldDependency",
    "FactorArtifact",
    "FactorCandidateRecord",
    "FactorCatalog",
    "FactorCollectionDefinition",
    "FactorDefinition",
    "FactorDependency",
    "FactorExecutor",
    "FactorIntegrityError",
    "FactorManager",
    "FactorMaterialization",
    "FactorOutputError",
    "FactorRegistration",
    "FactorRegistry",
    "FactorScope",
    "FactorSourceRecord",
    "HuataiLogMarketCapFactor",
    "ManagedFactorRegistry",
    "MomentumFactor",
    "ParentMaterializationRef",
    "PublicExecutableFactorSuite",
    "PublicFactorIntegrityError",
    "PublicFactorLibrary",
    "PublicFactorLibraryManifest",
    "PublicFactorObjectRef",
    "PublicFactorStore",
    "ReviewedGtjaPriceFactor",
    "ValueFactor",
    "builtin_public_factor_library",
    "factor_output_schema",
    "public_executable_factor_suite",
]
