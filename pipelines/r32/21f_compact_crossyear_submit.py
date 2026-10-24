"""Stage 3.2 compact, cross-year scheduler for standard-calendar weather extraction.

Never changes the historical Stage 21 pipeline or its frozen plan.
Preview by default. Production submission requires calibrated numerical parity.
The extraction implementation is imported UNCHANGED from 21_standard_weather_submit.py.

Use only for Stage 20b missing climate IDs, NOT for ERA5FB_DAILY fallback jobs.
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

PROJECT = "eco-sector-503721-b7"
ASSET_ROOT = f"projects/{PROJECT}/assets"
ACTIVE = {"READY", "RUNNING", "PENDING", "SUBMITTED", "COMPLETED", "SUCCEEDED"}
FAILURE = {"FAILED", "CANCELLED", "CANCELED"}


def find_legacy(repo: Path) -> Path:
    choices = [
        Path(__file__).resolve().parent / "21_standard_weather_submit.py",
        repo / "21_standard_weather_submit.py",
    ]
    for path in choices:
        if path.is_file():
            return path
    raise FileNotFoundError(f"Cannot locate Stage21 weather algorithm: {choices}")


def parse_blocks(value: str | None, available: set[int]) -> set[int]:
    if not value:
        return available
    requested: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if "-" in item:
            low, high = (int(x) for x in item.split("-", 1))
            if low > high:
                raise ValueError(f"Bad block range: {item}")
            requested.update(range(low, high + 1))
        else:
            requested.add(int(item))
    if not requested <= available:
        raise ValueError(f"Unknown blocks: {sorted(requested - available)}")
    return requested


def source_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(calendar: Path, max_requests: int, max_dates: int):
    if max_requests <= 0 or max_dates <= 0:
        raise ValueError("Limits must be positive")
    requests = calendar / "20_missing_climate_requests_chunked.parquet"
    manifest = calendar / "20_missing_task_manifest.csv"
    if not requests.is_file() or not manifest.is_file():
        raise FileNotFoundError("Stage20b missing requests/manifest not found. Complete Stage20b first.")
    con = duckdb.connect()
    try:
        result = con.execute("""
            SELECT CAST(year AS INTEGER) AS year,
                   CAST(month AS INTEGER) AS month,
                   CAST(request_chunk AS INTEGER) AS request_chunk,
                   COUNT(*) AS requests,
                   LIST_SORT(LIST(DISTINCT CAST(date_str AS VARCHAR))) AS dates
            FROM read_parquet(?)
            GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """, [str(requests)]).fetchdf()
        num, distinct = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT climate_id) FROM read_parquet(?)",
            [str(requests)]
        ).fetchone()
    finally:
        con.close()
    if int(num) != int(distinct):
        raise RuntimeError(f"Duplicate or null climate_id: rows={num} distinct={distinct}")
    base = pd.read_csv(manifest, encoding="utf-8-sig")
    needed = {"year", "month", "request_chunk", "requests"}
    if not needed.issubset(base.columns):
        raise RuntimeError(f"Missing manifest fields: {sorted(needed - set(base.columns))}")
    local = {(int(x.year), int(x.month), int(x.request_chunk)): int(x.requests)
             for x in result.itertuples(index=False)}
    old = {(int(x.year), int(x.month), int(x.request_chunk)): int(x.requests)
           for x in base.itertuples(index=False)}
    if local != old or sum(local.values()) != num:
        raise RuntimeError("Request parquet/manifest mismatch; do not submit")
    blocks = []
    members: list[dict] = []
    date_set: set[str] = set()
    nrows = 0
    def flush():
        nonlocal members, date_set, nrows
        if not members:
            return
        blocks.append({
            "block": len(blocks), "requests": nrows,
            "distinct_dates": len(date_set),
            "first_date": min(date_set), "last_date": max(date_set),
            "years": ",".join(str(x) for x in sorted({m["year"] for m in members})),
            "members_json": json.dumps(members, separators=(",", ":")),
        })
        members, date_set, nrows = [], set(), 0
    for row in result.itertuples(index=False):
        dates = set(row.dates)
        size = int(row.requests)
        if size > max_requests or len(dates) > max_dates:
            raise ValueError(f"Single monthly request chunk too large: {row.year}-{row.month} C{row.request_chunk}")
        if members and (nrows + size > max_requests or len(date_set | dates) > max_dates):
            flush()
        members.append({
            "year": int(row.year), "month": int(row.month),
            "chunk": int(row.request_chunk), "requests": size,
        })
        date_set |= dates
        nrows += size
    flush()
    frame = pd.DataFrame(blocks)
    if frame.empty or frame.requests.sum() != num:
        raise RuntimeError("Packed plan doesn't cover the full request set")
    original = set(local)
    assigned = [(m["year"], m["month"], m["chunk"])
                for r in frame.itertuples(index=False) for m in json.loads(r.members_json)]
    if len(assigned) != len(set(assigned)) or set(assigned) != original:
        raise RuntimeError("Monthly chunk duplicated or missing in compact plan")
    return frame, {
        "request_rows": int(num), "monthly_chunks": len(original),
        "parquet_sha256": source_digest(requests),
        "manifest_sha256": source_digest(manifest),
        "max_requests": max_requests, "max_dates": max_dates,
    }


def fingerprint(frame: pd.DataFrame, provenance: dict) -> str:
    payload = {
        "input": provenance,
        "blocks": frame[["block", "requests", "distinct_dates",
                         "first_date", "last_date", "members_json"]].to_dict("records"),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def get_reference_module(repo: Path):
    refpath = find_legacy(repo)
    spec = importlib.util.spec_from_file_location("legacy21_reference", refpath)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {refpath}")
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def select_features(ee, members: list[dict]):
    # Build one filtered FeatureCollection per source year, never import all
    # 27 year assets for a single compact task.
    years = sorted({int(m["year"]) for m in members})
    selected = None
    for year in years:
        data = ee.FeatureCollection(f"{ASSET_ROOT}/standard_requests_{year}")
        filters = [ee.Filter.And(ee.Filter.eq("month", int(m["month"])),
                                 ee.Filter.eq("request_chunk", int(m["chunk"])))
                   for m in members if m["year"] == year]
        condition = filters[0] if len(filters) == 1 else ee.Filter.Or(*filters)
        piece = data.filter(condition)
        selected = piece if selected is None else selected.merge(piece)
    return selected


def verify_assets(ee, frame: pd.DataFrame, selected_blocks: set[int]):
    # Also validate imported type. The source manifest is the authoritative
    # row-count check; the compact schedule may cover only some year chunks.
    all_years = sorted({
        int(m["year"]) for row in frame.itertuples(index=False)
        if row.block in selected_blocks for m in json.loads(row.members_json)
    })
    for year in all_years:
        asset = f"{ASSET_ROOT}/standard_requests_{year}"
        info = ee.data.getAsset(asset)
        if info.get("type") != "TABLE":
            raise RuntimeError(f"Not a TABLE: {asset}")
        original_file = Path(verify_assets.calendar) / "gee_upload_standard" / f"standard_requests_{year}.csv"
        if not original_file.is_file():
            raise FileNotFoundError(original_file)
        expected = sum(1 for _ in original_file.open(encoding="utf-8-sig")) - 1
        raw = ee.FeatureCollection(asset)
        observed = int(raw.size().getInfo())
        if observed != expected:
            raise RuntimeError(f"{asset}: imported rows={observed}, CSV rows={expected}")
        attrs = ee.Feature(raw.first()).toDictionary(
            ["grid_x", "grid_y", "year", "month", "request_chunk", "date_str"]
        ).getInfo()
        for key in ("grid_x", "grid_y", "year", "month", "request_chunk"):
            if not isinstance(attrs.get(key), (int, float)):
                raise RuntimeError(f"{asset}: {key} is not numeric: {attrs}")
        if not isinstance(attrs.get("date_str"), str):
            raise RuntimeError(f"{asset}: date_str is not text")
        print(f"[ASSET PASS] {year} rows={observed:,}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=Path(r"E:\Global_flooding"))
    p.add_argument("--max-requests", type=int, default=60000)
    p.add_argument("--max-dates", type=int, default=540)
    p.add_argument("--blocks", help="Subset indices e.g. 0,3-5; otherwise all")
    p.add_argument("--submit", action="store_true", help="Actually submit tasks")
    p.add_argument("--verify-assets", action="store_true")
    p.add_argument("--plan-id", help="Required exact 12-character plan identifier for --submit")
    p.add_argument("--max-tasks", type=int, default=1, help="New tasks, 0=all")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--delay", type=float, default=1)
    return p.parse_args()


def main():
    a = parse_args()
    if a.max_tasks < 0 or a.delay < 0:
        raise ValueError("Invalid submission limits")
    root = a.base / "standard_calendar_20b"
    frame, provenance = build_plan(root, a.max_requests, a.max_dates)
    signature = fingerprint(frame, provenance)
    tag = signature[:12]
    out = root / "21_compact"
    out.mkdir(parents=True, exist_ok=True)
    freeze_path = out / "frozen_plan.json"
    frozen = None
    if freeze_path.exists():
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
        if frozen["signature"] != signature:
            raise RuntimeError(
                "Compact plan was frozen with different source/caps. "
                "Restore original settings; never reuse task names for changed data."
            )
    frame = frame.copy()
    frame["task_name"] = [f"STD32C_{tag[:8]}_B{int(i):03d}" for i in frame.block]
    frame["filename"] = [f"std32c_{tag[:8]}_b{int(i):03d}" for i in frame.block]
    frame.to_csv(out / "compact_task_plan.csv", index=False, encoding="utf-8-sig")
    (out / "compact_plan_provenance.json").write_text(
        json.dumps({"signature": signature, "parameters": provenance,
                    "blocks": len(frame)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    selected = parse_blocks(a.blocks, set(map(int, frame.block)))
    print(f"[COMPACT PLAN] {len(frame)} tasks; {int(frame.requests.sum()):,} requests; "
          f"max rows={int(frame.requests.max()):,}, max dates={int(frame.distinct_dates.max())}")
    print(f"[PLAN ID] {tag}  (paste into --plan-id to approve submission)")
    print(f"[PLAN FILE] {out / 'compact_task_plan.csv'}")
    print("[NOTE] Fewer queued tasks ≠ faster compute. Test a high-date block and high-row block before bulk export.")
    print(frame[["block","requests","distinct_dates","years","task_name"]].to_string(index=False))
    if not a.submit and not a.verify_assets:
        print("[DRY RUN] No GEE task created, no assets touched.")
        return
    if a.submit and a.plan_id != tag:
        raise RuntimeError("For submission supply --plan-id EXACTLY as shown in preview")
    if a.submit:
        qc = root / "calibration_qc" / "21c_calibration_status.json"
        if not qc.is_file() or json.loads(qc.read_text(encoding="utf-8")).get("status") != "PASS":
            raise RuntimeError("Numerical calibration PASS is mandatory; refusing production submission.")
    import ee
    ee.Initialize(project=PROJECT)
    verify_assets.calendar = root
    verify_assets(ee, frame, selected)
    if not a.submit:
        print("[VERIFIED] No tasks submitted.")
        return
    prior_log = out / "submitted_tasks.jsonl"
    prior = {}
    if prior_log.exists():
        for line in prior_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                prior.setdefault(item["name"], []).append(item)
    tasks = ee.data.getTaskList()
    legacy = [t.get("description", "") for t in tasks
              if str(t.get("description", "")).startswith("STDClimate_")
              and t.get("state") in ACTIVE]
    if legacy:
        raise RuntimeError(
            f"Existing STDClimate tasks detected ({len(legacy)}). Reconcile "
            "overlapping climate_id coverage before changing plans; no tasks submitted."
        )
    remote = {}
    for task in tasks:
        name = task.get("description", "")
        if name.startswith("STD32C_"):
            remote.setdefault(name, []).append(task.get("state", "UNKNOWN"))
    if frozen is None:
        freeze_path.write_text(
            json.dumps({"signature": signature, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()},
                       indent=2), encoding="utf-8"
        )
    module = get_reference_module(Path(__file__).resolve().parents[2])
    process, _ = module.make_ee_functions()
    count = 0
    for row in frame.itertuples(index=False):
        if row.block not in selected:
            continue
        states = remote.get(row.task_name, [])
        if any(state in ACTIVE for state in states):
            print(f"[SKIP REMOTE] {row.task_name}: {states}")
            continue
        if row.task_name in prior and not states:
            print(f"[SKIP PRIOR LOCAL LOG] {row.task_name}; manually check historical Tasks/Drive")
            continue
        if states and not a.retry_failed:
            print(f"[SKIP TERMINAL] {row.task_name}: {states}")
            continue
        if states and any(x not in FAILURE for x in states):
            print(f"[SKIP UNRECOGNIZED STATE] {row.task_name}: {states}")
            continue
        subset = select_features(ee, json.loads(row.members_json))
        output = process(subset)
        task = ee.batch.Export.table.toDrive(
            collection=output, description=row.task_name,
            folder="Groundsource_Standard_Climate_Compact",
            fileNamePrefix=row.filename,
            fileFormat="CSV", selectors=module.OUTPUT_COLS,
        )
        task.start()
        record = {
            "name": row.task_name, "task_id": task.id, "plan_id": tag,
            "block": int(row.block), "requests": int(row.requests),
            "dates": int(row.distinct_dates), "years": row.years,
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        with prior_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[STARTED] {row.task_name} id={task.id}, "
              f"{row.requests} rows, {row.distinct_dates} dates")
        count += 1
        if a.max_tasks and count >= a.max_tasks:
            break
        time.sleep(a.delay)
    print(f"[DONE] newly submitted={count}; submission log={prior_log}")


if __name__ == "__main__":
    main()
