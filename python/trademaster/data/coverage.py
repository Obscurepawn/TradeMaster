"""Cache-first coverage proof and explicit provider gap filling."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, Self, cast, runtime_checkable

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic_core import ValidationError

from trademaster.contracts import (
    AvailabilityPolicy,
    CoverageResult,
    DatasetCoverage,
    DatasetRequest,
    SnapshotManifest,
    SnapshotObject,
    SnapshotRequest,
    _require_utc,
    bind_snapshot_provenance,
)

from .catalog import CatalogObject, DuckDbCatalog
from .object_store import ParquetObjectStore
from .registry import DatasetRegistry, DatasetSpec, normalize_request
from .status import latest_status_rows


class CoverageGapError(RuntimeError):
    pass


class ProviderPage(BaseModel):
    """One normalized canonical page returned by a provider adapter."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, arbitrary_types_allowed=True
    )

    table: pa.Table
    partition_key: str
    coverage_keys: tuple[str, ...]
    next_page_token: str | None
    upstream_object_sha256s: tuple[str, ...] = ()

    @field_validator("next_page_token")
    @classmethod
    def validate_page_token(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("next page token cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        if not self.partition_key:
            raise ValueError("provider page partition cannot be empty")
        if not self.coverage_keys or self.coverage_keys != tuple(
            sorted(set(self.coverage_keys))
        ):
            raise ValueError("provider page coverage keys must be non-empty, unique and sorted")
        if self.upstream_object_sha256s != tuple(
            sorted(set(self.upstream_object_sha256s))
        ) or any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in self.upstream_object_sha256s
        ):
            raise ValueError("upstream object hashes must be canonical sha256 values")
        return self


@runtime_checkable
class CanonicalDataProvider(Protocol):
    def fetch(
        self,
        request: DatasetRequest,
        *,
        coverage_keys: tuple[str, ...],
        page_token: str | None,
    ) -> ProviderPage: ...


@runtime_checkable
class RequestResolver(Protocol):
    """Resolve configured universes and exact coverage keys without changing intent."""

    def resolve(self, request: DatasetRequest) -> DatasetRequest: ...


@dataclass(frozen=True)
class _CoveragePlan:
    objects: tuple[CatalogObject, ...]
    missing_keys: tuple[str, ...]


def _tuple_sha256(values: tuple[str, ...]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class CacheFirstDataPortal:
    def __init__(
        self,
        *,
        catalog: DuckDbCatalog,
        store: ParquetObjectStore,
        registry: DatasetRegistry,
        provider: CanonicalDataProvider,
        clock: Callable[[], datetime],
        request_resolver: RequestResolver | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.registry = registry
        self.provider = provider
        self.clock = clock
        self.request_resolver = request_resolver

    def ensure(self, request: DatasetRequest) -> CoverageResult:
        request = self._resolve_request(request)
        plan = self._plan(request)
        if not plan.missing_keys:
            return self._complete_result(request, plan.objects)

        suspension_evidence: dict[tuple[str, str], str] = {}

        pages = self._fetch_all_pages(request, plan.missing_keys)
        tables_by_partition: dict[str, list[pa.Table]] = {}
        keys_by_partition: dict[str, list[str]] = {}
        upstream_by_partition: dict[str, list[str]] = {}
        for page in pages:
            tables_by_partition.setdefault(page.partition_key, []).append(page.table)
            keys_by_partition.setdefault(page.partition_key, []).extend(page.coverage_keys)
            upstream_by_partition.setdefault(page.partition_key, []).extend(
                page.upstream_object_sha256s
            )

        missing_bar_pairs: set[tuple[str, str]] = set()
        if request.dataset == "daily_bars":
            for partition_key, tables in tables_by_partition.items():
                table = pa.concat_tables(tables)
                actual_pairs = {
                    (
                        str(row["instrument_id"]),
                        f'{row["venue"]}:{row["trade_date"].isoformat()}',
                    )
                    for row in table.select(
                        ["instrument_id", "venue", "trade_date"]
                    ).to_pylist()
                }
                expected_pairs = {
                    (instrument_id, coverage_key)
                    for instrument_id in request.instruments
                    for coverage_key in keys_by_partition[partition_key]
                }
                missing_bar_pairs.update(expected_pairs - actual_pairs)
        if missing_bar_pairs:
            status_request = DatasetRequest(
                dataset="daily_limits_status",
                start=request.start,
                end=request.end,
                instruments=tuple(
                    sorted({instrument_id for instrument_id, _ in missing_bar_pairs})
                ),
                fields=("suspended",),
                coverage_keys=tuple(
                    sorted({coverage_key for _, coverage_key in missing_bar_pairs})
                ),
            )
            status_result = self.ensure(status_request)
            status_objects = {
                obj.sha256: obj
                for obj in self.catalog.objects("daily_limits_status")
                if obj.sha256 in status_result.covered_object_sha256s
            }
            for sha256, obj in status_objects.items():
                status_table = pq.read_table(
                    self.store.paths.data_root / obj.uri,
                    columns=[
                        "instrument_id",
                        "venue",
                        "trade_date",
                        "suspended",
                        "known_at",
                        "source_revision",
                    ],
                )
                try:
                    current_rows = latest_status_rows(status_table)
                except ValueError as error:
                    raise CoverageGapError(str(error)) from error
                for pair, row in current_rows.items():
                    if row["suspended"] is True:
                        suspension_evidence[pair] = sha256

        created_at = self.clock()
        for partition_key in sorted(tables_by_partition):
            keys = tuple(sorted(keys_by_partition[partition_key]))
            partial = DatasetRequest(
                dataset=request.dataset,
                start=request.start,
                end=request.end,
                instruments=request.instruments,
                fields=request.fields,
                coverage_keys=keys,
            )
            table = pa.concat_tables(tables_by_partition[partition_key])
            absence_proofs: tuple[str, ...] = ()
            if request.dataset == "daily_bars":
                actual_pairs = {
                    (
                        str(row["instrument_id"]),
                        f'{row["venue"]}:{row["trade_date"].isoformat()}',
                    )
                    for row in table.select(
                        ["instrument_id", "venue", "trade_date"]
                    ).to_pylist()
                }
                expected_pairs = {
                    (instrument_id, coverage_key)
                    for instrument_id in request.instruments
                    for coverage_key in keys
                }
                missing_pairs = expected_pairs - actual_pairs
                if any(pair not in suspension_evidence for pair in missing_pairs):
                    raise CoverageGapError(
                        "missing daily bar lacks a suspension absence proof"
                    )
                absence_proofs = tuple(
                    sorted(
                        f"{instrument_id}|{coverage_key}|suspended|"
                        f"{suspension_evidence[(instrument_id, coverage_key)]}"
                        for instrument_id, coverage_key in missing_pairs
                    )
                )
            self.store.publish_and_register(
                self.catalog,
                partial,
                table,
                partition_key=partition_key,
                created_at=created_at,
                upstream_object_sha256s=tuple(
                    sorted(set(upstream_by_partition[partition_key]))
                ),
                absence_proofs=absence_proofs,
            )

        completed = self._plan(request)
        if completed.missing_keys:
            raise CoverageGapError("provider publication did not close requested coverage")
        return self._complete_result(request, completed.objects)

    def query(self, request: DatasetRequest, *, as_of: datetime) -> pa.Table:
        _require_utc(as_of)
        request = self._resolve_request(request)
        snapshot = self.snapshot(SnapshotRequest(datasets=(request,), as_of=as_of))
        paths = [
            str(self.store.paths.data_root / obj.uri)
            for obj in snapshot.objects
            if obj.dataset == request.dataset
        ]
        if not paths:
            raise CoverageGapError("snapshot has no objects for query")
        spec = self.registry[request.dataset]
        projection = tuple(
            dict.fromkeys(
                spec.primary_key
                + request.fields
                + spec.coverage_key_fields
                + ("event_time", "known_at", "source_revision")
            )
        )
        quoted_projection = ", ".join(f'"{name}"' for name in projection)
        quoted_primary = ", ".join(f'"{name}"' for name in spec.primary_key)
        revision_order = ", ".join(
            [
                "known_at DESC",
                *(f'"{name}" DESC' for name in spec.revision_order_fields),
                "source_revision DESC",
            ]
        )
        predicates = ["event_time >= ?", "event_time <= ?", "known_at <= ?"]
        parameters: list[object] = [paths, request.start, request.end, as_of]
        if request.instruments:
            placeholders = ",".join("?" for _ in request.instruments)
            predicates.append(f"instrument_id IN ({placeholders})")
            parameters.extend(request.instruments)
        sql = f"""
            SELECT {quoted_projection}
            FROM (
                SELECT {quoted_projection},
                       row_number() OVER (
                           PARTITION BY {quoted_primary}
                           ORDER BY {revision_order}
                       ) AS revision_rank
                FROM read_parquet(?, union_by_name = true)
                WHERE {' AND '.join(predicates)}
            )
            WHERE revision_rank = 1
            ORDER BY {quoted_primary}
        """
        connection = duckdb.connect()
        try:
            table = connection.execute(sql, parameters).to_arrow_table()
        finally:
            connection.close()
        for index, field in enumerate(table.schema):
            if pa.types.is_timestamp(field.type) and field.type != pa.timestamp("us", tz="UTC"):
                table = table.set_column(
                    index,
                    field.with_type(pa.timestamp("us", tz="UTC")),
                    table.column(index).cast(pa.timestamp("us", tz="UTC")),
                )
        return bind_snapshot_provenance(table, snapshot)

    def snapshot(self, request: SnapshotRequest) -> SnapshotManifest:
        resolved = list(
            self._merge_compatible_requests(
                tuple(
                    self._resolve_request(dataset_request)
                    for dataset_request in request.datasets
                )
            )
        )
        selected_explicit_status_sha256s: set[str] = set()
        for status_request in (
            item for item in resolved if item.dataset == "daily_limits_status"
        ):
            self.ensure(status_request)
            status_plan = self._plan(status_request, known_at_cutoff=request.as_of)
            selected_explicit_status_sha256s.update(
                obj.sha256 for obj in status_plan.objects
            )
        for dataset_request in tuple(resolved):
            if dataset_request.dataset == "daily_bars":
                self.ensure(dataset_request)
                bar_plan = self._plan(
                    dataset_request, known_at_cutoff=request.as_of
                )
                if bar_plan.missing_keys:
                    raise CoverageGapError(
                        "PIT snapshot lacks daily bars known by its as_of cutoff"
                    )
                proofs = tuple(
                    proof
                    for obj in bar_plan.objects
                    for proof in self.store.verify_publication(obj).absence_proofs
                )
                if not proofs:
                    continue
                proof_sha256s = {proof.rsplit("|", 1)[1] for proof in proofs}
                if proof_sha256s <= selected_explicit_status_sha256s:
                    continue
                proof_pairs = {
                    (parts[0], parts[1])
                    for proof in proofs
                    if len(parts := proof.split("|")) == 4
                }
                dependency = DatasetRequest(
                    dataset="daily_limits_status",
                    start=dataset_request.start,
                    end=dataset_request.end,
                    instruments=tuple(
                        sorted({instrument_id for instrument_id, _ in proof_pairs})
                    ),
                    fields=("suspended",),
                    coverage_keys=tuple(
                        sorted({coverage_key for _, coverage_key in proof_pairs})
                    ),
                )
                if dependency not in resolved:
                    resolved.append(dependency)
        request = SnapshotRequest(
            datasets=self._merge_compatible_requests(tuple(resolved)),
            as_of=request.as_of,
        )
        coverages: list[DatasetCoverage] = []
        snapshot_objects: list[SnapshotObject] = []
        absence_evidence_sha256s: set[str] = set()
        snapshot_absence_proofs: set[str] = set()
        for dataset_request in request.datasets:
            self.ensure(dataset_request)
            plan = self._plan(dataset_request, known_at_cutoff=request.as_of)
            if plan.missing_keys:
                raise CoverageGapError(
                    "PIT snapshot lacks coverage entirely known by its as_of cutoff"
                )
            objects = plan.objects
            for obj in objects:
                publication = self.store.verify_publication(obj)
                absence_evidence_sha256s.update(
                    proof.rsplit("|", 1)[1]
                    for proof in publication.absence_proofs
                )
                snapshot_absence_proofs.update(publication.absence_proofs)
            instrument_digests = {obj.instrument_set_sha256 for obj in objects}
            instrument_counts = {obj.instrument_count for obj in objects}
            if len(instrument_digests) != 1 or len(instrument_counts) != 1:
                raise CoverageGapError("snapshot objects disagree on resolved universe")
            snapshot_objects.extend(
                self._snapshot_object(obj)
                for obj in objects
            )
            coverages.append(
                DatasetCoverage(
                    request=dataset_request,
                    covered_start=dataset_request.start,
                    covered_end=dataset_request.end,
                    fields=dataset_request.fields,
                    resolved_instrument_set_sha256=next(iter(instrument_digests)),
                    resolved_instrument_count=next(iter(instrument_counts)),
                    object_sha256s=tuple(sorted(obj.sha256 for obj in objects)),
                    row_count=sum(obj.row_count for obj in objects),
                    known_at_max=max(obj.known_at_max for obj in objects),
                )
            )
        snapshot_sha256s = {obj.sha256 for obj in snapshot_objects}
        self._validate_current_absence_proofs(
            snapshot_absence_proofs, as_of=request.as_of
        )
        if not absence_evidence_sha256s <= snapshot_sha256s:
            raise CoverageGapError(
                "snapshot does not contain the exact absence evidence objects"
            )
        manifest = SnapshotManifest.build(
            request=request,
            availability_policy=AvailabilityPolicy(
                policy_id="provider-known-at/v1",
                known_at_field="known_at",
                publication_lag_policy_id="dataset-registry/v1",
            ),
            objects=tuple(snapshot_objects),
            coverages=tuple(coverages),
        )
        self._persist_snapshot(manifest)
        return manifest

    def load_snapshot(self, snapshot_id: str) -> SnapshotManifest:
        """Load a persisted snapshot only after re-verifying its immutable objects."""

        if re.fullmatch(r"[0-9a-f]{64}", snapshot_id) is None:
            raise CoverageGapError("snapshot id must be a lowercase sha256")
        path = self.store.paths.snapshots / snapshot_id / "manifest.json"
        if not path.is_file():
            raise CoverageGapError("persisted snapshot manifest is missing")
        try:
            manifest = SnapshotManifest.model_validate_json(path.read_bytes())
        except (OSError, ValidationError, ValueError) as error:
            raise CoverageGapError("persisted snapshot manifest is invalid") from error
        if manifest.snapshot_id != snapshot_id:
            raise CoverageGapError("persisted snapshot path disagrees with manifest identity")

        absence_proofs: set[str] = set()
        evidence_sha256s: set[str] = set()
        verified_objects: dict[str, CatalogObject] = {}
        for snapshot_object in manifest.objects:
            matches = tuple(
                obj
                for obj in self.catalog.objects(snapshot_object.dataset)
                if obj.sha256 == snapshot_object.sha256
            )
            if len(matches) != 1:
                raise CoverageGapError("snapshot object is not uniquely catalogued")
            publication = self.store.verify_publication(matches[0])
            verified_objects[snapshot_object.sha256] = publication.object
            if self._snapshot_object(publication.object) != snapshot_object:
                raise CoverageGapError("snapshot object metadata no longer matches manifest")
            absence_proofs.update(publication.absence_proofs)
            evidence_sha256s.update(
                proof.rsplit("|", 1)[1] for proof in publication.absence_proofs
            )
        for coverage in manifest.coverages:
            self._validate_global_revision_consistency(
                coverage.request,
                known_at_cutoff=manifest.as_of,
            )
            self._validate_no_shadowed_revisions(
                coverage.request,
                selected=tuple(
                    verified_objects[sha256]
                    for sha256 in coverage.object_sha256s
                ),
                known_at_cutoff=manifest.as_of,
            )
        self._validate_current_absence_proofs(
            absence_proofs, as_of=manifest.as_of
        )
        if not evidence_sha256s <= {obj.sha256 for obj in manifest.objects}:
            raise CoverageGapError(
                "snapshot does not contain the exact absence evidence objects"
            )
        return manifest

    @staticmethod
    def _snapshot_object(obj: CatalogObject) -> SnapshotObject:
        return SnapshotObject(
            dataset=obj.dataset,
            partition=obj.partition_key,
            uri=obj.uri,
            sha256=obj.sha256,
            schema_sha256=obj.schema_sha256,
            schema_version=obj.schema_version,
            fields=obj.fields,
            coverage_keys=obj.coverage_keys,
            resolved_instrument_set_sha256=obj.instrument_set_sha256,
            resolved_instrument_count=obj.instrument_count,
            row_count=obj.row_count,
            event_time_start=obj.event_time_start,
            event_time_end=obj.event_time_end,
            known_at_max=obj.known_at_max,
        )

    def _validate_current_absence_proofs(
        self, proofs: set[str], *, as_of: datetime
    ) -> None:
        for proof in proofs:
            instrument_id, coverage_key, _, evidence_sha256 = proof.split("|")
            evidence_revisions: list[tuple[datetime, bool, str]] = []
            revisions: list[tuple[datetime, bool, str]] = []
            for obj in self.catalog.objects("daily_limits_status"):
                publication = self.store.verify_publication(obj)
                table = pq.read_table(
                    publication.path,
                    columns=[
                        "instrument_id",
                        "venue",
                        "trade_date",
                        "suspended",
                        "known_at",
                        "source_revision",
                    ],
                )
                for row in table.to_pylist():
                    row_key = f'{row["venue"]}:{row["trade_date"].isoformat()}'
                    if row["instrument_id"] != instrument_id or row_key != coverage_key:
                        continue
                    known_at = cast(datetime, row["known_at"])
                    if known_at > as_of:
                        continue
                    revisions.append(
                        (
                            known_at,
                            bool(row["suspended"]),
                            str(row["source_revision"]),
                        )
                    )
                    if obj.sha256 == evidence_sha256:
                        evidence_revisions.append(
                            (
                                known_at,
                                bool(row["suspended"]),
                                str(row["source_revision"]),
                            )
                        )
            if not evidence_revisions or not revisions:
                raise CoverageGapError("exact absence evidence is no longer resolvable")
            evidence_known_at = max(item[0] for item in evidence_revisions)
            evidence_latest = {
                (item[1], item[2])
                for item in evidence_revisions
                if item[0] == evidence_known_at
            }
            if len(evidence_latest) != 1:
                raise CoverageGapError("exact absence evidence has an ambiguous revision")
            evidence_suspended, _ = next(iter(evidence_latest))
            if not evidence_suspended:
                raise CoverageGapError("exact absence evidence does not prove suspension")
            latest_known_at = max(item[0] for item in revisions)
            latest = {(item[1], item[2]) for item in revisions if item[0] == latest_known_at}
            if len(latest) != 1:
                raise CoverageGapError("exact absence evidence has an ambiguous revision")
            if latest_known_at > evidence_known_at:
                raise CoverageGapError("exact absence evidence was superseded")

    @staticmethod
    def _merge_compatible_requests(
        requests: tuple[DatasetRequest, ...],
    ) -> tuple[DatasetRequest, ...]:
        merged = list(requests)
        while True:
            pair: tuple[int, int] | None = None
            for left_index, left in enumerate(merged):
                for right_index in range(left_index + 1, len(merged)):
                    right = merged[right_index]
                    if (
                        left.dataset == right.dataset
                        and left.instruments == right.instruments
                        and set(left.coverage_keys) & set(right.coverage_keys)
                    ):
                        pair = (left_index, right_index)
                        break
                if pair is not None:
                    break
            if pair is None:
                break
            left_index, right_index = pair
            left = merged[left_index]
            right = merged[right_index]
            combined = DatasetRequest(
                dataset=left.dataset,
                start=min(left.start, right.start),
                end=max(left.end, right.end),
                instruments=left.instruments,
                fields=tuple(sorted(set(left.fields) | set(right.fields))),
                coverage_keys=tuple(
                    sorted(set(left.coverage_keys) | set(right.coverage_keys))
                ),
            )
            merged.pop(right_index)
            merged.pop(left_index)
            merged.append(combined)
        return tuple(
            sorted(
                merged,
                key=lambda item: (
                    item.dataset,
                    item.start,
                    item.end,
                    item.instruments,
                    item.fields,
                    item.coverage_keys,
                ),
            )
        )

    def _resolve_request(self, request: DatasetRequest) -> DatasetRequest:
        original = request
        if self.request_resolver is not None and (
            not request.coverage_keys
            or (
                "instrument_id" in self.registry[request.dataset].required_fields
                and not request.instruments
            )
        ):
            request = self.request_resolver.resolve(request)
            if (
                request.dataset != original.dataset
                or request.start != original.start
                or request.end != original.end
                or request.fields != original.fields
            ):
                raise CoverageGapError("request resolver changed immutable request intent")
            if (
                original.instruments
                and request.instruments != original.instruments
            ) or (
                original.coverage_keys
                and request.coverage_keys != original.coverage_keys
            ):
                raise CoverageGapError("request resolver changed existing request scope")
        request = normalize_request(
            self.registry,
            dataset=request.dataset,
            start=request.start,
            end=request.end,
            instruments=request.instruments,
            fields=request.fields,
            coverage_keys=request.coverage_keys,
        )
        spec = self.registry[request.dataset]
        if "instrument_id" in spec.required_fields and not request.instruments:
            raise CoverageGapError("instrument universe must be resolved before data access")
        if request.coverage_keys:
            return request
        raise CoverageGapError(
            "coverage keys must be resolved by an explicit market/calendar resolver"
        )

    def _plan(
        self, request: DatasetRequest, *, known_at_cutoff: datetime | None = None
    ) -> _CoveragePlan:
        requested_keys = set(request.coverage_keys)
        requested_instrument_sha = _tuple_sha256(request.instruments)
        self._validate_global_revision_consistency(
            request, known_at_cutoff=known_at_cutoff
        )
        candidates: list[CatalogObject] = []
        for obj in self.catalog.objects(request.dataset):
            keys = set(obj.coverage_keys)
            if not keys or not keys <= requested_keys:
                continue
            if not set(request.fields) <= set(obj.fields):
                continue
            if request.instruments and (
                obj.instrument_set_sha256 != requested_instrument_sha
                or obj.instrument_count != len(request.instruments)
            ):
                continue
            if obj.event_time_start < request.start or obj.event_time_end > request.end:
                continue
            publication = self.store.verify_publication(obj)
            if publication.coverage not in self.catalog.coverage_for_object(obj):
                continue
            if known_at_cutoff is not None and obj.known_at_max > known_at_cutoff:
                known_times = cast(
                    list[datetime],
                    pq.read_table(publication.path, columns=["known_at"])[
                        "known_at"
                    ].to_pylist(),
                )
                if any(known_at <= known_at_cutoff for known_at in known_times):
                    raise CoverageGapError(
                        "canonical object straddles PIT cutoff and cannot be selected safely"
                    )
                continue
            candidates.append(obj)
        final = sorted(candidates, key=lambda candidate: candidate.sha256)
        if known_at_cutoff is not None:
            self._validate_no_shadowed_revisions(
                request,
                selected=tuple(final),
                known_at_cutoff=known_at_cutoff,
            )
        covered = {
            coverage_key
            for obj in final
            for coverage_key in obj.coverage_keys
        }
        return _CoveragePlan(
            objects=tuple(final),
            missing_keys=tuple(sorted(requested_keys - covered)),
        )

    def _validate_global_revision_consistency(
        self,
        request: DatasetRequest,
        *,
        known_at_cutoff: datetime | None,
    ) -> None:
        spec = self.registry[request.dataset]
        requested_keys = set(request.coverage_keys)
        identities: dict[tuple[object, ...], tuple[str, str]] = {}
        for obj in self.catalog.objects(request.dataset):
            if not set(obj.coverage_keys) & requested_keys:
                continue
            if obj.event_time_end < request.start or obj.event_time_start > request.end:
                continue
            publication = self.store.verify_publication(obj)
            table = pq.read_table(publication.path)
            for row in table.to_pylist():
                if not self._row_is_in_request_scope(
                    request,
                    spec,
                    row,
                    known_at_cutoff=known_at_cutoff,
                ):
                    continue
                identity = tuple(
                    row[name]
                    for name in spec.primary_key
                    + ("known_at",)
                    + spec.revision_order_fields
                )
                source_revision = str(row["source_revision"])
                payload_sha256 = hashlib.sha256(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode()
                ).hexdigest()
                current = (source_revision, payload_sha256)
                previous = identities.setdefault(identity, current)
                if previous != current:
                    raise CoverageGapError(
                        "canonical objects disagree on global business revision identity"
                    )

    def _validate_no_shadowed_revisions(
        self,
        request: DatasetRequest,
        *,
        selected: tuple[CatalogObject, ...],
        known_at_cutoff: datetime,
    ) -> None:
        selected_latest = self._latest_scoped_revisions(
            request, selected, known_at_cutoff=known_at_cutoff
        )
        global_latest = self._latest_scoped_revisions(
            request,
            self.catalog.objects(request.dataset),
            known_at_cutoff=known_at_cutoff,
        )
        if any(
            selected_latest.get(primary_key) != revision
            for primary_key, revision in global_latest.items()
        ):
            raise CoverageGapError(
                "newer eligible revision exists outside selected object scope"
            )

    def _latest_scoped_revisions(
        self,
        request: DatasetRequest,
        objects: tuple[CatalogObject, ...],
        *,
        known_at_cutoff: datetime,
    ) -> dict[
        tuple[object, ...],
        tuple[tuple[datetime, tuple[str, ...], str], str],
    ]:
        spec = self.registry[request.dataset]
        requested_keys = set(request.coverage_keys)
        latest: dict[
            tuple[object, ...],
            tuple[tuple[datetime, tuple[str, ...], str], str],
        ] = {}
        for obj in objects:
            if not set(obj.coverage_keys) & requested_keys:
                continue
            if obj.event_time_end < request.start or obj.event_time_start > request.end:
                continue
            publication = self.store.verify_publication(obj)
            for row in pq.read_table(publication.path).to_pylist():
                if not self._row_is_in_request_scope(
                    request,
                    spec,
                    row,
                    known_at_cutoff=known_at_cutoff,
                ):
                    continue
                known_at = cast(datetime, row["known_at"])
                primary_key = tuple(row[name] for name in spec.primary_key)
                source_revision = str(row["source_revision"])
                rank = (
                    known_at,
                    tuple(str(row[name]) for name in spec.revision_order_fields),
                    source_revision,
                )
                payload_sha256 = hashlib.sha256(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode()
                ).hexdigest()
                current = (rank, payload_sha256)
                previous = latest.get(primary_key)
                if previous is None or current[0] > previous[0]:
                    latest[primary_key] = current
                elif current[0] == previous[0] and current[1] != previous[1]:
                    raise CoverageGapError(
                        "canonical objects disagree on global business revision identity"
                    )
        return latest

    @staticmethod
    def _row_is_in_request_scope(
        request: DatasetRequest,
        spec: DatasetSpec,
        row: dict[str, object],
        *,
        known_at_cutoff: datetime | None,
    ) -> bool:
        event_time = cast(datetime, row["event_time"])
        known_at = cast(datetime, row["known_at"])
        if event_time < request.start or event_time > request.end:
            return False
        if known_at_cutoff is not None and known_at > known_at_cutoff:
            return False
        if request.instruments and row.get("instrument_id") not in set(
            request.instruments
        ):
            return False
        coverage_key = ":".join(
            value.isoformat() if hasattr(value, "isoformat") else str(value)
            for value in (row[name] for name in spec.coverage_key_fields)
        )
        return coverage_key in set(request.coverage_keys)

    def _fetch_all_pages(
        self, request: DatasetRequest, missing_keys: tuple[str, ...]
    ) -> tuple[ProviderPage, ...]:
        pages: list[ProviderPage] = []
        seen_keys: set[str] = set()
        seen_tokens: set[str] = set()
        token: str | None = None
        while True:
            page = self.provider.fetch(
                request, coverage_keys=missing_keys, page_token=token
            )
            page_keys = set(page.coverage_keys)
            if not page_keys <= set(missing_keys):
                raise CoverageGapError("provider returned an unrequested coverage key")
            if seen_keys & page_keys:
                raise CoverageGapError("duplicate provider coverage key")
            seen_keys.update(page_keys)
            pages.append(page)
            token = page.next_page_token
            if token is None:
                break
            if token in seen_tokens:
                raise CoverageGapError("provider page token cycle")
            seen_tokens.add(token)
        if seen_keys != set(missing_keys):
            raise CoverageGapError("provider did not return every missing coverage key")
        return tuple(pages)

    @staticmethod
    def _complete_result(
        request: DatasetRequest, objects: tuple[CatalogObject, ...]
    ) -> CoverageResult:
        return CoverageResult(
            request=request,
            complete=True,
            covered_object_sha256s=tuple(sorted(obj.sha256 for obj in objects)),
        )

    def _persist_snapshot(self, manifest: SnapshotManifest) -> None:
        directory = self.store.paths.snapshots / manifest.snapshot_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "manifest.json"
        payload = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if destination.exists():
            if destination.read_bytes() != payload:
                raise CoverageGapError("snapshot manifest identity collision")
            return
        descriptor, name = tempfile.mkstemp(
            prefix="manifest-", suffix=".tmp", dir=directory
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != payload:
                    raise CoverageGapError("snapshot manifest identity collision")
            directory_descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary.unlink(missing_ok=True)
