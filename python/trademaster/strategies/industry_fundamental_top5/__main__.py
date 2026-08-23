"""Command line entry point for the real ten-year strategy run."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Literal, cast

from .real import RealStrategyConfig, run_real_strategy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the real Tushare SW2021 industry fundamental TopN strategy"
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2016, 8, 12))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 11))
    parser.add_argument("--initial-cash", type=int, default=500_000)
    parser.add_argument("--top-per-industry", type=int, default=5)
    parser.add_argument("--top-industries-by-mean-score", type=int)
    parser.add_argument(
        "--industry-membership-policy",
        choices=("historical_interval_required", "static_latest_experiment"),
        default="historical_interval_required",
    )
    args = parser.parse_args()
    outcome = run_real_strategy(
        data_root=args.data_root,
        output_root=args.output_root,
        runner_command=(str(args.runner.resolve()),),
        config=RealStrategyConfig(
            start=args.start,
            end=args.end,
            initial_cash_cny=args.initial_cash,
            top_per_industry=args.top_per_industry,
            top_industries_by_mean_score=args.top_industries_by_mean_score,
            industry_membership_policy=cast(
                Literal["historical_interval_required", "static_latest_experiment"],
                args.industry_membership_policy,
            ),
        ),
    )
    print(
        json.dumps(
            {
                "bundle_manifest": str(outcome.bundle.manifest_path),
                "report": str(outcome.bundle.report_path),
                "markdown_report": str(outcome.markdown_report_path),
                "summary": str(outcome.summary_path),
                "provider_calls": outcome.provider_calls_before_cache_probe,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
