from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from samplelaw.cli import main
from tests.helpers import BAD, EXAMPLES, GOLD

def _verify_args(sample: Path) -> list[str]:
    return [
        "verify",
        "--plan",
        str(EXAMPLES / "plans" / "plan_basic.yaml"),
        "--source",
        str(EXAMPLES / "data" / "demo_members.csv"),
        "--sample",
        str(sample),
        "--format",
        "json",
    ]

def test_version_and_verify_success(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--version"])
    assert captured.value.code == 0
    capsys.readouterr()
    assert main(_verify_args(GOLD / "plan_basic_sample.csv")) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["replay"]["status"] == "MATCH"

def test_verify_diverged_returns_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_verify_args(BAD / "sample_mutated.csv")) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["replay"]["status"] == "DIVERGED"

def test_missing_ledger_and_run_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", "--ledger", str(tmp_path / "missing.sqlite")]) == 2
    assert "LEDGER-004" in capsys.readouterr().err
    shutil.copytree(EXAMPLES, tmp_path / "examples")
    config = tmp_path / "examples" / "pipeline.yaml"
    assert (
        main(
            [
                "run",
                "--config",
                str(config),
                "--json",
                "--generated-at",
                "2026-11-14T00:00:00Z",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["overall_status"] == "WARN"
    assert len(summary["jobs"]) == 3
    ledger = tmp_path / "out" / "ledger.sqlite"
    assert ledger.is_file()
    assert main(["inspect", "--ledger", str(ledger), "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 3
    assert all(row["replay_status"] == "MATCH" for row in rows)
