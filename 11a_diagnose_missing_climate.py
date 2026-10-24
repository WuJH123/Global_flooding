"""Audit expected Earth Engine climate CSV exports against local files and task states.

Usage (PowerShell):
  E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\11a_diagnose_missing_climate.py
  E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\11a_diagnose_missing_climate.py --local-only

This script NEVER re-runs, cancels, deletes, or overwrites any EE task or file.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

PROJECT = "eco-sector-503721-b7"
ROOT = Path(r"E:\Global_flooding\Climate_all")
MANIFEST = Path(r"E:\Global_flooding\climate_requests_chunked\climate_request_chunk_manifest.csv")
REPORT_DIR = Path(r"E:\Global_flooding\climate_global")

PRODUCTION_RE = re.compile(r"^climate_(\d{4})_m(\d{2})_c(\d+)\.csv$", re.IGNORECASE)


def get_remote_operations() -> tuple[list[dict], str]:
    """List EE operations; fall back to legacy task list if needed."""
    import ee

    ee.Initialize(project=PROJECT)
    try:
        ops = ee.data.listOperations(limit=2000)
        items = []
        for op in ops:
            md = op.get("metadata") or {}
            items.append({
                "description": str(md.get("description", "")),
                "state": str(md.get("state", "UNKNOWN")),
                "updated": str(md.get("update_time", "")),
                "task_id": str(op.get("name", "")),
                "error": str((op.get("error") or {}).get("message", "")),
                "destination": "; ".join(map(str, md.get("destination_uris") or [])),
            })
        return items, "listOperations"
    except Exception as exc:
        print(f"[INFO] listOperations unavailable: {exc}; trying getTaskList")
        tasks = ee.data.getTaskList()
        items = []
        for task in tasks:
            items.append({
                "description": str(task.get("description", "")),
                "state": str(task.get("state", "UNKNOWN")),
                "updated": str(task.get("update_timestamp_ms", "")),
                "task_id": str(task.get("id", "")),
                "error": str(task.get("error_message", "")),
                "destination": "; ".join(map(str, task.get("destination_uris") or [])),
            })
        return items, "getTaskList"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true", help="Do not query Earth Engine")
    args = parser.parse_args()

    if not MANIFEST.is_file():
        raise FileNotFoundError(MANIFEST)
    if not ROOT.is_dir():
        raise FileNotFoundError(ROOT)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = pd.read_csv(MANIFEST, usecols=["year", "month", "request_chunk", "requests"])
    for col in manifest.columns:
        manifest[col] = pd.to_numeric(manifest[col], errors="raise").astype(int)
    if manifest.duplicated(["year", "month", "request_chunk"]).any():
        raise RuntimeError("Manifest has duplicated (year, month, request_chunk) keys")

    found = defaultdict(list)
    all_csv = list(ROOT.rglob("*.csv"))
    for path in all_csv:
        match = PRODUCTION_RE.fullmatch(path.name)
        if match:
            key = tuple(map(int, match.groups()))
            found[key].append(path)

    remote_by_desc = defaultdict(list)
    source = "not queried"
    if not args.local_only:
        try:
            remote_items, source = get_remote_operations()
            for item in remote_items:
                if item["description"]:
                    remote_by_desc[item["description"]].append(item)
            for items in remote_by_desc.values():
                items.sort(key=lambda x: x["updated"], reverse=True)
        except Exception as exc:
            print(f"[WARNING] Could not query EE task status: {exc}")

    rows = []
    for r in manifest.itertuples(index=False):
        key = (r.year, r.month, r.request_chunk)
        description = f"Climate_{r.year}_M{r.month:02d}_C{r.request_chunk}"
        paths = found.get(key, [])
        histories = remote_by_desc.get(description, [])
        latest = histories[0] if histories else {}
        rows.append({
            "year": r.year,
            "month": r.month,
            "request_chunk": r.request_chunk,
            "expected_rows": r.requests,
            "description": description,
            "expected_filename": f"climate_{r.year}_m{r.month:02d}_c{r.request_chunk}.csv",
            "local_files": len(paths),
            "local_paths": "; ".join(str(x) for x in paths),
            "latest_ee_state": latest.get("state", "UNKNOWN"),
            "latest_ee_task_id": latest.get("task_id", ""),
            "latest_ee_error": latest.get("error", ""),
            "latest_ee_destination": latest.get("destination", ""),
            "ee_attempts_found": len(histories),
        })

    report = pd.DataFrame(rows)
    missing = report[report.local_files == 0].copy()
    duplicated = report[report.local_files > 1].copy()

    report.to_csv(REPORT_DIR / "climate_file_reconciliation.csv", index=False, encoding="utf-8-sig")
    missing.to_csv(REPORT_DIR / "climate_missing_files.csv", index=False, encoding="utf-8-sig")

    print("=" * 76)
    print("Global climate export reconciliation")
    print(f"Expected manifest tasks: {len(report)}")
    print(f"Exact-name local files: {(report.local_files > 0).sum()}")
    print(f"Missing task files: {len(missing)}")
    print(f"Duplicate production task files: {len(duplicated)}")
    print(f"EE task metadata source: {source}")
    print("=" * 76)

    for item in missing.itertuples(index=False):
        print(f"\n[MISSING] {item.description}   expected rows: {item.expected_rows:,}")
        print(f"  Expected name: {item.expected_filename}")
        print(f"  EE status (latest observed): {item.latest_ee_state}")
        if item.latest_ee_task_id:
            print(f"  Task ID: {item.latest_ee_task_id}")
        if item.latest_ee_error:
            print(f"  EE error: {item.latest_ee_error}")
        if item.latest_ee_destination:
            print(f"  EE output URI: {item.latest_ee_destination}")
        fuzzy = [str(p) for p in all_csv if f"{item.year}_m{item.month:02d}_c{item.request_chunk}" in p.name.lower()]
        if fuzzy:
            print("  Similar local filenames:")
            for f in fuzzy[:10]:
                print(f"    {f}")
        print("  Action: If READY/RUNNING/PENDING, wait. If COMPLETED/SUCCEEDED, find/download in Drive. If FAILED, retry this task only.")

    if duplicated.empty and missing.empty:
        print("\nAll production CSV files exist under the expected exact filenames.")
        print("Next: run 11_global_climate_qc.py to verify content and row counts.")
    if not duplicated.empty:
        print("\n[DUPLICATE] Some production tasks appear in multiple local files. Resolve before QC.")
        print(duplicated[["description", "local_paths"]].to_string(index=False))
    print(f"\nSaved: {REPORT_DIR / 'climate_file_reconciliation.csv'}")
    print(f"Saved: {REPORT_DIR / 'climate_missing_files.csv'}")


if __name__ == "__main__":
    main()
