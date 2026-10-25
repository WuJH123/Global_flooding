"""Synthetic Stage26 environmental modifier tests: no external datasets."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import duckdb

ROOT=Path(__file__).resolve().parents[1]
p=ROOT/"pipelines"/"r33"/"26_city_environment_modifiers.py"
spec=importlib.util.spec_from_file_location("stage26test",p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_static_one_row_grid_and_moderation(tmp_path):
    rng=np.random.default_rng(2032)
    num=360
    n=num*4
    group=np.repeat(np.arange(num),4)
    climate=rng.lognormal(1,0.6,size=n)
    api=rng.lognormal(1,0.4,size=n)
    sm=np.clip(rng.normal(.25,.08,size=n),0,1)
    pan=pd.DataFrame({
        "grid_id":["g"+str(i) for i in group],
        "calendar_stratum_id":["s"+str(i) for i in group],
        "year":np.repeat(np.where(np.arange(num)<280,2021,2024),4),
        "grid_x":np.repeat(np.arange(num)*25,4),
        "grid_y":0,
        "episode_onset":np.tile([1,0,0,0],num),
        "imerg_p24_utc_mm_mean":climate,
        "era5_api7_mm_mean":api,
        "imerg_p3h_max_mm_max":np.maximum(0,climate*.65+rng.uniform(size=n)),
        "era5_sm1_prevday_mean":sm
    })
    context=pd.DataFrame({
        "grid_id":["g"+str(i) for i in range(num)],
        "city_id":["city"+str(i) for i in range(num)],
        "cru_pre_annual_mm":rng.lognormal(7,0.7,size=num),
        "basin_urb_pc_sse":rng.uniform(0,100,size=num),
        "basin_slope_degrees":rng.uniform(0,12,size=num),
        "merit_local_relief_m_approx":rng.uniform(0,300,size=num)
    })
    f=tmp_path/"static.parquet"
    con=duckdb.connect()
    con.register("x",context)
    con.execute("COPY x TO ? (FORMAT PARQUET)",[str(f)])
    con.close()
    s=m.load_context(f,["cru_pre_annual_mm","basin_urb_pc_sse"])
    assert len(s)==num
    valid=m.prepare_static(pan,s,["cru_pre_annual_mm","basin_urb_pc_sse"])
    assert valid.all()
    assert pan.city_id.notna().all()
    results,metric=m.moderation_fit(pan,"basin_urb_pc_sse",tmp_path,"temporal",2026,25,99)
    assert set(results.model)=={"M0_common","M1_env_modulation"}
    assert "P24_x_ENV" in set(results.term)
    assert "API7_x_ENV" in set(results.term)
    assert metric["test_strata"]==80
    assert metric["test_onsets"]==80
    assert np.isfinite(metric["gain"])
    assert metric["gain_ci_low"]<=metric["gain_ci_high"]


def test_missing_environment_rejected_without_imputation():
    df=pd.DataFrame({"grid_id":["g1","g2"],"cru_pre_annual_mm":[1000,np.nan]})
    context=pd.DataFrame({"grid_id":["g1","g2"],"city_id":["C1","C2"],
                          "cru_pre_annual_mm":[1000,np.nan]})
    ok=m.prepare_static(df,context,["cru_pre_annual_mm"])
    assert list(ok)==[True,False]
