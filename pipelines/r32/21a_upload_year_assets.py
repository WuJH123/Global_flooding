"""Preview or upload the canonical Stage20b yearly request CSV tables to GEE.

Dry-run by default. Windows-safe CLI discovery resolves gcloud.CMD and
earthengine.exe to their exact paths before calling subprocess.

This imports request tables only; it does NOT submit any climate calculations.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

KEYS = {
    "climate_id", "grid_id", "grid_x", "grid_y", "year",
    "month", "request_chunk", "date_str", "longitude", "latitude",
}


def resolve_cli(name: str, explicit_path: str | None = None) -> str:
    """Find an actual executable/script path; bare 'gcloud' fails in some Windows Popen environments."""
    found = explicit_path or shutil.which(name)
    if not found and os.name == "nt":
        found = shutil.which(name + ".cmd") or shutil.which(name + ".exe")
    if not found:
        raise RuntimeError(
            f"Could not find {name} in PATH. In PowerShell check 'Get-Command {name}' "
            f"and 'where.exe {name}'. You may pass --{name}-cli with its full path."
        )
    path = Path(found).expanduser()
    if not path.is_file():
        raise RuntimeError(f"Executable path for {name} does not exist: {path}")
    return str(path.resolve())


def run_cli(executable: str, args: list[str], *, check: bool = True,
            capture_output: bool = False) -> subprocess.CompletedProcess:
    """Invoke the resolved absolute path. Windows .CMD files may require cmd.exe fallback."""
    argv = [str(executable), *[str(x) for x in args]]
    options = {"check": check, "text": True, "capture_output": capture_output}
    try:
        return subprocess.run(argv, **options)
    except FileNotFoundError:
        if os.name != "nt" or Path(executable).suffix.lower() not in (".cmd", ".bat"):
            raise
        # Some Windows CreateProcess configurations cannot launch a .CMD directly.
        # All arguments come from validated bucket/project IDs and local file paths.
        cmd = os.environ.get("COMSPEC", "cmd.exe")
        command = subprocess.list2cmdline(argv)
        return subprocess.run([cmd, "/d", "/c", command], **options)


def assert_year_csv(path: Path) -> int:
    year = path.stem[-4:]
    count = 0
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        missing = KEYS - set(rows.fieldnames or [])
        if missing:
            raise ValueError(f"Missing fields in {path.name}: {sorted(missing)}")
        for rec in rows:
            cid = rec["climate_id"]
            if not cid or cid in seen:
                raise ValueError(f"Blank/duplicate climate_id in {path.name}: {cid}")
            seen.add(cid)
            lon, lat = float(rec["longitude"]), float(rec["latitude"])
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"Invalid coordinate: {cid}")
            if rec["year"].strip() != year:
                raise ValueError(f"Wrong year: {cid}")
            count += 1
    return count


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=Path(r"E:\Global_flooding"))
    p.add_argument("--bucket", default="gs://groundsource-climate-503721-b7")
    p.add_argument("--project", default="eco-sector-503721-b7")
    p.add_argument("--year", type=int, help="Import only the selected year")
    p.add_argument("--upload", action="store_true", help="Perform GCS copy and Earth Engine table ingestion")
    p.add_argument("--gcloud-cli", help="Full path to gcloud.cmd or gcloud.exe (Windows override)")
    p.add_argument("--earthengine-cli", help="Full path to earthengine.exe (venv override)")
    a = p.parse_args()

    if not re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", a.bucket):
        raise ValueError("--bucket must be a bucket root such as gs://your-bucket-name")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", a.project):
        raise ValueError("--project is not a valid Cloud project ID")

    root = a.base / "standard_calendar_20b" / "gee_upload_standard"
    paths = sorted(root.glob("standard_requests_*.csv"))
    if a.year is not None:
        paths = [x for x in paths if x.stem == f"standard_requests_{a.year}"]
    if not paths:
        raise RuntimeError(f"No yearly request CSVs in {root}")

    # Resolve paths ONCE before any cloud write: no accidental partial batch if CLI is unavailable.
    gcloud = earthengine = None
    if a.upload:
        gcloud = resolve_cli("gcloud", a.gcloud_cli)
        earthengine = resolve_cli("earthengine", a.earthengine_cli)
        print(f"[CLI] gcloud={gcloud}")
        print(f"[CLI] earthengine={earthengine}")

    total = 0
    started = 0
    for path in paths:
        count = assert_year_csv(path)
        total += count
        year = path.stem[-4:]
        uri = f"{a.bucket.rstrip('/')}/stage32/{path.name}"
        asset = f"projects/{a.project}/assets/standard_requests_{year}"
        print(f"[CHECK] {year}: {count:,} requests → {asset}", flush=True)

        if not a.upload:
            continue

        # If an asset already exists, skip upload BEFORE copying anything to GCS.
        info = run_cli(earthengine, ["asset", "info", asset],
                       check=False, capture_output=True)
        if info.returncode == 0:
            print(f"[SKIP] Existing asset: {asset} (verify its record count separately)")
            continue
        failure_message = ((info.stderr or "") + " " + (info.stdout or "")).lower()
        if not any(code in failure_message for code in (
            "not found", "does not exist", "404", "not_found",
        )):
            raise RuntimeError(
                f"Cannot check GEE asset {asset}. This may be an auth/permission problem, "
                f"not a missing asset. Output: {(info.stderr or info.stdout)[:500]}"
            )

        # Create the manifest before GCS write so an upload can be inspected/recovered.
        manifest = {
            "name": asset,
            "sources": [{
                "uris": [uri],
                "xColumn": "longitude",
                "yColumn": "latitude",
                "crs": "EPSG:4326",
            }],
        }
        manifest_file = root / f"manifest_standard_{year}.json"
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        print(f"[GCS UPLOAD] {path.name} -> {uri}", flush=True)
        run_cli(gcloud, ["storage", "cp", str(path), uri])
        print(f"[GEE INGEST] {asset}", flush=True)
        run_cli(earthengine, ["upload", "table", "--manifest", str(manifest_file)])
        started += 1
        print(f"[SUBMITTED] {year}: ingest started; NOT YET verified as ready.", flush=True)

    print(f"[DONE] {len(paths)} yearly CSVs, {total:,} requests, "
          f"upload={a.upload}, new_ingest_submissions={started}")
    if a.upload:
        print("Wait for each table ingestion to complete. Then run 21f --verify-assets "
              "and compare row counts before submitting STD32C weather exports.")
        print("Do not rerun --upload for a year while its previous ingest task is pending.")
    else:
        print("Preview only; add --upload to create table ingestions.")


if __name__ == "__main__":
    main()
