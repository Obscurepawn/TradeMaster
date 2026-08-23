# Signal Layer Guide

This module converts snapshot-bound factor observations into deterministic runtime
signal records. It does not inspect cash, positions, market limits, or lots.

Cross-sectional target-weight signals include zero targets for non-selected members
so a later portfolio rebalancer can express exits. Ranking is stable by factor value
and then instrument ID. Decimal weights use eight places and must sum exactly to one
across selected instruments.

The signal cutoff and strictly later eligible execution time come from `SignalContext`;
generators implement the public one-argument `SignalGenerator.generate(context)` contract.
A T-close signal may not execute at that close. Snapshot identity and provenance are
copied into every output row and Arrow metadata. Each generator binds one exact factor
ID/version and never mixes observations from multiple factors.
