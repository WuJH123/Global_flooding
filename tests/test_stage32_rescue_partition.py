"""No-cloud tests for splitting failed compact exports without altering completed ones."""
import importlib.util
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
R32=ROOT/"pipelines"/"r32"

def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

base=module(R32/"21f_compact_crossyear_submit.py","stage21f_test_for_rescue")
rescue=module(R32/"21g_rescue_failed_compact.py","stage21g_rescue_test")

def fixture_calendar(tmp_path):
    root=tmp_path/"standard_calendar_20b"
    (root/"21_compact").mkdir(parents=True)
    dates=[]
    for month in (1,2,3,4):
        for day in (1,2,3):
            dates.append({"climate_id":f"c_{month}_{day}", "year":2020,"month":month,
                          "request_chunk":0,"date_str":f"2020-{month:02d}-{day:02d}"})
    df=pd.DataFrame(dates)
    con=duckdb.connect()
    con.register("src",df)
    con.execute("COPY src TO ? (FORMAT PARQUET)",
                [str(root/"20_missing_climate_requests_chunked.parquet")])
    con.close()
    df.groupby(["year","month","request_chunk"]).size().rename("requests").reset_index().to_csv(
        root/"20_missing_task_manifest.csv",index=False
    )
    original, prov=base.build_plan(root,100,100)
    signature=base.fingerprint(original,prov)
    original["task_name"]=[f"STD32C_{signature[:8]}_B{i:03d}" for i in original.block]
    original["filename"]=[f"std32c_{signature[:8]}_b{i:03d}" for i in original.block]
    original.to_csv(root/"21_compact"/"compact_task_plan.csv",index=False)
    return root

def test_rescue_partitions_source_months_without_overlap(tmp_path):
    root=fixture_calendar(tmp_path)
    plan, signature, _=rescue.build_rescue_plan(root,[0],6,8,100,100)
    assert len(plan)==2
    assert list(plan.requests)==[6,6]
    assert sum(plan.requests)==12
    assert all(int(row.distinct_dates)<=8 for row in plan.itertuples())
    assert list(plan.parent_block)==[0,0]
    original=pd.read_csv(root/"21_compact"/"compact_task_plan.csv")
    before=json.loads(original.iloc[0].members_json)
    after=[m for row in plan.itertuples() for m in json.loads(row.members_json)]
    assert before==after
    again, second_sig, _=rescue.build_rescue_plan(root,[0],6,8,100,100)
    assert signature==second_sig
    assert plan.task_name.tolist()==again.task_name.tolist()

def test_rescue_bad_caps_do_not_silently_drop_rows(tmp_path):
    root=fixture_calendar(tmp_path)
    with pytest.raises(ValueError,match="Indivisible chunk"):
        rescue.build_rescue_plan(root,[0],2,8,100,100)
    with pytest.raises(ValueError,match="Unknown compact parent"):
        rescue.build_rescue_plan(root,[1],6,8,100,100)
