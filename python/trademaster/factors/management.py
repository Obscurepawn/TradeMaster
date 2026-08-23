"""Stable factor definitions and dependency-aware managed registration."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademaster.contracts import Factor
from trademaster.data.registry import DatasetRegistry, default_dataset_registry

_IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_SHA256 = r"^[0-9a-f]{64}$"


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("factor parameters must be canonical JSON values") from error


def _parameter_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("factor parameters cannot contain NaN or infinity")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("factor parameters cannot contain NaN or infinity")
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_parameter_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not key for key in value):
            raise ValueError("factor parameter keys must be nonempty strings")
        return {
            key: _parameter_value(item)
            for key, item in sorted(cast(dict[str, object], value).items())
        }
    raise ValueError("factor parameters must be canonical JSON values")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


class DatasetFieldDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["dataset"] = "dataset"
    dataset: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    fields: tuple[str, ...]

    @model_validator(mode="after")
    def validate_fields(self) -> DatasetFieldDependency:
        if not self.fields or self.fields != tuple(sorted(set(self.fields))):
            raise ValueError("dataset dependency fields must be nonempty, unique and sorted")
        return self


class FactorDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["factor"] = "factor"
    factor_id: str = Field(pattern=rf"^{_IDENTIFIER}$")
    factor_version: str = Field(pattern=rf"^{_IDENTIFIER}$")


Dependency = Annotated[
    DatasetFieldDependency | FactorDependency,
    Field(discriminator="kind"),
]


def _dependency_key(value: Dependency) -> tuple[str, str, str]:
    if isinstance(value, DatasetFieldDependency):
        return (value.kind, value.dataset, ",".join(value.fields))
    return (value.kind, value.factor_id, value.factor_version)


def _parse_dependencies(values: tuple[str, ...]) -> tuple[Dependency, ...]:
    parsed: list[Dependency] = []
    for value in values:
        factor_match = re.fullmatch(rf"factor:({_IDENTIFIER})@({_IDENTIFIER})", value)
        if factor_match is not None:
            parsed.append(
                FactorDependency(
                    factor_id=factor_match.group(1),
                    factor_version=factor_match.group(2),
                )
            )
            continue
        dataset_match = re.fullmatch(r"([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)", value)
        if dataset_match is None:
            raise ValueError(f"unknown factor dependency: {value}")
        parsed.append(
            DatasetFieldDependency(
                dataset=dataset_match.group(1),
                fields=(dataset_match.group(2),),
            )
        )
    return tuple(sorted(parsed, key=_dependency_key))


def _implementation_bytes(factor: Factor) -> bytes:
    implementation_type = type(factor)
    source_path = inspect.getsourcefile(implementation_type)
    if source_path is not None:
        path = Path(source_path)
        if path.is_file():
            return path.read_bytes()
    try:
        return inspect.getsource(implementation_type).encode()
    except (OSError, TypeError) as error:
        raise ValueError("factor implementation source is unavailable") from error


class FactorDefinition(BaseModel):
    """Immutable, content-addressed identity for one factor implementation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-definition/v1"]
    factor_id: str = Field(pattern=rf"^{_IDENTIFIER}$")
    version: str = Field(pattern=rf"^{_IDENTIFIER}$")
    family: str = Field(min_length=1)
    description: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    dependencies: tuple[Dependency, ...]
    parameters: tuple[tuple[str, str], ...]
    lookback_sessions: int = Field(ge=0)
    pit_required: bool
    frequency: Literal["event", "daily", "weekly", "monthly", "quarterly"]
    scope: Literal["time_series", "cross_sectional", "grouped_cross_sectional"]
    unit: str = Field(min_length=1)
    direction: Literal[-1, 0, 1]
    code_sha256: str = Field(pattern=_SHA256)
    parameters_sha256: str = Field(pattern=_SHA256)
    definition_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def validate_canonical_identity(self) -> FactorDefinition:
        if self.dependencies != tuple(sorted(self.dependencies, key=_dependency_key)):
            raise ValueError("factor dependencies must be canonically sorted")
        parameter_names = tuple(name for name, _ in self.parameters)
        if parameter_names != tuple(sorted(set(parameter_names))) or any(
            not name for name in parameter_names
        ):
            raise ValueError("factor parameters must be unique and sorted")
        decoded_parameters: dict[str, object] = {}
        for name, value in self.parameters:
            try:
                decoded_parameters[name] = json.loads(value)
            except ValueError as error:
                raise ValueError("factor parameter value is not canonical JSON") from error
            if _canonical_json(decoded_parameters[name]) != value:
                raise ValueError("factor parameter value is not canonical JSON")
        if self.parameters_sha256 != _sha256_json(decoded_parameters):
            raise ValueError("factor parameters hash mismatch")
        payload = self.model_dump(
            mode="json",
            exclude={"definition_sha256"},
        )
        if self.definition_sha256 != _sha256_json(payload):
            raise ValueError("factor definition hash mismatch")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return (self.factor_id, self.version)

    @classmethod
    def build(
        cls,
        factor: Factor,
        *,
        family: str,
        description: str,
        parameters: dict[str, object],
        frequency: Literal["event", "daily", "weekly", "monthly", "quarterly"],
        scope: Literal["time_series", "cross_sectional", "grouped_cross_sectional"],
        unit: str,
        direction: Literal[-1, 0, 1],
    ) -> FactorDefinition:
        normalized = {key: _parameter_value(value) for key, value in sorted(parameters.items())}
        if any(not key for key in normalized):
            raise ValueError("factor parameter keys must be nonempty strings")
        encoded_parameters = tuple(
            (key, _canonical_json(value)) for key, value in normalized.items()
        )
        implementation_type = type(factor)
        dependencies = _parse_dependencies(factor.spec.dependencies)
        base: dict[str, object] = {
            "schema_id": "trademaster.factor-definition/v1",
            "factor_id": factor.spec.factor_id,
            "version": factor.spec.version,
            "family": family,
            "description": description,
            "implementation": (
                f"{implementation_type.__module__}:{implementation_type.__qualname__}"
            ),
            "dependencies": dependencies,
            "parameters": encoded_parameters,
            "lookback_sessions": factor.spec.lookback_sessions,
            "pit_required": factor.spec.pit_required,
            "frequency": frequency,
            "scope": scope,
            "unit": unit,
            "direction": direction,
            "code_sha256": hashlib.sha256(_implementation_bytes(factor)).hexdigest(),
            "parameters_sha256": _sha256_json(normalized),
        }
        hash_payload = {
            **base,
            "dependencies": [item.model_dump(mode="json") for item in dependencies],
        }
        base["definition_sha256"] = _sha256_json(hash_payload)
        return cls.model_validate(base)


@dataclass(frozen=True, slots=True)
class FactorRegistration:
    factor: Factor
    definition: FactorDefinition

    def __post_init__(self) -> None:
        if (
            self.factor.spec.factor_id,
            self.factor.spec.version,
        ) != self.definition.identity:
            raise ValueError("factor implementation and definition identities differ")

    @classmethod
    def create(
        cls,
        factor: Factor,
        *,
        family: str,
        description: str,
        parameters: dict[str, object],
        frequency: Literal["event", "daily", "weekly", "monthly", "quarterly"],
        scope: Literal["time_series", "cross_sectional", "grouped_cross_sectional"],
        unit: str,
        direction: Literal[-1, 0, 1],
    ) -> FactorRegistration:
        return cls(
            factor=factor,
            definition=FactorDefinition.build(
                factor,
                family=family,
                description=description,
                parameters=parameters,
                frequency=frequency,
                scope=scope,
                unit=unit,
                direction=direction,
            ),
        )


class ManagedFactorRegistry:
    """Read-only factor registry with stable definition hashes and an acyclic DAG."""

    def __init__(
        self,
        registrations: tuple[FactorRegistration, ...],
        *,
        dataset_registry: DatasetRegistry | None = None,
        external_dataset_fields: tuple[tuple[str, str], ...] = (),
    ) -> None:
        if external_dataset_fields != tuple(sorted(set(external_dataset_fields))):
            raise ValueError("external dataset fields must be unique and sorted")
        by_identity: dict[tuple[str, str], FactorRegistration] = {}
        by_definition_sha: dict[str, FactorRegistration] = {}
        for registration in registrations:
            identity = registration.definition.identity
            if identity in by_identity:
                raise ValueError(f"duplicate factor identity: {identity[0]}@{identity[1]}")
            if registration.definition.definition_sha256 in by_definition_sha:
                raise ValueError("duplicate factor definition hash")
            by_identity[identity] = registration
            by_definition_sha[registration.definition.definition_sha256] = registration
        datasets = dataset_registry or default_dataset_registry()
        for registration in by_identity.values():
            for dependency in registration.definition.dependencies:
                if isinstance(dependency, FactorDependency):
                    if (dependency.factor_id, dependency.factor_version) not in by_identity:
                        raise ValueError(
                            "unknown factor dependency: "
                            f"{dependency.factor_id}@{dependency.factor_version}"
                        )
                    continue
                if all(
                    (dependency.dataset, field) in external_dataset_fields
                    for field in dependency.fields
                ):
                    continue
                try:
                    spec = datasets[dependency.dataset]
                except KeyError as error:
                    raise ValueError(f"unknown dataset dependency: {dependency.dataset}") from error
                unknown = set(dependency.fields) - set(spec.query_fields)
                if unknown:
                    raise ValueError(
                        "unknown dataset dependency fields: "
                        f"{dependency.dataset}.{','.join(sorted(unknown))}"
                    )
        self._by_identity = by_identity
        self._by_definition_sha = by_definition_sha
        self._external_dataset_fields = external_dataset_fields
        self._validate_acyclic()

    @property
    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._by_identity))

    @property
    def external_dataset_fields(self) -> tuple[tuple[str, str], ...]:
        return self._external_dataset_fields

    def get(self, factor_id: str, version: str) -> FactorRegistration:
        try:
            return self._by_identity[(factor_id, version)]
        except KeyError as error:
            raise KeyError(f"unknown factor: {factor_id}@{version}") from error

    def get_by_definition_sha(self, definition_sha256: str) -> FactorRegistration:
        try:
            return self._by_definition_sha[definition_sha256]
        except KeyError as error:
            raise KeyError(f"unknown factor definition: {definition_sha256}") from error

    def _factor_dependencies(self, identity: tuple[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (item.factor_id, item.factor_version)
                for item in self._by_identity[identity].definition.dependencies
                if isinstance(item, FactorDependency)
            )
        )

    def _validate_acyclic(self) -> None:
        visiting: set[tuple[str, str]] = set()
        visited: set[tuple[str, str]] = set()

        def visit(identity: tuple[str, str]) -> None:
            if identity in visiting:
                raise ValueError("factor dependency graph contains a cycle")
            if identity in visited:
                return
            visiting.add(identity)
            for dependency in self._factor_dependencies(identity):
                visit(dependency)
            visiting.remove(identity)
            visited.add(identity)

        for identity in sorted(self._by_identity):
            visit(identity)

    def plan(self, factor_id: str, version: str) -> tuple[FactorRegistration, ...]:
        target = (factor_id, version)
        if target not in self._by_identity:
            raise KeyError(f"unknown factor: {factor_id}@{version}")
        ordered: list[FactorRegistration] = []
        visited: set[tuple[str, str]] = set()

        def append(identity: tuple[str, str]) -> None:
            if identity in visited:
                return
            for dependency in self._factor_dependencies(identity):
                append(dependency)
            visited.add(identity)
            ordered.append(self._by_identity[identity])

        append(target)
        return tuple(ordered)


__all__ = [
    "DatasetFieldDependency",
    "Dependency",
    "FactorDefinition",
    "FactorDependency",
    "FactorRegistration",
    "ManagedFactorRegistry",
]
