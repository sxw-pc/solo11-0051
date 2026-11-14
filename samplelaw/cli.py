"""Command-line entry point for run, verify, inspect and optional preview."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from samplelaw import __version__
from samplelaw.config.loader import load_plan
from samplelaw.config.schema import SourceSpec
from samplelaw.ledger.sqlite_ledger import SQLiteLedger
from samplelaw.report.writer import render_report
from samplelaw.runner import load_source, pipeline_summary, run_pipeline, verify_sample
from samplelaw.source.csv_reader import read_csv_dataset
from samplelaw.util.errors import LedgerError, SamplelawError
from samplelaw.util.logging import configure_logging

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="samplelaw",
        description="Offline sampling-plan legality, replay and representativeness checks.",
    )
    parser.add_argument("--version", action="version", version=f"samplelaw {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run every job in a pipeline YAML")
    run.add_argument("--config", required=True, help="path to pipeline.yaml")
    run.add_argument("--generated-at", help="fixed ISO-8601 report timestamp")
    run.add_argument("--json", action="store_true", help="print a JSON run summary")
    verify = sub.add_parser("verify", help="replay and cover-check one persisted sample")
    verify.add_argument("--plan", required=True)
    verify.add_argument("--source", required=True)
    verify.add_argument("--sample", required=True)
    verify.add_argument("--source-type", choices=["csv", "sqlite"], default=None)
    verify.add_argument("--table", default=None, help="SQLite source table")
    verify.add_argument("--encoding", default=None, help="CSV source encoding")
    verify.add_argument("--format", choices=["markdown", "json", "html"], default="markdown")
    verify.add_argument("--output", default=None)
    verify.add_argument("--generated-at", help="fixed ISO-8601 report timestamp")
    inspect = sub.add_parser("inspect", help="query the local SQLite ledger")
    inspect.add_argument("--ledger", default="out/ledger.sqlite")
    inspect.add_argument("--plan-fingerprint", default=None)
    inspect.add_argument("--batch-id", default=None)
    inspect.add_argument("--limit", type=int, default=20)
    inspect.add_argument("--format", choices=["table", "json"], default="table")
    preview = sub.add_parser("preview", help="optionally serve generated reports locally")
    preview.add_argument("--dir", default="out/reports")
    preview.add_argument("--port", type=int, default=8765)
    return parser

def _source_type(args: argparse.Namespace) -> str:
    if args.source_type:
        return args.source_type
    return "sqlite" if Path(args.source).suffix.lower() in {".sqlite", ".db"} else "csv"

def _load_source_args(args: argparse.Namespace) -> object:
    return load_source(
        SourceSpec(
            kind=_source_type(args),
            path=Path(args.source).resolve(),
            table=args.table,
            encoding=args.encoding,
            name=Path(args.source).stem,
        )
    )

def _run(args: argparse.Namespace) -> int:
    result = run_pipeline(args.config, generated_at=args.generated_at)
    summary = pipeline_summary(result)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"samplelaw run: {summary['overall_status']} ({result.generated_at})")
        for job in summary["jobs"]:
            duplicate = " duplicate" if job["duplicate_batch"] else ""
            print(
                f"  [{job['overall_status']}] {job['id']}: replay={job['replay_status']} "
                f"coverage={job['coverage_status']} deviance={job['deviance_status']} "
                f"rows={job['sample_rows']} batch={job['batch_id']}{duplicate}"
            )
            print(f"    sample: {job['sample']}")
            for fmt, path in job["reports"].items():
                print(f"    {fmt}: {path}")
        print(f"  ledger: {result.ledger_path}")
    return 1 if summary["overall_status"] == "FAIL" else 0

def _verify(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    source = _load_source_args(args)
    sample = read_csv_dataset(args.sample, name=Path(args.sample).stem)
    _, replay, coverage, _, document = verify_sample(plan, source, sample, generated_at=args.generated_at)
    content = render_report(document, args.format)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content.encode("utf-8"))
    else:
        sys.stdout.write(content)
    if replay.status == "DIVERGED" or coverage.status == "FAIL" or document.overall_status == "FAIL":
        return 2
    if replay.status != "MATCH" or coverage.status == "WARN" or document.overall_status == "WARN":
        return 1
    return 0

def _inspect(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    if not ledger_path.is_file():
        raise LedgerError("LEDGER-004", f"ledger not found: {ledger_path}", path=str(ledger_path))
    with SQLiteLedger(ledger_path) as ledger:
        if args.batch_id:
            batch = ledger.get_batch(args.batch_id)
            batches = [batch] if batch else []
        else:
            batches = ledger.query_batches(plan_fingerprint=args.plan_fingerprint, limit=args.limit)
    if args.format == "json":
        print(json.dumps([batch.to_dict() for batch in batches], ensure_ascii=False, indent=2))
    else:
        print("batch_id\tplan_name\tstatus\treplay\tcoverage\tdeviance\trows\tcreated_at")
        for batch in batches:
            print(
                f"{batch.batch_id}\t{batch.plan_name}\t{batch.overall_status}\t"
                f"{batch.replay_status}\t{batch.coverage_status}\t"
                f"{batch.deviance_status}\t{batch.sample_count}\t{batch.created_at}"
            )
    return 0 if batches else 2

def _preview(args: argparse.Namespace) -> int:
    from samplelaw.preview import run_preview
    run_preview(args.dir, port=args.port)
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "verify":
            return _verify(args)
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "preview":
            return _preview(args)
        parser.error(f"unknown command: {args.command}")
    except (SamplelawError, ValueError, OSError) as exc:
        print(f"samplelaw: {exc}", file=sys.stderr)
        return 2
    return 2
