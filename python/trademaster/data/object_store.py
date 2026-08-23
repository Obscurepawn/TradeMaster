"""Atomic, content-addressed storage for canonical Parquet objects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pyarrow as pa
import pyarrow.parquet as pq

from trademaster.contracts import DatasetRequest, _require_utc

from .catalog import CatalogCoverage, CatalogObject, DuckDbCatalog
from .config import DataPaths
from .registry import CoverageKeyMode, CoverageShape, DatasetRegistry, DatasetSpec
from .status import latest_status_rows
from .tushare_replay import (
    validate_tushare_raw_request_scope,
    validate_tushare_staging_replay,
)

_OBJECT_METADATA_KEY = b"trademaster.object/v1"
_TUSHARE_ENDPOINTS_BY_DATASET: dict[str, frozenset[str]] = {
    "trade_calendar": frozenset({"trade_cal"}),
    "instrument_master": frozenset({"stock_basic", "etf_basic"}),
    "daily_bars": frozenset({"daily", "fund_daily"}),
    "daily_limits_status": frozenset({"stk_limit", "suspend_d", "stock_st"}),
    "adj_factors": frozenset({"adj_factor"}),
    "daily_basic": frozenset({"daily_basic"}),
    "index_bars": frozenset({"index_daily"}),
    "index_membership": frozenset({"index_weight"}),
    "industry_membership": frozenset({"index_member_all"}),
    "financial_indicators": frozenset({"fina_indicator"}),
}
_TUSHARE_PAGE_LIMIT_BY_ENDPOINT = {
    "trade_cal": 6000,
    "stock_basic": 6000,
    "etf_basic": 2000,
    "daily": 6000,
    "fund_daily": 800,
    "stk_limit": 5800,
    "suspend_d": 5000,
    "stock_st": 1000,
    "adj_factor": 6000,
    "daily_basic": 6000,
    "index_daily": 8000,
    "index_weight": 1000,
    "index_member_all": 2000,
    "fina_indicator": 100,
}
_MUTABLE_TUSHARE_ENDPOINTS = frozenset({"suspend_d", "stock_st"})


class ObjectIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishedObject:
    object: CatalogObject
    coverage: CatalogCoverage
    path: Path
    upstream_object_sha256s: tuple[str, ...] = ()
    absence_proofs: tuple[str, ...] = ()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _schema_sha256(schema: pa.Schema) -> str:
    return hashlib.sha256(schema.remove_metadata().serialize().to_pybytes()).hexdigest()


def _tuple_sha256(values: tuple[str, ...]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class ParquetObjectStore:
    def __init__(self, paths: DataPaths, registry: DatasetRegistry) -> None:
        self.paths = paths
        self.registry = registry

    def publish(
        self,
        request: DatasetRequest,
        table: pa.Table,
        *,
        partition_key: str,
        created_at: datetime,
        upstream_object_sha256s: tuple[str, ...] = (),
        absence_proofs: tuple[str, ...] = (),
    ) -> PublishedObject:
        """Validate, fsync, and atomically link an immutable canonical object."""

        _require_utc(created_at)
        spec = self.registry[request.dataset]
        self._validate_partition(spec, partition_key)
        self._validate_table(spec, request, table, absence_proofs)
        self._validate_absence_proofs(absence_proofs)
        if upstream_object_sha256s != tuple(sorted(set(upstream_object_sha256s))) or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in upstream_object_sha256s
        ):
            raise ObjectIntegrityError("upstream object hashes must be canonical")
        self._validate_upstream_objects(upstream_object_sha256s, request, table)
        metadata_payload = {
            "version": "object/v1",
            "request": request.model_dump(mode="json"),
            "partition_key": partition_key,
            "created_at": created_at.isoformat(),
            "upstream_object_sha256s": upstream_object_sha256s,
            "absence_proofs": absence_proofs,
        }
        metadata = dict(table.schema.metadata or {})
        metadata[_OBJECT_METADATA_KEY] = json.dumps(
            metadata_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        persisted = table.replace_schema_metadata(metadata)

        self.paths.temporary.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{request.dataset}-", suffix=".parquet.tmp", dir=self.paths.temporary
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            pq.write_table(
                persisted,
                temporary,
                compression="zstd",
                version="2.6",
                write_statistics=True,
            )
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            digest = _sha256_file(temporary)
            destination = (
                self.paths.canonical / request.dataset / partition_key / f"{digest}.parquet"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if _sha256_file(destination) != digest:
                    raise ObjectIntegrityError("existing object content hash mismatch")
            _fsync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)
            _fsync_directory(self.paths.temporary)
        return self._publication_from_path(destination)

    def publish_and_register(
        self,
        catalog: DuckDbCatalog,
        request: DatasetRequest,
        table: pa.Table,
        *,
        partition_key: str,
        created_at: datetime,
        upstream_object_sha256s: tuple[str, ...] = (),
        absence_proofs: tuple[str, ...] = (),
    ) -> PublishedObject:
        publication = self.publish(
            request,
            table,
            partition_key=partition_key,
            created_at=created_at,
            upstream_object_sha256s=upstream_object_sha256s,
            absence_proofs=absence_proofs,
        )
        catalog.register_publication(publication.object, publication.coverage)
        return publication

    def verify(self, obj: CatalogObject) -> CatalogObject:
        return self.verify_publication(obj).object

    def verify_publication(self, obj: CatalogObject) -> PublishedObject:
        path = (self.paths.data_root / obj.uri).resolve()
        if not path.is_relative_to(self.paths.canonical):
            raise ObjectIntegrityError("object URI escapes canonical root")
        if not path.is_file() or _sha256_file(path) != obj.sha256:
            raise ObjectIntegrityError("object content hash mismatch")
        publication = self._publication_from_path(path)
        if publication.object != obj:
            raise ObjectIntegrityError("object metadata mismatch")
        return publication

    def recover(self, catalog: DuckDbCatalog) -> tuple[PublishedObject, ...]:
        """Delete unpublished temp files and register every valid canonical orphan."""

        self.paths.temporary.mkdir(parents=True, exist_ok=True)
        for temporary in self.paths.temporary.glob("*.parquet.tmp"):
            temporary.unlink()
        _fsync_directory(self.paths.temporary)

        recovered: list[PublishedObject] = []
        for path in sorted(self.paths.canonical.rglob("*.parquet")):
            publication = self._publication_from_path(path)
            catalog.register_publication(publication.object, publication.coverage)
            recovered.append(publication)
        return tuple(recovered)

    @staticmethod
    def _validate_partition(spec: DatasetSpec, partition_key: str) -> None:
        parts = partition_key.split("/")
        if len(parts) != len(spec.partition_fields):
            raise ObjectIntegrityError("partition does not match dataset registry")
        names: list[str] = []
        for part in parts:
            if "=" not in part:
                raise ObjectIntegrityError("partition component must use field=value")
            name, value = part.split("=", 1)
            if re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None:
                raise ObjectIntegrityError("unsafe partition value")
            names.append(name)
        if tuple(names) != spec.partition_fields:
            raise ObjectIntegrityError("partition fields do not match dataset registry")

    def _validate_upstream_objects(
        self,
        sha256s: tuple[str, ...],
        request: DatasetRequest,
        canonical_table: pa.Table,
    ) -> None:
        if not sha256s:
            if self.paths.canonical_source_policy == "tushare_only":
                raise ObjectIntegrityError(
                    "tushare-only canonical publication requires raw/staging provenance"
                )
            return
        wanted = set(sha256s)
        found: dict[str, tuple[str, Path]] = {}
        for layer, root in (("raw", self.paths.raw), ("staging", self.paths.staging)):
            for path in root.rglob("*.parquet"):
                if path.stem not in wanted:
                    continue
                if _sha256_file(path) != path.stem:
                    raise ObjectIntegrityError("upstream object content hash mismatch")
                found[path.stem] = (layer, path)
        if set(found) != wanted:
            raise ObjectIntegrityError("upstream object hash cannot be resolved")
        staging = [path for layer, path in found.values() if layer == "staging"]
        if not staging:
            raise ObjectIntegrityError("canonical provenance requires a staging object")
        raw_endpoints: set[str] = set()
        raw_rows_by_sha256: dict[str, list[dict[str, object]]] = {}
        raw_endpoint_by_sha256: dict[str, str] = {}
        raw_ingested_at_by_sha256: dict[str, datetime] = {}
        raw_request_by_sha256: dict[str, dict[str, object]] = {}
        raw_pages_by_request: dict[
            str, list[tuple[str, int, int, int, datetime, str]]
        ] = {}
        for sha256, (layer, path) in found.items():
            if layer != "raw":
                continue
            metadata = pq.read_schema(path).metadata or {}
            raw = metadata.get(b"trademaster.provider_request/v1")
            if raw is None:
                raise ObjectIntegrityError("raw provider metadata is missing")
            try:
                raw_payload: Any = json.loads(raw)
                endpoint = raw_payload["endpoint"]
                request_payload = {
                    "endpoint": endpoint,
                    "fields": raw_payload["fields"],
                    "params": raw_payload["params"],
                }
                expected = hashlib.sha256(
                    json.dumps(
                        request_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            except (KeyError, TypeError, ValueError) as error:
                raise ObjectIntegrityError("raw provider metadata is invalid") from error
            if raw_payload.get("provider_request_sha256") != expected:
                raise ObjectIntegrityError("raw provider request digest mismatch")
            if not isinstance(endpoint, str) or path.parent.parent.name != endpoint:
                raise ObjectIntegrityError("raw provider endpoint path mismatch")
            allowed_endpoints = _TUSHARE_ENDPOINTS_BY_DATASET[request.dataset]
            if endpoint not in allowed_endpoints:
                raise ObjectIntegrityError("raw provider endpoint is invalid for dataset")
            try:
                ingested_at = datetime.fromisoformat(str(raw_payload["ingested_at"]))
                _require_utc(ingested_at)
                page_index = int(raw_payload["page_index"])
                page_cursor = int(raw_payload["page_cursor"])
                returned_rows = int(raw_payload["returned_rows"])
                row_source_revisions = raw_payload["row_source_revisions"]
            except (KeyError, TypeError, ValueError) as error:
                raise ObjectIntegrityError("raw provider page proof is invalid") from error
            raw_table = pq.read_table(path)
            raw_rows = (
                []
                if raw_table.column_names == ["_empty"]
                else raw_table.to_pylist()
            )
            raw_rows_by_sha256[sha256] = cast(
                list[dict[str, object]], raw_rows
            )
            raw_endpoint_by_sha256[sha256] = endpoint
            raw_ingested_at_by_sha256[sha256] = ingested_at
            raw_request_by_sha256[sha256] = cast(
                dict[str, object], request_payload
            )
            actual_revisions = sorted(
                hashlib.sha256(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode()
                ).hexdigest()
                for row in raw_rows
            )
            page_fingerprint = hashlib.sha256(
                json.dumps(
                    raw_rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            if (
                page_index < 0
                or page_cursor < 0
                or returned_rows != len(raw_rows)
                or row_source_revisions != actual_revisions
            ):
                raise ObjectIntegrityError("raw provider page content proof is invalid")
            raw_pages_by_request.setdefault(expected, []).append(
                (
                    endpoint,
                    page_index,
                    page_cursor,
                    returned_rows,
                    ingested_at,
                    page_fingerprint,
                )
            )
            raw_endpoints.add(endpoint)
        for request_sha256, pages in raw_pages_by_request.items():
            ordered = sorted(pages, key=lambda item: item[1])
            endpoint = ordered[0][0]
            page_limit = _TUSHARE_PAGE_LIMIT_BY_ENDPOINT[endpoint]
            if (
                any(item[0] != endpoint for item in ordered)
                or [item[1] for item in ordered] != list(range(len(ordered)))
                or any(
                    page_cursor != page_index * page_limit
                    for _, page_index, page_cursor, *_ in ordered
                )
                or any(
                    item[3] != page_limit for item in ordered[:-1]
                )
            ):
                raise ObjectIntegrityError("raw provider page chain is invalid")
            if ordered[-1][3] >= page_limit:
                raise ObjectIntegrityError("raw provider terminal page is missing")
            nonempty_fingerprints = [item[5] for item in ordered if item[3] > 0]
            if len(nonempty_fingerprints) != len(set(nonempty_fingerprints)):
                raise ObjectIntegrityError("raw chain repeated a provider page")
            if endpoint in _MUTABLE_TUSHARE_ENDPOINTS:
                ingested_at_values = {item[4] for item in ordered}
                if len(ingested_at_values) != 1:
                    raise ObjectIntegrityError(
                        "mutable raw observation cannot splice ingestion times"
                    )
                supplied_pages = {item[1]: item[5] for item in ordered}
                latest_time, latest_pages = self._latest_mutable_raw_observation(
                    endpoint=endpoint,
                    request_sha256=request_sha256,
                )
                if ingested_at_values != {latest_time} or supplied_pages != latest_pages:
                    raise ObjectIntegrityError(
                        "mutable raw observation is not the latest complete revision"
                    )
        allowed_endpoints = _TUSHARE_ENDPOINTS_BY_DATASET[request.dataset]
        if not raw_endpoints or not raw_endpoints <= allowed_endpoints:
            raise ObjectIntegrityError("raw provider endpoint set is invalid")
        if request.dataset == "daily_limits_status" and raw_endpoints != allowed_endpoints:
            raise ObjectIntegrityError("status provenance requires every provider endpoint")

        staging_tables: list[pa.Table] = []
        staging_coverage_keys: set[str] = set()
        referenced_raw_sha256s: set[str] = set()
        for path in staging:
            metadata = pq.read_schema(path).metadata or {}
            raw = metadata.get(b"trademaster.staging/v1")
            if raw is None:
                raise ObjectIntegrityError("staging provenance metadata is missing")
            try:
                staging_payload: Any = json.loads(raw)
                raw_sha256s = tuple(staging_payload["raw_object_sha256s"])
                normalized_at = datetime.fromisoformat(
                    str(staging_payload["normalized_at"])
                )
                _require_utc(normalized_at)
            except (KeyError, TypeError, ValueError) as error:
                raise ObjectIntegrityError("staging provenance metadata is invalid") from error
            if raw_sha256s != tuple(sorted(set(raw_sha256s))) or any(
                sha256 not in found or found[sha256][0] != "raw"
                for sha256 in raw_sha256s
            ):
                raise ObjectIntegrityError("staging raw provenance is unresolved")
            coverage_key = staging_payload.get("coverage_key")
            if staging_payload.get("dataset") != request.dataset or coverage_key not in set(
                request.coverage_keys
            ):
                raise ObjectIntegrityError("staging request scope is invalid")
            partial = request.model_copy(update={"coverage_keys": (coverage_key,)})
            expected_request_sha256 = hashlib.sha256(
                json.dumps(
                    partial.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if staging_payload.get("request_sha256") != expected_request_sha256:
                raise ObjectIntegrityError("staging request digest mismatch")
            staging_table = pq.read_table(path)
            if staging_table.schema.remove_metadata() != self.registry[
                request.dataset
            ].arrow_schema:
                raise ObjectIntegrityError("staging canonical schema mismatch")
            actual_source_revisions = sorted(
                cast(list[str], staging_table["source_revision"].to_pylist())
            )
            if staging_payload.get("source_revision_sha256s") != actual_source_revisions:
                raise ObjectIntegrityError("staging source revisions mismatch")
            raw_by_endpoint: dict[str, list[dict[str, object]]] = {}
            raw_requests_by_endpoint: dict[str, list[dict[str, object]]] = {}
            for raw_sha256 in raw_sha256s:
                raw_endpoint = raw_endpoint_by_sha256[raw_sha256]
                raw_by_endpoint.setdefault(raw_endpoint, []).extend(
                    raw_rows_by_sha256[raw_sha256]
                )
                raw_requests_by_endpoint.setdefault(raw_endpoint, []).append(
                    raw_request_by_sha256[raw_sha256]
                )
            if (
                request.dataset == "daily_limits_status"
                and set(raw_by_endpoint) != allowed_endpoints
            ):
                raise ObjectIntegrityError(
                    "each status staging requires every provider endpoint"
                )
            if normalized_at < max(
                raw_ingested_at_by_sha256[raw_sha256]
                for raw_sha256 in raw_sha256s
            ):
                raise ObjectIntegrityError(
                    "staging normalized_at precedes referenced raw ingestion time"
                )
            try:
                validate_tushare_raw_request_scope(
                    request=partial,
                    raw_requests_by_endpoint=raw_requests_by_endpoint,
                    etf_instruments=frozenset(self.paths.etf_instruments),
                )
                validate_tushare_staging_replay(
                    request=partial,
                    staging_table=staging_table,
                    raw_by_endpoint=raw_by_endpoint,
                    normalized_at=normalized_at,
                    schema=self.registry[request.dataset].arrow_schema,
                    primary_key=self.registry[request.dataset].primary_key,
                    etf_instruments=frozenset(self.paths.etf_instruments),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ObjectIntegrityError(
                    f"raw normalization replay failed: {error}"
                ) from error
            staging_tables.append(staging_table.replace_schema_metadata(None))
            staging_coverage_keys.add(cast(str, coverage_key))
            referenced_raw_sha256s.update(raw_sha256s)

        supplied_raw_sha256s = {
            sha256 for sha256, (layer, _) in found.items() if layer == "raw"
        }
        if referenced_raw_sha256s != supplied_raw_sha256s:
            raise ObjectIntegrityError("canonical raw provenance set is not exact")
        if staging_coverage_keys != set(request.coverage_keys):
            raise ObjectIntegrityError("staging coverage does not match canonical request")
        staging_union = pa.concat_tables(staging_tables).combine_chunks()
        canonical = canonical_table.replace_schema_metadata(None).combine_chunks()
        sort_keys: list[tuple[str, Literal["ascending", "descending"]]] = [
            (name, "ascending")
            for name in self.registry[request.dataset].primary_key
            + ("source_revision",)
        ]
        if staging_union.num_rows:
            staging_union = staging_union.sort_by(sort_keys)
            canonical = canonical.sort_by(sort_keys)
        if not staging_union.equals(canonical, check_metadata=False):
            raise ObjectIntegrityError("staging content disagrees with canonical object")

    def _latest_mutable_raw_observation(
        self,
        *,
        endpoint: str,
        request_sha256: str,
    ) -> tuple[datetime, dict[int, str]]:
        observations: list[tuple[datetime, int, str]] = []
        for path in sorted((self.paths.raw / endpoint).glob("ingest_date=*/*.parquet")):
            metadata = pq.read_schema(path).metadata or {}
            raw = metadata.get(b"trademaster.provider_request/v1")
            if raw is None:
                continue
            try:
                payload: Any = json.loads(raw)
            except (TypeError, ValueError) as error:
                raise ObjectIntegrityError("raw provider metadata is invalid") from error
            if payload.get("provider_request_sha256") != request_sha256:
                continue
            if _sha256_file(path) != path.stem:
                raise ObjectIntegrityError("mutable raw observation hash is invalid")
            try:
                ingested_at = datetime.fromisoformat(str(payload["ingested_at"]))
                _require_utc(ingested_at)
                page_index = int(payload["page_index"])
                returned_rows = int(payload["returned_rows"])
            except (KeyError, TypeError, ValueError) as error:
                raise ObjectIntegrityError(
                    "mutable raw observation metadata is invalid"
                ) from error
            table = pq.read_table(path)
            rows = [] if table.column_names == ["_empty"] else table.to_pylist()
            if page_index < 0 or returned_rows != len(rows):
                raise ObjectIntegrityError("mutable raw observation page is invalid")
            fingerprint = hashlib.sha256(
                json.dumps(
                    rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            observations.append((ingested_at, page_index, fingerprint))
        if not observations:
            raise ObjectIntegrityError("mutable raw observation is missing")
        latest_time = max(item[0] for item in observations)
        latest: dict[int, set[str]] = {}
        for ingested_at, page_index, fingerprint in observations:
            if ingested_at == latest_time:
                latest.setdefault(page_index, set()).add(fingerprint)
        if any(len(fingerprints) != 1 for fingerprints in latest.values()):
            raise ObjectIntegrityError("mutable raw observation is ambiguous")
        return latest_time, {
            page_index: next(iter(fingerprints))
            for page_index, fingerprints in latest.items()
        }

    def _validate_absence_proofs(self, proofs: tuple[str, ...]) -> None:
        if proofs != tuple(sorted(set(proofs))):
            raise ObjectIntegrityError("absence proofs must be unique and sorted")
        for proof in proofs:
            try:
                instrument_id, coverage_key, reason, object_sha256 = proof.split("|")
            except ValueError as error:
                raise ObjectIntegrityError("absence proof is malformed") from error
            if not instrument_id or reason != "suspended" or re.fullmatch(
                r"[0-9a-f]{64}", object_sha256
            ) is None:
                raise ObjectIntegrityError("absence proof is invalid")
            matches = tuple(self.paths.canonical.rglob(f"{object_sha256}.parquet"))
            if len(matches) != 1 or _sha256_file(matches[0]) != object_sha256:
                raise ObjectIntegrityError("absence evidence object is unresolved")
            evidence = self._publication_from_path(matches[0])
            if evidence.object.dataset != "daily_limits_status":
                raise ObjectIntegrityError("absence evidence has the wrong dataset type")
            table = pq.read_table(
                matches[0],
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
                latest = latest_status_rows(table)
            except ValueError as error:
                raise ObjectIntegrityError(str(error)) from error
            row = latest.get((instrument_id, coverage_key))
            if row is None or row["suspended"] is not True:
                raise ObjectIntegrityError("absence evidence does not prove suspension")

    @staticmethod
    def _validate_table(
        spec: DatasetSpec,
        request: DatasetRequest,
        table: pa.Table,
        absence_proofs: tuple[str, ...] = (),
    ) -> None:
        fields = set(table.column_names)
        if fields != set(spec.required_fields):
            raise ObjectIntegrityError("canonical fields differ from registry schema")
        if not set(request.fields) <= fields:
            raise ObjectIntegrityError("table does not contain requested fields")
        if any(table[name].null_count for name in spec.required_fields):
            raise ObjectIntegrityError("required canonical fields cannot contain nulls")
        for name, canonical_type in spec.field_types:
            if table.schema.field(name).type != spec.arrow_schema.field(name).type:
                raise ObjectIntegrityError(
                    f"canonical field type differs from registry: {name}"
                )
        if table.schema.remove_metadata() != spec.arrow_schema:
            raise ObjectIntegrityError("canonical schema order or nullability differs from registry")
        if table.num_rows == 0 and not absence_proofs:
            raise ObjectIntegrityError("canonical object cannot be empty")
        if (
            spec.coverage_key_mode is CoverageKeyMode.SESSION
            and "instrument_id" in spec.required_fields
            and not request.instruments
        ):
            raise ObjectIntegrityError("instrument universe must be resolved before publication")

        unique_fields = spec.primary_key + ("source_revision",)
        identities = [
            tuple(row[name] for name in unique_fields)
            for row in table.select(unique_fields).to_pylist()
        ]
        if len(identities) != len(set(identities)):
            raise ObjectIntegrityError("duplicate canonical primary key revision")
        if spec.revision_order_fields:
            revision_identities = [
                tuple(row[name] for name in spec.primary_key)
                + (row["known_at"],)
                + tuple(row[name] for name in spec.revision_order_fields)
                for row in table.select(
                    spec.primary_key + ("known_at",) + spec.revision_order_fields
                ).to_pylist()
            ]
            if len(revision_identities) != len(set(revision_identities)):
                raise ObjectIntegrityError("ambiguous business revision order")
        event_times = cast(list[datetime], table["event_time"].to_pylist())
        if any(value < request.start or value > request.end for value in event_times):
            raise ObjectIntegrityError("event_time falls outside normalized request")
        known_times = cast(list[datetime], table["known_at"].to_pylist())
        if any(
            known_at < event_time
            for event_time, known_at in zip(event_times, known_times)
        ):
            raise ObjectIntegrityError("known_at cannot precede event_time")

        actual_instruments: tuple[str, ...] = ()
        if "instrument_id" in fields:
            actual_instruments = tuple(
                sorted(set(cast(list[str], table["instrument_id"].to_pylist())))
            )
        absence_pairs = {
            (parts[0], parts[1])
            for proof in absence_proofs
            if len(parts := proof.split("|")) == 4
        }
        logical_instruments = tuple(
            sorted(set(actual_instruments) | {item[0] for item in absence_pairs})
        )
        if request.instruments and logical_instruments != request.instruments:
            raise ObjectIntegrityError("table instrument universe differs from request")
        coverage_rows = table.select(spec.coverage_key_fields).to_pylist()
        actual_coverage_keys = tuple(
            sorted(
                {
                    ":".join(
                        value.isoformat() if isinstance(value, (date, datetime)) else str(value)
                        for value in (row[field] for field in spec.coverage_key_fields)
                    )
                    for row in coverage_rows
                }
            )
        )
        logical_coverage_keys = tuple(
            sorted(set(actual_coverage_keys) | {item[1] for item in absence_pairs})
        )
        if logical_coverage_keys != request.coverage_keys:
            raise ObjectIntegrityError("table coverage keys differ from request")
        if spec.coverage_shape is CoverageShape.INSTRUMENT_SESSION_MATRIX:
            venues = {key.split(":", 1)[0] for key in request.coverage_keys}
            if len(venues) != 1:
                raise ObjectIntegrityError(
                    "instrument-session matrix requests must be split by venue"
                )
            actual_pairs = {
                (
                    cast(str, row["instrument_id"]),
                    ":".join(
                        value.isoformat()
                        if isinstance(value, (date, datetime))
                        else str(value)
                        for value in (row[field] for field in spec.coverage_key_fields)
                    ),
                )
                for row in table.select(
                    ("instrument_id",) + spec.coverage_key_fields
                ).to_pylist()
            }
            expected_pairs = {
                (instrument_id, coverage_key)
                for instrument_id in request.instruments
                for coverage_key in request.coverage_keys
            }
            if actual_pairs | absence_pairs != expected_pairs:
                raise ObjectIntegrityError("incomplete instrument-session matrix")

    def _publication_from_path(self, path: Path) -> PublishedObject:
        path = path.resolve()
        if not path.is_relative_to(self.paths.canonical):
            raise ObjectIntegrityError("canonical object escapes configured root")
        digest = _sha256_file(path)
        if path.name != f"{digest}.parquet":
            raise ObjectIntegrityError("canonical filename does not match content hash")
        try:
            table = pq.read_table(path)
        except Exception as error:
            raise ObjectIntegrityError("canonical object is not readable Parquet") from error
        raw_metadata = (table.schema.metadata or {}).get(_OBJECT_METADATA_KEY)
        if raw_metadata is None:
            raise ObjectIntegrityError("canonical object metadata is missing")
        try:
            payload: Any = json.loads(raw_metadata)
            if not isinstance(payload, dict) or payload.get("version") != "object/v1":
                raise ValueError("wrong object metadata version")
            request = DatasetRequest.model_validate_json(
                json.dumps(payload["request"], ensure_ascii=False, separators=(",", ":"))
            )
            partition_key = str(payload["partition_key"])
            created_at = datetime.fromisoformat(str(payload["created_at"]))
            upstream_object_sha256s = tuple(payload.get("upstream_object_sha256s", ()))
            absence_proofs = tuple(payload.get("absence_proofs", ()))
            if upstream_object_sha256s != tuple(
                sorted(set(upstream_object_sha256s))
            ) or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in upstream_object_sha256s
            ):
                raise ValueError("invalid upstream object hashes")
            _require_utc(created_at)
        except Exception as error:
            raise ObjectIntegrityError("canonical object metadata is invalid") from error
        spec = self.registry[request.dataset]
        self._validate_upstream_objects(upstream_object_sha256s, request, table)
        self._validate_absence_proofs(absence_proofs)
        self._validate_partition(spec, partition_key)
        self._validate_table(spec, request, table, absence_proofs)

        event_times = cast(list[datetime], table["event_time"].to_pylist())
        known_times = cast(list[datetime], table["known_at"].to_pylist())
        if not known_times:
            for proof in absence_proofs:
                evidence_sha256 = proof.rsplit("|", 1)[1]
                evidence_path = next(
                    self.paths.canonical.rglob(f"{evidence_sha256}.parquet")
                )
                known_times.extend(
                    cast(list[datetime], pq.read_table(evidence_path)["known_at"].to_pylist())
                )
        instruments: tuple[str, ...] = ()
        if request.instruments:
            instruments = request.instruments
        elif "instrument_id" in table.column_names:
            instruments = tuple(sorted(set(cast(list[str], table["instrument_id"].to_pylist()))))
        relative = path.relative_to(self.paths.data_root).as_posix()
        obj = CatalogObject(
            dataset=request.dataset,
            partition_key=partition_key,
            uri=relative,
            sha256=digest,
            schema_sha256=_schema_sha256(table.schema),
            schema_version=spec.schema_version,
            row_count=table.num_rows,
            event_time_start=min(event_times) if event_times else request.start,
            event_time_end=max(event_times) if event_times else request.end,
            known_at_max=max(known_times),
            fields=tuple(sorted(table.column_names)),
            coverage_keys=request.coverage_keys,
            instrument_set_sha256=_tuple_sha256(instruments),
            instrument_count=len(instruments),
            created_at=created_at,
        )
        coverage = CatalogCoverage.from_request_object(request, obj, verified_at=created_at)
        return PublishedObject(
            object=obj,
            coverage=coverage,
            path=path,
            upstream_object_sha256s=upstream_object_sha256s,
            absence_proofs=absence_proofs,
        )
