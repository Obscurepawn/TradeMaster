"""Read-only IndexOne observations with immutable Parquet evidence and offline reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, Self

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from trademaster.contracts import _require_utc

from .indexone_client import (
    INDEXONE_OPERATIONS,
    IndexOneClient,
    IndexOneDataError,
    IndexOneError,
)
from .research import ResearchObjectEvidence, ResearchQueryResult

_METADATA = b"trademaster.indexone/v1"


class IndexOneQueryClient(Protocol):
    def query(
        self, operation: str, *, params: Mapping[str, object], fields: tuple[str, ...]
    ) -> pa.Table: ...


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _schema_hash(table: pa.Table) -> str:
    return hashlib.sha256(table.schema.remove_metadata().serialize().to_pybytes()).hexdigest()


class IndexOneDataSource:
    """Cache exact provider responses; fetch time does not imply historical availability.

    ``page_limit`` bounds the returned row count. IndexOne's execute endpoint has
    no pagination, so oversized responses fail instead of publishing a prefix.
    """

    def __init__(
        self,
        *,
        root: Path,
        client: IndexOneQueryClient | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        offline: bool = False,
        refresh: bool = False,
    ) -> None:
        if offline and refresh:
            raise ValueError("offline and refresh cannot be combined")
        self.root = root.expanduser().resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError("IndexOne data root cannot be the filesystem root")
        self.root.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._clock = clock
        self._offline = offline
        self._refresh = refresh
        self._catalog = duckdb.connect(str(self.root / "indexone.duckdb"))
        self._catalog.execute(
            """
            CREATE TABLE IF NOT EXISTS indexone_requests (
                request_sha256 VARCHAR PRIMARY KEY,
                request_json VARCHAR NOT NULL,
                relative_path VARCHAR NOT NULL,
                content_sha256 VARCHAR NOT NULL,
                schema_sha256 VARCHAR NOT NULL,
                row_count BIGINT NOT NULL,
                fetched_at VARCHAR NOT NULL
            )
            """
        )

    def close(self) -> None:
        self._catalog.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def query(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int = 100_000,
    ) -> pa.Table:
        return self.query_with_evidence(
            endpoint, params=params, fields=fields, page_limit=page_limit
        ).table

    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int = 100_000,
    ) -> ResearchQueryResult:
        operation = INDEXONE_OPERATIONS.get(endpoint, endpoint)
        if operation not in INDEXONE_OPERATIONS.values():
            raise ValueError("unsupported IndexOne data operation")
        if (
            not fields
            or any(not isinstance(field, str) or not field for field in fields)
            or len(set(fields)) != len(fields)
            or type(page_limit) is not int
            or page_limit < 1
        ):
            raise ValueError("IndexOne fields must be unique and row bound must be positive")
        request = {
            "operation": operation,
            "params": dict(params),
            "fields": fields,
            "page_limit": page_limit,
        }
        try:
            request_json = _json(request)
        except (TypeError, ValueError):
            raise ValueError("IndexOne parameters must contain finite JSON values") from None
        request_sha = hashlib.sha256(request_json.encode()).hexdigest()
        cached = self._catalog.execute(
            "SELECT request_json, relative_path, content_sha256, schema_sha256, "
            "row_count, fetched_at FROM indexone_requests WHERE request_sha256 = ?",
            [request_sha],
        ).fetchone()
        if cached is not None and not self._refresh:
            return self._read_result(operation, request_sha, request_json, fields, cached)
        if self._offline:
            raise IndexOneDataError("IndexOne request is absent from the offline cache")
        if self._client is None:
            self._client = IndexOneClient.from_environment()
        try:
            table = self._client.query(operation, params=params, fields=fields)
        except IndexOneError:
            raise
        except Exception:  # noqa: BLE001 - external clients may echo credentials in any error.
            raise IndexOneDataError("IndexOne query failed before cache publication") from None
        if not isinstance(table, pa.Table) or table.column_names != list(fields):
            raise IndexOneDataError("IndexOne response does not match requested columns")
        if table.num_rows > page_limit:
            raise IndexOneDataError("IndexOne response exceeds row bound; split the request")
        fetched_at = _require_utc(self._clock()).isoformat()
        metadata = _json({"request": json.loads(request_json), "fetched_at": fetched_at})
        table = table.replace_schema_metadata({_METADATA: metadata.encode()})
        schema_sha = _schema_hash(table)
        directory = self.root / "raw" / "indexone" / operation
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.resolve().is_relative_to(self.root):
            raise IndexOneDataError("IndexOne cache directory escapes its data root")
        descriptor, temporary_name = tempfile.mkstemp(dir=directory, suffix=".tmp")
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            pq.write_table(table, temporary, compression="zstd")
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            digest = _hash_file(temporary)
            path = directory / f"{digest}.parquet"
            try:
                os.link(temporary, path)
            except FileExistsError:
                if _hash_file(path) != digest:
                    raise IndexOneDataError("IndexOne content-address collision") from None
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        relative_path = str(path.relative_to(self.root))
        self._catalog.execute(
            """
            INSERT INTO indexone_requests VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (request_sha256) DO UPDATE SET
                request_json = excluded.request_json,
                relative_path = excluded.relative_path,
                content_sha256 = excluded.content_sha256,
                schema_sha256 = excluded.schema_sha256,
                row_count = excluded.row_count,
                fetched_at = excluded.fetched_at
            """,
            [
                request_sha,
                request_json,
                relative_path,
                digest,
                schema_sha,
                table.num_rows,
                fetched_at,
            ],
        )
        return self._read_result(
            operation,
            request_sha,
            request_json,
            fields,
            (request_json, relative_path, digest, schema_sha, table.num_rows, fetched_at),
        )

    def _read_result(
        self,
        operation: str,
        request_sha: str,
        request_json: str,
        fields: tuple[str, ...],
        row: Sequence[object],
    ) -> ResearchQueryResult:
        try:
            saved_request, relative, digest, schema_sha, count, fetched = row
            expected_path = Path("raw") / "indexone" / operation / f"{digest}.parquet"
            if (
                saved_request != request_json
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or relative != str(expected_path)
                or type(count) is not int
                or not isinstance(fetched, str)
            ):
                raise ValueError("invalid catalog record")
            path = self.root / expected_path
            if not path.resolve().is_relative_to(self.root) or _hash_file(path) != digest:
                raise ValueError("invalid cached bytes")
            table = pq.ParquetFile(path).read()
            metadata = json.loads((table.schema.metadata or {})[_METADATA])
            expected_metadata = {"request": json.loads(request_json), "fetched_at": fetched}
            if (
                metadata != expected_metadata
                or table.num_rows != count
                or table.column_names != list(fields)
                or _schema_hash(table) != schema_sha
            ):
                raise ValueError("invalid cached metadata")
            fetched_at = _require_utc(datetime.fromisoformat(fetched))
        except (OSError, ValueError, TypeError, KeyError, pa.ArrowException):
            raise IndexOneDataError(
                "IndexOne cached object failed integrity verification"
            ) from None
        return ResearchQueryResult(
            table=table,
            evidence=ResearchObjectEvidence(
                endpoint=operation,
                request_sha256=request_sha,
                content_sha256=digest,
                path=path,
                row_count=count,
                fetched_at=fetched_at,
            ),
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download or reopen cached IndexOne data.")
    parser.add_argument("--data-root", type=Path, default=Path("data/indexone"))
    parser.add_argument(
        "--request", type=Path, required=True, help="JSON dataset, params and fields"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text())
        if (
            not isinstance(request, dict)
            or set(request) - {"dataset", "params", "fields", "page_limit"}
            or not isinstance(request.get("dataset"), str)
            or not isinstance(request.get("params"), dict)
            or not isinstance(request.get("fields"), list)
        ):
            raise ValueError("invalid request")
    except (OSError, ValueError):
        print(
            "tm-indexone: invalid request file; expected dataset, params and fields",
            file=sys.stderr,
        )
        return 2
    try:
        with IndexOneDataSource(
            root=args.data_root, offline=args.offline, refresh=args.refresh
        ) as source:
            result = source.query_with_evidence(
                request["dataset"],
                params=request["params"],
                fields=tuple(request["fields"]),
                page_limit=request.get("page_limit", 100_000),
            )
    except (IndexOneError, ValueError, OSError) as error:
        print(f"tm-indexone: {error}", file=sys.stderr)
        return 1
    print(
        _json(
            {
                "rows": result.evidence.row_count,
                "path": str(result.evidence.path),
                "request_sha256": result.evidence.request_sha256,
                "content_sha256": result.evidence.content_sha256,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
