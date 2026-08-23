# Strategy Layer Guide

Strategy packages compose snapshot-bound managed factors/signals, the Rust runtime, and reports.
They own selection and portfolio intent, but reusable factor formulas, transforms, versions and
evaluation belong to `trademaster.factors`. Strategies must not implement an independent cash,
fill, position, fee, or settlement engine in Python.

Every real strategy run persists its full configuration hash, candidate/factor/selection snapshot
identities, runner request/result, summary and reports in a verifiable bundle. Tushare access is
cache-first and credentials remain external.
