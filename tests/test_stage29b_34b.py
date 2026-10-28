"""Data-free regression tests for rainfall interaction identifiability and city gradients."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]

def module(path):
    sp=importlib.util.spec_from_file_location("test_" + path.stem,path)
    m=importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m

def test_ablation_isolates_both_interactions():
    m=module(ROOT/"pipelines/r32/29b_interaction_ablation_surface.py")
    names=m.MODELS
    assert "P24_x_API7" not in names["B0_no_interactions"]
    assert "C3_x_API7" not in names["B0_no_interactions"]
    assert names["B3_both_interactions"]==names["B0_no_interactions"]+("P24_x_API7","C3_x_API7")
    assert ("B1_P24_x_API7","B3_both_interactions") in m.COMPARISONS
    assert ("B2_C3_x_API7","B3_both_interactions") in m.COMPARISONS
    d=m.design_features({"P24":np.array([1.,2.]),"API7":np.array([2.,3.]),
                         "SM1":np.array([0.,.2])},np.array([.5,-.5]))
    assert np.allclose(d["P24_x_API7"],[2,6])
    assert np.allclose(d["C3_x_API7"],[1,-1.5])

def test_surface_relative_ratio_ci_math_and_reference():
    m=module(ROOT/"pipelines/r32/29b_interaction_ablation_surface.py")
    terms=m.MODELS["B3_both_interactions"]
    beta=np.array([.6,.4,.05,.1,-.2,.12])
    cov=np.eye(len(terms))*.01
    ref=m.design_row(terms,0,0)
    at=m.design_row(terms,1,1)
    value,ratio,lo,hi=m.contrast(beta,cov,at,ref)
    assert abs(value-(.6+.4-.2))<1e-12
    assert abs(ratio-np.exp(.8))<1e-12
    assert lo<ratio<hi
    zero=m.contrast(beta,cov,ref,ref)
    assert zero==(0.,1.,1.,1.)

def test_surface_empirical_support_masks_sparse_regions():
    m=module(ROOT/"pipelines/r32/29b_interaction_ablation_surface.py")
    rng=np.random.default_rng(42)
    n=1200
    z=rng.normal(size=n)
    x={"P24":z,"API7":z+.06*rng.normal(size=n)}
    # Many corners of the design are unsupported when predictors co-vary.
    terms=m.MODELS["B3_both_interactions"]
    trans={"P24":{"train_sd":1.,"train_mean":2.},
           "API7":{"train_sd":1.,"train_mean":2.}}
    surf,curve=m.make_surface(np.ones(len(terms))*.1,np.eye(len(terms))*.001,
                              terms,x,np.ones(n,bool),trans,"spatial","mean",
                              n=11,support_min=20)
    assert len(surf)==121 and len(curve)==33
    assert surf.within_empirical_support.any()
    assert (~surf.within_empirical_support).any()
    assert surf.p24_mm.ge(0).all()
    assert curve.ratio_ci_low.le(curve.conditional_ratio_same_api7_reference).all()

def test_continuous_modifiers_train_only_and_static_terms():
    m=module(ROOT/"pipelines/r33/34b_continuous_environment_response.py")
    city=np.arange(50)
    d=pd.DataFrame({"city_id":np.repeat(city,3).astype(str),
                    "ghsl_built_fraction_wmean":np.repeat(np.linspace(.01,.3,50),3)})
    tr=(d.city_id.astype(int)<40).to_numpy()
    vals,meta=m.transform_environment(d,tr,["ghsl_built_fraction_wmean"])
    original=meta["ghsl_built_fraction_wmean"]["train_log_mean"]
    changed=d.copy()
    changed.loc[~tr,"ghsl_built_fraction_wmean"]=1000
    _,meta2=m.transform_environment(changed,tr,["ghsl_built_fraction_wmean"])
    assert meta2["ghsl_built_fraction_wmean"]["train_log_mean"]==original
    assert np.allclose(vals["Z_built"][0:3],vals["Z_built"][0])
    terms=m.features_to_models(
        {"P24":1,"API7":1,"SM1":1,"C3_resid":1,"P24_x_API7":1,
         "P24_x_T2":1,"API7_x_T2":1,"C3_resid_x_T2":1,
         "P24_x_Z_built":1},["T1","T2"],["ghsl_built_fraction_wmean"])
    assert terms["E0_global"]==m.BASE
    assert "P24_x_Z_built" in terms["E2_continuous_P24"]
    assert "P24_x_Z_built" not in terms["E1_type"]
    with pytest.raises(ValueError,match="Unrecognized"):
        m.get_features(None,"not_a_real_source")

def synth_city_panel(tmp_path,ncities=240):
    rng=np.random.default_rng(2026)
    rows=[]
    static=[]
    env=[]
    types=[]
    for i in range(ncities):
        gid=f"{i}_0";cid=f"C{i:04d}"
        typ="T2" if i%3==0 else "T1"
        static.append({"grid_id":gid,"city_id":cid})
        env.append({"city_id":cid,"epoch":2020,
                    "ghsl_built_fraction_wmean":.01+.002*(i%33),
                    "merit_local_relief_m_approx":20+7*(i%43),
                    "cru_pre_annual_mm":500+30*(i%49)})
        types.append({"city_id":cid,"environment_type_id":typ})
        for yr in (2021,2024):
            rainfall=rng.gamma(2,8,size=4)
            peak=rainfall*(.3+.25*rng.random(4))+rng.uniform(0,3,4)
            api=rng.gamma(2,15,size=4)
            moisture=np.clip(rng.normal(.25,.09,size=4),.03,.8)
            case=int(np.argmax(.7*np.log1p(rainfall)+.22*np.log1p(api)+rng.normal(0,.3,4)))
            for day in range(4):
                rows.append({"grid_id":gid,"grid_x":i,"grid_y":0,
                    "calendar_stratum_id":f"{gid}_{yr}","year":yr,
                    "episode_onset":int(day==case),"reported_non_onset_day":0,
                    "imerg_p24_utc_mm_mean":rainfall[day],
                    "imerg_p24_utc_mm_max":rainfall[day]*1.1,
                    "imerg_p3h_max_mm_mean":peak[day],
                    "imerg_p3h_max_mm_max":peak[day]*1.1,
                    "era5_api7_mm_mean":api[day],
                    "era5_sm1_prevday_mean":moisture[day]})
    return pd.DataFrame(rows),pd.DataFrame(static),pd.DataFrame(env),pd.DataFrame(types)

def test_full_continuous_city_fit_shared_splits(tmp_path):
    m=module(ROOT/"pipelines/r33/34b_continuous_environment_response.py")
    frame,static,environment,types=synth_city_panel(tmp_path)
    folder=tmp_path/"mechanism_project/05_static_merged"
    folder.mkdir(parents=True)
    static.to_parquet(folder/"grid_static_context_m3.parquet",index=False)
    features=["ghsl_built_fraction_wmean","merit_local_relief_m_approx","cru_pre_annual_mm"]
    d,qc=m.link_complete_environment(frame,types,static,environment,features)
    assert len(d)==len(frame)
    assert qc["complete_onsets"]==480
    coeff,comparison,marginal,details=m.fit_validation(
        d,tmp_path,features,"city","mean",2026,1,99,False)
    assert {"E0_global","E1_type","E2_continuous_P24"}.issubset(set(coeff.model))
    assert {"S_built","S_relief","S_climate"}.issubset(set(coeff.model))
    assert len(comparison)==6+len(features)
    assert ((comparison.baseline=="S_built") & (comparison.augmented=="E2_continuous_P24")).any()
    assert ((comparison.baseline=="S_relief") & (comparison.augmented=="E2_continuous_P24")).any()
    assert details["test_onsets"]>20
    assert (comparison.test_clusters>=15).all()
    assert marginal.ratio_ci_low.le(marginal.conditional_P24_ratio).all()
    # All evaluated on precisely the same test risk sets.
    assert comparison.test_onsets.nunique()==1
    # Removing one city with NaN must not silently impute a covariate.
    environment.loc[0,"cru_pre_annual_mm"]=np.nan
    _,after=m.link_complete_environment(frame,types,static,environment,features)
    assert after["retained_cities"]==239
    assert after["complete_onsets"]==478


def test_29b_smoke_png_does_not_overwrite_production(tmp_path):
    m=module(ROOT/"pipelines/r32/29b_interaction_ablation_surface.py")
    rng=np.random.default_rng(5)
    n=1000
    raw=rng.normal(size=n)
    terms=m.MODELS["B3_both_interactions"]
    meta={"P24":{"train_sd":1.,"train_mean":2.},
          "API7":{"train_sd":1.,"train_mean":2.}}
    surface,_=m.make_surface(np.ones(len(terms))*.1,
                             np.eye(len(terms))*.001,
                             terms,{"P24":raw,"API7":raw+rng.normal(0,.3,n)},
                             np.ones(n,bool),meta,"city","mean",n=7,support_min=10)
    m.plot_surface(surface,tmp_path,"_smoke1000")
    assert (tmp_path/"29b_surface_city_mean_smoke1000.png").is_file()
    assert not (tmp_path/"29b_surface_city_mean.png").exists()
