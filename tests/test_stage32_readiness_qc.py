"""Readiness audit: synthetic missing weather and uninformative stratum."""
import importlib.util
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"pipelines"/"r32"/"22c_validate_compact_model_input.py"
SPEC=importlib.util.spec_from_file_location("r32audit22c",SCRIPT)
audit_mod=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_mod)


def synthetic(tmp_path):
    rows=[]
    for group, days, missing_sm, continuing in [
        ("A",[1,8,15],None,None),
        ("B",[2,9],9,None),
        ("C",[3,10],None,10),
    ]:
        for day in days:
            onset=int(day==days[0])
            repeat=int(day==continuing)
            row={
                "climate_id":f"g1_2020-07-{day:02d}",
                "grid_id":"g1","grid_x":1,"grid_y":2,
                "day":f"2020-07-{day:02d}","year":2020,
                "calendar_stratum_id":group,
                "reported_non_onset_day":repeat,
                "reported_flood_day":int(onset or repeat),
                "episode_onset":onset,
                "eligible_risk_day":int(not repeat),
                "y_episode_onset":onset,
            }
            row.update({k:1.5 for k in audit_mod.WEATHER_COLS})
            row["era5_sm1_prevday_mean"]=None if day==missing_sm else .24
            row["era5_sm1_prevday_max"]=None if day==missing_sm else .30
            rows.append(row)
    path=tmp_path/"joined.parquet"
    con=duckdb.connect()
    con.register("data",pd.DataFrame(rows))
    con.execute("COPY data TO ? (FORMAT PARQUET)",[str(path)])
    con.close()
    return path


def test_22c_distinguishes_pre_weather_and_complete_risk_set(tmp_path):
    path=synthetic(tmp_path)
    out=tmp_path/"qc"
    audit_mod.audit(path,out,expected_rows=7,expected_onsets=3)
    obj=json.loads((out/"22c_model_readiness.json").read_text())
    assert obj["status"]=="PASS_STRUCTURE"
    assert obj["raw"]["rows"]==7
    assert obj["raw"]["pre_weather_risk_days"]==6
    assert obj["raw"]["bad_model_weather_days"]==1
    assert obj["raw"]["bad_any_weather_days"]==1
    assert obj["model_readiness"]["eligible_strata"]==1
    assert obj["model_readiness"]["eligible_model_days"]==3
    assert obj["model_readiness"]["eligible_model_onsets"]==1
    assert obj["model_readiness"]["dropped_onsets"]==2


def test_22c_rejects_wrong_expected_onset_count(tmp_path):
    path=synthetic(tmp_path)
    with pytest.raises(RuntimeError,match="onset_days"):
        audit_mod.audit(path,tmp_path/"badqc",expected_rows=7,expected_onsets=4)



def test_22c_uses_explicit_quoted_duckdb_reserved_aliases():
    """Guard Windows/older DuckDB: unquoted rows/days aliases can fail parsing."""
    content = SCRIPT.read_text(encoding="utf-8")
    assert 'COUNT(*) AS "rows"' in content
    assert 'COUNT(*) AS "days"' in content
    assert "COUNT(*) rows," not in content
    assert "COUNT(*) AS days," not in content
