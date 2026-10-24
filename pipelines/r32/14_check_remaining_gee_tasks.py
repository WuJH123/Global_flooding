"""Read-only Earth Engine climate export audit against the local 330-task manifest.

This script never creates, cancels, or retries tasks.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

import ee
import pandas as pd

PROJECT = "eco-sector-503721-b7"
MANIFEST = Path(
    r"E:\Global_flooding\climate_requests_chunked"
    r"\climate_request_chunk_manifest.csv"
)
REPORT = Path(r"E:\Global_flooding\climate_requests_chunked\gee_tasks_audit.csv")
# Python ee.data.listOperations() accepts no limit= keyword in the installed API.
# The API follows server pagination internally.

STATE_MAP = {
    "COMPLETED": "SUCCEEDED",
    "READY": "PENDING",
    "UNSUBMITTED": "UNSUBMITTED",
    "CANCEL_REQUESTED": "CANCELLING",
}

def normalize_state(value: str | None) -> str:
    value = str(value or "UNKNOWN").upper()
    return STATE_MAP.get(value, value)


def choose_status(operations: list[dict]) -> tuple[str, dict | None]:
    """A completed version takes precedence; then any currently active attempt."""
    if not operations:
        return "NOT_IN_RETURNED_HISTORY", None

    def newest(items: list[dict]) -> dict:
        return max(
            items,
            key=lambda x: str(
                (x.get("metadata") or {}).get("create_time")
                or (x.get("metadata") or {}).get("createTime")
                or (x.get("metadata") or {}).get("update_time")
                or (x.get("metadata") or {}).get("updateTime")
                or ""
            ),
        )

    by_state = {}
    for op in operations:
        state = normalize_state(op.get("metadata", {}).get("state"))
        by_state.setdefault(state, []).append(op)

    for state in ("SUCCEEDED", "RUNNING", "PENDING", "CANCELLING", "FAILED", "CANCELLED"):
        if state in by_state:
            return state, newest(by_state[state])

    op = newest(operations)
    return normalize_state(op.get("metadata", {}).get("state")), op


def main() -> int:
    if not MANIFEST.is_file():
        print(f"ERROR: missing manifest: {MANIFEST}")
        return 1
    manifest = pd.read_csv(MANIFEST)
    required = {"year", "month", "request_chunk", "requests"}
    if not required.issubset(manifest.columns):
        print("ERROR: invalid manifest columns:", sorted(required - set(manifest.columns)))
        return 1
    if manifest.duplicated(["year", "month", "request_chunk"]).any():
        print("ERROR: duplicate year-month-chunk rows in manifest")
        return 1
    ee.Initialize(project=PROJECT)
    print("Fetching Earth Engine task history (read-only)...")
    ops = ee.data.listOperations()
    by_desc = {}
    for op in ops:
        meta = op.get("metadata") or {}
        description = meta.get("description") or ""
        if description.startswith("Climate_"):
            by_desc.setdefault(description, []).append(op)

    rows = []
    for record in manifest.itertuples(index=False):
        year = int(record.year)
        month = int(record.month)
        chunk = int(record.request_chunk)
        description = f"Climate_{year}_M{month:02d}_C{chunk}"
        state, chosen = choose_status(by_desc.get(description, []))
        meta = (chosen or {}).get("metadata") or {}
        error = (chosen or {}).get("error") or {}
        rows.append({
            "description": description,
            "year": year,
            "month": month,
            "request_chunk": chunk,
            "expected_requests": int(record.requests),
            "state": state,
            "observed_attempts": len(by_desc.get(description, [])),
            "operation_name": (chosen or {}).get("name", ""),
            "error_message": error.get("message", ""),
            "batch_eecu_seconds": (
                meta.get("batch_eecu_usage_seconds")
                or meta.get("batchEecuUsageSeconds", "")
            ),
        })

    out = pd.DataFrame(rows)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(REPORT, index=False, encoding="utf-8-sig")

    print(f"Manifest tasks: {len(out)}")
    print(f"Operations returned: {len(ops)}")
    print("\nSTATUS SUMMARY")
    for state, n in sorted(Counter(out.state).items()):
        print(f"  {state:<24} {n:>4}")

    pending = out[out.state != "SUCCEEDED"].copy()
    if pending.empty:
        print("\nAll manifest tasks have a successful Earth Engine operation.")
    else:
        print("\nUNFINISHED / UNVERIFIED TASKS")
        print(pending[["description", "state", "expected_requests", "error_message"]].to_string(index=False, max_colwidth=90))

    unobserved = out[out.state == "NOT_IN_RETURNED_HISTORY"]
    if not unobserved.empty:
        print("\nNOTE: 'NOT_IN_RETURNED_HISTORY' means no matching operation was returned. ")
        print("It does NOT prove that the task failed or was never submitted. Check older task logs and Drive outputs.")

    print("\nRead-only audit saved to:", REPORT)
    print("No tasks have been submitted, restarted, or cancelled.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        print(f"ERROR: {type(ex).__name__}: {ex}")
        sys.exit(2)
