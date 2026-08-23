from trademaster.contracts import (
    BacktestConfig,
    DataPortal,
    EventStrategy,
    Factor,
    SignalGenerator,
    run_artifact_schemas,
)


def test_public_protocols_and_artifact_schemas_exist() -> None:
    assert DataPortal is not None
    assert Factor is not None
    assert SignalGenerator is not None
    assert EventStrategy is not None
    assert BacktestConfig.model_fields

    schemas = run_artifact_schemas()
    assert set(schemas) == {
        "signals",
        "orders",
        "accepted_orders",
        "fills",
        "rejections",
        "expiries",
        "ledger_postings",
        "account_states",
        "position_lots",
        "positions",
        "nav",
    }
