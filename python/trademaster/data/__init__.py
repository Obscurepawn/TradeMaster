"""Public data-foundation interfaces."""

from .catalog import (
    CatalogConflictError,
    CatalogCoverage,
    CatalogObject,
    CatalogVersionError,
    DuckDbCatalog,
)
from .config import DataConfig, DataPaths
from .coverage import (
    CacheFirstDataPortal,
    CanonicalDataProvider,
    CoverageGapError,
    ProviderPage,
    RequestResolver,
)
from .object_store import ObjectIntegrityError, ParquetObjectStore, PublishedObject
from .registry import (
    CanonicalFieldType,
    CoverageKeyMode,
    CoverageShape,
    DatasetRegistry,
    DatasetSpec,
    default_dataset_registry,
    normalize_request,
)
from .research import ResearchDataSource, ResearchObjectEvidence, ResearchQueryResult
from .tushare_provider import (
    TushareClient,
    TushareCredentialError,
    TushareDataError,
    TushareProvider,
)

__all__ = [
    "CacheFirstDataPortal",
    "CanonicalDataProvider",
    "CanonicalFieldType",
    "CatalogConflictError",
    "CatalogCoverage",
    "CatalogObject",
    "CatalogVersionError",
    "CoverageGapError",
    "CoverageKeyMode",
    "CoverageShape",
    "DataConfig",
    "DataPaths",
    "DatasetRegistry",
    "DatasetSpec",
    "DuckDbCatalog",
    "ObjectIntegrityError",
    "ParquetObjectStore",
    "ProviderPage",
    "PublishedObject",
    "RequestResolver",
    "ResearchDataSource",
    "ResearchObjectEvidence",
    "ResearchQueryResult",
    "TushareClient",
    "TushareCredentialError",
    "TushareDataError",
    "TushareProvider",
    "default_dataset_registry",
    "normalize_request",
]
