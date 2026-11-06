"""Read-only IndexOne operations with schema validation and isolated credentials."""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from http.client import HTTPException
from types import MappingProxyType
from typing import Any, Protocol, Self
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pyarrow as pa

INDEXONE_OPERATIONS: Mapping[str, str] = MappingProxyType(
    {
        "eod": "i1_core_eod",
        "fundamentals": "i1_core_fundamentals",
        "securities": "core_securities_reference",
        "fx": "i1_core_fx",
    }
)
_OPERATIONS = frozenset(INDEXONE_OPERATIONS.values())
_BASE_URL = "https://api.indexone.io"
_MAX_RESPONSE_BYTES = 128 * 1024 * 1024


class IndexOneError(RuntimeError):
    """A safe error that contains no provider error body or credential."""


class IndexOneCredentialError(IndexOneError):
    """The API credential is absent or the requested data is not authorized."""


class IndexOneRequestError(IndexOneError):
    """The query is invalid or its bounded HTTP attempts failed."""


class IndexOneDataError(IndexOneError):
    """The response cannot be represented by the advertised data contract."""


class IndexOneTransport(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> tuple[int, bytes]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # Even a redirect to another HTTPS host must not receive x-api-key.
        return None


def _http_transport(request: Request, *, timeout: float) -> tuple[int, bytes]:
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            return int(response.status), response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        # Provider failure bodies can echo credentials and are never retained.
        return error.code, b""


def _matches_type(value: object, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    if kind == "integer":
        return type(value) is int
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "null":
        return value is None
    return False


def _validate_parameter(value: object, definition: Mapping[str, Any]) -> None:
    kind = definition.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if (
        kind is not None
        and not any(isinstance(item, str) and _matches_type(value, item) for item in kinds)
        and (value is not None or definition.get("default", object()) is not None)
    ):
        raise IndexOneRequestError("IndexOne parameter has an invalid type")
    if "enum" in definition and value not in definition["enum"]:
        raise IndexOneRequestError("IndexOne parameter is outside the allowed values")
    if isinstance(value, list):
        if len(value) < definition.get("minItems", 0):
            raise IndexOneRequestError("IndexOne parameter has too few values")
        item_definition = definition.get("items")
        if isinstance(item_definition, dict):
            for item in value:
                _validate_parameter(item, item_definition)


def _validate_filters(
    filters: object, definition: Mapping[str, Any], columns: Mapping[str, Any]
) -> None:
    if not isinstance(filters, list):
        raise IndexOneRequestError("IndexOne filters must be an array")
    try:
        operators = definition["items"]["properties"]["operator"]["enum"]
    except (KeyError, TypeError):
        raise IndexOneDataError("IndexOne filter schema is unsupported") from None
    if not isinstance(operators, list) or not all(isinstance(item, str) for item in operators):
        raise IndexOneDataError("IndexOne filter schema is unsupported")
    for item in filters:
        if not isinstance(item, dict) or set(item) != {"field", "operator", "value"}:
            raise IndexOneRequestError("IndexOne filters need field, operator and value")
        field, operator, value = item["field"], item["operator"], item["value"]
        if not isinstance(field, str) or field not in columns or operator not in operators:
            raise IndexOneRequestError("IndexOne filter field or operator is unsupported")
        if operator in {"is_null", "is_not_null"}:
            if value is not None:
                raise IndexOneRequestError("IndexOne null filters require a null value")
            continue
        values = value if operator in {"in", "not_in"} else [value]
        if not isinstance(values, list):
            raise IndexOneRequestError("IndexOne membership filters require an array")
        for member in values:
            _validate_parameter(member, columns[field])


def _arrow_type(definition: Mapping[str, Any]) -> pa.DataType:
    kind = definition.get("type")
    types: dict[str, pa.DataType] = {
        "string": pa.string(),
        "number": pa.float64(),
        "integer": pa.int64(),
        "boolean": pa.bool_(),
    }
    if not isinstance(kind, str) or kind not in types:
        raise IndexOneDataError("IndexOne output field type is unsupported")
    return types[kind]


class IndexOneClient:
    """Fetch one allowed data operation; no arbitrary execution graphs or writes."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: IndexOneTransport = _http_transport,
        timeout: float = 30,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key
            or any(not 33 <= ord(character) <= 126 for character in api_key)
        ):
            raise IndexOneCredentialError("IndexOne API key is absent or invalid")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 120
        ):
            raise IndexOneRequestError("IndexOne timeout must be between 0 and 120 seconds")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise IndexOneRequestError("IndexOne max_attempts must be between 1 and 5")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._manifest: dict[str, Any] | None = None

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        api_key_env: str = "INDEXONE_API_KEY",
        transport: IndexOneTransport = _http_transport,
        timeout: float = 30,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Self:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env) is None:
            raise IndexOneRequestError("IndexOne credential environment name is invalid")
        environment = os.environ if environment is None else environment
        api_key = environment.get(api_key_env)
        if not api_key:
            raise IndexOneCredentialError(f"required IndexOne credential is absent: {api_key_env}")
        return cls(
            api_key, transport=transport, timeout=timeout, max_attempts=max_attempts, sleep=sleep
        )

    def _request_json(self, path: str, body: dict[str, object] | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers.update({"Content-Type": "application/json", "x-api-key": self._api_key})
            try:
                data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
            except (TypeError, ValueError, OverflowError):
                raise IndexOneRequestError(
                    "IndexOne request must contain finite JSON values"
                ) from None
        request = Request(_BASE_URL + path, data=data, headers=headers)
        for attempt in range(self._max_attempts):
            try:
                status, raw = self._transport(request, timeout=self._timeout)
            except (OSError, HTTPException):
                status, raw = 503, b""
            except Exception:  # noqa: BLE001 - transport exception text may contain the key
                raise IndexOneRequestError("IndexOne transport failed") from None
            if status in {401, 403}:
                raise IndexOneCredentialError("IndexOne rejected the API key or data permission")
            if status == 429 or 500 <= status <= 599:
                if attempt + 1 < self._max_attempts:
                    self._sleep(min(2.0**attempt, 4.0))
                    continue
                raise IndexOneRequestError("IndexOne transient request failure exhausted retries")
            if status != 200:
                raise IndexOneRequestError(f"IndexOne request failed with HTTP {status}")
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise IndexOneDataError("IndexOne response exceeds the byte limit")
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise IndexOneDataError("IndexOne response is not valid JSON") from None
            if not isinstance(value, dict):
                raise IndexOneDataError("IndexOne response envelope is invalid")
            return value
        raise AssertionError("bounded IndexOne request loop did not return")

    def _operation_schema(self, operation: str) -> dict[str, Any]:
        if self._manifest is None:
            manifest = self._request_json("/schema").get("operation_manifest")
            if not isinstance(manifest, dict):
                raise IndexOneDataError("IndexOne operation schema is invalid")
            self._manifest = manifest
        definition = self._manifest.get(operation)
        if not isinstance(definition, dict) or definition.get("id") != operation:
            raise IndexOneDataError("IndexOne operation is absent from the live schema")
        return definition

    def query(
        self, operation: str, *, params: Mapping[str, object], fields: tuple[str, ...]
    ) -> pa.Table:
        operation = INDEXONE_OPERATIONS.get(operation, operation)
        if operation not in _OPERATIONS:
            raise IndexOneRequestError("IndexOne operation is not an allowed data query")
        if (
            not isinstance(fields, tuple)
            or not fields
            or any(not isinstance(field, str) or not field for field in fields)
            or len(set(fields)) != len(fields)
        ):
            raise IndexOneRequestError("IndexOne fields must be a nonempty unique tuple")
        definition = self._operation_schema(operation)
        parameters = definition.get("parameters")
        output = definition.get("output")
        if not isinstance(parameters, dict) or not isinstance(output, dict):
            raise IndexOneDataError("IndexOne operation schema is unsupported")
        columns = output.get("columns")
        if output.get("type") != "dataframe" or not isinstance(columns, dict):
            raise IndexOneDataError("IndexOne output schema is unsupported")
        if any(field not in columns for field in fields):
            raise IndexOneRequestError("IndexOne requested field is absent from the live schema")
        if (
            not isinstance(params, Mapping)
            or set(params) - set(parameters)
            or "attributes" in params
        ):
            raise IndexOneRequestError("IndexOne parameter is not allowed for this operation")
        for name, parameter in parameters.items():
            if not isinstance(parameter, dict):
                raise IndexOneDataError("IndexOne parameter schema is unsupported")
            if name in params:
                _validate_parameter(params[name], parameter)
            elif parameter.get("minItems", 0) > 0:
                raise IndexOneRequestError("IndexOne operation requires a nonempty filter")
        if "filters" in params:
            _validate_filters(params["filters"], parameters["filters"], columns)
        duplicate_keys = params.get("drop_duplicates")
        if isinstance(duplicate_keys, list) and any(
            not isinstance(key, str) or key not in columns for key in duplicate_keys
        ):
            raise IndexOneRequestError("IndexOne duplicate keys must be known output columns")
        if any(not isinstance(columns[field], dict) for field in fields):
            raise IndexOneDataError("IndexOne output field schema is invalid")
        arrow_schema = pa.schema([(field, _arrow_type(columns[field])) for field in fields])
        payload = self._request_json(
            "/execute",
            {
                "operations": [
                    {
                        "id": "data",
                        "operation": operation,
                        "input": [],
                        "parameters": {**params, "attributes": list(fields)},
                    }
                ]
            },
        )
        envelope_state = payload.get("state", {})
        if (
            not isinstance(envelope_state, dict)
            or payload.get("error")
            or any(
                envelope_state.get(key)
                for key in ("error", "suspended", "pending", "executing", "skipped")
            )
            or envelope_state.get("completed", True) is not True
        ):
            raise IndexOneDataError("IndexOne execution failed or is incomplete")
        if "data" in payload:
            if set(payload) != {"data"}:
                raise IndexOneDataError("IndexOne keyed response needs exactly the requested node")
            node = payload["data"]
        else:
            operations = payload.get("operations")
            if not isinstance(operations, list) or len(operations) != 1:
                raise IndexOneDataError("IndexOne response needs exactly one operation")
            node = operations[0]
        if (
            not isinstance(node, dict)
            or node.get("id") != "data"
            or node.get("operation") != operation
        ):
            raise IndexOneDataError("IndexOne response operation does not match the request")
        state = node.get("state")
        if (
            not isinstance(state, dict)
            or state.get("error") is not False
            or any(state.get(key) for key in ("suspended", "pending", "executing", "skipped"))
            or state.get("completed", True) is not True
        ):
            raise IndexOneDataError("IndexOne operation failed or is incomplete")
        if any(
            container.get(key)
            for container in (payload, envelope_state, node, state)
            for key in ("truncated", "output_truncated", "has_more")
        ):
            raise IndexOneDataError("IndexOne returned an incomplete result")
        values = node.get("output")
        arrays: dict[str, list[object]]
        if isinstance(values, dict):
            if any(field not in values for field in fields):
                raise IndexOneDataError("IndexOne output lacks a requested field")
            if any(not isinstance(column, list) for column in values.values()):
                raise IndexOneDataError("IndexOne column output must contain arrays")
            if len({len(column) for column in values.values()}) != 1:
                raise IndexOneDataError("IndexOne output columns have different lengths")
            arrays = {field: values[field] for field in fields}
        elif isinstance(values, list):
            arrays = {field: [] for field in fields}
            for row in values:
                if not isinstance(row, dict) or any(field not in row for field in fields):
                    raise IndexOneDataError("IndexOne output lacks a requested field")
                for field in fields:
                    arrays[field].append(row[field])
        else:
            raise IndexOneDataError("IndexOne output must contain rows or column arrays")
        for field, field_values in arrays.items():
            for value in field_values:
                if value is not None and not _matches_type(value, columns[field]["type"]):
                    raise IndexOneDataError("IndexOne output value violates its field type")
        try:
            return pa.Table.from_pydict(arrays, schema=arrow_schema)
        except (TypeError, ValueError, OverflowError, pa.ArrowException):
            raise IndexOneDataError(
                "IndexOne output cannot be represented as an Arrow table"
            ) from None


__all__ = [
    "INDEXONE_OPERATIONS",
    "IndexOneClient",
    "IndexOneCredentialError",
    "IndexOneDataError",
    "IndexOneError",
    "IndexOneRequestError",
]
