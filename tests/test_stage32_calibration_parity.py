"""Cloud-free parity gate tests using synthetic source/CSV weather."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COMPARE = ROOT / "pipelines" / "r32" / "21c_compare_calibration.py"
LEGACY = ROOT / "21_standard_weather_submit.py"
SPEC = importlib.util.spec_from_file_location("stage21test", LEGACY)
REF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REF)


def test_parity_uses_algorithm_sha_and_coverage(tmp_path):
    root = tmp_path / "base"
    climate_dir = root / "climate_global"
    climate_dir.mkdir(parents=True)
    rows = pd.DataFrame([
        {"climate_id": f"grid_{i}_2020-07-01", "date_str": "2020-07-01",
         **{feature: 1.5 for feature in REF.WEATHER_COLS}}
        for i in range(12)
    ])
    conn = duckdb.connect()
    conn.register("original", rows)
    conn.execute("COPY original TO ? (FORMAT PARQUET)", [
        str(climate_dir / "climate_features_primary.parquet")])
    conn.close()
    csv = tmp_path / "std32_calibration_2020.csv"
    rows.to_csv(csv, index=False)

    def run():
        return subprocess.run(
            [sys.executable, str(COMPARE), "--base", str(root),
             "--calibration-csv", str(csv)],
            capture_output=True, text=True)

    first = run()
    assert first.returncode == 0, first.stdout + first.stderr
    status_file = root / "standard_calendar_20b" / "calibration_qc" / "21c_calibration_status.json"
    status = json.loads(status_file.read_text())
    assert status["status"] == "PASS"
    assert len(status["weather_source_sha256"]) == 64
    assert not status["unvalidated_features"]

    # Near-all-missing fields must fail rather than pass vacuous numeric equality.
    field = REF.WEATHER_COLS[0]
    rows.loc[:9, field] = float("nan")
    rows.to_csv(csv, index=False)
    second = run()
    assert second.returncode != 0
    failed = json.loads(status_file.read_text())
    assert failed["status"] == "FAIL"
    assert field in failed["unvalidated_features"]
