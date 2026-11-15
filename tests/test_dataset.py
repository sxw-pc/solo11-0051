from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from samplelaw.source.csv_reader import (
    commit_staged_csv,
    read_csv_dataset,
    staging_csv_path,
    write_csv_rows,
    write_staged_csv,
)
from samplelaw.source.dataset import Dataset, infer_column_types, primary_key_candidates
from samplelaw.source.sqlite_reader import read_sqlite_dataset
from samplelaw.util.errors import DataError
from samplelaw.util.hashing import row_fingerprint

def test_type_inference_and_key_candidates() -> None:
    dataset = Dataset.from_rows(
        "demo",
        ["id", "amount", "ratio", "day", "label", "mixed"],
        [
            {"id": "1", "amount": "10", "ratio": "0.5", "day": "2026-01-02", "label": "demo-a", "mixed": "1.0"},
            {"id": "2", "amount": "10", "ratio": "0.5", "day": "2026-01-02", "label": "demo-a", "mixed": "1.0"},
        ],
    )
    assert infer_column_types(dataset) == {
        "id": "integer",
        "amount": "integer",
        "ratio": "float",
        "day": "date",
        "label": "string",
        "mixed": "float",
    }
    assert primary_key_candidates(dataset) == ["id"]

def test_missing_column_is_data_002() -> None:
    dataset = Dataset.from_rows("demo", ["id"], [{"id": "1"}])
    with pytest.raises(DataError) as captured:
        dataset.require_columns(["id", "channel"])
    assert captured.value.code == "DATA-002"

def test_row_fingerprint_is_value_sensitive() -> None:
    columns = ["id", "name"]
    first = row_fingerprint({"id": "1", "name": "demo-a"}, columns)
    second = row_fingerprint({"id": "1", "name": "demo-b"}, columns)
    assert len(first) == 64
    assert first == row_fingerprint({"id": "1", "name": "demo-a"}, columns)
    assert first != second

def test_csv_bom_and_gbk_detection(tmp_path: Path) -> None:
    bom = tmp_path / "bom.csv"
    bom.write_text("id,name\n1,demo-a\n", encoding="utf-8-sig")
    assert read_csv_dataset(bom).rows[0]["name"] == "demo-a"
    gbk = tmp_path / "gbk.csv"
    gbk.write_text("id,name\n1,demo-组织\n", encoding="gbk")
    dataset = read_csv_dataset(gbk)
    assert dataset.rows[0]["name"] == "demo-组织"
    assert dataset.encoding == "gbk"

def test_csv_empty_and_duplicate_header(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("id,name\n", encoding="utf-8")
    with pytest.raises(DataError) as captured:
        read_csv_dataset(empty)
    assert captured.value.code == "DATA-001"
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("id,id\n1,2\n", encoding="utf-8")
    with pytest.raises(DataError) as captured:
        read_csv_dataset(duplicate)
    assert captured.value.code == "DATA-004"

def test_csv_writer_is_lf_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    write_csv_rows(path, ["id", "name"], [{"id": "1", "name": "demo-a"}])
    raw = path.read_bytes()
    assert b"\r\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    assert read_csv_dataset(path).rows == ({"id": "1", "name": "demo-a"},)

def test_staged_csv_does_not_touch_final_path_until_commit(tmp_path: Path) -> None:
    final = tmp_path / "sample.csv"
    final.write_text("id,name\n0,previous\n", encoding="utf-8")
    original = final.read_bytes()
    staging = staging_csv_path(final)
    assert staging.name != final.name and staging.parent == final.parent
    write_staged_csv(staging, ["id", "name"], [{"id": "1", "name": "demo-a"}])
    # The official path is untouched while the sample is only staged.
    assert final.read_bytes() == original
    assert read_csv_dataset(staging).rows == ({"id": "1", "name": "demo-a"},)
    commit_staged_csv(staging, final)
    assert not staging.exists()
    assert read_csv_dataset(final).rows == ({"id": "1", "name": "demo-a"},)

def test_write_csv_rows_leaves_no_staging_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "sample.csv"
    write_csv_rows(path, ["id"], [{"id": "1"}])
    assert path.is_file()
    assert list(path.parent.glob("*.tmp")) == []

def test_commit_staged_csv_overwrites_existing_sample(tmp_path: Path) -> None:
    final = tmp_path / "sample.csv"
    write_csv_rows(final, ["id"], [{"id": "old"}])
    staging = staging_csv_path(final)
    write_staged_csv(staging, ["id"], [{"id": "new"}])
    commit_staged_csv(staging, final)
    assert read_csv_dataset(final).rows == ({"id": "new"},)
    assert list(tmp_path.glob("*.tmp")) == []

def test_sqlite_reads_table_and_empty_table(tmp_path: Path) -> None:
    database = tmp_path / "demo.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE demo_rows (id INTEGER PRIMARY KEY, channel TEXT, amount REAL)")
        connection.execute("INSERT INTO demo_rows VALUES (1, 'web', 10.5)")
    dataset = read_sqlite_dataset(database, table="demo_rows")
    assert dataset.columns == ("id", "channel", "amount")
    assert dataset.rows[0] == {"id": "1", "channel": "web", "amount": "10.5"}
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE empty_rows (id INTEGER)")
    with pytest.raises(DataError) as captured:
        read_sqlite_dataset(database, table="empty_rows")
    assert captured.value.code == "DATA-001"

def test_sqlite_missing_table_is_data_004(tmp_path: Path) -> None:
    database = tmp_path / "demo.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE present (id INTEGER)")
    with pytest.raises(DataError) as captured:
        read_sqlite_dataset(database, table="absent")
    assert captured.value.code == "DATA-004"
