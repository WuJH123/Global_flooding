"""CI: Stage27 same-eligible risk sets, aggregation parity and Stage28 joint context."""
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd
import duckdb

ROOT=Path(__file__).resolve().parents[1]

def script(path):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location("test_"+p.stem,p)
    m=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


def panel(n=300):
    rng=np.random.default_rng(2708)
    group=np.repeat(np.arange(n),4)
    daily=np.maximum(0,rng.lognormal(1.6,.65,size=4*n)-1)
    short=daily*rng.uniform(.25,.75,size=4*n)
    api=rng.lognormal(1.8,.8,size=4*n)
    sm=rng.uniform(.1,.5,size=4*n)
    return pd.DataFrame({
        "grid_id":["g"+str(k) for k in group],
        "grid_x":group*25,"grid_y":0,
        "calendar_stratum_id":["s"+str(k) for k in group],
        "year":np.repeat(np.where(np.arange(n)<230,2020,2024),4),
        "episode_onset":np.tile([0,1,0,0],n),
        "reported_non_onset_day":0,
        "imerg_p24_utc_mm_mean":daily,
        "imerg_p24_utc_mm_max":daily*1.4,
        "imerg_p3h_max_mm_mean":short,
        "imerg_p3h_max_mm_max":short*1.3,
        "era5_api7_mm_mean":api,"era5_sm1_prevday_mean":sm,
    })


def test_27_same_sample_all_rainfall_definitions(tmp_path):
    m=script(Path("pipelines/r32/27_rainfall_parity_identifiability.py"))
    frame=panel()
    file=tmp_path/"p.parquet"
    con=duckdb.connect()
    con.register("t",frame)
    con.execute("COPY t TO ? (FORMAT PARQUET)",[str(file)])
    con.close()
    df,qc=m.load_common(file)
    assert qc["common_days"]==1200
    assert qc["common_strata"]==300
    assert qc["common_onsets"]==300
    qc2=m.observed_support(df)
    assert all(z["p3h_gt_p24_same_stat_count"]==0 for z in qc2)
    for name in ("mean_mean","max_max","mean_max"):
        c,g,meta=m.fit_pair(df,tmp_path,"temporal",name,25,2026,99)
        assert len(c)>4 and len(g)==2
        assert all(v["gain_ci_low"]<=v["gain_ci_high"] for v in g)
        assert meta["rainfall_representation"]==name


def test_28_same_static_complete_sample_and_nesting(tmp_path):
    m=script(Path("pipelines/r33/28_joint_terrain_context.py"))
    df=panel()
    n=300
    rng=np.random.default_rng(2808)
    static=pd.DataFrame({
        "grid_id":["g"+str(k) for k in range(n)],
        "city_id":["city"+str(k) for k in range(n)],
        "merit_local_relief_m_approx":rng.lognormal(4,1.0,n),
        "basin_slope_degrees":rng.uniform(0.5,18,n),
        "cru_pre_annual_mm":rng.lognormal(7,.5,n),
        "basin_urb_pc_sse":rng.uniform(0,100,n)
    })
    data,cover=m.common_static(df,static)
    assert cover["common_grid_days"]==1200
    assert cover["common_onsets"]==300
    correlations=m.static_correlations(data)
    assert correlations["unique_grids"]==300
    c,g,t=m.fit_validation(data,tmp_path,"temporal","mean_mean",2026,25,99)
    assert len(g)==5
    assert any(v["baseline"]=="J2_slope" and v["augmented"]=="J3_relief_slope" for v in g)
    assert all(v["gain_ci_low"]<=v["gain_ci_high"] for v in g)
    assert "environment" in t


def test_28_static_missing_means_common_cohort_loss():
    m=script(Path("pipelines/r33/28_joint_terrain_context.py"))
    df=panel(20)
    fixed=pd.DataFrame({
        "grid_id":["g"+str(k) for k in range(20)],
        "city_id":["c"+str(k) for k in range(20)],
        "merit_local_relief_m_approx":list(range(19))+[np.nan],
        "basin_slope_degrees":2.0,
        "cru_pre_annual_mm":1000.0,
        "basin_urb_pc_sse":20.0,
    })
    data,qc=m.common_static(df,fixed)
    assert len(data)==76
    assert qc["common_onsets"]==19
