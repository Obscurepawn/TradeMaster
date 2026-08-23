"""Compatibility facade for the factor-driven industry fundamental strategy."""

from .selection import (
    FundamentalRecord,
    IndustryFundamentalConfig,
    IndustryFundamentalStrategy,
    IndustryFundamentalTop5Config,
    IndustryFundamentalTop5Strategy,
    IndustryMeanScore,
    ScoredCandidate,
    SelectedTarget,
    scored_candidates_from_factors,
)

__all__ = [
    "FundamentalRecord",
    "IndustryFundamentalConfig",
    "IndustryFundamentalStrategy",
    "IndustryFundamentalTop5Config",
    "IndustryFundamentalTop5Strategy",
    "IndustryMeanScore",
    "ScoredCandidate",
    "SelectedTarget",
    "scored_candidates_from_factors",
]
