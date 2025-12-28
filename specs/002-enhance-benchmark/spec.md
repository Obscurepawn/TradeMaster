# Feature Specification: Enhance Baseline Support

**Feature Branch**: `002-enhance-benchmark`
**Created**: 2025-12-28
**Status**: Draft

## User Scenarios & Testing

### User Story 1 - Multiple Baseline Comparison (Priority: P1)

The developer wants to compare their strategy against multiple market indices (e.g., HS300 and CSI500) simultaneously to better understand relative performance across different market segments.

**Acceptance Scenarios**:
1. **Given** a YAML config with multiple entries in the `baseline` field, **When** the backtest is run, **Then** the system fetches data for all specified baselines.
2. **Given** a successful backtest run with multiple baselines, **When** the result chart is generated, **Then** each baseline has its own labeled yield curve on the same plot as the strategy.

## Requirements

### Functional Requirements
- **FR-001**: Rename `benchmark` parameter to `baseline` in YAML configuration.
- **FR-002**: System MUST support `baseline` as either a single string or a list of strings (ticker symbols).
- **FR-003**: System MUST fetch historical daily data for all specified baselines via the abstraction layer.
- **FR-004**: System MUST calculate daily yield for each baseline relative to the backtest start date.
- **FR-005**: Result visualization (Matplotlib) MUST overlay yield curves for all baselines on the strategy equity curve.

## Success Criteria
- **SC-001**: A config file using `baseline: [sh000300, sh000905]` runs successfully.
- **SC-002**: The generated PNG chart shows the strategy curve plus one curve for each specified baseline.
