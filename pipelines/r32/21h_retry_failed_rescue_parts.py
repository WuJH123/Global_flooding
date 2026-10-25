"""Stage21h: re-partition ONLY failed Stage21g child tasks, by individual date.

Preserves old 21f and 21g immutable plans and successful exports. Uses the
original calibrated Stage21 weather processing algorithm without changes.
A retry shard contains a disjoint exact subset of source (grid,date) requests.
Default: preview only. Submission requires exact frozen plan ID and verifies
the selected Stage21g tasks have FAILED server-side by recorded task ID.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import time
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd


def load_stage21f():
    script = Path(__file__).with_name("21f_compact_crossyear_submit.py")
    spec = importlib.util.spec_from_file_location("stage21f_retry2", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def selected_pairs(raw: str) -> list[tuple[int,int]]:
    out = []
    for v in raw.split(","):
        left, right = v.strip().split(":")
        out.append((int(left), int(right)))
    if not out or len(set(out)) != len(out):
        raise ValueError("Select unique existing parent:part identifiers e.g. 8:1,8:2")
    return sorted(out)


def prepare_plan(root: Path, pairs: list[tuple[int,int]], max_requests: int, max_dates: int):
    if max_requests < 1 or max_dates < 1:
        raise ValueError("Both limits must be positive")
    upstream = root / "21_rescue" / "rescue_plan.csv"
    primary = root / "20_missing_climate_requests_chunked.parquet"
    for f in (upstream, primary):
        if not f.is_file():
            raise FileNotFoundError(f)
    rescue = pd.read_csv(upstream, encoding="utf-8-sig")
    keypairs = {(int(r.parent_block), int(r.recovery_part)) for r in rescue.itertuples(index=False)}
    if len(keypairs) != len(rescue):
        raise RuntimeError("Upstream rescue plan has repeated parent:part")
    if not set(pairs) <= keypairs:
        raise RuntimeError(f"Missing upstream rescue targets: {set(pairs)-keypairs}")
    selected = {}
    for key in pairs:
        row = rescue.loc[(rescue.parent_block == key[0]) &
                         (rescue.recovery_part == key[1])].iloc[0]
        selected[key] = row
    requested_months = {
        (int(m["year"]), int(m["month"]), int(m["chunk"]))
        for row in selected.values() for m in json.loads(row.members_json)
    }
    db = duckdb.connect()
    try:
        daily = db.execute("""
          SELECT CAST(year AS INTEGER) AS year,
                 CAST(month AS INTEGER) AS month,
                 CAST(request_chunk AS INTEGER) AS chunk,
                 CAST(date_str AS VARCHAR) AS date_str,
                 COUNT(*) AS n,
                 COUNT(DISTINCT climate_id) AS u
          FROM read_parquet(?)
          GROUP BY 1,2,3,4
          ORDER BY 1,2,3,4
        """,[str(primary)]).fetchdf()
    finally:
        db.close()
    date_lookup = defaultdict(list)
    for r in daily.itertuples(index=False):
        key=(int(r.year),int(r.month),int(r.chunk))
        if key in requested_months:
            if int(r.n)!=int(r.u):
                raise RuntimeError(f"Duplicate climate IDs on {key}/{r.date_str}")
            date_lookup[key].append((str(r.date_str), int(r.n)))
    all_parts = []
    for key, row in selected.items():
        entries = json.loads(row.members_json)
        atomic = []
        original = {}
        for m in entries:
            monthkey=(int(m["year"]),int(m["month"]),int(m["chunk"]))
            data=date_lookup[monthkey]
            if not data or sum(v for _,v in data)!=int(m["requests"]):
                raise RuntimeError(f"Upstream month count mismatch: {monthkey}")
            original[monthkey]=set(d for d,_ in data)
            for date, count in data:
                if count>max_requests:
                    raise RuntimeError(f"One grid-day group larger than cap: {date} {count}")
                atomic.append((monthkey,date,count))
        chunks = []
        days = set()
        size = 0
        def flush():
            nonlocal chunks, days, size
            if not chunks:
                return
            grouped = {}
            for mon, date, count in chunks:
                m = grouped.setdefault(mon, {"year":mon[0],"month":mon[1],
                                             "chunk":mon[2],"dates":[],"requests":0})
                m["dates"].append(date)
                m["requests"] += count
            all_parts.append({
                "parent_block": key[0], "parent_part": key[1],
                "retry_part": sum(1 for x in all_parts if (x["parent_block"],x["parent_part"])==key),
                "requests":size, "distinct_dates":len(days),
                "members_json":json.dumps(list(grouped.values()),separators=(",",":")),
            })
            chunks, days, size = [], set(), 0
        for monthkey,date,count in atomic:
            if chunks and (size+count>max_requests or len(days|{date})>max_dates):
                flush()
            chunks.append((monthkey,date,count))
            days.add(date)
            size += count
        flush()
        newdates=defaultdict(set)
        emitted=0
        for x in all_parts:
            if (x["parent_block"],x["parent_part"])!=key:
                continue
            emitted+=x["requests"]
            for m in json.loads(x["members_json"]):
                mk=(int(m["year"]),int(m["month"]),int(m["chunk"]))
                if newdates[mk].intersection(m["dates"]):
                    raise RuntimeError(f"Duplicate request dates in {mk}")
                newdates[mk].update(m["dates"])
        if newdates!=original or emitted!=int(row.requests):
            raise RuntimeError(f"Retry plan is not an exact partition of B{key[0]:03d}_S{key[1]:02d}")
    stage=load_stage21f()
    signature=hashlib.sha256(json.dumps({
        "upstream_sha256":stage.source_digest(upstream),
        "source_sha256":stage.source_digest(primary),
        "targets":pairs,"caps":[max_requests,max_dates],"parts":all_parts,
    },sort_keys=True,separators=(",",":")).encode()).hexdigest()
    tag=signature[:8]
    plan=pd.DataFrame(all_parts)
    plan["block"]=range(len(plan))
    plan["task_name"]=[
        f"STD32T_{tag}_B{r.parent_block:03d}_S{r.parent_part:02d}_T{r.retry_part:02d}"
        for r in plan.itertuples(index=False)]
    plan["filename"]=[
        f"std32t_{tag}_b{r.parent_block:03d}_s{r.parent_part:02d}_t{r.retry_part:02d}"
        for r in plan.itertuples(index=False)]
    return plan,signature


def verify_failed_children(ee, root: Path, pairs):
    ledger=root/"21_rescue"/"rescue_submissions.jsonl"
    if not ledger.is_file():
        raise FileNotFoundError(ledger)
    records=[json.loads(s) for s in ledger.read_text(encoding="utf-8").splitlines() if s.strip()]
    for p,s in pairs:
        hits=[r for r in records if int(r["parent_block"])==p and int(r["part"])==s]
        if len(hits)!=1:
            raise RuntimeError(f"Expected one original task for B{p:03d}_S{s:02d}, got {len(hits)}")
        status=ee.data.getTaskStatus([hits[0]["task_id"]])
        if not status or status[0].get("state")!="FAILED":
            raise RuntimeError(f"Target B{p:03d}_S{s:02d} must be FAILED; got {status}")
        print(f"[FAILED CHILD VERIFIED] B{p:03d}_S{s:02d} {hits[0]['task_id']}")


def select_from_year_assets(ee, members, stage):
    by_year=defaultdict(list)
    for m in members:
        if not m.get("dates"):
            raise RuntimeError(f"Empty date subset in {m}")
        by_year[int(m["year"])].append(m)
    merged=None
    for year in sorted(by_year):
        base=ee.FeatureCollection(f"{stage.ASSET_ROOT}/standard_requests_{year}")
        filters=[
            ee.Filter.And(
                ee.Filter.eq("month",int(m["month"])),
                ee.Filter.eq("request_chunk",int(m["chunk"])),
                ee.Filter.inList("date_str",list(m["dates"])),
            ) for m in by_year[year]
        ]
        filt=filters[0] if len(filters)==1 else ee.Filter.Or(*filters)
        piece=base.filter(filt)
        merged=piece if merged is None else merged.merge(piece)
    return merged


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--targets",default="8:1,8:2")
    p.add_argument("--max-requests",type=int,default=10000)
    p.add_argument("--max-dates",type=int,default=90)
    p.add_argument("--parts",help="Global retry-plan indices, e.g. 0,2-3")
    p.add_argument("--plan-id",help="Exact 12-character ID from preview")
    p.add_argument("--submit",action="store_true")
    p.add_argument("--max-tasks",type=int,default=1,help="0 means all not yet submitted")
    args=p.parse_args()
    if args.max_tasks<0:
        raise ValueError("--max-tasks must be >=0")
    root=args.base/"standard_calendar_20b"
    pairs=selected_pairs(args.targets)
    plan,sig=prepare_plan(root,pairs,args.max_requests,args.max_dates)
    store=root/"21_retry"
    store.mkdir(parents=True,exist_ok=True)
    frozen=store/"frozen_retry_plan.json"
    if frozen.exists() and json.loads(frozen.read_text(encoding="utf-8")).get("signature")!=sig:
        raise RuntimeError("Existing retry plan frozen. Restore the original targets/caps.")
    plan.to_csv(store/"retry_plan.csv",index=False,encoding="utf-8-sig")
    summary={"signature":sig,"targets":pairs,"requests":int(plan.requests.sum()),
             "task_count":len(plan),"max_requests":args.max_requests,
             "max_dates":args.max_dates}
    (store/"retry_plan_meta.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print("[RETRY PLAN]",json.dumps(summary))
    print("[PLAN ID]",sig[:12])
    print(plan[["block","parent_block","parent_part","retry_part","requests","distinct_dates","task_name"]].to_string(index=False))
    print("[PLAN FILE]",store/"retry_plan.csv")
    if not args.submit:
        print("[PREVIEW ONLY] No Earth Engine API called; successful outputs untouched.")
        return
    if args.plan_id!=sig[:12]:
        raise RuntimeError("Exact 12-character --plan-id is required for submission")
    stage=load_stage21f()
    weather_source=stage.find_legacy(Path(__file__).resolve().parents[2])
    qcfile=root/"calibration_qc"/"21c_calibration_status.json"
    if not qcfile.is_file():
        raise FileNotFoundError(qcfile)
    qc=json.loads(qcfile.read_text(encoding="utf-8"))
    if qc.get("status")!="PASS" or qc.get("weather_source_sha256")!=stage.source_digest(weather_source):
        raise RuntimeError("Current canonical weather algorithm not calibrated PASS")
    import ee
    ee.Initialize(project=stage.PROJECT)
    verify_failed_children(ee,root,pairs)
    selected=stage.parse_blocks(args.parts,set(map(int,plan.block)))
    stage.verify_assets.calendar=root
    stage.verify_assets(ee,plan,selected)
    logs=store/"retry_submissions.jsonl"
    prior=set()
    if logs.exists():
        prior={json.loads(s)["task_name"] for s in logs.read_text(encoding="utf-8").splitlines() if s.strip()}
    remote={str(t.get("description")) for t in ee.data.getTaskList()
            if str(t.get("description","")).startswith("STD32T_")}
    if not frozen.exists():
        frozen.write_text(json.dumps(summary,indent=2),encoding="utf-8")
    reference=stage.get_reference_module(Path(__file__).resolve().parents[2])
    compute,_=reference.make_ee_functions()
    created=0
    for row in plan.itertuples(index=False):
        if int(row.block) not in selected:
            continue
        if row.task_name in prior or row.task_name in remote:
            print("[SKIP ALREADY KNOWN]",row.task_name)
            continue
        collection=select_from_year_assets(ee,json.loads(row.members_json),stage)
        output=compute(collection)
        task=ee.batch.Export.table.toDrive(
            collection=output,description=row.task_name,
            folder="Groundsource_Standard_Climate_Compact_Retry2",
            fileNamePrefix=row.filename,fileFormat="CSV",
            selectors=reference.OUTPUT_COLS,
        )
        task.start()
        item={"task_name":row.task_name,"task_id":task.id,
              "parent_block":int(row.parent_block),"parent_part":int(row.parent_part),
              "retry_part":int(row.retry_part),"requests":int(row.requests),
              "utc":dt.datetime.now(dt.timezone.utc).isoformat()}
        with logs.open("a",encoding="utf-8") as f:
            f.write(json.dumps(item)+"\n")
        print("[STARTED]",item)
        created+=1
        if args.max_tasks and created>=args.max_tasks:
            break
        time.sleep(.5)
    print(f"[DONE] newly submitted={created}; ledger={logs}")


if __name__=="__main__":
    main()
