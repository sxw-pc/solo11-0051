"""Unit tests for staged-sample fingerprint verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from samplelaw.plan.sampling import execute_sampling
from samplelaw.runner import verify_persisted_sample
from samplelaw.source.csv_reader import read_csv_dataset, staging_csv_path, write_staged_csv
from samplelaw.util.errors import DataError, SamplingError
from tests.helpers import make_plan, simple_dataset


def _stage(tmp_path: Path, columns, rows, *, job_id: str = "unit-job"):  # type: ignore[no-untyped-def]
    final = tmp_path / "samples" / f"{job_id}.csv"
    staging = staging_csv_path(final)
    write_staged_csv(staging, columns, rows)
    return staging, read_csv_dataset(staging, name=f"{job_id}-sample")


def test_verified_roundtrip_matches_in_memory_fingerprint(tmp_path: Path) -> None:
    source = simple_dataset()
    plan = make_plan(per_stratum=2)
    sampling = execute_sampling(source, plan)
    staging, persisted = _stage(tmp_path, source.columns, sampling.rows)
    # Must not raise.
    verify_persisted_sample(plan, source, persisted, sampling, staging_path=staging)


def test_mismatch_raises_samp_004(tmp_path: Path) -> None:
    source = simple_dataset()
    plan = make_plan(per_stratum=2)
    sampling = execute_sampling(source, plan)
    tampered = [dict(row) for row in sampling.rows]
    tampered[0]["id"] = "tampered-id"
    staging, persisted = _stage(tmp_path, source.columns, tampered)
    with pytest.raises(SamplingError) as captured:
        verify_persisted_sample(plan, source, persisted, sampling, staging_path=staging)
    assert captured.value.code == "SAMP-004"
    assert str(staging) == captured.value.path


def test_verification_uses_declared_fingerprint_column_order(tmp_path: Path) -> None:
    source = simple_dataset()
    plan = make_plan(per_stratum=2, fingerprint_columns=("id", "channel", "tier"))
    sampling = execute_sampling(source, plan)
    # Persist with a different header order. Every fingerprint column is still
    # present, so a header-order-sensitive check would wrongly fail; the
    # declared plan order must win instead.
    reordered_columns = ["tier", "id", "channel"]
    staging, persisted = _stage(tmp_path, reordered_columns, sampling.rows)
    verify_persisted_sample(plan, source, persisted, sampling, staging_path=staging)

    # But tampering a value must still be detected regardless of header order.
    tampered = [dict(row) for row in sampling.rows]
    tampered[1]["channel"] = "tampered-channel"
    staging_bad, persisted_bad = _stage(
        tmp_path, reordered_columns, tampered, job_id="unit-job-bad"
    )
    with pytest.raises(SamplingError) as captured:
        verify_persisted_sample(plan, source, persisted_bad, sampling, staging_path=staging_bad)
    assert captured.value.code == "SAMP-004"


def test_verification_rejects_sample_missing_fingerprint_column(tmp_path: Path) -> None:
    source = simple_dataset()
    plan = make_plan(per_stratum=2, fingerprint_columns=("id", "channel"))
    sampling = execute_sampling(source, plan)
    staging, persisted = _stage(tmp_path, ["id", "tier"], sampling.rows)
    with pytest.raises(DataError) as captured:
        verify_persisted_sample(plan, source, persisted, sampling, staging_path=staging)
    assert captured.value.code == "DATA-002"
