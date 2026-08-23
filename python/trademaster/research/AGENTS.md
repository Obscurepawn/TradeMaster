# Research Pipeline Guide

This module owns reproducible research orchestration over already isolated data
providers. It may plan and resume bulk downloads, assemble PIT-safe factor panels,
build labels, run factor diagnostics, and produce research reports.

Bulk download plans are immutable and content-addressed. Parquet provider responses
remain authoritative; DuckDB stores task progress and discovery only. A successful
API call is not a coverage proof. Historical universe, endpoint-specific absence,
provider permissions, and source-history limitations must remain separate states.
Every completed object must bind the plan task's canonical request SHA to both the
evidence record and embedded Parquet request metadata. Workers claim tasks through an
owner/expiry lease; never fetch after a failed claim or commit after losing the lease.
Builders consume `completed_evidence_snapshot`, not private DuckDB task tables.

Research factor panels never mutate execution or ledger state. Close-derived
features cannot use a same-close forward-return entry. Every evaluation binds the
factor definition, source objects, universe policy, label alignment, and config.
Date-only financial announcements become available on the next eligible session;
when revision publication time is absent, revised rows cannot be backfilled to the
original announcement date. Dense-session factor windows retain null/suspended rows,
and `is_valid` always implies a finite factor value or forward return.
