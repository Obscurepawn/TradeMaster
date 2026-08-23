"""Credential-isolated rate limiting for long-running Tushare research downloads."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal, cast

from trademaster.data.research import TushareQueryClient

from .full_a import AcquisitionBlockedError

ProviderErrorKind = Literal[
    "permission_blocked",
    "credential",
    "invalid_request",
    "transient",
    "unknown",
]
NonTransientProviderErrorKind = Literal["invalid_request", "unknown"]
ProviderErrorClassifier = Callable[[Exception], ProviderErrorKind]

_PROVIDER_ERROR_KINDS = frozenset(
    {"permission_blocked", "credential", "invalid_request", "transient", "unknown"}
)
_PERMISSION_CODES = frozenset({403})
_CREDENTIAL_CODES = frozenset({401})
_INVALID_REQUEST_CODES = frozenset({400, 404, 405, 422})
_TRANSIENT_CODES = frozenset({408, 429, 500, 502, 503, 504})

_PERMISSION_MARKERS = (
    "没有访问该接口的权限",
    "没有权限访问该接口",
    "无权限访问",
    "权限不足",
    "积分不足",
    "permission denied",
    "forbidden",
)
_CREDENTIAL_MARKERS = (
    "token无效",
    "token 无效",
    "无效token",
    "无效 token",
    "invalid token",
    "token is invalid",
    "token invalid",
    "认证失败",
    "鉴权失败",
    "unauthorized",
)
_INVALID_REQUEST_MARKERS = (
    "参数错误",
    "参数无效",
    "字段不存在",
    "接口不存在",
    "invalid parameter",
    "invalid argument",
    "bad request",
    "unknown field",
    "unsupported endpoint",
)
_TRANSIENT_MARKERS = (
    "每分钟最多",
    "每小时最多",
    "访问频率",
    "频率过高",
    "请求过于频繁",
    "请稍后重试",
    "服务器繁忙",
    "服务暂不可用",
    "限流",
    "超时",
    "too many requests",
    "rate limit",
    "temporarily unavailable",
    "service unavailable",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "connection refused",
    "connection error",
    "failed to establish a new connection",
    "remote end closed connection",
    "network is unreachable",
    "temporary failure in name resolution",
)


def _provider_code(error: Exception) -> int | None:
    """Read only common public scalar code attributes from a provider exception."""

    for attribute in ("status_code", "http_status", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
    return None


def _contains_any(message: str, markers: tuple[str, ...]) -> bool:
    return any(marker in message for marker in markers)


def classify_provider_error(error: Exception) -> ProviderErrorKind:
    """Classify stable error codes/messages without importing Tushare internals."""

    if isinstance(error, (TimeoutError, ConnectionError)):
        return "transient"

    code = _provider_code(error)
    if code in _CREDENTIAL_CODES:
        return "credential"
    if code in _PERMISSION_CODES:
        return "permission_blocked"
    if code in _INVALID_REQUEST_CODES:
        return "invalid_request"
    if code in _TRANSIENT_CODES:
        return "transient"

    message = str(error).casefold()
    if _contains_any(message, _CREDENTIAL_MARKERS):
        return "credential"
    if _contains_any(message, _PERMISSION_MARKERS):
        return "permission_blocked"
    if _contains_any(message, _INVALID_REQUEST_MARKERS):
        return "invalid_request"
    if _contains_any(message, _TRANSIENT_MARKERS):
        return "transient"
    return "unknown"


class ResearchProviderError(RuntimeError):
    """Secret-safe provider failure exposed by the research client boundary."""


class ResearchCredentialError(ResearchProviderError):
    """Missing or rejected provider credential; never retryable."""


class ResearchProviderRequestError(ResearchProviderError):
    """A non-retryable invalid or unclassified provider failure."""

    def __init__(self, kind: NonTransientProviderErrorKind, api_name: str) -> None:
        super().__init__(f"Tushare {kind} failure for endpoint: {api_name}")
        self.kind = kind
        self.api_name = api_name


class ResearchTransientProviderError(ResearchProviderError):
    """A transient provider failure that exhausted the configured retry budget."""

    def __init__(self, api_name: str, attempts: int) -> None:
        super().__init__(
            f"Tushare transient failure exhausted {attempts} attempts for endpoint: {api_name}"
        )
        self.api_name = api_name
        self.attempts = attempts


class CacheOnlyTushareClient:
    """Provider boundary that proves a command did not need external access."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def query(
        self,
        api_name: str,
        *,
        fields: str,
        limit: int,
        offset: int,
        **params: object,
    ) -> Any:
        self.calls.append((api_name, offset))
        raise RuntimeError(f"cache-only mode has a coverage gap: {api_name}")


class RateLimitedTushareClient:
    """Single-worker limiter with bounded exponential retries and no secret repr."""

    def __init__(
        self,
        client: TushareQueryClient,
        *,
        minimum_interval_seconds: float = 0.13,
        endpoint_intervals: Mapping[str, float] | None = None,
        max_attempts: int = 4,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        error_classifier: ProviderErrorClassifier = classify_provider_error,
    ) -> None:
        if minimum_interval_seconds < 0 or max_attempts < 1:
            raise ValueError("Tushare rate-limit configuration is invalid")
        intervals = dict(endpoint_intervals or {"stock_basic": 1.25})
        if any(not endpoint or value < 0 for endpoint, value in intervals.items()):
            raise ValueError("Tushare endpoint interval is invalid")
        self._client = client
        self._minimum_interval = minimum_interval_seconds
        self._endpoint_intervals = intervals
        self._max_attempts = max_attempts
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._error_classifier = error_classifier
        self._last_call: float | None = None
        self.calls: list[tuple[str, int]] = []

    @classmethod
    def from_environment(
        cls,
        *,
        token_env: str = "TUSHARE_TOKEN",
        minimum_interval_seconds: float = 0.13,
        max_attempts: int = 4,
    ) -> RateLimitedTushareClient:
        token = os.environ.get(token_env)
        if not token:
            raise ResearchCredentialError(f"required Tushare credential is absent: {token_env}")
        tushare: Any = importlib.import_module("tushare")
        return cls(
            cast(TushareQueryClient, tushare.pro_api(token)),
            minimum_interval_seconds=minimum_interval_seconds,
            max_attempts=max_attempts,
        )

    def query(
        self,
        api_name: str,
        *,
        fields: str,
        limit: int,
        offset: int,
        **params: object,
    ) -> Any:
        interval = self._endpoint_intervals.get(api_name, self._minimum_interval)
        for attempt in range(self._max_attempts):
            if self._last_call is not None:
                remaining = interval - (self._monotonic() - self._last_call)
                if remaining > 0:
                    self._sleeper(remaining)
            self._last_call = self._monotonic()
            self.calls.append((api_name, offset))
            try:
                return self._client.query(
                    api_name,
                    fields=fields,
                    limit=limit,
                    offset=offset,
                    **params,
                )
            except Exception as error:  # noqa: BLE001 - provider SDK has no stable exception base
                kind = self._error_classifier(error)
                if kind not in _PROVIDER_ERROR_KINDS:
                    kind = "unknown"
                if kind == "permission_blocked":
                    raise AcquisitionBlockedError(
                        "permission_blocked",
                        f"Tushare permission blocked endpoint: {api_name}",
                    ) from None
                if kind == "credential":
                    raise ResearchCredentialError(
                        f"Tushare credential was rejected for endpoint: {api_name}"
                    ) from None
                if kind in {"invalid_request", "unknown"}:
                    raise ResearchProviderRequestError(kind, api_name) from None
                if attempt + 1 == self._max_attempts:
                    raise ResearchTransientProviderError(api_name, self._max_attempts) from None
                self._sleeper(0.5 * (2**attempt))
        raise AssertionError("Tushare retry loop must return or raise")


__all__ = [
    "CacheOnlyTushareClient",
    "ProviderErrorClassifier",
    "ProviderErrorKind",
    "RateLimitedTushareClient",
    "ResearchCredentialError",
    "ResearchProviderError",
    "ResearchProviderRequestError",
    "ResearchTransientProviderError",
    "classify_provider_error",
]
