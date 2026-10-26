"""Synthetic integration tests for 29–35; require no downloaded urban/flood datasets."""
from pathlib import Path
import importlib.util
import json
import numpy as np
import pandas as pd
import duckdb

ROOT=Path(__file__).resolve().parents[1]
def mod(path):
    path=ROOT/path
    spec=importlib.util.spec_from_file_location("test_new_"+path.stem,path)
    r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
    return r

def panel(n=300):
    rng=np.random.default_rng(94)
    groups=np.repeat(np.arange(n),4)
    rainfall=rng.lognormal(1,.5,4*n)
    peak=rainfall*rng.uniform(.1,.7,4*n)
    api=rng.lognormal(1,.4,4*n)
    soil=rng.uniform(.08,.45,4*n)
    return pd.DataFrame({
        "grid_id":["g"+str(j) for j in groups],
        "grid_x":25*groups,"grid_y":0,
        "calendar_stratum_id":["s"+str(j) for j in groups],
        "year":np.repeat(np.where(np.arange(n)<225,2020,2024),4),
        "episode_onset":np.tile([1,0,0,0],n),
        "reported_non_onset_day":0,
        "imerg_p24_utc_mm_mean":rainfall,
        "imerg_p24_utc_mm_max":rainfall*1.2,
        "imerg_p3h_max_mm_mean":peak,
        "imerg_p3h_max_mm_max":peak*1.3,
        "era5_api7_mm_mean":api,
        "era5_sm1_prevday_mean":soil,
    })

def test_29_residual_independence_and_nested_models(tmp_path):
    s=mod(Path("pipelines/r32/29_rainfall_structure.py"))
    df=panel()
    train=df.year.le(2022).to_numpy()
    c,params=s.residual_peak(df.imerg_p24_utc_mm_mean.to_numpy(),
                             df.imerg_p3h_max_mm_mean.to_numpy(),train)
    assert np.isfinite(c).all()
    assert abs(float(c[train].mean()))<1e-8
    assert abs(params["train_corr_P24_C3"])<1e-9
    coefs,gains,manifest=s.model_one(df,tmp_path,"temporal","mean",25,2026,99)
    assert len(gains)==3
    assert all(v["gain_ci_low"]<=v["gain_ci_high"] for v in gains)
    assert manifest["source_P3H"]=="imerg_p3h_max_mm_mean"
    assert gains[0]["null_conditional_logloss"]>1.38

def test_30_real_years_only_and_weighted_city(tmp_path):
    s=mod(Path("pipelines/r33/30_urban_context.py"))
    root=tmp_path/"Groundsource_GHSL_2020";root.mkdir()
    rows=pd.DataFrame({
        "grid_id":["0_0","1_0"],
        "pop_total":[100,300],"pop_density":[4,12],
        "urban_pop_share":[.2,.8],"dense_pop_share":[.1,.4],
        "built_fraction":[.1,.5],"urban_area_fraction":[.3,.7],
        "dense_area_fraction":[.1,.4]})
    p=root/"groundsource_ghsl_2020_chunk_0.csv"
    rows.to_csv(p,index=False)
    paths=s.discover(tmp_path)
    gh,log=s.read_ghsl(paths,{"0_0","1_0"})
    static=pd.DataFrame({"grid_id":["0_0","1_0"],"city_id":["cityX","cityX"]})
    links,kind=s.grid_to_city(static,None)
    cities=s.aggregate_cities(gh,links)
    assert len(cities)==1
    assert abs(cities.iloc[0]["ghsl_built_fraction_wmean"]-.3)<1e-10
    links2=pd.DataFrame({"grid_id":["0_0","1_0"],"city_id":["cityX","cityX"],
                         "overlap_km2":[1,3]})
    w=s.aggregate_cities(gh,links2)
    assert abs(w.iloc[0]["ghsl_built_fraction_wmean"]-.4)<1e-10

def test_31_no_fake_hand_and_coverage_flags():
    s=mod(Path("pipelines/r33/31_hydrogeomorphic_features.py"))
    hand=np.array([[0.,1.],[4.,9.]])
    imp=np.array([[.5,.5],[.1,.1]])
    r=s.geom_stats(hand,np.array([[100,105],[110,140.]]),imp)
    assert r["hand_lt2_fraction"]==.5
    assert r["impervious_mean_fraction"]==.3
    assert 0<r["impervious_surface_weighted_hand_lt2_share"]<1
    bad=s.geom_stats(np.array([[np.nan,1],[np.nan,np.nan]]),None,None)
    assert np.isnan(bad["hand_p10_m"])

def test_32_city_holdout_same_as_stage23_and_no_reporting():
    s=mod(Path("pipelines/r33/32_mechanism_feature_selection.py"))
    st=mod(Path("pipelines/r32/23_conditional_poisson_stage32.py"))
    cities=["city"+str(i) for i in range(180)]
    a=s.hash_test(cities,2026)
    b=np.array([st.digest(str(v)+"_"+str(2026))%100<20 for v in cities])
    assert np.array_equal(a,b)
    rng=np.random.default_rng(25)
    df=pd.DataFrame({
        "city_id":cities,
        "ghsl_built_fraction_wmean":rng.uniform(0,1,180),
        "ghsl_urban_area_fraction_wmean":rng.uniform(0,1,180),
        "merit_local_relief_m_approx":rng.uniform(0,100,180),
        "basin_slope_degrees":rng.uniform(0,6,180),
        "cru_pre_annual_mm":rng.uniform(100,1800,180),
        "ghsl_pop_density_wmean":rng.uniform(0,2000,180)})
    result=s.select_city_features(df)
    assert result["train_cities"]+result["test_cities"]==180
    assert "ghsl_pop_density_wmean" not in result["selected"]
    assert result["selected"]

def test_33_type_assign_no_flood_outcome():
    s=mod(Path("pipelines/r33/33_city_typology.py"))
    rng=np.random.default_rng(22)
    n=160
    city=pd.DataFrame({"city_id":["c"+str(i) for i in range(n)],
                       "ghsl_built_fraction_wmean":np.r_[rng.uniform(.1,.3,80),rng.uniform(.7,.9,80)],
                       "merit_local_relief_m_approx":np.r_[rng.uniform(0,40,80),rng.uniform(100,200,80)],
                       "grid_count_primary":2})
    cls,profile,score,manifest=s.types(city,["ghsl_built_fraction_wmean","merit_local_relief_m_approx"],
                                       seed=2026,k_min=2,k_max=4)
    assert len(cls)==n
    assert cls.environment_type_id.nunique()>=2
    assert manifest["train_cities"]+manifest["holdout_cities"]==n
    assert "episode_onset" not in manifest["features"]

def test_34_city_type_response(tmp_path):
    s=mod(Path("pipelines/r33/34_city_type_response.py"))
    p=panel(300)
    static=pd.DataFrame({"grid_id":["g"+str(j) for j in range(300)],
                         "city_id":["c"+str(j) for j in range(300)]})
    types=pd.DataFrame({"city_id":static.city_id,
                        "environment_type_id":["T1" if j%2==0 else "T2" for j in range(300)],
                        "low_support_city":False})
    typed=s.prepare(p,types,static)
    coeff,gain,meta=s.fit_validation(typed,tmp_path,"temporal",2026,25,99,"mean")
    assert len(gain)==1
    assert gain[0]["gain_ci_low"]<=gain[0]["gain_ci_high"]
    assert "T2" in meta["other_types"]
    assert any("_x_T2" in row["term"] for row in coeff)

def test_35_spatiotemporal_connected_components():
    s=mod(Path("pipelines/r33/35_storm_proxy_groups.py"))
    df=pd.DataFrame({"grid_id":["0_0","1_0","10_10","2_0"],
                     "grid_x":[0,1,10,2],"grid_y":[0,0,10,0],
                     "episode_start":["2020-01-01","2020-01-02","2020-01-02","2020-02-14"]})
    mem,groups=s.connected_proxy(df,lag_days=1)
    assert len(groups)==3
    assert mem.loc[0,"proxy_storm_id"]==mem.loc[1,"proxy_storm_id"]
    assert mem.loc[0,"proxy_storm_id"]!=mem.loc[2,"proxy_storm_id"]
