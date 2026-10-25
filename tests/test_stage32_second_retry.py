"""Cloud-free test: second-level retry is a strictly disjoint partition."""
import importlib.util
import json
from pathlib import Path
import duckdb
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"pipelines"/"r32"/"21h_retry_failed_rescue_parts.py"
SPEC=importlib.util.spec_from_file_location("stage21h_recovery_test",SCRIPT)
retry=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retry)


def make_fixture(tmp_path):
    root=tmp_path/"standard_calendar_20b"
    (root/"21_rescue").mkdir(parents=True)
    rows=[]
    months=[(2023,1,0,3),(2023,2,0,5),(2023,3,0,6),(2023,4,0,4)]
    for year,month,chunk,n in months:
        for day in (1,2,3):
            for index in range(n):
                rows.append({"year":year,"month":month,"request_chunk":chunk,
                             "date_str":f"{year}-{month:02d}-{day:02d}",
                             "climate_id":f"g{month}{day}_{index}"})
    df=pd.DataFrame(rows)
    conn=duckdb.connect()
    conn.register("src",df)
    conn.execute("COPY src TO ? (FORMAT PARQUET)",
                 [str(root/"20_missing_climate_requests_chunked.parquet")])
    conn.close()
    rescued=[
        {"parent_block":8,"recovery_part":1,"requests":(3+5)*3,
         "members_json":json.dumps([
             {"year":2023,"month":1,"chunk":0,"requests":9},
             {"year":2023,"month":2,"chunk":0,"requests":15}
         ])},
        {"parent_block":8,"recovery_part":2,"requests":(6+4)*3,
         "members_json":json.dumps([
             {"year":2023,"month":3,"chunk":0,"requests":18},
             {"year":2023,"month":4,"chunk":0,"requests":12}
         ])}
    ]
    pd.DataFrame(rescued).to_csv(root/"21_rescue"/"rescue_plan.csv",index=False)
    return root


def test_granular_plan_exact_date_partition(tmp_path):
    root=make_fixture(tmp_path)
    plan,digest=retry.prepare_plan(root,[(8,1),(8,2)],10,2)
    assert int(plan.requests.sum())==54
    assert len(plan)>2
    assert all(plan.requests<=10)
    assert all(plan.distinct_dates<=2)
    assert len(digest)==64
    expected={(2023,1,0,f"2023-01-{d:02d}") for d in (1,2,3)}
    expected|={(2023,2,0,f"2023-02-{d:02d}") for d in (1,2,3)}
    expected|={(2023,3,0,f"2023-03-{d:02d}") for d in (1,2,3)}
    expected|={(2023,4,0,f"2023-04-{d:02d}") for d in (1,2,3)}
    observed=[]
    for item in plan.itertuples(index=False):
        for sub in json.loads(item.members_json):
            observed.extend((sub["year"],sub["month"],sub["chunk"],date) for date in sub["dates"])
    assert len(observed)==len(expected)
    assert set(observed)==expected
    second,again=retry.prepare_plan(root,[(8,1),(8,2)],10,2)
    assert digest==again
    assert second.task_name.tolist()==plan.task_name.tolist()


def test_reject_nonexistent_or_invalid_requests(tmp_path):
    root=make_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="Missing upstream rescue targets"):
        retry.prepare_plan(root,[(8,7)],10,2)
    with pytest.raises(RuntimeError,match="One grid-day"):
        retry.prepare_plan(root,[(8,2)],3,2)
    with pytest.raises(ValueError):
        retry.selected_pairs("8:1,8:1")
