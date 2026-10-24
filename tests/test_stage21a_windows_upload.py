"""Stage21a uploader tests. No gcloud, Earth Engine, or credentials required."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] /
          "pipelines/r32/21a_upload_year_assets.py")
SPEC = importlib.util.spec_from_file_location("uploader21a", SCRIPT)
upload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upload)


def test_windows_resolves_actual_cmd_and_venv_exe(tmp_path, monkeypatch):
    cloud = tmp_path / "Google Cloud SDK" / "bin"
    cloud.mkdir(parents=True)
    cmd = cloud / "gcloud.cmd"
    cmd.write_text("@echo off\n")
    env = tmp_path / "venv" / "Scripts"
    env.mkdir(parents=True)
    ee_cli = env / "earthengine.exe"
    ee_cli.write_bytes(b"test")
    monkeypatch.setattr(upload.sys, "platform", "win32")
    monkeypatch.setattr(upload.sys, "executable", str(env / "python.exe"))

    def which(command):
        if command == "gcloud.cmd":
            return str(cmd)
        return None

    monkeypatch.setattr(upload.shutil, "which", which)
    assert upload.resolve_cli("gcloud") == str(cmd.resolve())
    assert upload.resolve_cli("earthengine") == str(ee_cli.resolve())
    with pytest.raises(FileNotFoundError):
        upload.resolve_cli("gcloud", str(tmp_path / "missing.cmd"))


def test_ledger_idempotent_and_hash_mismatch(tmp_path):
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("test")
    ledger = tmp_path / "jobs.jsonl"
    upload.log_submission(
        ledger, year="2020", asset="projects/p/assets/a",
        uri="gs://bucket/2020.csv", csv_digest=upload.sha256(csv_file),
    )
    logged = upload.read_ledger(ledger)
    assert logged["2020"]["status"] == "INGESTION_SUBMITTED"
    assert logged["2020"]["csv_sha256"] == upload.sha256(csv_file)
    assert len(ledger.read_text().splitlines()) == 1


def test_year_csv_validation_checks_identifiers(tmp_path):
    file = tmp_path / "standard_requests_2020.csv"
    good = {
        "climate_id": "x", "grid_id": "1_2", "grid_x": "1", "grid_y": "2",
        "year": "2020", "month": "7", "request_chunk": "0",
        "date_str": "2020-07-01", "longitude": "35", "latitude": "14",
    }
    with file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=good.keys())
        writer.writeheader()
        writer.writerow(good)
    assert upload.validate_csv(file) == 1
    with file.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=good.keys())
        writer.writerow(good)
    with pytest.raises(ValueError, match="duplicate"):
        upload.validate_csv(file)
