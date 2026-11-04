# Rust Runner Guide

This library reads saved event and snapshot requests for the Python extension.
Parse canonical event JSON, construct checked domain values, run the event loop
and encode the audit records. For a snapshot request, resolve its market path
relative to the request file and pass the verified data to the daily engine.
The engine owns sizing, execution, fees, settlement and accounting.

For event requests, validate the field set, canonical scaled integers, UTC
timestamps, identifiers and calendar before entering the engine. Snapshot
requests carry ISO session dates and integer amounts. Verify the market hash
and check the manifest when the request carries a snapshot ID. Keep
credentials and host paths out of run results.
