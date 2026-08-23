"""Content-addressed governance records for public factor libraries."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal, Self, TypeVar

import duckdb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .management import ManagedFactorRegistry

_IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_SHA256 = r"^[0-9a-f]{64}$"

ImplementationStatus = Literal[
    "implemented",
    "implemented_variant",
    "ready",
    "blocked_data",
    "blocked_semantics",
    "risk_model_only",
    "shadow_only",
]
DataAvailability = Literal[
    "current_cache_partial",
    "derived_from_current",
    "provider_extension",
    "benchmark_contract_required",
    "blocked_data",
]


class PublicFactorIntegrityError(RuntimeError):
    """A public-factor record or its discovery index is inconsistent."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _record_sha256(record: BaseModel, field: str) -> str:
    return _sha256(_canonical_json(record.model_dump(mode="json", exclude={field})))


def _canonical_tuple(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))) or any(not value for value in values):
        raise ValueError(f"{label} must be nonempty, unique and sorted")


class FactorSourceRecord(BaseModel):
    """Immutable provenance and redistribution boundary for one source release."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-source/v1"]
    source_id: str = Field(pattern=rf"^{_IDENTIFIER}$")
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    released_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reference_url: str = Field(pattern=r"^https://")
    artifact_sha256: str | None = Field(default=None, pattern=_SHA256)
    license_class: Literal["paper-disclosure", "apache-2.0", "provider-contract"]
    redistribution: Literal["metadata-only", "notice-required", "contract-restricted"]
    notes: tuple[str, ...]
    source_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def validate_identity(self) -> FactorSourceRecord:
        _canonical_tuple(self.notes, "source notes")
        if self.source_sha256 != _record_sha256(self, "source_sha256"):
            raise ValueError("factor source hash mismatch")
        return self

    @classmethod
    def build(cls, **values: object) -> FactorSourceRecord:
        base = {"schema_id": "trademaster.factor-source/v1", **values}
        base["source_sha256"] = _sha256(_canonical_json(base))
        return cls.model_validate(base)


class FactorCandidateRecord(BaseModel):
    """Governed factor candidate; it is not necessarily executable."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-candidate/v1"]
    candidate_id: str = Field(pattern=rf"^{_IDENTIFIER}$")
    revision: str = Field(pattern=rf"^{_IDENTIFIER}$")
    collection_id: str = Field(pattern=rf"^{_IDENTIFIER}$")
    source_ids: tuple[str, ...]
    display_name: str = Field(min_length=1)
    aliases: tuple[str, ...]
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    formula_reference: str = Field(min_length=1)
    formula_expression: str | None
    required_inputs: tuple[str, ...]
    required_operators: tuple[str, ...]
    data_availability: DataAvailability
    implementation_status: ImplementationStatus
    implementation_identity: str | None
    semantic_differences: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    expected_direction: Literal[-1, 0, 1]
    distribution_status: Literal["internal-research", "metadata-only", "redistributable"]
    candidate_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def validate_identity(self) -> FactorCandidateRecord:
        for values, label in (
            (self.source_ids, "candidate sources"),
            (self.aliases, "candidate aliases"),
            (self.required_inputs, "candidate inputs"),
            (self.required_operators, "candidate operators"),
            (self.semantic_differences, "candidate semantic differences"),
            (self.blocked_reasons, "candidate blocked reasons"),
        ):
            _canonical_tuple(values, label)
        if self.implementation_identity is not None:
            import re

            if re.fullmatch(rf"{_IDENTIFIER}@{_IDENTIFIER}", self.implementation_identity) is None:
                raise ValueError("candidate implementation identity is invalid")
        if self.implementation_status in {"implemented", "implemented_variant"} and (
            self.implementation_identity is None
        ):
            raise ValueError("implemented candidate requires a managed definition identity")
        if (
            self.implementation_status in {"implemented", "implemented_variant"}
            and self.formula_expression is None
        ):
            raise ValueError("implemented candidate requires a reviewed formula expression")
        if self.implementation_status == "ready" and self.formula_expression is None:
            raise ValueError("ready candidate requires a reviewed formula expression")
        if self.implementation_status.startswith("blocked_") and not self.blocked_reasons:
            raise ValueError("blocked candidate requires a reason")
        if self.candidate_sha256 != _record_sha256(self, "candidate_sha256"):
            raise ValueError("factor candidate hash mismatch")
        return self

    @classmethod
    def build(cls, **values: object) -> FactorCandidateRecord:
        base = {"schema_id": "trademaster.factor-candidate/v1", **values}
        base["candidate_sha256"] = _sha256(_canonical_json(base))
        return cls.model_validate(base)


class FactorCollectionDefinition(BaseModel):
    """Ordered and content-addressed membership for one public factor collection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.factor-collection/v1"]
    collection_id: str = Field(pattern=rf"^{_IDENTIFIER}$")
    display_name: str = Field(min_length=1)
    collection_type: Literal[
        "alpha_library",
        "style_factor_set",
        "financial_factor_set",
        "data_vendor_factor_set",
        "derived_factor_set",
        "risk_model",
    ]
    source_ids: tuple[str, ...]
    member_ids: tuple[str, ...]
    expected_count: int = Field(gt=0)
    reported_count: int | None = Field(default=None, gt=0)
    notes: tuple[str, ...]
    collection_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def validate_identity(self) -> FactorCollectionDefinition:
        _canonical_tuple(self.source_ids, "collection sources")
        if len(self.member_ids) != len(set(self.member_ids)) or any(
            not value for value in self.member_ids
        ):
            raise ValueError("collection members must be nonempty and unique")
        if self.expected_count != len(self.member_ids):
            raise ValueError("collection expected count differs from membership")
        _canonical_tuple(self.notes, "collection notes")
        if self.collection_sha256 != _record_sha256(self, "collection_sha256"):
            raise ValueError("factor collection hash mismatch")
        return self

    @classmethod
    def build(cls, **values: object) -> FactorCollectionDefinition:
        base = {"schema_id": "trademaster.factor-collection/v1", **values}
        base["collection_sha256"] = _sha256(_canonical_json(base))
        return cls.model_validate(base)


class PublicFactorObjectRef(BaseModel):
    """One exact JSON object bound into the authoritative library manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["source", "collection", "candidate"]
    identity: str = Field(pattern=rf"^{_IDENTIFIER}$")
    record_sha256: str = Field(pattern=_SHA256)
    manifest_uri: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def validate_uri(self) -> PublicFactorObjectRef:
        uri = PurePosixPath(self.manifest_uri)
        if uri.is_absolute() or ".." in uri.parts or not uri.parts:
            raise ValueError("public factor object URI is invalid")
        return self


class PublicFactorLibraryManifest(BaseModel):
    """Authoritative complete object set for one immutable public-factor library."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_id: Literal["trademaster.public-factor-library/v1"]
    library_sha256: str = Field(pattern=_SHA256)
    sources: tuple[PublicFactorObjectRef, ...]
    collections: tuple[PublicFactorObjectRef, ...]
    candidates: tuple[PublicFactorObjectRef, ...]
    manifest_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def validate_identity(self) -> PublicFactorLibraryManifest:
        for refs, expected_kind in (
            (self.sources, "source"),
            (self.collections, "collection"),
            (self.candidates, "candidate"),
        ):
            identities = tuple(item.identity for item in refs)
            if identities != tuple(sorted(set(identities))) or any(
                item.kind != expected_kind for item in refs
            ):
                raise ValueError("public factor library object set is not canonical")
        if not self.sources or not self.collections or not self.candidates:
            raise ValueError("public factor library object set cannot be empty")
        if self.manifest_sha256 != _record_sha256(self, "manifest_sha256"):
            raise ValueError("public factor library manifest hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        library_sha256: str,
        sources: tuple[PublicFactorObjectRef, ...],
        collections: tuple[PublicFactorObjectRef, ...],
        candidates: tuple[PublicFactorObjectRef, ...],
    ) -> PublicFactorLibraryManifest:
        base: dict[str, object] = {
            "schema_id": "trademaster.public-factor-library/v1",
            "library_sha256": library_sha256,
            "sources": sources,
            "collections": collections,
            "candidates": candidates,
        }
        base["manifest_sha256"] = _sha256(
            _canonical_json(
                {
                    **base,
                    "sources": [item.model_dump(mode="json") for item in sources],
                    "collections": [item.model_dump(mode="json") for item in collections],
                    "candidates": [item.model_dump(mode="json") for item in candidates],
                }
            )
        )
        return cls.model_validate(base)


class PublicFactorLibrary:
    """Validated in-memory view over source, collection, and candidate records."""

    def __init__(
        self,
        *,
        sources: tuple[FactorSourceRecord, ...],
        collections: tuple[FactorCollectionDefinition, ...],
        candidates: tuple[FactorCandidateRecord, ...],
    ) -> None:
        self.sources = tuple(sorted(sources, key=lambda item: item.source_id))
        self.collections = tuple(sorted(collections, key=lambda item: item.collection_id))
        self.candidates = tuple(sorted(candidates, key=lambda item: item.candidate_id))
        self._sources = _unique(self.sources, lambda item: item.source_id, "source")
        self._collections = _unique(self.collections, lambda item: item.collection_id, "collection")
        self._candidates = _unique(self.candidates, lambda item: item.candidate_id, "candidate")
        for collection in self.collections:
            unknown_sources = set(collection.source_ids) - set(self._sources)
            if unknown_sources:
                raise ValueError("factor collection references an unknown source")
            actual = {
                item.candidate_id
                for item in candidates
                if item.collection_id == collection.collection_id
            }
            if actual != set(collection.member_ids):
                raise ValueError("factor collection membership differs from candidate records")
        for candidate in self.candidates:
            if candidate.collection_id not in self._collections:
                raise ValueError("factor candidate references an unknown collection")
            if set(candidate.source_ids) - set(self._sources):
                raise ValueError("factor candidate references an unknown source")
        member_ids = tuple(
            item for collection in self.collections for item in collection.member_ids
        )
        if set(member_ids) != set(self._candidates) or len(member_ids) != len(set(member_ids)):
            raise ValueError("factor candidates must belong to exactly one collection")
        self.library_sha256 = _sha256(
            _canonical_json(
                {
                    "sources": [item.source_sha256 for item in self.sources],
                    "collections": [item.collection_sha256 for item in self.collections],
                    "candidates": [item.candidate_sha256 for item in self.candidates],
                }
            )
        )

    def source(self, source_id: str) -> FactorSourceRecord:
        try:
            return self._sources[source_id]
        except KeyError as error:
            raise KeyError(f"unknown factor source: {source_id}") from error

    def collection(self, collection_id: str) -> FactorCollectionDefinition:
        try:
            return self._collections[collection_id]
        except KeyError as error:
            raise KeyError(f"unknown factor collection: {collection_id}") from error

    def candidate(self, candidate_id: str) -> FactorCandidateRecord:
        try:
            return self._candidates[candidate_id]
        except KeyError as error:
            raise KeyError(f"unknown factor candidate: {candidate_id}") from error

    def validate_implementations(self, registry: ManagedFactorRegistry) -> None:
        """Fail closed when an executable catalog mapping is absent or has drifted."""

        for candidate in self.candidates:
            if candidate.implementation_status not in {
                "implemented",
                "implemented_variant",
            }:
                continue
            if candidate.implementation_identity is None:
                raise ValueError("implemented candidate has no managed definition")
            factor_id, version = candidate.implementation_identity.rsplit("@", 1)
            try:
                registration = registry.get(factor_id, version)
            except KeyError as error:
                raise ValueError(
                    "implemented candidate references an unknown factor definition"
                ) from error
            if (
                candidate.implementation_status == "implemented"
                and registration.definition.factor_id != candidate.candidate_id
            ):
                raise ValueError("exact candidate mapping uses a different factor identity")


Record = TypeVar("Record", bound=BaseModel)


def _unique[UniqueRecord: BaseModel](
    records: tuple[UniqueRecord, ...],
    key: Callable[[UniqueRecord], str],
    label: str,
) -> dict[str, UniqueRecord]:
    # Kept local to avoid introducing a public generic registry abstraction.
    result: dict[str, UniqueRecord] = {}
    for record in records:
        identity = key(record)
        if identity in result:
            raise ValueError(f"duplicate public factor {label}: {identity}")
        result[identity] = record
    return result


class PublicFactorStore:
    """Immutable JSON authority with a DuckDB discovery catalog."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError("public factor root cannot be the filesystem root")
        self.library_root = self.root / "library"
        self.temporary = self.library_root / ".tmp"
        self.temporary.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(str(self.library_root / "catalog.duckdb"))
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_sources (
                source_id VARCHAR PRIMARY KEY,
                record_sha256 VARCHAR NOT NULL,
                manifest_uri VARCHAR NOT NULL UNIQUE,
                manifest_sha256 VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_collections (
                collection_id VARCHAR PRIMARY KEY,
                collection_type VARCHAR NOT NULL,
                expected_count INTEGER NOT NULL,
                record_sha256 VARCHAR NOT NULL,
                manifest_uri VARCHAR NOT NULL UNIQUE,
                manifest_sha256 VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_candidates (
                candidate_id VARCHAR PRIMARY KEY,
                revision VARCHAR NOT NULL,
                collection_id VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                implementation_status VARCHAR NOT NULL,
                data_availability VARCHAR NOT NULL,
                record_sha256 VARCHAR NOT NULL,
                manifest_uri VARCHAR NOT NULL UNIQUE,
                manifest_sha256 VARCHAR NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_candidate_dependencies (
                candidate_id VARCHAR NOT NULL,
                dependency VARCHAR NOT NULL,
                PRIMARY KEY (candidate_id, dependency)
            )
            """
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as error:
            raise PublicFactorIntegrityError("public factor path escapes its root") from error

    def _write(self, path: Path, payload: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest_sha256 = _sha256(payload)
        if path.exists():
            if path.read_bytes() != payload:
                raise PublicFactorIntegrityError("public factor object identity collision")
            return manifest_sha256
        with tempfile.NamedTemporaryFile(
            dir=self.temporary,
            prefix="public-factor-",
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
        return manifest_sha256

    def _persist_record(
        self,
        *,
        kind: str,
        identity: str,
        record_sha256: str,
        record: BaseModel,
    ) -> tuple[str, str]:
        payload = _canonical_json(record.model_dump(mode="json"))
        path = self.library_root / kind / identity / f"{record_sha256}.json"
        return self._relative(path), self._write(path, payload)

    @staticmethod
    def _assert_existing(
        existing: tuple[object, ...] | None,
        expected: tuple[object, ...],
        label: str,
    ) -> bool:
        if existing is None:
            return False
        if tuple(str(value) for value in existing) != tuple(str(value) for value in expected):
            raise PublicFactorIntegrityError(f"public factor {label} identity conflicts")
        return True

    def sync(
        self,
        library: PublicFactorLibrary,
        *,
        managed_registry: ManagedFactorRegistry,
    ) -> None:
        library.validate_implementations(managed_registry)
        source_rows: list[tuple[object, ...]] = []
        collection_rows: list[tuple[object, ...]] = []
        candidate_rows: list[tuple[object, ...]] = []
        dependency_rows: list[tuple[str, str]] = []
        source_refs: list[PublicFactorObjectRef] = []
        collection_refs: list[PublicFactorObjectRef] = []
        candidate_refs: list[PublicFactorObjectRef] = []
        for source_record in library.sources:
            uri, manifest_sha = self._persist_record(
                kind="sources",
                identity=source_record.source_id,
                record_sha256=source_record.source_sha256,
                record=source_record,
            )
            source_rows.append(
                (source_record.source_id, source_record.source_sha256, uri, manifest_sha)
            )
            source_refs.append(
                PublicFactorObjectRef(
                    kind="source",
                    identity=source_record.source_id,
                    record_sha256=source_record.source_sha256,
                    manifest_uri=uri,
                    manifest_sha256=manifest_sha,
                )
            )
        for collection_record in library.collections:
            uri, manifest_sha = self._persist_record(
                kind="collections",
                identity=collection_record.collection_id,
                record_sha256=collection_record.collection_sha256,
                record=collection_record,
            )
            collection_rows.append(
                (
                    collection_record.collection_id,
                    collection_record.collection_type,
                    collection_record.expected_count,
                    collection_record.collection_sha256,
                    uri,
                    manifest_sha,
                )
            )
            collection_refs.append(
                PublicFactorObjectRef(
                    kind="collection",
                    identity=collection_record.collection_id,
                    record_sha256=collection_record.collection_sha256,
                    manifest_uri=uri,
                    manifest_sha256=manifest_sha,
                )
            )
        for candidate_record in library.candidates:
            uri, manifest_sha = self._persist_record(
                kind="candidates",
                identity=candidate_record.candidate_id,
                record_sha256=candidate_record.candidate_sha256,
                record=candidate_record,
            )
            candidate_rows.append(
                (
                    candidate_record.candidate_id,
                    candidate_record.revision,
                    candidate_record.collection_id,
                    candidate_record.category,
                    candidate_record.implementation_status,
                    candidate_record.data_availability,
                    candidate_record.candidate_sha256,
                    uri,
                    manifest_sha,
                )
            )
            dependency_rows.extend(
                (candidate_record.candidate_id, dependency)
                for dependency in candidate_record.required_inputs
            )
            candidate_refs.append(
                PublicFactorObjectRef(
                    kind="candidate",
                    identity=candidate_record.candidate_id,
                    record_sha256=candidate_record.candidate_sha256,
                    manifest_uri=uri,
                    manifest_sha256=manifest_sha,
                )
            )
        manifest = PublicFactorLibraryManifest.build(
            library_sha256=library.library_sha256,
            sources=tuple(source_refs),
            collections=tuple(collection_refs),
            candidates=tuple(candidate_refs),
        )
        self._write(
            self.library_root / "manifest.json",
            _canonical_json(manifest.model_dump(mode="json")),
        )
        self._connection.execute("BEGIN TRANSACTION")
        try:
            self._sync_rows(
                "factor_sources",
                "source_id",
                source_rows,
                "INSERT INTO factor_sources VALUES (?, ?, ?, ?)",
            )
            self._sync_rows(
                "factor_collections",
                "collection_id",
                collection_rows,
                "INSERT INTO factor_collections VALUES (?, ?, ?, ?, ?, ?)",
            )
            self._sync_rows(
                "factor_candidates",
                "candidate_id",
                candidate_rows,
                "INSERT INTO factor_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            )
            existing_dependencies = tuple(
                (str(row[0]), str(row[1]))
                for row in self._connection.execute(
                    """
                    SELECT candidate_id, dependency FROM factor_candidate_dependencies
                    ORDER BY candidate_id, dependency
                    """
                ).fetchall()
            )
            expected_dependencies = tuple(sorted(dependency_rows))
            if existing_dependencies and existing_dependencies != expected_dependencies:
                raise PublicFactorIntegrityError(
                    "public factor dependency discovery identity conflicts"
                )
            if not existing_dependencies:
                self._connection.executemany(
                    "INSERT INTO factor_candidate_dependencies VALUES (?, ?)",
                    expected_dependencies,
                )
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    def _sync_rows(
        self,
        table: str,
        identity_column: str,
        rows: list[tuple[object, ...]],
        insert_sql: str,
    ) -> None:
        for row in rows:
            existing = self._connection.execute(
                f"SELECT * EXCLUDE ({identity_column}) FROM {table} WHERE {identity_column} = ?",
                [row[0]],
            ).fetchone()
            if self._assert_existing(existing, row[1:], table):
                continue
            self._connection.execute(insert_sql, list(row))

    def source_count(self) -> int:
        return len(self._load_manifest().sources)

    def collection_count(self) -> int:
        return len(self._load_manifest().collections)

    def candidate_count(self) -> int:
        return len(self._load_manifest().candidates)

    def candidate_ids(
        self,
        *,
        collection_id: str | None = None,
        category: str | None = None,
        implementation_status: str | None = None,
        data_availability: str | None = None,
        dependency: str | None = None,
        managed_registry: ManagedFactorRegistry,
        library: PublicFactorLibrary | None = None,
    ) -> tuple[str, ...]:
        authoritative = library or self.load(managed_registry=managed_registry)
        clauses: list[str] = []
        parameters: list[str] = []
        for column, value in (
            ("collection_id", collection_id),
            ("category", category),
            ("implementation_status", implementation_status),
            ("data_availability", data_availability),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if dependency is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM factor_candidate_dependencies AS dependency_index "
                "WHERE dependency_index.candidate_id = factor_candidates.candidate_id "
                "AND dependency_index.dependency = ?)"
            )
            parameters.append(dependency)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        rows = self._connection.execute(
            "SELECT candidate_id FROM factor_candidates" + where + " ORDER BY candidate_id",
            parameters,
        ).fetchall()
        discovered = tuple(str(row[0]) for row in rows)
        expected = tuple(
            item.candidate_id
            for item in authoritative.candidates
            if (collection_id is None or item.collection_id == collection_id)
            and (category is None or item.category == category)
            and (
                implementation_status is None or item.implementation_status == implementation_status
            )
            and (data_availability is None or item.data_availability == data_availability)
            and (dependency is None or dependency in item.required_inputs)
        )
        if discovered != expected:
            raise PublicFactorIntegrityError(
                "public factor discovery query differs from authoritative JSON"
            )
        return discovered

    def candidate_path(self, candidate_id: str) -> Path:
        manifest = self._load_manifest()
        try:
            ref = next(item for item in manifest.candidates if item.identity == candidate_id)
        except StopIteration as error:
            raise KeyError(f"unknown factor candidate: {candidate_id}") from error
        return self._resolve_uri(ref.manifest_uri)

    def _resolve_uri(self, uri: str) -> Path:
        relative = PurePosixPath(uri)
        if relative.is_absolute() or ".." in relative.parts:
            raise PublicFactorIntegrityError("public factor manifest URI is invalid")
        path = (self.root / relative).resolve()
        self._relative(path)
        return path

    def _load_refs(
        self,
        refs: tuple[PublicFactorObjectRef, ...],
        model: type[Record],
        *,
        identity_field: str,
        record_hash_field: str,
    ) -> tuple[Record, ...]:
        records: list[Record] = []
        for ref in refs:
            path = self._resolve_uri(ref.manifest_uri)
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise PublicFactorIntegrityError("public factor manifest is missing") from error
            if _sha256(payload) != ref.manifest_sha256:
                raise PublicFactorIntegrityError("public factor manifest hash mismatch")
            try:
                record = model.model_validate_json(payload, strict=True)
            except ValueError as error:
                raise PublicFactorIntegrityError("public factor manifest is invalid") from error
            if (
                str(getattr(record, identity_field)) != ref.identity
                or str(getattr(record, record_hash_field)) != ref.record_sha256
            ):
                raise PublicFactorIntegrityError("public factor object identity mismatch")
            records.append(record)
        return tuple(records)

    def _load_manifest(self) -> PublicFactorLibraryManifest:
        path = self.library_root / "manifest.json"
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise PublicFactorIntegrityError(
                "authoritative public factor library manifest is missing"
            ) from error
        try:
            return PublicFactorLibraryManifest.model_validate_json(payload, strict=True)
        except ValueError as error:
            raise PublicFactorIntegrityError(
                "authoritative public factor library manifest is invalid"
            ) from error

    def _validate_catalog(
        self,
        library: PublicFactorLibrary,
        manifest: PublicFactorLibraryManifest,
    ) -> None:
        source_refs = {item.identity: item for item in manifest.sources}
        collection_refs = {item.identity: item for item in manifest.collections}
        candidate_refs = {item.identity: item for item in manifest.candidates}
        expected_sources = tuple(
            (
                item.source_id,
                item.source_sha256,
                source_refs[item.source_id].manifest_uri,
                source_refs[item.source_id].manifest_sha256,
            )
            for item in library.sources
        )
        expected_collections = tuple(
            (
                item.collection_id,
                item.collection_type,
                item.expected_count,
                item.collection_sha256,
                collection_refs[item.collection_id].manifest_uri,
                collection_refs[item.collection_id].manifest_sha256,
            )
            for item in library.collections
        )
        expected_candidates = tuple(
            (
                item.candidate_id,
                item.revision,
                item.collection_id,
                item.category,
                item.implementation_status,
                item.data_availability,
                item.candidate_sha256,
                candidate_refs[item.candidate_id].manifest_uri,
                candidate_refs[item.candidate_id].manifest_sha256,
            )
            for item in library.candidates
        )
        expected_dependencies = tuple(
            sorted(
                (item.candidate_id, dependency)
                for item in library.candidates
                for dependency in item.required_inputs
            )
        )
        actual_sources = tuple(
            tuple(row)
            for row in self._connection.execute(
                "SELECT * FROM factor_sources ORDER BY source_id"
            ).fetchall()
        )
        actual_collections = tuple(
            tuple(row)
            for row in self._connection.execute(
                "SELECT * FROM factor_collections ORDER BY collection_id"
            ).fetchall()
        )
        actual_candidates = tuple(
            tuple(row)
            for row in self._connection.execute(
                "SELECT * FROM factor_candidates ORDER BY candidate_id"
            ).fetchall()
        )
        actual_dependencies = tuple(
            (str(row[0]), str(row[1]))
            for row in self._connection.execute(
                """
                SELECT candidate_id, dependency FROM factor_candidate_dependencies
                ORDER BY candidate_id, dependency
                """
            ).fetchall()
        )
        if (
            actual_sources != expected_sources
            or actual_collections != expected_collections
            or actual_candidates != expected_candidates
            or actual_dependencies != expected_dependencies
        ):
            raise PublicFactorIntegrityError(
                "public factor catalog differs from the authoritative JSON manifest"
            )

    def load(self, *, managed_registry: ManagedFactorRegistry) -> PublicFactorLibrary:
        manifest = self._load_manifest()
        library = PublicFactorLibrary(
            sources=self._load_refs(
                manifest.sources,
                FactorSourceRecord,
                identity_field="source_id",
                record_hash_field="source_sha256",
            ),
            collections=self._load_refs(
                manifest.collections,
                FactorCollectionDefinition,
                identity_field="collection_id",
                record_hash_field="collection_sha256",
            ),
            candidates=self._load_refs(
                manifest.candidates,
                FactorCandidateRecord,
                identity_field="candidate_id",
                record_hash_field="candidate_sha256",
            ),
        )
        if library.library_sha256 != manifest.library_sha256:
            raise PublicFactorIntegrityError("public factor library content hash mismatch")
        library.validate_implementations(managed_registry)
        self._validate_catalog(library, manifest)
        return library


__all__ = [
    "DataAvailability",
    "FactorCandidateRecord",
    "FactorCollectionDefinition",
    "FactorSourceRecord",
    "ImplementationStatus",
    "PublicFactorIntegrityError",
    "PublicFactorLibrary",
    "PublicFactorLibraryManifest",
    "PublicFactorObjectRef",
    "PublicFactorStore",
]
