"""Stable public API for the managed-factor industry fundamental strategy."""

from trademaster.strategies.industry_fundamental_top5 import (
    FundamentalRecord,
    IndustryFundamentalConfig,
    IndustryFundamentalStrategy,
    IndustryMeanScore,
    ScoredCandidate,
    SelectedTarget,
    scored_candidates_from_factors,
)
from trademaster.strategies.industry_fundamental_top5.real import (
    FundamentalStrategyRunError,
    RealStrategyConfig,
    RealStrategyOutcome,
    run_real_strategy,
)

__all__ = [
    "FundamentalRecord",
    "FundamentalStrategyRunError",
    "IndustryFundamentalConfig",
    "IndustryFundamentalStrategy",
    "IndustryMeanScore",
    "RealStrategyConfig",
    "RealStrategyOutcome",
    "ScoredCandidate",
    "SelectedTarget",
    "run_real_strategy",
    "scored_candidates_from_factors",
]
