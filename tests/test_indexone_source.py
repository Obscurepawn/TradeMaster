from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
from trademaster.data.indexone import IndexOneDataSource
from trademaster.data.indexone_client import IndexOneClient, IndexOneDataError

FIELDS = ("id", "date", "open", "close")
PARAMS: dict[str, object] = {
    "filters": [{"field": "id", "operator": "eq", "value": "BBG000B9XRY4"}]
}
NOW = datetime(2026, 11, 6, 1, tzinfo=UTC)


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object], tuple[str, ...]]] = []
        self.table = pa.table(
            {"id": ["BBG000B9XRY4"], "date": ["2024-01-02"], "open": [100.0], "close": [102.0]}
        )

    def query(
        self, operation: str, *, params: Mapping[str, object], fields: tuple[str, ...]
    ) -> pa.Table:
        self.calls.append((operation, params, fields))
        return self.table


def test_repeated_query_and_offline_reopen_keep_the_same_evidence(tmp_path: Path) -> None:
    client = Client()
    with IndexOneDataSource(root=tmp_path, client=client, clock=lambda: NOW) as source:
        first = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
        again = source.query_with_evidence(
            "i1_core_eod", params=PARAMS, fields=FIELDS, page_limit=100
        )
    assert len(client.calls) == 1
    assert client.calls[0][0] == "i1_core_eod"
    assert first.evidence == again.evidence
    assert first.evidence.fetched_at == NOW
    assert (
        hashlib.sha256(first.evidence.path.read_bytes()).hexdigest()
        == first.evidence.content_sha256
    )
    with IndexOneDataSource(root=tmp_path, offline=True) as source:
        loaded = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    assert loaded.table.equals(first.table)
    assert loaded.evidence == first.evidence


def test_offline_miss_does_not_construct_a_network_client(tmp_path: Path) -> None:
    with (
        IndexOneDataSource(root=tmp_path, offline=True) as source,
        pytest.raises(IndexOneDataError, match="offline"),
    ):
        source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)


def test_cache_identity_includes_fields_params_and_row_bound(tmp_path: Path) -> None:
    client = Client()
    with IndexOneDataSource(root=tmp_path, client=client, clock=lambda: NOW) as source:
        first = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
        second = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=101)
        other = {"filters": [{"field": "date", "operator": "eq", "value": "2024-01-02"}]}
        third = source.query_with_evidence("eod", params=other, fields=FIELDS, page_limit=100)
        client.table = client.table.select(tuple(reversed(FIELDS)))
        fourth = source.query_with_evidence(
            "eod", params=PARAMS, fields=tuple(reversed(FIELDS)), page_limit=100
        )
    assert len(client.calls) == 4
    assert len({r.evidence.request_sha256 for r in (first, second, third, fourth)}) == 4


def test_refresh_keeps_old_immutable_object(tmp_path: Path) -> None:
    client = Client()
    with IndexOneDataSource(root=tmp_path, client=client, clock=lambda: NOW) as source:
        first = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    before = first.evidence.path.read_bytes()
    client.table = client.table.set_column(3, "close", pa.array([103.0]))
    with IndexOneDataSource(
        root=tmp_path, client=client, refresh=True, clock=lambda: NOW
    ) as source:
        second = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    assert second.evidence.path != first.evidence.path
    assert first.evidence.path.read_bytes() == before
    with IndexOneDataSource(root=tmp_path, offline=True) as source:
        latest = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    assert latest.evidence == second.evidence


@pytest.mark.parametrize("tamper", ["bytes", "row_count", "path", "request"])
def test_corrupt_cache_fails_without_network(tmp_path: Path, tamper: str) -> None:
    client = Client()
    with IndexOneDataSource(root=tmp_path, client=client, clock=lambda: NOW) as source:
        result = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    if tamper == "bytes":
        result.evidence.path.write_bytes(b"corrupt")
    else:
        with duckdb.connect(str(tmp_path / "indexone.duckdb")) as catalog:
            if tamper == "row_count":
                catalog.execute("UPDATE indexone_requests SET row_count = 999")
            elif tamper == "path":
                catalog.execute("UPDATE indexone_requests SET relative_path = '../escape.parquet'")
            else:
                catalog.execute("UPDATE indexone_requests SET request_json = '{}' ")
    with (
        IndexOneDataSource(root=tmp_path, client=client, offline=True) as source,
        pytest.raises(IndexOneDataError),
    ):
        source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    assert len(client.calls) == 1


@pytest.mark.parametrize("bad_result", ["too_many", "missing_field"])
def test_invalid_response_is_not_cached(tmp_path: Path, bad_result: str) -> None:
    client = Client()
    if bad_result == "too_many":
        client.table = pa.concat_tables([client.table, client.table])
    else:
        client.table = client.table.drop(["open"])
    with (
        IndexOneDataSource(root=tmp_path, client=client, clock=lambda: NOW) as source,
        pytest.raises(IndexOneDataError),
    ):
        source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=1)
    with duckdb.connect(str(tmp_path / "indexone.duckdb")) as catalog:
        assert catalog.execute("SELECT count(*) FROM indexone_requests").fetchone() == (0,)
    assert not list(tmp_path.rglob("*.parquet"))


def test_empty_result_is_cached_with_requested_columns(tmp_path: Path) -> None:
    client = Client()
    client.table = client.table.slice(0, 0)
    with IndexOneDataSource(root=tmp_path, client=client, clock=lambda: NOW) as source:
        result = source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    assert result.table.num_rows == 0
    assert result.table.column_names == list(FIELDS)


def test_cli_download_and_offline_read_print_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trademaster.data import indexone

    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({"dataset": "eod", "params": PARAMS, "fields": FIELDS}))
    client = Client()
    monkeypatch.setattr(IndexOneClient, "from_environment", lambda: client)
    args = ["--data-root", str(tmp_path / "cache"), "--request", str(request_path)]
    assert indexone.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["rows"] == 1
    assert Path(first["path"]).is_file()
    assert indexone.main([*args, "--offline"]) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert len(client.calls) == 1


def test_cache_error_does_not_echo_an_external_exception(tmp_path: Path) -> None:
    class BrokenClient(Client):
        def query(
            self, operation: str, *, params: Mapping[str, object], fields: tuple[str, ...]
        ) -> pa.Table:
            raise RuntimeError("provider echoed very-private-test-key")

    with (
        IndexOneDataSource(root=tmp_path, client=BrokenClient()) as source,
        pytest.raises(IndexOneDataError) as caught,
    ):
        source.query_with_evidence("eod", params=PARAMS, fields=FIELDS, page_limit=100)
    assert "very-private-test-key" not in str(caught.value)
    assert caught.value.__cause__ is None
