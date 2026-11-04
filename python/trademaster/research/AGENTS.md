# Research pipeline

Use the existing provider interfaces to plan and resume bulk downloads, build
point-in-time factor panels and labels, run diagnostics and produce reports.

## Downloads

Keep download plans immutable and address them by content hash. Parquet holds
provider responses. DuckDB tracks tasks and indexes objects. Verify coverage
separately from API success. Track the historical universe, each endpoint's
rules for absent rows, provider permissions and limits on historical data as
distinct states.

For a completed task, match the request SHA against both the evidence record
and the request metadata in Parquet. A worker can fetch once it holds the
task's lease and can commit only while it still holds that lease. Builders read
`completed_evidence_snapshot`. Keep the DuckDB task tables private.

## Panels and evaluation

Date-only financial announcements become available in the next eligible
session. If a revision has no publication time, never backfill its values to
the original announcement date. Keep rows for missing observations and
suspensions in dense-session windows. A factor value or forward return marked
`is_valid` must be finite.

For a feature computed at the close, the forward-return entry must be later
than that close. Record the factor definition, source objects, universe policy,
label alignment and configuration for each evaluation.
