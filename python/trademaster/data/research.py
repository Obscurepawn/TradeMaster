"""Evidence-bearing research data source contracts for strategy and factor assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa


@dataclass(frozen=True, slots=True)
class ResearchObjectEvidence:
    endpoint: str
    request_sha256: str
    content_sha256: str
    path: Path
    row_count: int
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchQueryResult:
    table: pa.Table
    evidence: ResearchObjectEvidence


class ResearchDataSource(Protocol):
    """Cache-first source that never drops the immutable object used by a query."""

    def query_with_evidence(
        self,
        endpoint: str,
        *,
        params: Mapping[str, object],
        fields: tuple[str, ...],
        page_limit: int,
    ) -> ResearchQueryResult: ...


class TushareQueryClient(Protocol):
    """Minimal provider protocol shared by data adapters and research orchestration."""

    def query(
        self,
        api_name: str,
        *,
        fields: str,
        limit: int,
        offset: int,
        **params: object,
    ) -> Any: ...


__all__ = [
    "ResearchDataSource",
    "ResearchObjectEvidence",
    "ResearchQueryResult",
    "TushareQueryClient",
]
