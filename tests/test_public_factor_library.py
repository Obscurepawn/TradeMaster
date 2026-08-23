from __future__ import annotations

from collections import Counter
from pathlib import Path

import duckdb
import pytest
from trademaster.factors.builtin import builtin_managed_factor_registry
from trademaster.factors.public_cn import builtin_public_factor_library
from trademaster.factors.public_library import (
    FactorCandidateRecord,
    PublicFactorIntegrityError,
    PublicFactorLibrary,
    PublicFactorStore,
)


def test_builtin_public_factor_library_has_complete_stable_collections() -> None:
    library = builtin_public_factor_library()

    huatai = library.collection("huatai-53-2020.06.02")
    assert huatai.expected_count == len(huatai.member_ids) == 53
    assert Counter(library.candidate(item).category for item in huatai.member_ids) == {
        "beta": 1,
        "growth": 4,
        "leverage": 5,
        "momentum_reversal": 13,
        "quality": 12,
        "size": 1,
        "turnover": 4,
        "value": 8,
        "volatility": 5,
    }
    assert Counter(library.candidate(item).data_availability for item in huatai.member_ids) == {
        "current_cache_partial": 14,
        "provider_extension": 36,
        "benchmark_contract_required": 3,
    }

    gtja = library.collection("gtja-alpha191-2017.06.15")
    assert gtja.member_ids == tuple(f"gtja191.alpha{number:03d}" for number in range(1, 192))
    assert Counter(library.candidate(item).data_availability for item in gtja.member_ids) == {
        "current_cache_partial": 146,
        "derived_from_current": 40,
        "benchmark_contract_required": 4,
        "blocked_data": 1,
    }
    assert Counter(library.candidate(item).implementation_status for item in gtja.member_ids) == {
        "implemented": 10,
        "blocked_semantics": 180,
        "blocked_data": 1,
    }

    assert library.collection("huatai-moneyflow-50-2018.05.17").expected_count == 50
    assert library.collection("huatai-financial-quality-51-2018.05.25").expected_count == 51
    assert library.collection("huatai-consensus-19-2018.12.14").expected_count == 19
    historical = library.collection("huatai-historical-quantile-2019.10.15")
    assert historical.expected_count == 86
    assert historical.reported_count == 89
    assert library.collection("huatai-risk-model-2019.06.12").expected_count == 10


def test_public_factor_candidates_record_formula_data_and_local_variant_boundaries() -> None:
    library = builtin_public_factor_library()

    ep = library.candidate("huatai53.value.ep")
    assert ep.formula_expression == "1 / pe_ttm"
    assert ep.implementation_status == "implemented_variant"
    assert ep.implementation_identity == "fundamental.earnings_yield@1"
    assert "negative PE" in " ".join(ep.semantic_differences)

    ocf_ratio = library.candidate("huatai53.quality.ocf_to_net_profit_ytd")
    assert ocf_ratio.implementation_identity is None
    assert ocf_ratio.formula_expression is not None
    assert "net_profit" in ocf_ratio.formula_expression
    assert "ocf_to_or" in " ".join(ocf_ratio.semantic_differences)

    alpha14 = library.candidate("gtja191.alpha014")
    assert alpha14.formula_expression == "close - delay(close, 5)"
    assert alpha14.required_inputs == (
        "adj_factors.adj_factor",
        "daily_bars.close",
    )
    assert alpha14.implementation_status == "implemented"
    assert alpha14.implementation_identity == "gtja191.alpha014@1"

    alpha30 = library.candidate("gtja191.alpha030")
    assert alpha30.data_availability == "blocked_data"
    assert {
        "factor_returns.hml",
        "factor_returns.mkt",
        "factor_returns.smb",
    } <= set(alpha30.required_inputs)

    alpha119 = library.candidate("gtja191.alpha119")
    assert alpha119.data_availability == "derived_from_current"
    assert "derived_market.vwap_cny_per_share" in alpha119.required_inputs


def test_public_factor_store_persists_content_addressed_json_and_duckdb_discovery(
    tmp_path: Path,
) -> None:
    library = builtin_public_factor_library()
    registry = builtin_managed_factor_registry()
    with PublicFactorStore(tmp_path) as store:
        store.sync(library, managed_registry=registry)
        assert store.source_count() == len(library.sources)
        assert store.collection_count() == len(library.collections)
        assert store.candidate_count() == len(library.candidates)
        loaded = store.load(managed_registry=registry)
        assert (
            len(
                store.candidate_ids(
                    collection_id="gtja-alpha191-2017.06.15",
                    managed_registry=registry,
                    library=loaded,
                )
            )
            == 191
        )
        assert store.candidate_ids(
            collection_id="gtja-alpha191-2017.06.15",
            implementation_status="implemented",
            managed_registry=registry,
            library=loaded,
        ) == (
            "gtja191.alpha014",
            "gtja191.alpha015",
            "gtja191.alpha018",
            "gtja191.alpha020",
            "gtja191.alpha031",
            "gtja191.alpha034",
            "gtja191.alpha046",
            "gtja191.alpha053",
            "gtja191.alpha058",
            "gtja191.alpha088",
        )
        assert "gtja191.alpha014" in store.candidate_ids(
            dependency="daily_bars.close",
            managed_registry=registry,
            library=loaded,
        )
        assert loaded.library_sha256 == library.library_sha256

        alpha14_path = store.candidate_path("gtja191.alpha014")

    alpha14_path.write_text("{}", encoding="utf-8")
    with (
        PublicFactorStore(tmp_path) as store,
        pytest.raises(PublicFactorIntegrityError, match="hash mismatch"),
    ):
        store.load(managed_registry=registry)


def test_public_factor_store_rejects_changed_content_under_same_candidate_identity(
    tmp_path: Path,
) -> None:
    library = builtin_public_factor_library()
    registry = builtin_managed_factor_registry()
    original = library.candidate("huatai53.value.ep")
    values = original.model_dump(mode="python", exclude={"candidate_sha256"})
    values["semantic_differences"] = tuple(
        sorted((*original.semantic_differences, "unauthorized silent change"))
    )
    changed = FactorCandidateRecord.build(**values)
    conflicting = PublicFactorLibrary(
        sources=library.sources,
        collections=library.collections,
        candidates=tuple(
            changed if item.candidate_id == changed.candidate_id else item
            for item in library.candidates
        ),
    )

    store = PublicFactorStore(tmp_path)
    try:
        store.sync(library, managed_registry=registry)
        with pytest.raises(PublicFactorIntegrityError, match="identity (collision|conflicts)"):
            store.sync(conflicting, managed_registry=registry)
    finally:
        store.close()


def test_public_factor_store_fails_closed_on_catalog_membership_or_discovery_drift(
    tmp_path: Path,
) -> None:
    library = builtin_public_factor_library()
    registry = builtin_managed_factor_registry()
    with PublicFactorStore(tmp_path) as store:
        store.sync(library, managed_registry=registry)

    catalog_path = tmp_path / "library/catalog.duckdb"
    with duckdb.connect(str(catalog_path)) as connection:
        connection.execute("DELETE FROM factor_candidates WHERE candidate_id = 'gtja191.alpha001'")
    with (
        PublicFactorStore(tmp_path) as store,
        pytest.raises(PublicFactorIntegrityError, match="catalog"),
    ):
        store.load(managed_registry=registry)

    with PublicFactorStore(tmp_path) as store:
        store.sync(library, managed_registry=registry)
    with duckdb.connect(str(catalog_path)) as connection:
        connection.execute(
            """
            UPDATE factor_candidates SET implementation_status = 'ready'
            WHERE candidate_id = 'gtja191.alpha014'
            """
        )
    with (
        PublicFactorStore(tmp_path) as store,
        pytest.raises(PublicFactorIntegrityError, match="catalog"),
    ):
        store.load(managed_registry=registry)


def test_implemented_candidates_require_formula_and_registered_definition(
    tmp_path: Path,
) -> None:
    library = builtin_public_factor_library()
    original = library.candidate("gtja191.alpha014")
    without_formula = original.model_dump(mode="python", exclude={"candidate_sha256"})
    without_formula["formula_expression"] = None
    with pytest.raises(ValueError, match="formula"):
        FactorCandidateRecord.build(**without_formula)

    missing = original.model_dump(mode="python", exclude={"candidate_sha256"})
    missing["implementation_identity"] = "missing.factor@1"
    changed = FactorCandidateRecord.build(**missing)
    conflicting = PublicFactorLibrary(
        sources=library.sources,
        collections=library.collections,
        candidates=tuple(
            changed if item.candidate_id == changed.candidate_id else item
            for item in library.candidates
        ),
    )
    with (
        PublicFactorStore(tmp_path) as store,
        pytest.raises(ValueError, match="unknown factor"),
    ):
        store.sync(conflicting, managed_registry=builtin_managed_factor_registry())
