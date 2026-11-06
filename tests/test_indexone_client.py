from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from typing import Any
from urllib.request import Request

import pyarrow as pa
import pytest
from trademaster.data.indexone_client import (
    INDEXONE_OPERATIONS,
    IndexOneClient,
    IndexOneCredentialError,
    IndexOneDataError,
    IndexOneRequestError,
)


def schema() -> dict[str, object]:
    columns = {
        "id": {"type": "string"},
        "date": {"type": "string", "semantic": "iso_date"},
        "close": {"type": "number"},
        "active": {"type": "boolean"},
        "history": {"type": "array"},
    }
    parameters = {
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "operator": {"type": "string", "enum": ["eq", "gte", "in", "is_null"]},
                    "value": {},
                },
            },
        },
        "attributes": {"type": "array", "items": {"type": "string"}},
    }
    operations: dict[str, Any] = {
        operation: {
            "id": operation,
            "parameters": dict(parameters),
            "output": {"type": "dataframe", "columns": columns},
        }
        for operation in INDEXONE_OPERATIONS.values()
    }
    operations["i1_core_fundamentals"]["parameters"]["storage_type"] = {
        "type": "string",
        "enum": ["quarterly", "annual", "ttm"],
    }
    operations["i1_core_fundamentals"]["parameters"]["filters"] = {
        **parameters["filters"],
        "minItems": 1,
    }
    return {"operation_manifest": operations}


def response(rows: object, *, operation: str = "i1_core_eod", **state: object) -> dict[str, object]:
    return {
        "operations": [
            {
                "id": "data",
                "operation": operation,
                "state": {"error": False, "suspended": False, **state},
                "output": rows,
            }
        ]
    }


class Transport:
    def __init__(self, *replies: tuple[int, object] | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[Request] = []

    def __call__(self, request: Request, *, timeout: float) -> tuple[int, bytes]:
        assert timeout > 0
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


def client(transport: Transport, **kwargs: Any) -> IndexOneClient:
    return IndexOneClient.from_environment(
        environment={"INDEXONE_API_KEY": "test-only-api-key"},
        transport=transport,
        sleep=lambda _: None,
        **kwargs,
    )


def test_authentication_schema_projection_and_typed_rows() -> None:
    rows = [{"id": "BBG-DEMO", "date": "2024-01-02", "close": 102, "active": None}]
    transport = Transport((200, schema()), (200, response(rows)), (200, response([])))
    source = client(transport)
    filters = [{"field": "id", "operator": "eq", "value": "BBG-DEMO"}]
    fields = ("id", "date", "close", "active")
    table = source.query("eod", params={"filters": filters}, fields=fields)
    assert table.to_pylist() == rows
    expected_types: dict[str, pa.DataType] = {
        "id": pa.string(),
        "date": pa.string(),
        "close": pa.float64(),
        "active": pa.bool_(),
    }
    assert table.schema == pa.schema(expected_types)
    assert source.query("i1_core_eod", params={}, fields=fields).schema == table.schema
    assert [request.full_url for request in transport.requests] == [
        "https://api.indexone.io/schema",
        "https://api.indexone.io/execute",
        "https://api.indexone.io/execute",
    ]
    assert transport.requests[0].get_method() == "GET"
    request = transport.requests[1]
    assert request.get_header("X-api-key") == "test-only-api-key"
    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == {
        "operations": [
            {
                "id": "data",
                "operation": "i1_core_eod",
                "parameters": {"filters": filters, "attributes": list(fields)},
                "input": [],
            }
        ]
    }
    assert "test-only-api-key" not in repr(source)


def test_missing_credential_does_not_contact_provider() -> None:
    transport = Transport()
    with pytest.raises(IndexOneCredentialError, match="INDEXONE_API_KEY"):
        IndexOneClient.from_environment(environment={}, transport=transport)
    assert not transport.requests


@pytest.mark.parametrize("alias", tuple(INDEXONE_OPERATIONS))
def test_all_allowed_operations_use_the_catalog_contract(alias: str) -> None:
    operation = INDEXONE_OPERATIONS[alias]
    transport = Transport((200, schema()), (200, response([], operation=operation)))
    table = client(transport).query(
        alias,
        params={"filters": [{"field": "id", "operator": "eq", "value": "demo"}]},
        fields=("id",),
    )
    assert table.num_rows == 0 and table.schema == pa.schema([("id", pa.string())])


@pytest.mark.parametrize("key", ["", " ", "line\nbreak", "nonascii-密钥"])
def test_invalid_credential_never_enters_http_headers(key: str) -> None:
    transport = Transport()
    with pytest.raises(IndexOneCredentialError):
        IndexOneClient(key, transport=transport)
    assert not transport.requests


@pytest.mark.parametrize(
    "operation,params,fields",
    [
        ("create_index", {}, ("id",)),
        ("eod", {"websocket_id": "stream"}, ("id",)),
        ("eod", {"attributes": ["id"]}, ("id",)),
        ("eod", {}, ("invented",)),
        ("eod", {}, ("id", "id")),
        ("eod", {"filters": [{"field": "ticker", "operator": "eq", "value": "AAPL"}]}, ("id",)),
        ("eod", {"filters": [{"field": "id", "operator": "delete", "value": "x"}]}, ("id",)),
        ("eod", {"filters": [{"field": "id", "operator": "in", "value": "x"}]}, ("id",)),
        (
            "eod",
            {"filters": [{"field": "close", "operator": "eq", "value": float("nan")}]},
            ("id",),
        ),
        ("fundamentals", {"storage_type": "monthly", "filters": []}, ("id",)),
        ("fundamentals", {"storage_type": "annual"}, ("id",)),
    ],
)
def test_invalid_request_never_reaches_execute(
    operation: str, params: dict[str, object], fields: tuple[str, ...]
) -> None:
    transport = Transport((200, schema()))
    with pytest.raises(IndexOneRequestError):
        client(transport).query(operation, params=params, fields=fields)
    assert all(request.get_method() == "GET" for request in transport.requests)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"operations": []},
        response(None),
        response({"id": ["BBG-DEMO"]}),
        response([], error="test-only-api-key"),
        response([], suspended=True),
        response([], truncated=True),
        {"operations": [{"id": "other", "state": {"error": False}, "output": []}]},
        response([{"id": "BBG-DEMO"}]),
        response([{"id": 123, "close": 1}]),
        response([{"id": "BBG-DEMO", "close": True}]),
        response([{"id": "BBG-DEMO", "close": "1.0"}]),
        response([{"id": "BBG-DEMO", "close": float("inf")}]),
        b"{not-json}",
    ],
)
def test_invalid_or_incomplete_output_has_safe_error(body: object) -> None:
    transport = Transport((200, schema()), (200, body))
    with pytest.raises(IndexOneDataError) as caught:
        client(transport).query("eod", params={}, fields=("id", "close"))
    assert "test-only-api-key" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "status,error",
    [
        (401, IndexOneCredentialError),
        (403, IndexOneCredentialError),
        (400, IndexOneRequestError),
        (302, IndexOneRequestError),
    ],
)
def test_permanent_http_failure_is_not_retried(status: int, error: type[Exception]) -> None:
    transport = Transport((200, schema()), (status, {"error": "test-only-api-key"}))
    with pytest.raises(error) as caught:
        client(transport).query("eod", params={}, fields=("id",))
    assert len(transport.requests) == 2
    assert "test-only-api-key" not in str(caught.value)


def test_retry_transient_http_and_transport_failures_with_bound() -> None:
    transport = Transport(
        (429, {}), (200, schema()), OSError("test-only-api-key"), (503, {}), (200, response([]))
    )
    assert client(transport).query("eod", params={}, fields=("id",)).num_rows == 0
    assert len(transport.requests) == 5
    failed = Transport((503, {}), (503, {}))
    with pytest.raises(IndexOneRequestError):
        client(failed, max_attempts=2).query("eod", params={}, fields=("id",))
    assert len(failed.requests) == 2


def test_unexpected_transport_exception_does_not_leak_credential() -> None:
    transport = Transport(RuntimeError("test-only-api-key"))
    with pytest.raises(IndexOneRequestError) as caught:
        client(transport).query("eod", params={}, fields=("id",))
    assert "test-only-api-key" not in "".join(traceback.format_exception(caught.value))
    assert len(transport.requests) == 1


def test_envelope_error_cannot_be_treated_as_success() -> None:
    body = response([])
    body["state"] = {"error": "test-only-api-key"}
    with pytest.raises(IndexOneDataError):
        client(Transport((200, schema()), (200, body))).query("eod", params={}, fields=("id",))


def test_unsupported_output_type_fails_before_execution() -> None:
    transport = Transport((200, schema()))
    with pytest.raises(IndexOneDataError, match="type"):
        client(transport).query("eod", params={}, fields=("history",))
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "change", [lambda body: body.clear(), lambda body: body.update(operation_manifest=[])]
)
def test_schema_mismatch_is_not_a_successful_empty_query(
    change: Callable[[dict[str, object]], None],
) -> None:
    body = schema()
    change(body)
    with pytest.raises(IndexOneDataError, match="schema"):
        client(Transport((200, body))).query("eod", params={}, fields=("id",))


def keyed_response(output: object, **state: object) -> dict[str, object]:
    return {
        "data": {
            "id": "data",
            "operation": "i1_core_eod",
            "state": {
                "error": False,
                "completed": True,
                "executing": False,
                "pending": False,
                "suspended": False,
                "skipped": False,
                **state,
            },
            "output": output,
            "metadata": {"execution_time_ms": 5},
        }
    }


@pytest.mark.parametrize("keyed", [True, False])
@pytest.mark.parametrize("empty", [True, False])
def test_column_arrays_support_both_live_and_documented_envelopes(keyed: bool, empty: bool) -> None:
    output = {
        "id": [] if empty else ["BBG-DEMO", "BBG-DEMO"],
        "date": [] if empty else ["2024-01-02", "2024-01-03"],
        "close": [] if empty else [102.0, None],
    }
    body = keyed_response(output) if keyed else response(output)
    table = client(Transport((200, schema()), (200, body))).query(
        "eod",
        params={},
        fields=("date", "id", "close"),
    )
    assert table.to_pydict() == {name: output[name] for name in ("date", "id", "close")}
    assert table.schema.field("close").type == pa.float64()


@pytest.mark.parametrize(
    "output",
    [
        None,
        {},
        {"id": []},
        {"id": "BBG-DEMO", "close": [102.0]},
        {"id": ["BBG-DEMO"], "close": []},
        {"id": ["BBG-DEMO"], "close": [True]},
        {"id": ["BBG-DEMO"], "close": [float("nan")]},
    ],
)
def test_live_column_arrays_reject_partial_or_invalid_data(output: object) -> None:
    with pytest.raises(IndexOneDataError):
        client(Transport((200, schema()), (200, keyed_response(output)))).query(
            "eod",
            params={},
            fields=("id", "close"),
        )


@pytest.mark.parametrize(
    "flag,value",
    [
        ("completed", False),
        ("executing", True),
        ("pending", True),
        ("skipped", True),
        ("error", True),
        ("suspended", True),
        ("truncated", True),
    ],
)
@pytest.mark.parametrize("keyed", [True, False])
def test_unfinished_operation_never_returns_data(flag: str, value: bool, keyed: bool) -> None:
    state: dict[str, Any] = {flag: value}
    body = keyed_response({"id": []}, **state) if keyed else response([], **state)
    with pytest.raises(IndexOneDataError):
        client(Transport((200, schema()), (200, body))).query("eod", params={}, fields=("id",))


def test_keyed_envelope_rejects_unrequested_or_ambiguous_nodes() -> None:
    for body in (
        {**keyed_response({"id": []}), "other": {}},
        {**keyed_response({"id": []}), **response([])},
    ):
        with pytest.raises(IndexOneDataError):
            client(Transport((200, schema()), (200, body))).query("eod", params={}, fields=("id",))
