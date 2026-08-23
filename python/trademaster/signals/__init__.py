"""Deterministic factor-to-runtime signal generation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Self, cast

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.contracts import SignalContext, bind_snapshot_provenance
from trademaster.factors import factor_output_schema


class SignalGenerationError(RuntimeError):
    pass


class TopNSignalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    strategy_id: str = Field(min_length=1)
    factor_id: str = Field(min_length=1)
    factor_version: str = Field(min_length=1)
    top_n: int = Field(gt=0)
    minimum_valid_instruments: int = Field(gt=0)
    ascending: bool = False

    @model_validator(mode="after")
    def validate_minimum(self) -> Self:
        if self.minimum_valid_instruments < self.top_n:
            raise ValueError("minimum valid instruments cannot be below top_n")
        return self


class ThresholdQuantitySignalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    strategy_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    factor_id: str = Field(min_length=1)
    factor_version: str = Field(min_length=1)
    buy_above: float
    sell_below: float
    target_quantity: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if not self.sell_below < self.buy_above:
            raise ValueError("sell threshold must be below buy threshold")
        return self


def signal_output_schema() -> pa.Schema:
    fields: Any = [
        pa.field("signal_id", pa.string(), nullable=False),
        pa.field("strategy_id", pa.string(), nullable=False),
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("signal_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field(
            "eligible_execution_time",
            pa.timestamp("us", tz="UTC"),
            nullable=False,
        ),
        pa.field("intent_type", pa.string(), nullable=False),
        pa.field("value", pa.decimal128(38, 8), nullable=False),
        pa.field("reason", pa.string(), nullable=False),
        pa.field("snapshot_id", pa.string(), nullable=False),
    ]
    return pa.schema(fields)


class TopNTargetWeightGenerator:
    def __init__(self, config: TopNSignalConfig) -> None:
        self.config = config

    def generate(
        self,
        context: SignalContext,
    ) -> pa.Table:
        if context.factors.schema.remove_metadata() != factor_output_schema():
            raise SignalGenerationError("factor table schema drift")
        signal_time = context.as_of
        eligible_execution_time = context.eligible_execution_time
        latest: dict[str, dict[str, object]] = {}
        for row in context.factors.to_pylist():
            if (
                row["factor_id"] != self.config.factor_id
                or row["factor_version"] != self.config.factor_version
            ):
                continue
            instrument_id = str(row["instrument_id"])
            current = latest.get(instrument_id)
            event_time = cast(datetime, row["event_time"])
            if current is not None and event_time == cast(
                datetime, current["event_time"]
            ):
                raise SignalGenerationError("duplicate factor observation")
            if current is None or event_time > cast(datetime, current["event_time"]):
                latest[instrument_id] = row
        valid = [row for row in latest.values() if bool(row["is_valid"])]
        if len(valid) < self.config.minimum_valid_instruments:
            raise SignalGenerationError("insufficient valid instruments for Top-N")
        ranked = sorted(
            valid,
            key=lambda row: (
                cast(float, row["value"])
                if self.config.ascending
                else -cast(float, row["value"]),
                str(row["instrument_id"]),
            ),
        )
        selected = [str(row["instrument_id"]) for row in ranked[: self.config.top_n]]
        quantum = Decimal("0.00000001")
        base = (Decimal(1) / Decimal(self.config.top_n)).quantize(
            quantum, rounding=ROUND_DOWN
        )
        weights = {instrument_id: base for instrument_id in selected}
        weights[selected[0]] += Decimal(1) - base * self.config.top_n
        rows: list[dict[str, object]] = []
        for instrument_id in sorted(latest):
            value = weights.get(instrument_id, Decimal(0)).quantize(quantum)
            identity = {
                "strategy_id": self.config.strategy_id,
                "instrument_id": instrument_id,
                "signal_time": signal_time.isoformat(),
                "eligible_execution_time": eligible_execution_time.isoformat(),
                "value": str(value),
                "snapshot_id": context.snapshot.snapshot_id,
            }
            signal_id = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            rows.append(
                {
                    "signal_id": signal_id,
                    "strategy_id": self.config.strategy_id,
                    "instrument_id": instrument_id,
                    "signal_time": signal_time,
                    "eligible_execution_time": eligible_execution_time,
                    "intent_type": "target_weight",
                    "value": value,
                    "reason": "top_n_factor_rank",
                    "snapshot_id": context.snapshot.snapshot_id,
                }
            )
        return bind_snapshot_provenance(
            pa.Table.from_pylist(rows, schema=signal_output_schema()),
            context.snapshot,
        )


class ThresholdQuantitySignalGenerator:
    """Generate one live target-quantity intent on a threshold crossing."""

    def __init__(self, config: ThresholdQuantitySignalConfig) -> None:
        self.config = config

    def generate(
        self,
        context: SignalContext,
    ) -> pa.Table:
        if context.factors.schema.remove_metadata() != factor_output_schema():
            raise SignalGenerationError("factor table schema drift")
        signal_time = context.as_of
        observations = sorted(
            (
                row
                for row in context.factors.to_pylist()
                if row["instrument_id"] == self.config.instrument_id
                and row["factor_id"] == self.config.factor_id
                and row["factor_version"] == self.config.factor_version
            ),
            key=lambda row: cast(datetime, row["event_time"]),
        )
        keys = [cast(datetime, row["event_time"]) for row in observations]
        if len(keys) != len(set(keys)):
            raise SignalGenerationError("duplicate single-asset factor observation")
        rows: list[dict[str, object]] = []
        if (
            len(observations) >= 2
            and bool(observations[-2]["is_valid"])
            and bool(observations[-1]["is_valid"])
        ):
            previous = cast(float, observations[-2]["value"])
            current = cast(float, observations[-1]["value"])
            target: Decimal | None = None
            reason = ""
            if previous <= self.config.buy_above < current:
                target = Decimal(self.config.target_quantity)
                reason = "crossed_above_buy_threshold"
            elif previous >= self.config.sell_below > current:
                target = Decimal(0)
                reason = "crossed_below_sell_threshold"
            if target is not None:
                value = target.quantize(Decimal("0.00000001"))
                identity = {
                    "strategy_id": self.config.strategy_id,
                    "instrument_id": self.config.instrument_id,
                    "signal_time": signal_time.isoformat(),
                    "eligible_execution_time": context.eligible_execution_time.isoformat(),
                    "value": str(value),
                    "snapshot_id": context.snapshot.snapshot_id,
                }
                rows.append(
                    {
                        "signal_id": hashlib.sha256(
                            json.dumps(
                                identity,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                        "strategy_id": self.config.strategy_id,
                        "instrument_id": self.config.instrument_id,
                        "signal_time": signal_time,
                        "eligible_execution_time": context.eligible_execution_time,
                        "intent_type": "quantity",
                        "value": value,
                        "reason": reason,
                        "snapshot_id": context.snapshot.snapshot_id,
                    }
                )
        return bind_snapshot_provenance(
            pa.Table.from_pylist(rows, schema=signal_output_schema()),
            context.snapshot,
        )


__all__ = [
    "SignalGenerationError",
    "ThresholdQuantitySignalConfig",
    "ThresholdQuantitySignalGenerator",
    "TopNSignalConfig",
    "TopNTargetWeightGenerator",
    "signal_output_schema",
]
