import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest
from trademaster.contracts import (
    account_state_hash_from_artifacts,
    run_artifact_schemas,
    validate_artifact_table,
    validate_run_artifacts,
)

TYPE_NAMES = {
    "utf8": "string",
    "timestamp_us_utc": "timestamp[us, tz=UTC]",
    "decimal128_38_8": "decimal128(38, 8)",
    "decimal128_38_0": "decimal128(38, 0)",
    "uint8": "uint8",
}


def test_python_artifact_schemas_match_shared_golden() -> None:
    golden = json.loads(Path("crates/tm-core/schemas/run-artifacts-v1.json").read_text())
    schemas = run_artifact_schemas()

    assert golden["schema_id"] == "trademaster.run-artifacts/v1"
    assert set(golden["records"]) == set(schemas)
    assert set(golden["sort_keys"]) == set(schemas)
    for record_name, fields in golden["records"].items():
        schema = schemas[record_name]
        assert [field["name"] for field in fields] == schema.names
        assert [TYPE_NAMES[field["type"]] for field in fields] == [str(field.type) for field in schema]
        assert [field["nullable"] for field in fields] == [field.nullable for field in schema]


def test_non_nullable_artifact_fields_reject_nulls() -> None:
    schema = run_artifact_schemas()["signals"]
    invalid = pa.Table.from_pylist([{}], schema=schema)

    with pytest.raises(ValueError, match="non-nullable"):
        validate_artifact_table("signals", invalid)


def test_artifact_enum_values_and_schema_identity_are_enforced() -> None:
    golden = json.loads(Path("crates/tm-core/schemas/run-artifacts-v1.json").read_text())
    assert golden["enums"]["side"] == ["buy", "sell"]
    schema = run_artifact_schemas()["orders"]
    assert schema.metadata[b"trademaster.schema_id"] == b"trademaster.run-artifacts/v1"

    valid = {
        field.name: _valid_value(field.type)
        for field in schema
    }
    valid["side"] = "not_a_side"
    table = pa.Table.from_pylist([valid], schema=schema)
    with pytest.raises(ValueError, match="enum"):
        validate_artifact_table("orders", table)


def test_artifact_replay_order_and_ledger_balance_are_enforced() -> None:
    nav_schema = run_artifact_schemas()["nav"]
    nav_rows = [
        {
            "run_id": "r1",
            "event_seq": Decimal(seq),
            "event_time": datetime(2025, 1, seq, tzinfo=UTC),
            "cash": Decimal(0),
            "market_value": Decimal(0),
            "net_asset_value": Decimal(0),
        }
        for seq in (2, 1)
    ]
    with pytest.raises(ValueError, match="canonical ordering"):
        validate_artifact_table("nav", pa.Table.from_pylist(nav_rows, schema=nav_schema))

    posting_schema = run_artifact_schemas()["ledger_postings"]
    posting = {
        "run_id": "r1",
        "event_seq": Decimal(1),
        "posting_seq": Decimal(1),
        "posting_id": "p1",
        "event_time": datetime(2025, 1, 1, tzinfo=UTC),
        "account": "cash",
        "debit_credit": "debit",
        "unit_type": "currency",
        "unit_id": "CNY",
        "raw_units": Decimal(100),
        "scale": 8,
        "source_id": "f1",
    }
    with pytest.raises(ValueError, match="balance"):
        validate_artifact_table(
            "ledger_postings", pa.Table.from_pylist([posting], schema=posting_schema)
        )


def test_shared_positive_and_time_constraints_are_enforced() -> None:
    schemas = run_artifact_schemas()
    fill = {
        field.name: _valid_value(field.type)
        for field in schemas["fills"]
    }
    fill.update(
        run_id="r1",
        event_seq=Decimal(1),
        order_seq=Decimal(1),
        fill_seq=Decimal(1),
        quantity=Decimal(100),
        price=Decimal(-1),
    )
    with pytest.raises(ValueError, match="positive"):
        validate_artifact_table(
            "fills", pa.Table.from_pylist([fill], schema=schemas["fills"])
        )

    signal = {
        field.name: _valid_value(field.type)
        for field in schemas["signals"]
    }
    signal.update(
        run_id="r1",
        event_seq=Decimal(1),
        signal_seq=Decimal(1),
        intent_type="quantity",
        snapshot_id="a" * 64,
        signal_time=datetime(2025, 1, 1, tzinfo=UTC),
        eligible_execution_time=datetime(2025, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="strict time"):
        validate_artifact_table(
            "signals", pa.Table.from_pylist([signal], schema=schemas["signals"])
        )


def test_completed_run_enforces_global_ids_and_order_lifecycle() -> None:
    schemas = run_artifact_schemas()
    signal = _artifact_row("signals", event_seq=1, signal_seq=1)
    signal.update(
        signal_id="s1",
        strategy_id="strategy",
        instrument_id="000001.SZ",
        signal_time=datetime(2025, 1, 1, tzinfo=UTC),
        eligible_execution_time=datetime(2025, 1, 2, tzinfo=UTC),
        intent_type="quantity",
        value=Decimal(100),
        reason="test",
        snapshot_id="a" * 64,
    )
    duplicate = signal | {"event_seq": Decimal(2), "signal_seq": Decimal(1)}
    with pytest.raises(ValueError, match="unique"):
        validate_artifact_table(
            "signals", pa.Table.from_pylist([signal, duplicate], schema=schemas["signals"])
        )

    order = _artifact_row("orders", event_seq=2, order_seq=1)
    order.update(
        order_id="o1",
        signal_id="s1",
        instrument_id="000001.SZ",
        side="buy",
        requested_quantity=Decimal(100),
        eligible_at=datetime(2025, 1, 2, tzinfo=UTC),
        submitted_at=datetime(2025, 1, 2, tzinfo=UTC),
    )
    accepted = _artifact_row("accepted_orders", event_seq=2, order_seq=1)
    accepted.update(
        order_id="o1", accepted_at=datetime(2025, 1, 2, tzinfo=UTC)
    )
    fill = _artifact_row("fills", event_seq=2, order_seq=1, fill_seq=1)
    fill.update(
        fill_id="f1",
        order_id="o1",
        event_time=datetime(2025, 1, 2, tzinfo=UTC),
        quantity=Decimal(100),
        price=Decimal(10),
        commission=Decimal(0),
        tax=Decimal(0),
        transfer_fee=Decimal(0),
        slippage=Decimal(0),
    )
    debit = _artifact_row("ledger_postings", event_seq=2, posting_seq=1)
    debit.update(
        posting_id="p1",
        event_time=datetime(2025, 1, 2, tzinfo=UTC),
        account="position",
        debit_credit="debit",
        unit_type="currency",
        unit_id="CNY",
        raw_units=Decimal(1000),
        scale=8,
        source_id="f1",
    )
    credit = debit | {
        "posting_seq": Decimal(2),
        "posting_id": "p2",
        "account": "cash",
        "debit_credit": "credit",
    }
    nav = _artifact_row("nav", event_seq=2)
    nav.update(
        event_time=datetime(2025, 1, 2, tzinfo=UTC),
        cash=Decimal(0),
        market_value=Decimal(0),
        net_asset_value=Decimal(0),
    )
    transition = _artifact_row("account_states", event_seq=2, transition_seq=1)
    transition.update(
        transition_id="t1",
        source_id="o1",
        event_time=datetime(2025, 1, 2, tzinfo=UTC),
        before_state_hash="a" * 64,
        after_state_hash=account_state_hash_from_artifacts(nav, [], []),
    )
    artifacts = {
        name: pa.Table.from_pylist([], schema=schema) for name, schema in schemas.items()
    }
    for name, rows in {
        "signals": [signal],
        "orders": [order],
        "accepted_orders": [accepted],
        "fills": [fill],
        "ledger_postings": [debit, credit],
        "nav": [nav],
        "account_states": [transition],
    }.items():
        artifacts[name] = pa.Table.from_pylist(rows, schema=schemas[name])
    validate_run_artifacts(artifacts)

    reversed_transition = transition | {
        "transition_seq": Decimal(2),
        "transition_id": "t2",
        "source_id": "settlement:backward",
        "event_time": datetime(2025, 1, 1, tzinfo=UTC),
        "before_state_hash": transition["after_state_hash"],
    }
    reversed_time = dict(artifacts)
    reversed_time["account_states"] = pa.Table.from_pylist(
        [transition, reversed_transition], schema=schemas["account_states"]
    )
    with pytest.raises(ValueError, match="hash chain"):
        validate_run_artifacts(reversed_time)

    missing_ledger = dict(artifacts)
    missing_ledger["ledger_postings"] = pa.Table.from_pylist(
        [], schema=schemas["ledger_postings"]
    )
    with pytest.raises(ValueError, match="ledger"):
        validate_run_artifacts(missing_ledger)

    artifacts["fills"] = pa.Table.from_pylist(
        [fill | {"quantity": Decimal(99)}], schema=schemas["fills"]
    )
    with pytest.raises(ValueError, match="expiry"):
        validate_run_artifacts(artifacts)


def _artifact_row(record_name: str, **sequences: int) -> dict[str, object]:
    schema = run_artifact_schemas()[record_name]
    row = {field.name: _valid_value(field.type) for field in schema}
    row["run_id"] = "r1"
    row.update({name: Decimal(value) for name, value in sequences.items()})
    return row


def _valid_value(data_type: pa.DataType) -> object:
    if pa.types.is_string(data_type):
        return "x"
    if pa.types.is_timestamp(data_type):
        return datetime(2025, 1, 1, tzinfo=UTC)
    if pa.types.is_decimal(data_type):
        return Decimal(0)
    if pa.types.is_uint8(data_type):
        return 0
    raise AssertionError(f"unsupported test type: {data_type}")
