"""Replay the event fixture through the installed Python interface."""

import hashlib
from pathlib import Path

import pytest


def test_event_replay_returns_the_archived_result_bytes() -> None:
    from trademaster.replay import replay

    request = Path(__file__).resolve().parents[1] / "docs/examples/round-trip.json"
    result = replay(request)
    assert hashlib.sha256(result).hexdigest() == (
        "a5aca73c1f7f610d6935d88a04c6baa442f803b22bdec6a9f93abbcff5eb4454"
    )


def test_replay_reports_unknown_request_schema(tmp_path: Path) -> None:
    from trademaster.replay import replay

    request = tmp_path / "request.json"
    request.write_text('{"schema_id": "unknown/test"}')
    with pytest.raises(ValueError, match="unknown/test"):
        replay(request)
