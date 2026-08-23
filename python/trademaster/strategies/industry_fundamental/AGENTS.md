# Industry Fundamental Strategy Guide

This is the stable public package for the configurable industry fundamental strategy.
Top1, Top5, and Top-industry variants are configuration values, never type or package
identities. Factor calculation belongs to `trademaster.factors`; this package owns only
candidate dimensions, industry selection, target weights, run orchestration, and reports.

Formal runs bind managed factor definitions, materializations, their source snapshots,
strategy-definition hash, signals, Rust results, summary, and Markdown/HTML reports.
The legacy `industry_fundamental_top5` package is a compatibility implementation path.
