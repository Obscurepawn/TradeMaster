"""Thin CLI over the managed factor registry, store, graph, and evaluator."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pyarrow.parquet as pq

from trademaster.contracts import (
    FactorContext,
    SnapshotManifest,
    bind_snapshot_provenance,
)

from .builtin import builtin_managed_factor_registry
from .evaluation import (
    EvaluationLabelContext,
    FactorEvaluationConfig,
    FactorEvaluationStore,
    FactorEvaluator,
)
from .public_cn import builtin_public_factor_library
from .public_library import PublicFactorStore
from .storage import FactorManager, FactorScope


def _identity(value: str) -> tuple[str, str]:
    try:
        factor_id, version = value.rsplit("@", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("factor identity must be ID@VERSION") from error
    if not factor_id or not version:
        raise argparse.ArgumentTypeError("factor identity must be ID@VERSION")
    return factor_id, version


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("time must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("time must include UTC offset")
    return parsed.astimezone(UTC)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser(description="TradeMaster managed factor operations")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".trademaster/factors"),
        help="factor store root",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list built-in managed factor definitions")
    describe = commands.add_parser("describe", help="show one factor definition")
    describe.add_argument("identity", type=_identity)
    graph = commands.add_parser("graph", help="show stable dependency order")
    graph.add_argument("identity", type=_identity)
    verify = commands.add_parser("verify", help="verify one persisted materialization")
    verify.add_argument("materialization_id")

    commands.add_parser("library-sync", help="persist the built-in public factor catalog")
    library_list = commands.add_parser(
        "library-list", help="query persisted public factor candidates"
    )
    library_list.add_argument("--collection")
    library_list.add_argument("--category")
    library_list.add_argument("--status")
    library_list.add_argument("--data-availability")
    library_list.add_argument("--dependency")
    library_describe = commands.add_parser(
        "library-describe", help="show one persisted public factor candidate"
    )
    library_describe.add_argument("candidate_id")
    commands.add_parser("library-status", help="summarize persisted public factor readiness")

    materialize = commands.add_parser("materialize", help="materialize one factor")
    materialize.add_argument("identity", type=_identity)
    materialize.add_argument("--input-parquet", type=Path, required=True)
    materialize.add_argument("--snapshot-manifest", type=Path, required=True)
    materialize.add_argument("--start", type=_time, required=True)
    materialize.add_argument("--end", type=_time, required=True)
    materialize.add_argument("--as-of", type=_time, required=True)
    materialize.add_argument("--coverage-key", action="append", default=[])
    materialize.add_argument("--parent-materialization", action="append", default=[])

    evaluate = commands.add_parser("evaluate", help="evaluate one factor materialization")
    evaluate.add_argument("materialization_id")
    evaluate.add_argument("--forward-returns", type=Path, required=True)
    evaluate.add_argument("--label-snapshot", type=Path, required=True)
    evaluate.add_argument("--eligible-entry-times", type=Path, required=True)
    evaluate.add_argument("--horizons", required=True)
    evaluate.add_argument("--quantiles", type=int, default=5)
    evaluate.add_argument("--minimum-observations", type=int, default=20)
    evaluate.add_argument(
        "--return-alignment",
        choices=("next_eligible_open", "next_eligible_close"),
        default="next_eligible_close",
    )
    args = parser.parse_args()
    registry = builtin_managed_factor_registry()

    if args.command == "library-sync":
        library = builtin_public_factor_library()
        with PublicFactorStore(args.root) as store:
            store.sync(library, managed_registry=registry)
            _print(
                {
                    "schema_id": "trademaster.public-factor-sync/v1",
                    "library_sha256": library.library_sha256,
                    "sources": store.source_count(),
                    "collections": store.collection_count(),
                    "candidates": store.candidate_count(),
                }
            )
        return
    if args.command in {"library-list", "library-describe", "library-status"}:
        with PublicFactorStore(args.root) as store:
            library = store.load(managed_registry=registry)
            if args.command == "library-list":
                identities = store.candidate_ids(
                    collection_id=args.collection,
                    category=args.category,
                    implementation_status=args.status,
                    data_availability=args.data_availability,
                    dependency=args.dependency,
                    managed_registry=registry,
                    library=library,
                )
                _print(
                    {
                        "schema_id": "trademaster.public-factor-list/v1",
                        "library_sha256": library.library_sha256,
                        "factors": [
                            {
                                "candidate_id": item.candidate_id,
                                "collection_id": item.collection_id,
                                "category": item.category,
                                "implementation_status": item.implementation_status,
                                "data_availability": item.data_availability,
                                "implementation_identity": item.implementation_identity,
                            }
                            for identity in identities
                            for item in (library.candidate(identity),)
                        ],
                    }
                )
            elif args.command == "library-describe":
                _print(library.candidate(args.candidate_id).model_dump(mode="json"))
            else:
                _print(
                    {
                        "schema_id": "trademaster.public-factor-status/v1",
                        "library_sha256": library.library_sha256,
                        "total_candidates": len(library.candidates),
                        "by_collection": dict(
                            sorted(
                                Counter(item.collection_id for item in library.candidates).items()
                            )
                        ),
                        "by_implementation_status": dict(
                            sorted(
                                Counter(
                                    item.implementation_status for item in library.candidates
                                ).items()
                            )
                        ),
                        "by_data_availability": dict(
                            sorted(
                                Counter(
                                    item.data_availability for item in library.candidates
                                ).items()
                            )
                        ),
                    }
                )
        return

    if args.command == "list":
        _print(
            {
                "schema_id": "trademaster.factor-list/v1",
                "factors": [
                    item.definition.model_dump(mode="json")
                    for identity in registry.identities
                    for item in (registry.get(*identity),)
                ],
            }
        )
        return
    if args.command in {"describe", "graph"}:
        factor_id, version = args.identity
        if args.command == "describe":
            _print(registry.get(factor_id, version).definition.model_dump(mode="json"))
        else:
            _print(
                {
                    "schema_id": "trademaster.factor-graph/v1",
                    "target": f"{factor_id}@{version}",
                    "plan": [
                        f"{item.definition.factor_id}@{item.definition.version}"
                        for item in registry.plan(factor_id, version)
                    ],
                }
            )
        return

    with FactorManager(
        root=args.root,
        registry=registry,
        clock=lambda: datetime.now(UTC),
    ) as manager:
        if args.command == "verify":
            artifact = manager.load(args.materialization_id)
            _print(
                {
                    "schema_id": "trademaster.factor-verify/v1",
                    "materialization_id": artifact.manifest.materialization_id,
                    "output_sha256": artifact.manifest.output_sha256,
                    "row_count": artifact.manifest.output_row_count,
                }
            )
            return
        if args.command == "materialize":
            factor_id, version = args.identity
            snapshot = SnapshotManifest.model_validate_json(
                args.snapshot_manifest.read_bytes(), strict=True
            )
            inputs = bind_snapshot_provenance(pq.read_table(args.input_parquet), snapshot)
            raw_instruments = inputs["instrument_id"].to_pylist()
            if any(not isinstance(value, str) or not value for value in raw_instruments):
                raise ValueError("factor input instruments must be nonempty strings")
            instruments = tuple(sorted(set(cast(list[str], raw_instruments))))
            artifact = manager.materialize(
                factor_id,
                version,
                context=FactorContext(
                    as_of=args.as_of,
                    snapshot=snapshot,
                    inputs=inputs,
                ),
                scope=FactorScope(
                    start=args.start,
                    end=args.end,
                    as_of=args.as_of,
                    instruments=instruments,
                    coverage_keys=tuple(sorted(set(args.coverage_key))),
                ),
                parents=tuple(manager.load(value) for value in args.parent_materialization),
            )
            _print(artifact.manifest.model_dump(mode="json"))
            return
        if args.command == "evaluate":
            horizons = tuple(sorted({int(value) for value in args.horizons.split(",") if value}))
            artifact = manager.load(args.materialization_id)
            label_snapshot = SnapshotManifest.model_validate_json(
                args.label_snapshot.read_bytes(), strict=True
            )
            raw_entry_times = json.loads(args.eligible_entry_times.read_text(encoding="utf-8"))
            if not isinstance(raw_entry_times, list) or not all(
                isinstance(value, str) for value in raw_entry_times
            ):
                raise ValueError("eligible entry times must be a JSON string list")
            label_context = EvaluationLabelContext(
                label_snapshot=label_snapshot,
                eligible_entry_times=tuple(_time(value) for value in raw_entry_times),
            )
            result = FactorEvaluator().evaluate(
                artifact,
                bind_snapshot_provenance(pq.read_table(args.forward_returns), label_snapshot),
                config=FactorEvaluationConfig(
                    horizons=horizons,
                    quantiles=args.quantiles,
                    minimum_observations=args.minimum_observations,
                    return_alignment=args.return_alignment,
                ),
                label_context=label_context,
            )
            manifest = FactorEvaluationStore(
                root=args.root,
                catalog=manager.catalog,
                clock=lambda: datetime.now(UTC),
            ).persist(result)
            _print(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "summary": result.summary.model_dump(mode="json"),
                }
            )


if __name__ == "__main__":
    main()
