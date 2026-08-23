# Rust Runner Guide

This crate is the strict process boundary between Python orchestration and the authoritative
`tm-engine` runtime. It may parse canonical JSON, construct checked `tm-core` values, run the
existing `EventLoop`, and serialize an audit summary. It must not duplicate validation, execution,
fee, settlement, or ledger rules.

Reject unknown fields, non-canonical scaled integers, invalid RFC3339 UTC timestamps, identity
drift, and incomplete calendars before execution. Never put credentials or host paths in output.
