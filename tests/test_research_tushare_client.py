from __future__ import annotations

from typing import Any

import pytest
from trademaster.research.full_a import AcquisitionBlockedError
from trademaster.research.tushare import (
    RateLimitedTushareClient,
    ResearchCredentialError,
    ResearchProviderRequestError,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _Client:
    def __init__(self, *, failures: int = 0, error: Exception | None = None) -> None:
        self.failures = failures
        self.error = error or TimeoutError("provider timed out")
        self.calls = 0

    def query(self, api_name: str, *, fields: str, **params: object) -> Any:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return {"api": api_name, "fields": fields, "params": params}


class _CodedProviderError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def test_research_tushare_client_rate_limits_and_retries_without_losing_cursor() -> None:
    clock = _Clock()
    underlying = _Client(failures=2)
    client = RateLimitedTushareClient(
        underlying,
        minimum_interval_seconds=0.2,
        endpoint_intervals={},
        max_attempts=4,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    first = client.query("daily", fields="ts_code", limit=6000, offset=12000, trade_date="20200102")
    second = client.query(
        "daily", fields="ts_code", limit=6000, offset=18000, trade_date="20200103"
    )

    assert first["params"]["offset"] == 12000
    assert second["params"]["offset"] == 18000
    assert underlying.calls == 4
    assert clock.sleeps[:2] == [0.5, 1.0]
    assert clock.sleeps[-1] == 0.2
    assert client.calls == [
        ("daily", 12000),
        ("daily", 12000),
        ("daily", 12000),
        ("daily", 18000),
    ]


def test_stock_basic_uses_its_stricter_endpoint_interval() -> None:
    clock = _Clock()
    client = RateLimitedTushareClient(
        _Client(),
        minimum_interval_seconds=0.1,
        endpoint_intervals={"stock_basic": 1.25},
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    client.query("stock_basic", fields="ts_code", limit=6000, offset=0)
    client.query("stock_basic", fields="ts_code", limit=6000, offset=0)

    assert clock.sleeps == [1.25]


@pytest.mark.parametrize(
    "provider_message",
    [
        "抱歉，您没有访问该接口的权限，权限的具体详情请参阅接口文档",
        "您的积分不足，无法访问本接口",
    ],
)
def test_permission_failure_becomes_terminal_blocked_outcome_without_retry(
    provider_message: str,
) -> None:
    clock = _Clock()
    underlying = _Client(failures=4, error=RuntimeError(provider_message))
    client = RateLimitedTushareClient(
        underlying,
        endpoint_intervals={},
        max_attempts=4,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    with pytest.raises(AcquisitionBlockedError) as caught:
        client.query("stock_st", fields="ts_code", limit=6000, offset=0)

    assert caught.value.outcome == "permission_blocked"
    assert underlying.calls == 1
    assert clock.sleeps == []


def test_rejected_credential_is_typed_non_transient_and_secret_safe() -> None:
    secret = "provider-echoed-secret-token"
    underlying = _Client(failures=4, error=RuntimeError(f"无效 token: {secret}"))
    client = RateLimitedTushareClient(underlying, endpoint_intervals={}, max_attempts=4)

    with pytest.raises(ResearchCredentialError) as caught:
        client.query("daily", fields="ts_code", limit=6000, offset=0)

    assert underlying.calls == 1
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (RuntimeError("请求参数错误：字段不存在"), "invalid_request"),
        (_CodedProviderError(400, "bad request"), "invalid_request"),
    ],
)
def test_invalid_request_is_typed_and_not_retried(
    error: Exception,
    expected_kind: str,
) -> None:
    underlying = _Client(failures=4, error=error)
    client = RateLimitedTushareClient(underlying, endpoint_intervals={}, max_attempts=4)

    with pytest.raises(ResearchProviderRequestError) as caught:
        client.query("daily", fields="missing", limit=6000, offset=0)

    assert caught.value.kind == expected_kind
    assert underlying.calls == 1


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("每分钟最多访问该接口 200 次，请稍后重试"),
        _CodedProviderError(503, "service unavailable"),
        OSError("network is unreachable"),
    ],
)
def test_explicit_transient_provider_failure_uses_bounded_retry(error: Exception) -> None:
    clock = _Clock()
    underlying = _Client(failures=2, error=error)
    client = RateLimitedTushareClient(
        underlying,
        endpoint_intervals={},
        max_attempts=3,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    result = client.query("daily", fields="ts_code", limit=6000, offset=0)

    assert result["api"] == "daily"
    assert underlying.calls == 3
    assert clock.sleeps == [0.5, 1.0]


def test_unknown_provider_error_fails_closed_without_retry_or_secret_echo() -> None:
    secret = "provider-echoed-secret-token"
    underlying = _Client(failures=4, error=RuntimeError(f"unexpected: {secret}"))
    client = RateLimitedTushareClient(underlying, endpoint_intervals={}, max_attempts=4)

    with pytest.raises(ResearchProviderRequestError) as caught:
        client.query("daily", fields="ts_code", limit=6000, offset=0)

    assert caught.value.kind == "unknown"
    assert underlying.calls == 1
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
