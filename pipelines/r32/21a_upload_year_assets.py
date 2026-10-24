"""Preview (default) or ingest 20b yearly CSVs as Earth Engine Table Assets.

On Windows the Google Cloud CLI launcher is gcloud.cmd. Calling
subprocess.run(["gcloud", ...]) may raise WinError 2 even when PowerShell
can run gcloud. Resolve each CLI to its full actual launcher path.

The local submission ledger prevents accidental duplicate ingestion while
a table-upload task is still pending. Never uses the climate-task quota.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

KEYS = {
    "climate_id", "grid_id", "grid_x", "grid_y", "year", "month",
    "request_chunk", "date_str", "longitude", "latitude",
}
DEFAULT_PROJECT = "eco-sector-503721-b7"
DEFAULT_BUCKET = "gs://groundsource-climate-503721-b7"


def resolve_cli(tool: str, override: str | None = None) -> str:
    """Return absolute launcher, including .cmd/.exe on Windows."""
    if tool not in {"gcloud", "earthengine"}:
        raise ValueError(tool)
    candidates: list[str] = []
    if override:
        candidates.append(override)
    elif sys.platform == "win32":
        if tool == "gcloud":
            candidates.extend(("gcloud.cmd", "gcloud.exe", "gcloud"))
        else:
            candidates.extend((
                str(Path(sys.executable).resolve().parent / "earthengine.exe"),
                "earthengine.exe", "earthengine.cmd", "earthengine",
            ))
    else:
        candidates.append(tool)

    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())
        found = shutil.which(candidate)
        if found:
            return str(Path(found).resolve())

    raise FileNotFoundError(
        f"Cannot find {tool} launcher. On Windows, try: "
        f"Get-Command {tool}* | Select-Object Source. "
        f"Then pass --{'gcloud-cli' if tool == 'gcloud' else 'earthengine-cli'} "
        "with the full .cmd/.exe path. Do not reinstall a CLI that already "
        "works in PowerShell."
    )


def run_command(command: list[str], *, capture: bool = False, check: bool = True):
    """Use a resolved absolute executable in subprocess (WinError 2 fix)."""
    try:
        return subprocess.run(
            command, check=check, text=True, capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Launcher not found by Python subprocess: {command[0]!r}. "
            "Use --gcloud-cli or --earthengine-cli to specify the absolute file."
        ) from exc


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_ledger(file: Path) -> dict[str, dict]:
    latest = {}
    if file.is_file():
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                latest[str(record["year"])] = record
    return latest


def log_submission(file: Path, *, year: str, asset: str,
                   uri: str, csv_digest: str):
    record = {
        "year": year, "asset": asset, "uri": uri,
        "csv_sha256": csv_digest, "status": "INGESTION_SUBMITTED",
        "submitted_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    with file.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")


def validate_csv(path: Path) -> int:
    expected_year = path.stem[-4:]
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not KEYS.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"{path}: missing {sorted(KEYS - set(reader.fieldnames or []))}")
        seen = set()
        for row in reader:
            identifier = row["climate_id"]
            if not identifier or identifier in seen:
                raise ValueError(f"{path}: duplicate/blank climate_id {identifier!r}")
            seen.add(identifier)
            lon, lat = float(row["longitude"]), float(row["latitude"])
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"{path}: bad longitude/latitude for {identifier}")
            if str(row["year"]) != expected_year:
                raise ValueError(f"{path}: bad year for {identifier}")
            count += 1
    return count


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=Path(r"E:\Global_flooding"))
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--year", type=int, help="Import just this year, for first test")
    p.add_argument("--upload", action="store_true",
                   help="Actually copy CSVs to GCS and request EE Table ingestion")
    p.add_argument("--check-tools", action="store_true",
                   help="Check CLI paths/launchers without touching GCS or EE assets")
    p.add_argument("--gcloud-cli", help="Absolute gcloud.cmd path (Windows override)")
    p.add_argument("--earthengine-cli", help="Absolute earthengine.exe path (override)")
    args = p.parse_args()

    root = args.base / "standard_calendar_20b" / "gee_upload_standard"
    files = sorted(root.glob("standard_requests_*.csv"))
    if args.year is not None:
        files = [f for f in files if f.stem == f"standard_requests_{args.year}"]
    if not files:
        raise FileNotFoundError(f"No matching yearly CSV in {root}")
    if not args.bucket.startswith("gs://"):
        raise ValueError("--bucket must begin with gs://")

    gcloud = earthengine = None
    if args.upload or args.check_tools:
        gcloud = resolve_cli("gcloud", args.gcloud_cli)
        earthengine = resolve_cli("earthengine", args.earthengine_cli)
        print(f"[CLI] gcloud     = {gcloud}")
        print(f"[CLI] earthengine = {earthengine}")
        run_command([gcloud, "--version"])
        run_command([earthengine, "--help"], capture=True)
    if args.check_tools:
        print("[PASS] Both CLIs can start from Python subprocess; no upload made.")
        return

    # Validate all selected files before uploading even the first year.
    selected = [(path, validate_csv(path)) for path in files]
    print(f"[LOCAL] {len(selected)} CSVs; {sum(n for _, n in selected):,} rows")
    if not args.upload:
        for path, n in selected:
            print(f"[CHECK] {path.stem[-4:]}: {n:,} requests")
        print("[DRY RUN] Use --upload (no extra word) to ingest.")
        return

    # Confirm the Cloud Storage bucket is accessible before any GCS writes.
    run_command([gcloud, "storage", "ls", args.bucket.rstrip("/") + "/"],
                capture=True)
    ledger_path = root / "21a_upload_submissions.jsonl"
    submitted = read_ledger(ledger_path)
    newly_submitted = 0
    for path, count in selected:
        year = path.stem[-4:]
        uri = f"{args.bucket.rstrip('/')}/stage32/{path.name}"
        asset = f"projects/{args.project}/assets/standard_requests_{year}"
        csv_digest = sha256(path)
        print(f"[CHECK] {year}: {count:,} requests -> {asset}", flush=True)

        prior = submitted.get(year)
        if prior:
            if prior.get("csv_sha256") != csv_digest or prior.get("asset") != asset:
                raise RuntimeError(
                    f"Year {year} has a previous ingestion record with different "
                    f"CSV bytes or asset ID. Do not overwrite; inspect {ledger_path}"
                )
            print(f"[SKIP PREVIOUSLY SUBMITTED] {year}; check Tasks/Assets for status")
            continue

        existing = run_command([earthengine, "asset", "info", asset],
                               capture=True, check=False)
        if existing.returncode == 0:
            print(f"[SKIP EXISTING ASSET] {year}; validate rows in 21f")
            continue

        print(f"[GCS UPLOAD] {path.name} -> {uri}", flush=True)
        run_command([gcloud, "storage", "cp", str(path), uri])
        manifest = {
            "name": asset,
            "sources": [{
                "uris": [uri], "xColumn": "longitude",
                "yColumn": "latitude", "crs": "EPSG:4326",
            }],
        }
        manifest_file = root / f"manifest_standard_{year}.json"
        manifest_file.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[EE INGEST] {asset}", flush=True)
        run_command([earthengine, "upload", "table",
                     "--manifest", str(manifest_file)])
        log_submission(ledger_path, year=year, asset=asset,
                       uri=uri, csv_digest=csv_digest)
        newly_submitted += 1

    print(f"[DONE] requested={len(selected)} years; new EE ingestions={newly_submitted}")
    print(f"[LEDGER] {ledger_path}")
    print("Ingestion starts asynchronously. Verify each EE asset row count "
          "with 21f --verify-assets BEFORE submitting climate computation tasks.")


if __name__ == "__main__":
    main()
