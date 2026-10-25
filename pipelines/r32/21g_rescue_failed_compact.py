"""Stage 32 rescue: split only FAILED compact blocks into smaller table exports.

Does NOT alter the original 11-task plan, Stage20b input, or finished exports.
Each rescue child preserves canonical month/chunk membership. Never split a
single Stage20b chunk internally; run with a higher per-task cap if needed.
Default is DRY RUN; production requires a plan ID and proven FAILED parent tasks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import time
from pathlib import Path

import duckdb
import pandas as pd

ACTIVE = {"READY", "RUNNING", "PENDING", "SUBMITTED", "COMPLETED", "SUCCEEDED"}


def load_stage21f():
    source = Path(__file__).resolve().with_name("21f_compact_crossyear_submit.py")
    spec = importlib.util.spec_from_file_location("stage21f_rescue", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_blocks(value):
    selected = [int(x.strip()) for x in value.split(",")]
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Parent block list must be unique and nonempty")
    return sorted(selected)


def build_rescue_plan(root: Path, parents: list[int], max_requests: int,
                      max_dates: int, original_requests: int,
                      original_dates: int):
    stage = load_stage21f()
    original_plan, provenance = stage.build_plan(root, original_requests, original_dates)
    parent_signature = stage.fingerprint(original_plan, provenance)[:12]
    prior = root / "21_compact" / "compact_task_plan.csv"
    if not prior.exists():
        raise FileNotFoundError(prior)
    old = pd.read_csv(prior)
    if len(old) != len(original_plan):
        raise RuntimeError("Canonical task count changed; abort")
    for oldrow, expected in zip(old.itertuples(index=False), original_plan.itertuples(index=False)):
        if (int(oldrow.block) != int(expected.block) or
            str(oldrow.members_json) != str(expected.members_json) or
            int(oldrow.requests) != int(expected.requests)):
            raise RuntimeError(f"Original compact plan changed at block {oldrow.block}")
    tag_in_names = {str(n).split("_")[1][:8] for n in old.task_name}
    if tag_in_names != {parent_signature[:8]}:
        raise RuntimeError("Original plan tag differs from recorded task names")
    if any(x not in set(map(int, old.block)) for x in parents):
        raise ValueError("Unknown compact parent block")

    parquet = root / "20_missing_climate_requests_chunked.parquet"
    con = duckdb.connect()
    try:
        chunks = con.execute("""
            SELECT CAST(year AS INTEGER) AS year, CAST(month AS INTEGER) AS month,
                   CAST(request_chunk AS INTEGER) AS request_chunk,
                   COUNT(*) AS nrows, LIST_SORT(LIST(DISTINCT CAST(date_str AS VARCHAR))) AS days
            FROM read_parquet(?) GROUP BY 1, 2, 3
        """, [str(parquet)]).fetchdf()
    finally:
        con.close()
    info = {
        (int(r.year), int(r.month), int(r.request_chunk)):
        (int(r.nrows), set(r.days))
        for r in chunks.itertuples(index=False)
    }
    recs = []
    for parent in parents:
        parent_row = old.loc[old.block == parent].iloc[0]
        members = json.loads(parent_row.members_json)
        current, days, count = [], set(), 0
        sequence = 0

        def flush():
            nonlocal current, days, count, sequence
            if not current:
                return
            recs.append(dict(parent_block=parent, recovery_part=sequence,
                             requests=count, distinct_dates=len(days),
                             members_json=json.dumps(current, separators=(",", ":"))))
            sequence += 1
            current, days, count = [], set(), 0

        for m in members:
            key = (int(m["year"]), int(m["month"]), int(m["chunk"]))
            if key not in info:
                raise RuntimeError(f"Source monthly chunk absent: {key}")
            size, dates = info[key]
            if size != int(m["requests"]):
                raise RuntimeError(f"Source monthly chunk changed: {key}")
            if size > max_requests or len(dates) > max_dates:
                raise ValueError(f"Indivisible chunk {key}: {size} rows, {len(dates)} dates; "
                                 "increase caps or implement explicitly audited request-level subdivision")
            if current and (count + size > max_requests or len(days | dates) > max_dates):
                flush()
            current.append(m)
            count += size
            days |= dates
        flush()
        emitted = sum(row["requests"] for row in recs if row["parent_block"] == parent)
        if emitted != int(parent_row.requests):
            raise RuntimeError(f"Missing source rows in rescue for B{parent:03d}")
    # Absolute partition validity check: each canonical month/chunk occurs exactly once.
    for parent in parents:
        orig = json.loads(old.loc[old.block == parent, "members_json"].iloc[0])
        children = [
            m for rec in recs if rec["parent_block"] == parent
            for m in json.loads(rec["members_json"])
        ]
        if orig != children:
            raise RuntimeError(f"Rescue membership differs from canonical B{parent:03d}")
    signature_payload = {
        "parents": parents, "original_signature": parent_signature,
        "source_sha256": stage.source_digest(parquet),
        "requests": max_requests, "dates": max_dates, "entries": recs,
    }
    signature = hashlib.sha256(json.dumps(
        signature_payload, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    tag = signature[:8]
    frame = pd.DataFrame(recs)
    frame["block"] = list(range(len(frame)))
    frame["task_name"] = [
        f"STD32R_{tag}_B{int(r.parent_block):03d}_S{int(r.recovery_part):02d}"
        for r in frame.itertuples(index=False)
    ]
    frame["filename"] = [
        f"std32r_{tag}_b{int(r.parent_block):03d}_s{int(r.recovery_part):02d}"
        for r in frame.itertuples(index=False)
    ]
    return frame, signature, parent_signature


def check_parent_failures(ee, root: Path, parents: list[int]):
    ledger = root / "21_compact" / "submitted_tasks.jsonl"
    if not ledger.exists():
        raise FileNotFoundError("Original submitted_tasks.jsonl missing; cannot verify parent failure")
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    for parent in parents:
        found = [r for r in records if int(r.get("block", -1)) == parent]
        if len(found) != 1:
            raise RuntimeError(f"Parent B{parent:03d} does not have exactly one recorded original task")
        original_id = found[0]["task_id"]
        status = ee.data.getTaskStatus([original_id])
        if not status or status[0].get("state") != "FAILED":
            raise RuntimeError(f"Original B{parent:03d} task {original_id} not proven FAILED: {status}")
        print(f"[PARENT FAILED VERIFIED] B{parent:03d}: {original_id}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=Path(r"E:\Global_flooding"))
    ap.add_argument("--parents", default="7,8,9")
    ap.add_argument("--max-requests", type=int, default=30000)
    ap.add_argument("--max-dates", type=int, default=240)
    ap.add_argument("--original-max-requests", type=int, default=80000)
    ap.add_argument("--original-max-dates", type=int, default=730)
    ap.add_argument("--parts", help="Index ranges of tasks IN RESCUE PLAN: e.g. 0,2-4")
    ap.add_argument("--plan-id", help="Full 12-char rescue plan ID printed by preview")
    ap.add_argument("--max-tasks", type=int, default=1)
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--retry-failed", action="store_true",
                    help="Not yet supported: failed rescue tasks need audited supplementary plan")
    args = ap.parse_args()
    if args.retry_failed:
        raise RuntimeError("Do not blindly retry a failed rescue export; subdivide that exact child")
    if args.max_tasks < 0:
        raise ValueError("--max-tasks must be non-negative")
    parents = parse_blocks(args.parents)
    root = args.base / "standard_calendar_20b"
    frame, sig, parent_sig = build_rescue_plan(
        root, parents, args.max_requests, args.max_dates,
        args.original_max_requests, args.original_max_dates,
    )
    out = root / "21_rescue"
    out.mkdir(parents=True, exist_ok=True)
    frozen = out / "frozen_rescue_plan.json"
    if frozen.exists() and json.loads(frozen.read_text(encoding="utf-8")).get("signature") != sig:
        raise RuntimeError("Different rescue plan already frozen; keep original partition/caps")
    plan_path = out / "rescue_plan.csv"
    frame.to_csv(plan_path, index=False, encoding="utf-8-sig")
    meta = {"signature": sig, "parent_plan": parent_sig, "parents": parents,
            "rows": int(frame.requests.sum()), "tasks": len(frame),
            "max_requests": args.max_requests, "max_dates": args.max_dates}
    (out / "rescue_plan_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("[RESCUE PLAN]", json.dumps(meta))
    print("[PLAN ID]", sig[:12])
    print(frame[["parent_block", "recovery_part", "requests", "distinct_dates", "task_name"]].to_string(index=False))
    print("[PLAN FILE]", plan_path)
    if not args.submit:
        print("[DRY RUN] No tasks submitted; completed B000-B006/B010 are untouched")
        return
    if args.plan_id != sig[:12]:
        raise RuntimeError("Use exactly the rescue PLAN ID from the preview")
    # Source algorithm must match numerical parity calibration.
    qc_file = root / "calibration_qc" / "21c_calibration_status.json"
    if not qc_file.exists():
        raise RuntimeError("Stage21 weather calibration status missing; no exports submitted")
    qc = json.loads(qc_file.read_text(encoding="utf-8"))
    stage = load_stage21f()
    source_path = stage.find_legacy(Path(__file__).resolve().parents[2])
    if qc.get("status") != "PASS" or qc.get("weather_source_sha256") != stage.source_digest(source_path):
        raise RuntimeError("Weather algorithm calibration not PASS for current source")
    import ee
    ee.Initialize(project=stage.PROJECT)
    check_parent_failures(ee, root, parents)
    stage.verify_assets.calendar = root
    selected = stage.parse_blocks(args.parts, set(map(int, frame.block)))
    stage.verify_assets(ee, frame, selected)
    recent = ee.data.getTaskList()
    remote = {}
    for entry in recent:
        name = str(entry.get("description", ""))
        if name.startswith("STD32R_"):
            remote.setdefault(name, []).append(entry.get("state", "UNKNOWN"))
    ledger = out / "rescue_submissions.jsonl"
    logged = set()
    if ledger.exists():
        logged = {json.loads(line)["task_name"] for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()}
    mod = stage.get_reference_module(Path(__file__).resolve().parents[2])
    processor, _ = mod.make_ee_functions()
    if not frozen.exists():
        frozen.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    submitted = 0
    for idx, rec in enumerate(frame.itertuples(index=False)):
        if idx not in selected:
            continue
        if rec.task_name in logged or rec.task_name in remote:
            print(f"[SKIP EXISTS] {rec.task_name}; review task history for completed/failed status")
            continue
        subset = stage.select_features(ee, json.loads(rec.members_json))
        task = ee.batch.Export.table.toDrive(
            collection=processor(subset),
            description=rec.task_name,
            folder="Groundsource_Standard_Climate_Compact_Rescue",
            fileNamePrefix=rec.filename,
            fileFormat="CSV", selectors=mod.OUTPUT_COLS,
        )
        task.start()
        entry = {"task_name": rec.task_name, "task_id": task.id,
                 "parent_block": int(rec.parent_block),
                 "part": int(rec.recovery_part), "requests": int(rec.requests),
                 "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print("[STARTED]", entry)
        submitted += 1
        if args.max_tasks and submitted >= args.max_tasks:
            break
        time.sleep(0.5)
    print(f"[DONE] newly submitted={submitted}; ledger={ledger}")


if __name__ == "__main__":
    main()
