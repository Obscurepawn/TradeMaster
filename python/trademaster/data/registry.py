"""Canonical dataset registry and deterministic request normalization."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.contracts import DatasetRequest

_SYSTEM_FIELDS = ("event_time", "known_at", "source_revision")


class CoverageKeyMode(StrEnum):
    SESSION = "session"
    BUSINESS_KEY = "business_key"


class CoverageShape(StrEnum):
    KEY_SET = "key_set"
    INSTRUMENT_SESSION_MATRIX = "instrument_session_matrix"


class CanonicalFieldType(StrEnum):
    UTF8 = "utf8"
    DATE32 = "date32"
    BOOLEAN = "boolean"
    FLOAT64 = "float64"
    TIMESTAMP_US_UTC = "timestamp_us_utc"


class DatasetSpec(BaseModel):
    """Immutable storage and identity contract for one canonical dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: str = Field(min_length=1)
    provider_endpoint: str = Field(min_length=1)
    primary_key: tuple[str, ...]
    required_fields: tuple[str, ...]
    field_types: tuple[tuple[str, CanonicalFieldType], ...]
    partition_fields: tuple[str, ...]
    coverage_key_mode: CoverageKeyMode
    coverage_key_fields: tuple[str, ...]
    coverage_shape: CoverageShape = CoverageShape.KEY_SET
    revision_order_fields: tuple[str, ...] = ()
    schema_version: str = "1"

    @model_validator(mode="after")
    def validate_contract(self) -> DatasetSpec:
        groups = {
            "primary key": self.primary_key,
            "required fields": self.required_fields,
            "partition fields": self.partition_fields,
            "coverage key fields": self.coverage_key_fields,
        }
        if re.fullmatch(r"[a-z][a-z0-9_]*", self.name) is None:
            raise ValueError("dataset name must be snake_case")
        for label, values in groups.items():
            if not values or len(values) != len(set(values)):
                raise ValueError(f"{label} must be non-empty and unique")
            if any(re.fullmatch(r"[a-z][a-z0-9_]*", value) is None for value in values):
                raise ValueError(f"{label} must contain field identifiers")
        if not set(self.primary_key) <= set(self.required_fields):
            raise ValueError("primary key must be contained in required fields")
        if tuple(name for name, _ in self.field_types) != self.required_fields:
            raise ValueError("field types must exactly follow required fields")
        if not set(self.revision_order_fields) <= set(self.required_fields):
            raise ValueError("revision order fields must be contained in required fields")
        return self

    @property
    def query_fields(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.required_fields)
                | set(self.partition_fields)
                | set(self.coverage_key_fields)
                | set(_SYSTEM_FIELDS)
            )
        )

    @property
    def arrow_schema(self) -> pa.Schema:
        arrow_types = {
            CanonicalFieldType.UTF8: pa.string(),
            CanonicalFieldType.DATE32: pa.date32(),
            CanonicalFieldType.BOOLEAN: pa.bool_(),
            CanonicalFieldType.FLOAT64: pa.float64(),
            CanonicalFieldType.TIMESTAMP_US_UTC: pa.timestamp("us", tz="UTC"),
        }
        return pa.schema(
            [
                pa.field(name, arrow_types[field_type], nullable=False)
                for name, field_type in self.field_types
            ]
        )


class DatasetRegistry:
    """Read-only, content-addressed collection of dataset contracts."""

    def __init__(self, specs: tuple[DatasetSpec, ...]) -> None:
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("duplicate dataset name")
        ordered = tuple(sorted(specs, key=lambda spec: spec.name))
        self._specs = ordered
        self._by_name = MappingProxyType({spec.name: spec for spec in ordered})

    def __getitem__(self, name: str) -> DatasetSpec:
        try:
            return self._by_name[name]
        except KeyError as error:
            raise KeyError(f"unknown dataset: {name}") from error

    def __iter__(self) -> Iterator[DatasetSpec]:
        return iter(self._specs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._specs)

    @property
    def sha256(self) -> str:
        payload = [spec.model_dump(mode="json") for spec in self._specs]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _spec(
    name: str,
    endpoint: str,
    primary_key: tuple[str, ...],
    business_fields: tuple[str, ...],
    partitions: tuple[str, ...],
    mode: CoverageKeyMode,
    coverage_fields: tuple[str, ...],
    coverage_shape: CoverageShape = CoverageShape.KEY_SET,
    revision_order_fields: tuple[str, ...] = (),
) -> DatasetSpec:
    required_fields = tuple(
        dict.fromkeys(primary_key + business_fields + coverage_fields + _SYSTEM_FIELDS)
    )
    return DatasetSpec(
        name=name,
        provider_endpoint=endpoint,
        primary_key=primary_key,
        required_fields=required_fields,
        field_types=tuple((field, _canonical_field_type(field)) for field in required_fields),
        partition_fields=partitions,
        coverage_key_mode=mode,
        coverage_key_fields=coverage_fields,
        coverage_shape=coverage_shape,
        revision_order_fields=revision_order_fields,
    )


def _canonical_field_type(field: str) -> CanonicalFieldType:
    if field in {"event_time", "known_at", "open_at", "close_at"}:
        return CanonicalFieldType.TIMESTAMP_US_UTC
    if field in {
        "trade_date",
        "session_date",
        "effective_from",
        "effective_to",
        "list_date",
        "delist_date",
        "report_period",
    }:
        return CanonicalFieldType.DATE32
    if field in {"is_open", "suspended", "is_st"}:
        return CanonicalFieldType.BOOLEAN
    if field in {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pre_close",
        "up_limit",
        "down_limit",
        "adj_factor",
        "float_market_value",
        "total_market_value",
        "turnover_rate",
        "weight",
    }:
        return CanonicalFieldType.FLOAT64
    return CanonicalFieldType.UTF8


def default_dataset_registry() -> DatasetRegistry:
    """Return the frozen V1 Tushare-to-canonical dataset contracts."""

    session = CoverageKeyMode.SESSION
    business = CoverageKeyMode.BUSINESS_KEY
    return DatasetRegistry(
        (
            _spec(
                "trade_calendar",
                "trade_cal",
                ("venue", "session_date"),
                ("is_open", "open_at", "close_at"),
                ("venue", "session_year"),
                session,
                ("venue", "session_date"),
            ),
            _spec(
                "instrument_master",
                "stock_basic",
                ("instrument_id", "effective_from"),
                ("venue", "asset_class", "list_date", "delist_date"),
                ("dataset_scope",),
                business,
                ("instrument_id", "effective_from"),
            ),
            _spec(
                "daily_bars",
                "daily",
                ("instrument_id", "trade_date"),
                ("open", "high", "low", "close", "volume", "amount", "pre_close"),
                ("trade_year", "trade_month"),
                session,
                ("venue", "trade_date"),
                CoverageShape.INSTRUMENT_SESSION_MATRIX,
            ),
            _spec(
                "daily_limits_status",
                "stk_limit+suspend_d+stock_st",
                ("instrument_id", "trade_date"),
                ("up_limit", "down_limit", "suspended", "is_st"),
                ("trade_year", "trade_month"),
                session,
                ("venue", "trade_date"),
                CoverageShape.INSTRUMENT_SESSION_MATRIX,
            ),
            _spec(
                "adj_factors",
                "adj_factor",
                ("instrument_id", "trade_date"),
                ("adj_factor",),
                ("trade_year", "trade_month"),
                session,
                ("venue", "trade_date"),
            ),
            _spec(
                "daily_basic",
                "daily_basic",
                ("instrument_id", "trade_date"),
                ("float_market_value", "total_market_value", "turnover_rate"),
                ("trade_year", "trade_month"),
                session,
                ("venue", "trade_date"),
            ),
            _spec(
                "index_bars",
                "index_daily",
                ("instrument_id", "trade_date"),
                ("open", "high", "low", "close", "volume", "amount"),
                ("trade_year", "trade_month"),
                session,
                ("venue", "trade_date"),
            ),
            _spec(
                "index_membership",
                "index_weight",
                ("index_id", "instrument_id", "effective_from"),
                ("effective_to", "weight"),
                ("index_id", "effective_year"),
                business,
                ("index_id", "effective_from"),
            ),
            _spec(
                "industry_membership",
                "index_member_all",
                ("taxonomy", "instrument_id", "effective_from"),
                ("industry_id", "effective_to"),
                ("taxonomy", "effective_year"),
                business,
                ("taxonomy", "effective_from"),
            ),
            _spec(
                "financial_indicators",
                "fina_indicator",
                ("instrument_id", "report_period", "announcement_id"),
                ("revision", "report_values_json"),
                ("dataset_scope",),
                business,
                ("instrument_id", "report_period", "announcement_id"),
                revision_order_fields=("revision",),
            ),
        )
    )


def normalize_request(
    registry: DatasetRegistry,
    *,
    dataset: str,
    start: datetime,
    end: datetime,
    instruments: tuple[str, ...] = (),
    fields: tuple[str, ...] = (),
    coverage_keys: tuple[str, ...] = (),
) -> DatasetRequest:
    """Canonicalize a user request before hashing, catalog lookup, or provider access."""

    spec = registry[dataset]
    canonical_fields = tuple(sorted(set(fields)))
    unknown = set(canonical_fields) - set(spec.query_fields)
    if unknown:
        raise ValueError(f"unknown fields for {dataset}: {sorted(unknown)}")
    return DatasetRequest(
        dataset=dataset,
        start=start,
        end=end,
        instruments=tuple(sorted(set(instruments))),
        fields=canonical_fields,
        coverage_keys=tuple(sorted(set(coverage_keys))),
    )
