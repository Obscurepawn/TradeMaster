from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest
from trademaster.data import (
    CanonicalFieldType,
    CoverageKeyMode,
    DataConfig,
    DatasetRegistry,
    DatasetSpec,
    default_dataset_registry,
    normalize_request,
)


def test_data_config_uses_safe_derived_paths_without_reading_token(tmp_path: Path) -> None:
    config = DataConfig.from_environment(
        {
            "TRADEMASTER_DATA_ROOT": str(tmp_path / "lake"),
            "TRADEMASTER_LOG_DIR": str(tmp_path / "logs"),
            "TRADEMASTER_TUSHARE_TOKEN_ENV": "PRIVATE_TUSHARE_TOKEN",
            "TRADEMASTER_ETF_INSTRUMENTS": "510300.SH,159915.SZ",
            "PRIVATE_TUSHARE_TOKEN": "must-not-enter-config",
        }
    )

    assert config.tushare_token_env == "PRIVATE_TUSHARE_TOKEN"
    assert config.etf_instruments == ("159915.SZ", "510300.SH")
    assert config.paths.etf_instruments == ("159915.SZ", "510300.SH")
    assert "must-not-enter-config" not in repr(config)
    assert config.paths.catalog == (tmp_path / "lake" / "catalog.duckdb").resolve()
    assert config.paths.canonical == (tmp_path / "lake" / "canonical").resolve()
    assert config.paths.snapshots == (tmp_path / "lake" / "snapshots").resolve()
    assert config.paths.logs == (tmp_path / "logs").resolve()
    assert config.canonical_source_policy == "tushare_only"
    assert config.paths.canonical_source_policy == "tushare_only"

    config.paths.ensure_layout()
    assert config.paths.raw.is_dir()
    assert config.paths.staging.is_dir()
    assert config.paths.canonical.is_dir()
    assert config.paths.snapshots.is_dir()
    assert config.paths.temporary.is_dir()
    assert config.paths.logs.is_dir()


def test_data_config_rejects_root_as_mutable_data_or_log_directory() -> None:
    with pytest.raises(ValueError, match="filesystem root"):
        DataConfig(data_root=Path("/"), log_dir=Path("/tmp/trademaster-logs"))
    with pytest.raises(ValueError, match="filesystem root"):
        DataConfig(data_root=Path("/tmp/trademaster-data"), log_dir=Path("/"))


def test_data_config_canonicalizes_etf_identifiers_before_routing(
    tmp_path: Path,
) -> None:
    config = DataConfig(
        data_root=tmp_path / "data",
        log_dir=tmp_path / "logs",
        etf_instruments=("510300.sh",),
    )

    assert config.etf_instruments == ("510300.SH",)
    assert config.paths.etf_instruments == ("510300.SH",)


def test_default_registry_freezes_dataset_specific_keys_and_partitions() -> None:
    registry = default_dataset_registry()

    assert registry.names == (
        "adj_factors",
        "daily_bars",
        "daily_basic",
        "daily_limits_status",
        "financial_indicators",
        "index_bars",
        "index_membership",
        "industry_membership",
        "instrument_master",
        "trade_calendar",
    )
    assert registry["daily_bars"].primary_key == ("instrument_id", "trade_date")
    assert registry["daily_bars"].partition_fields == ("trade_year", "trade_month")
    assert registry["trade_calendar"].primary_key == ("venue", "session_date")
    assert registry["trade_calendar"].coverage_key_mode is CoverageKeyMode.SESSION
    daily_schema = registry["daily_bars"].arrow_schema
    assert daily_schema.names == list(registry["daily_bars"].required_fields)
    assert daily_schema.field("close").type == pa.float64()
    assert all(not field.nullable for field in daily_schema)
    assert registry["financial_indicators"].primary_key == (
        "instrument_id",
        "report_period",
        "announcement_id",
    )
    assert registry["financial_indicators"].coverage_key_mode is CoverageKeyMode.BUSINESS_KEY
    assert registry["financial_indicators"].revision_order_fields == ("revision",)
    assert len(registry.sha256) == 64
    assert registry.sha256 == default_dataset_registry().sha256


def test_registry_rejects_duplicate_names_and_invalid_field_contracts() -> None:
    valid = DatasetSpec(
        name="sample",
        provider_endpoint="sample_endpoint",
        primary_key=("instrument_id", "trade_date"),
        required_fields=("instrument_id", "trade_date", "value"),
        field_types=(
            ("instrument_id", CanonicalFieldType.UTF8),
            ("trade_date", CanonicalFieldType.DATE32),
            ("value", CanonicalFieldType.FLOAT64),
        ),
        partition_fields=("trade_year",),
        coverage_key_mode=CoverageKeyMode.SESSION,
        coverage_key_fields=("venue", "trade_date"),
    )
    with pytest.raises(ValueError, match="duplicate dataset"):
        DatasetRegistry((valid, valid))
    with pytest.raises(ValueError, match="primary key"):
        DatasetSpec(
            name="broken",
            provider_endpoint="broken",
            primary_key=("missing",),
            required_fields=("instrument_id",),
            field_types=(("instrument_id", CanonicalFieldType.UTF8),),
            partition_fields=("trade_year",),
            coverage_key_mode=CoverageKeyMode.SESSION,
            coverage_key_fields=("venue", "trade_date"),
        )


def test_normalize_request_is_deterministic_and_rejects_unknown_fields() -> None:
    registry = default_dataset_registry()
    start = datetime(2025, 1, 2, tzinfo=UTC)
    end = datetime(2025, 1, 3, tzinfo=UTC)

    request = normalize_request(
        registry,
        dataset="daily_bars",
        start=start,
        end=end,
        instruments=("600000.SH", "000001.SZ", "600000.SH"),
        fields=("close", "open", "close"),
        coverage_keys=("SSE:2025-01-03", "SSE:2025-01-02"),
    )
    assert request.instruments == ("000001.SZ", "600000.SH")
    assert request.fields == ("close", "open")
    assert request.coverage_keys == ("SSE:2025-01-02", "SSE:2025-01-03")

    with pytest.raises(ValueError, match="unknown fields"):
        normalize_request(
            registry,
            dataset="daily_bars",
            start=start,
            end=end,
            fields=("close", "future_alpha"),
        )
