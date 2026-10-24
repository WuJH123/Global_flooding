"""Synthetic tests: no Earth Engine account, no cloud data, no pyarrow required."""
import importlib.util
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

MODULE = Path(__file__).resolve().parents[1] / "pipelines" / "r32" / "21f_compact_crossyear_submit.py"
SPEC = importlib.util.spec_from_file_location("compact21f", MODULE)
compact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compact)


def make_stage20b(tmp_path):
    calendar = tmp_path / "standard_calendar_20b"
    calendar.mkdir()
    inputs = pd.DataFrame([
        {"year": 2020, "month": 1, "request_chunk": 0, "date_str": "2020-01-01", "climate_id": "a"},
        {"year": 2020, "month": 1, "request_chunk": 0, "date_str": "2020-01-02", "climate_id": "b"},
        {"year": 2020, "month": 2, "request_chunk": 0, "date_str": "2020-02-01", "climate_id": "c"},
        {"year": 2020, "month": 2, "request_chunk": 0, "date_str": "2020-02-02", "climate_id": "d"},
        {"year": 2021, "month": 1, "request_chunk": 0, "date_str": "2021-01-01", "climate_id": "e"},
    ])
    con = duckdb.connect()
    con.register("inputs", inputs)
    con.execute("COPY inputs TO ? (FORMAT PARQUET)", [str(calendar / "20_missing_climate_requests_chunked.parquet")])
    con.close()
    inputs.groupby(["year", "month", "request_chunk"]).size().rename("requests").reset_index().to_csv(
        calendar / "20_missing_task_manifest.csv", index=False)
    return calendar


def test_crossyear_packing_and_signature(tmp_path):
    calendar = make_stage20b(tmp_path)
    big, manifest = compact.build_plan(calendar, 50000, 540)
    assert len(big) == 1
    assert list(big.requests) == [5]
    assert list(big.distinct_dates) == [5]
    assert big.years.iloc[0] == "2020,2021"
    assert len(json.loads(big.members_json.iloc[0])) == 3
    exact, provenance = compact.build_plan(calendar, 3, 3)
    assert len(exact) == 2
    assert sum(exact.requests) == 5
    assert compact.fingerprint(big, manifest) == compact.fingerprint(big, manifest)
    assert compact.fingerprint(big, manifest) != compact.fingerprint(exact, provenance)


def test_unknown_block_rejected():
    assert compact.parse_blocks("0,2-3", {0,1,2,3}) == {0,2,3}
    with pytest.raises(ValueError):
        compact.parse_blocks("0,8", {0,1})
    with pytest.raises(ValueError):
        compact.parse_blocks("3-2", {1,2,3})


def test_missing_or_duplicate_requests_hard_fail(tmp_path):
    calendar = make_stage20b(tmp_path)
    con = duckdb.connect()
    path = calendar / "20_missing_climate_requests_chunked.parquet"
    con.execute("CREATE TABLE input AS SELECT * FROM read_parquet(?)", [str(path)])
    con.execute("INSERT INTO input SELECT * FROM input WHERE climate_id='a'")
    con.execute("COPY input TO ? (FORMAT PARQUET)", [str(calendar / "new.parquet")])
    con.close()
    (calendar / "new.parquet").replace(path)
    with pytest.raises(RuntimeError, match="Duplicate"):
        compact.build_plan(calendar, 100, 100)
