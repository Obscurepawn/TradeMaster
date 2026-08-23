"""Reproducible bulk research orchestration."""

from .factor_evaluation import (
    ForwardReturnPolicy,
    FullAFactorDiagnostics,
    FullAFactorEvaluationConfig,
    FullAFactorEvaluationResult,
    FullAFactorEvaluator,
    FullAForwardReturnBuilder,
)
from .factor_evaluation_duckdb import DuckDBFullAFactorEvaluator
from .factor_report import (
    FactorReportIntegrityError,
    FullAFactorReportArtifact,
    FullAFactorReportIdentity,
    FullAFactorReportManifest,
    FullAFactorReportStore,
)
from .full_a import (
    DownloadPlanManifest,
    DownloadRunStatus,
    DownloadTask,
    FullAResearchConfig,
    FullAResearchDownloader,
    FullAResearchPlanner,
    FullAResearchStore,
    ResearchInstrument,
)
from .full_a_fundamental import (
    FullAFundamentalConfig,
    FullAFundamentalCoverage,
    FullAFundamentalPanelBuilder,
    FullAFundamentalPanelManifest,
    FundamentalArtifactRef,
    FundamentalOutputStatus,
)
from .full_a_panel import (
    FactorResearchPanelBuilder,
    FactorResearchPanelManifest,
    FullAPanelConfig,
    FullAPanelCoverage,
    PanelArtifactRef,
)

__all__ = [
    "DownloadPlanManifest",
    "DownloadRunStatus",
    "DownloadTask",
    "DuckDBFullAFactorEvaluator",
    "FactorReportIntegrityError",
    "FactorResearchPanelBuilder",
    "FactorResearchPanelManifest",
    "ForwardReturnPolicy",
    "FullAFactorDiagnostics",
    "FullAFactorEvaluationConfig",
    "FullAFactorEvaluationResult",
    "FullAFactorEvaluator",
    "FullAFactorReportArtifact",
    "FullAFactorReportIdentity",
    "FullAFactorReportManifest",
    "FullAFactorReportStore",
    "FullAForwardReturnBuilder",
    "FullAFundamentalConfig",
    "FullAFundamentalCoverage",
    "FullAFundamentalPanelBuilder",
    "FullAFundamentalPanelManifest",
    "FullAPanelConfig",
    "FullAPanelCoverage",
    "FullAResearchConfig",
    "FullAResearchDownloader",
    "FullAResearchPlanner",
    "FullAResearchStore",
    "FundamentalArtifactRef",
    "FundamentalOutputStatus",
    "PanelArtifactRef",
    "ResearchInstrument",
]
