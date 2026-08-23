"""Cache-first Tushare access for the fundamental strategy."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from trademaster.data.research import (
    ResearchObjectEvidence,
    ResearchQueryResult,
    TushareQueryClient,
)


class DataCacheError(RuntimeError):
    """Cached evidence or a provider response violated the strategy data contract."""


_STABLE_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _require_stable_identifier(value: str, *, field: str) -> str:
    if _STABLE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be a stable lowercase identifier")
    return value


@dataclass(frozen=True, slots=True)
class PublishedStrategyObject:
    dataset: str
    relative_path: Path
    path: Path
    sha256: str
    schema_sha256: str
    row_count: int


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_arrow(value: Any) -> pa.Table:
    if isinstance(value, pa.Table):
        return value
    try:
        return pa.Table.from_pandas(value, preserve_index=False)
    except (AttributeError, TypeError, ValueError) as error:
        raise DataCacheError("Tushare response is not tabular") from error


class StrategyDataCache:
    """DuckDB catalog over immutable content-addressed Parquet provider responses."""

    def __init__(
        self,
        *,
        root: Path,
        client: TushareQueryClient,
        clock: Callable[[], datetime],
    ) -> None:
        self.root = root.resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError("strategy data root cannot be the filesystem root")
        self.raw_root = self.root / "raw"
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.clock = clock
        self._catalog = duckdb.connect(str(self.root / "catalog.duckdb"))
        self._catalog.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_requests (
                request_sha256 VARCHAR PRIMARY KEY,
                endpoint VARCHAR NOT NULL,
                params_json VARCHAR NOT NULL,
                fields_json VARCHAR NOT NULL,
                page_limit BIGINT NOT NULL,
                relative_path VARCHAR NOT NULL,
                content_sha256 VARCHAR NOT NULL,
                row_count BIGINT NOT NULL,
                fetched_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self._catalog.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_canonical_objects (
                sha256 VARCHAR PRIMARY KEY,
                dataset VARCHAR NOT NULL,
                relative_path VARCHAR NOT NULL,
                schema_sha256 VARCHAR NOT NULL,
                row_count BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def close(self) -> None:
        self._catalog.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def cached_objects(self) -> tuple[Path, ...]:
        rows = self._catalog.execute(
            "SELECT relative_path FROM strategy_requests ORDER BY request_sha256"
        ).fetchall()
        return tuple(self.root / str(row[0]) for row in rows)

    def query(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> pa.Table:
        return self.query_with_evidence(
            endpoint,
            params=params,
            fields=fields,
            page_limit=page_limit,
        ).table

    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult:
        _require_stable_identifier(endpoint, field="endpoint")
        if not fields or fields != tuple(dict.fromkeys(fields)) or page_limit < 1:
            raise ValueError("strategy data request is invalid")
        params_json = _canonical_json(dict(params))
        fields_json = _canonical_json(fields)
        request_payload = {
            "endpoint": endpoint,
            "params": json.loads(params_json),
            "fields": fields,
            "page_limit": page_limit,
        }
        request_sha = hashlib.sha256(_canonical_json(request_payload).encode()).hexdigest()
        cached = self._catalog.execute(
            """
            SELECT relative_path, content_sha256, row_count, epoch_us(fetched_at)
            FROM strategy_requests WHERE request_sha256 = ?
            """,
            [request_sha],
        ).fetchone()
        if cached is not None:
            relative_path = Path(str(cached[0]))
            content_sha = str(cached[1])
            row_count = int(cached[2])
            return ResearchQueryResult(
                table=self._load_cached(relative_path, content_sha, row_count),
                evidence=ResearchObjectEvidence(
                    endpoint=endpoint,
                    request_sha256=request_sha,
                    content_sha256=content_sha,
                    path=self.root / relative_path,
                    row_count=row_count,
                    fetched_at=(
                        datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=int(cached[3]))
                    ),
                ),
            )

        fetched_at = self.clock()
        offset = 0
        pages: list[pa.Table] = []
        fingerprints: set[str] = set()
        while True:
            page = _as_arrow(
                self.client.query(
                    endpoint,
                    fields=",".join(fields),
                    limit=page_limit,
                    offset=offset,
                    **dict(params),
                )
            )
            if page.num_rows > page_limit or not set(fields) <= set(page.column_names):
                raise DataCacheError("Tushare page violates fields or page limit")
            page = page.select(fields)
            fingerprint = hashlib.sha256(_canonical_json(page.to_pylist()).encode()).hexdigest()
            if page.num_rows > 0 and fingerprint in fingerprints:
                raise DataCacheError("Tushare pagination repeated a nonempty page")
            fingerprints.add(fingerprint)
            pages.append(page)
            if page.num_rows < page_limit:
                break
            offset += page_limit
        table = pa.concat_tables(pages, promote_options="default")
        metadata = dict(table.schema.metadata or {})
        metadata.update(
            {
                b"trademaster.strategy.endpoint": endpoint.encode(),
                b"trademaster.strategy.params": params_json.encode(),
                b"trademaster.strategy.fields": fields_json.encode(),
                b"trademaster.strategy.request_sha256": request_sha.encode(),
                b"trademaster.strategy.fetched_at": fetched_at.isoformat().encode(),
            }
        )
        table = table.replace_schema_metadata(metadata)
        endpoint_root = self.raw_root / endpoint
        endpoint_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=endpoint_root, prefix="object-", suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
        try:
            pq.write_table(table, temporary_path)
            content_sha = _file_sha256(temporary_path)
            relative_path = Path("raw") / endpoint / f"{content_sha}.parquet"
            final_path = self.root / relative_path
            if final_path.exists():
                if _file_sha256(final_path) != content_sha:
                    raise DataCacheError("content-addressed object hash collision")
                temporary_path.unlink()
            else:
                temporary_path.replace(final_path)
            self._catalog.execute("BEGIN TRANSACTION")
            try:
                self._catalog.execute(
                    """
                    INSERT INTO strategy_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        request_sha,
                        endpoint,
                        params_json,
                        fields_json,
                        page_limit,
                        str(relative_path),
                        content_sha,
                        table.num_rows,
                        fetched_at,
                    ],
                )
                self._catalog.execute("COMMIT")
            except Exception:
                self._catalog.execute("ROLLBACK")
                raise
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return ResearchQueryResult(
            table=self._load_cached(relative_path, content_sha, table.num_rows),
            evidence=ResearchObjectEvidence(
                endpoint=endpoint,
                request_sha256=request_sha,
                content_sha256=content_sha,
                path=self.root / relative_path,
                row_count=table.num_rows,
                fetched_at=fetched_at,
            ),
        )

    def publish_canonical(self, dataset: str, table: pa.Table) -> PublishedStrategyObject:
        _require_stable_identifier(dataset, field="dataset")
        if table.num_rows < 1:
            raise ValueError("canonical strategy object must be named and nonempty")
        canonical_root = self.root / "canonical" / dataset
        canonical_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=canonical_root, prefix="object-", suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
        try:
            pq.write_table(table, temporary_path)
            content_sha = _file_sha256(temporary_path)
            relative_path = Path("canonical") / dataset / f"{content_sha}.parquet"
            final_path = self.root / relative_path
            if final_path.exists():
                if _file_sha256(final_path) != content_sha:
                    raise DataCacheError("canonical object hash collision")
                temporary_path.unlink()
            else:
                temporary_path.replace(final_path)
            schema_sha = hashlib.sha256(
                table.schema.remove_metadata().to_string().encode()
            ).hexdigest()
            self._catalog.execute(
                """
                INSERT OR IGNORE INTO strategy_canonical_objects
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    content_sha,
                    dataset,
                    str(relative_path),
                    schema_sha,
                    table.num_rows,
                    self.clock(),
                ],
            )
            return PublishedStrategyObject(
                dataset=dataset,
                relative_path=relative_path,
                path=final_path,
                sha256=content_sha,
                schema_sha256=schema_sha,
                row_count=table.num_rows,
            )
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _load_cached(self, relative_path: Path, expected_sha: str, expected_rows: int) -> pa.Table:
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise DataCacheError("cached path escapes strategy data root") from error
        if not path.is_file() or _file_sha256(path) != expected_sha:
            raise DataCacheError("cached Parquet hash mismatch")
        try:
            table = (
                self._catalog.execute("SELECT * FROM read_parquet(?)", [str(path)])
                .arrow()
                .read_all()
            )
        except Exception as error:
            raise DataCacheError("cached Parquet is unreadable") from error
        if table.num_rows != expected_rows:
            raise DataCacheError("cached Parquet row count mismatch")
        return table


__all__ = [
    "DataCacheError",
    "PublishedStrategyObject",
    "StrategyDataCache",
    "TushareQueryClient",
]
