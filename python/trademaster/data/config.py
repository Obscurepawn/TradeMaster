"""Configuration and safe filesystem layout for the data foundation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, field_validator


def _resolved_non_root(value: Path) -> Path:
    resolved = value.expanduser().resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("mutable directory cannot be the filesystem root")
    return resolved


def _validated_etf_instruments(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(item.upper() for item in value)
    canonical = tuple(sorted(set(normalized)))
    if normalized != canonical or any(
        re.fullmatch(r"[A-Z0-9]+\.[A-Z]+", item) is None for item in normalized
    ):
        raise ValueError("ETF instruments must be unique, sorted market identifiers")
    return normalized


class DataPaths(BaseModel):
    """All mutable data paths derived from two explicitly configured roots."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data_root: Path
    logs: Path
    etf_instruments: tuple[str, ...] = ()
    canonical_source_policy: Literal["tushare_only", "trusted_imports"] = (
        "tushare_only"
    )

    _safe_paths = field_validator("data_root", "logs")(_resolved_non_root)
    _canonical_etf_instruments = field_validator("etf_instruments")(
        _validated_etf_instruments
    )

    @property
    def raw(self) -> Path:
        return self.data_root / "raw"

    @property
    def staging(self) -> Path:
        return self.data_root / "staging"

    @property
    def canonical(self) -> Path:
        return self.data_root / "canonical"

    @property
    def snapshots(self) -> Path:
        return self.data_root / "snapshots"

    @property
    def temporary(self) -> Path:
        return self.data_root / ".tmp"

    @property
    def catalog(self) -> Path:
        return self.data_root / "catalog.duckdb"

    def ensure_layout(self) -> None:
        """Create only the known, bounded mutable directories."""

        for directory in (
            self.raw,
            self.staging,
            self.canonical,
            self.snapshots,
            self.temporary,
            self.logs,
        ):
            directory.mkdir(parents=True, exist_ok=True)


class DataConfig(BaseModel):
    """Serializable data configuration that stores a token variable name only."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data_root: Path
    log_dir: Path
    tushare_token_env: str = "TUSHARE_TOKEN"
    etf_instruments: tuple[str, ...] = ()
    canonical_source_policy: Literal["tushare_only", "trusted_imports"] = (
        "tushare_only"
    )

    _safe_paths = field_validator("data_root", "log_dir")(_resolved_non_root)

    @field_validator("tushare_token_env")
    @classmethod
    def validate_token_environment_name(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
            raise ValueError("tushare token environment name is invalid")
        return value

    @field_validator("etf_instruments")
    @classmethod
    def validate_etf_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validated_etf_instruments(value)

    @property
    def paths(self) -> DataPaths:
        return DataPaths(
            data_root=self.data_root,
            logs=self.log_dir,
            etf_instruments=self.etf_instruments,
            canonical_source_policy=self.canonical_source_policy,
        )

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Self:
        env = os.environ if environment is None else environment
        return cls(
            data_root=Path(env.get("TRADEMASTER_DATA_ROOT", ".trademaster/data")),
            log_dir=Path(env.get("TRADEMASTER_LOG_DIR", ".trademaster/logs")),
            tushare_token_env=env.get("TRADEMASTER_TUSHARE_TOKEN_ENV", "TUSHARE_TOKEN"),
            etf_instruments=tuple(
                sorted(
                    {
                        item.strip()
                        for item in env.get("TRADEMASTER_ETF_INSTRUMENTS", "").split(",")
                        if item.strip()
                    }
                )
            ),
            canonical_source_policy=cast(
                Literal["tushare_only", "trusted_imports"],
                env.get("TRADEMASTER_CANONICAL_SOURCE_POLICY", "tushare_only"),
            ),
        )
