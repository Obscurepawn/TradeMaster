from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_factor_cli_lists_describes_and_plans_builtin_factors() -> None:
    listed = subprocess.run(
        [sys.executable, "-m", "trademaster.factors", "list"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["schema_id"] == "trademaster.factor-list/v1"
    assert len(payload["factors"]) == 22

    identity = "fundamental.composite.industry_relative@1"
    described = subprocess.run(
        [sys.executable, "-m", "trademaster.factors", "describe", identity],
        capture_output=True,
        check=False,
        text=True,
    )
    assert described.returncode == 0, described.stderr
    definition = json.loads(described.stdout)
    assert definition["factor_id"] == "fundamental.composite.industry_relative"
    assert len(definition["definition_sha256"]) == 64

    graphed = subprocess.run(
        [sys.executable, "-m", "trademaster.factors", "graph", identity],
        capture_output=True,
        check=False,
        text=True,
    )
    assert graphed.returncode == 0, graphed.stderr
    graph = json.loads(graphed.stdout)
    assert len(graph["plan"]) == 10
    assert graph["plan"][-1] == identity


def test_factor_cli_syncs_and_queries_public_factor_library(tmp_path: Path) -> None:
    root_args = ["--root", str(tmp_path)]
    synced = subprocess.run(
        [sys.executable, "-m", "trademaster.factors", *root_args, "library-sync"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert synced.returncode == 0, synced.stderr
    sync_payload = json.loads(synced.stdout)
    assert sync_payload["collections"] == 7
    assert sync_payload["candidates"] == 460

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trademaster.factors",
            *root_args,
            "library-list",
            "--collection",
            "gtja-alpha191-2017.06.15",
            "--status",
            "implemented",
            "--dependency",
            "daily_bars.close",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["schema_id"] == "trademaster.public-factor-list/v1"
    assert len(payload["factors"]) == 10

    described = subprocess.run(
        [
            sys.executable,
            "-m",
            "trademaster.factors",
            *root_args,
            "library-describe",
            "huatai53.value.ep",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert described.returncode == 0, described.stderr
    assert json.loads(described.stdout)["implementation_status"] == "implemented_variant"

    status = subprocess.run(
        [sys.executable, "-m", "trademaster.factors", *root_args, "library-status"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    status_payload = json.loads(status.stdout)
    assert status_payload["schema_id"] == "trademaster.public-factor-status/v1"
    assert status_payload["total_candidates"] == 460
