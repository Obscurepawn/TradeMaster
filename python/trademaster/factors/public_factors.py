"""First independently reviewed executable factors from the public catalog."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pyarrow as pa

from trademaster.contracts import FactorContext, FactorSpec

from .management import FactorRegistration


class HuataiLogMarketCapFactor:
    """Huatai size exposure using Tushare ``total_mv`` converted to CNY."""

    spec = FactorSpec(
        factor_id="huatai53.size.log_total_market_value",
        version="1",
        dependencies=("fundamental_inputs.total_market_value",),
        lookback_sessions=0,
    )

    def compute(self, context: FactorContext) -> pa.Table:
        from trademaster.factors import FactorOutputError, factor_output_schema

        required = {"instrument_id", "event_time", "total_market_value"}
        if not required <= set(context.inputs.column_names):
            raise FactorOutputError("Huatai size input schema is incomplete")
        output: list[dict[str, object]] = []
        for row in sorted(
            context.inputs.select(tuple(sorted(required))).to_pylist(),
            key=lambda item: (item["event_time"], str(item["instrument_id"])),
        ):
            raw = row["total_market_value"]
            market_value = float(raw) if raw is not None else 0.0
            valid = math.isfinite(market_value) and market_value > 0.0
            value = math.log(market_value * 10_000.0) if valid else 0.0
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


_FORMULAS = {
    14: "close - delay(close, 5)",
    15: "open / delay(close, 1) - 1",
    18: "close / delay(close, 5)",
    20: "100 * (close - delay(close, 6)) / delay(close, 6)",
    31: "100 * (close - mean(close, 12)) / mean(close, 12)",
    34: "mean(close, 12) / close",
    46: "(mean(close, 3) + mean(close, 6) + mean(close, 12) + mean(close, 24)) / (4 * close)",
    53: "100 * count(close > delay(close, 1), 12) / 12",
    58: "100 * count(close > delay(close, 1), 20) / 20",
    88: "100 * (close - delay(close, 20)) / delay(close, 20)",
}
_LOOKBACKS = {14: 5, 15: 1, 18: 5, 20: 6, 31: 11, 34: 11, 46: 23, 53: 12, 58: 20, 88: 20}


def _finite_positive(values: list[float]) -> bool:
    return all(math.isfinite(value) and value > 0.0 for value in values)


class ReviewedGtjaPriceFactor:
    """One low-ambiguity GTJA formula with an internal frozen HFQ transform."""

    def __init__(self, number: int) -> None:
        if number not in _FORMULAS:
            raise ValueError("GTJA formula has not passed the first semantic review")
        self.number = number
        dependencies = ["adj_factors.adj_factor", "daily_bars.close"]
        if number == 15:
            dependencies.append("daily_bars.open")
        self.spec = FactorSpec(
            factor_id=f"gtja191.alpha{number:03d}",
            version="1",
            dependencies=tuple(sorted(dependencies)),
            lookback_sessions=_LOOKBACKS[number],
        )

    def _value(
        self,
        closes: list[float],
        adjusted_open: float | None,
    ) -> tuple[float, bool]:
        number = self.number
        lookback = _LOOKBACKS[number]
        if len(closes) <= lookback:
            return 0.0, False
        current = closes[-1]
        if number == 15:
            if adjusted_open is None:
                return 0.0, False
            values = [closes[-2]]
            valid = _finite_positive(values) and (
                math.isfinite(adjusted_open) and adjusted_open > 0.0
            )
            return (
                (adjusted_open / closes[-2] - 1.0) if valid else 0.0,
                valid,
            )
        if number in {14, 18, 20, 88}:
            lag = {14: 5, 18: 5, 20: 6, 88: 20}[number]
            previous = closes[-lag - 1]
            valid = _finite_positive([current, previous])
            if not valid:
                return 0.0, False
            if number == 14:
                return current - previous, True
            if number == 18:
                return current / previous, True
            return 100.0 * (current - previous) / previous, True
        if number in {31, 34}:
            window = closes[-12:]
            valid = len(window) == 12 and _finite_positive(window)
            if not valid:
                return 0.0, False
            average = math.fsum(window) / 12.0
            if number == 31:
                return 100.0 * (current - average) / average, True
            return average / current, True
        if number == 46:
            window = closes[-24:]
            valid = len(window) == 24 and _finite_positive(window)
            if not valid:
                return 0.0, False
            moving_averages = [math.fsum(closes[-size:]) / size for size in (3, 6, 12, 24)]
            return math.fsum(moving_averages) / (4.0 * current), True
        count_window = 12 if number == 53 else 20
        window = closes[-count_window - 1 :]
        valid = len(window) == count_window + 1 and _finite_positive(window)
        if not valid:
            return 0.0, False
        up_sessions = sum(window[index] > window[index - 1] for index in range(1, len(window)))
        return 100.0 * up_sessions / count_window, True

    def compute(self, context: FactorContext) -> pa.Table:
        from trademaster.factors import FactorOutputError, factor_output_schema

        required = {"instrument_id", "event_time", "close", "adj_factor"}
        if self.number == 15:
            required.add("open")
        if not required <= set(context.inputs.column_names):
            raise FactorOutputError("reviewed GTJA factor input schema is incomplete")
        ordered = sorted(
            context.inputs.select(tuple(sorted(required))).to_pylist(),
            key=lambda row: (str(row["instrument_id"]), row["event_time"]),
        )
        histories: dict[str, list[float]] = {}
        output: list[dict[str, object]] = []
        for row in ordered:
            instrument_id = str(row["instrument_id"])
            raw_close = row["close"]
            raw_adjustment = row["adj_factor"]
            close = float(raw_close) if raw_close is not None else math.nan
            adjustment = float(raw_adjustment) if raw_adjustment is not None else math.nan
            close = (
                close * adjustment
                if math.isfinite(close)
                and close > 0.0
                and math.isfinite(adjustment)
                and adjustment > 0.0
                else math.nan
            )
            closes = histories.setdefault(instrument_id, [])
            closes.append(close)
            raw_open = row.get("open")
            adjusted_open = (
                float(raw_open) * adjustment
                if raw_open is not None
                and math.isfinite(float(raw_open))
                and float(raw_open) > 0.0
                and math.isfinite(adjustment)
                and adjustment > 0.0
                else None
            )
            value, valid = self._value(closes, adjusted_open)
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
        return pa.Table.from_pylist(output, schema=factor_output_schema())


@dataclass(frozen=True, slots=True)
class PublicExecutableFactorSuite:
    registrations: tuple[FactorRegistration, ...]
    external_dataset_fields: tuple[tuple[str, str], ...]


def public_executable_factor_suite() -> PublicExecutableFactorSuite:
    """Build independently implemented definitions that passed local golden cases."""

    size = HuataiLogMarketCapFactor()
    registrations = [
        FactorRegistration.create(
            size,
            family="huatai53",
            description="Natural log of total market value in CNY",
            parameters={
                "formula": "ln(total_market_value * 10000)",
                "source_release": "huatai-53-2020.06.02",
                "total_market_value_input_unit": "10k_cny",
            },
            frequency="daily",
            scope="cross_sectional",
            unit="log_cny",
            direction=-1,
        )
    ]
    units = {
        14: "hfq_price_difference",
        15: "ratio",
        18: "ratio",
        20: "percent",
        31: "percent",
        34: "ratio",
        46: "ratio",
        53: "percent",
        58: "percent",
        88: "percent",
    }
    for number in sorted(_FORMULAS):
        factor = ReviewedGtjaPriceFactor(number)
        registrations.append(
            FactorRegistration.create(
                factor,
                family="gtja191",
                description=f"Independently reviewed GTJA Alpha{number:03d}",
                parameters={
                    "formula": _FORMULAS[number],
                    "adjustment_formula": "raw_price * adj_factor",
                    "operator_semantics": "gtja-reviewed-price/v1",
                    "price_basis": "hfq",
                    "source_release": "gtja-alpha191-2017.06.15",
                },
                frequency="daily",
                scope="time_series",
                unit=units[number],
                direction=0,
            )
        )
    return PublicExecutableFactorSuite(
        registrations=tuple(registrations),
        external_dataset_fields=(("fundamental_inputs", "total_market_value"),),
    )


__all__ = [
    "HuataiLogMarketCapFactor",
    "PublicExecutableFactorSuite",
    "ReviewedGtjaPriceFactor",
    "public_executable_factor_suite",
]
