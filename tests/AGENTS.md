# Test Guide

Tests assert public behavior and stable records. Prefer real temporary
DuckDB/Parquet/Arrow collaborators. Mock only external, slow, destructive, or
nondeterministic boundaries. Every test has one primary reason to fail.

Credential-gated tests must report skip separately and never count as network
validation.
