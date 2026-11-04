# Signals

Convert factor observations from a data snapshot into deterministic runtime
signals. Signal generation has no access to cash, positions, market limits
or lot sizes.

For cross-sectional signals, rank by factor value and break ties by instrument
ID. Give unselected universe members a zero target so the rebalancer can exit
positions. Use eight-place decimal weights that sum to exactly one across the
selected instruments.

Implement `SignalGenerator.generate(context)`. Take the observation cutoff
and first eligible execution time from `SignalContext`. Schedule execution
after the observation cutoff. Each generator reads one factor ID/version.
Copy the snapshot identity and provenance into every output row and the
Arrow metadata.
