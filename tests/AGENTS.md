# Test Guide

Test public behavior and the records callers rely on. Prefer temporary DuckDB
databases, real Parquet files, and Arrow tables. Use mocks only for external,
slow, destructive, or nondeterministic operations. Give each test one primary
reason to fail.

When missing credentials prevent a network test from running, report it as
skipped and exclude it from the count of verified network tests.
