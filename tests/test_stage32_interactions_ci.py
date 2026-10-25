"""Synthetic model tests: no local flood data or Google account required."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/"pipelines"/"r32"/"25_conditional_interactions_ci.py"
spec=importlib.util.spec_from_file_location("stage25_test",PATH)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def make_data(nstrata=600,seed=2040):
    rng=np.random.default_rng(seed)
    size=4
    n=nstrata*size
    p24=np.maximum(0,rng.lognormal(1,1,n)-1)
    api=np.maximum(0,rng.lognormal(1,1,n)-1)
    sm=np.clip(rng.beta(3,5,n),.02,.9)
    p3=np.maximum(0,.6*p24+rng.lognormal(.7,.5,n))
    z24=(np.log1p(p24)-np.log1p(p24).mean())/np.log1p(p24).std()
    z7=(np.log1p(api)-np.log1p(api).mean())/np.log1p(api).std()
    eta=.5*z24+.2*z7+.65*z24*z7
    eta=eta.reshape(nstrata,size)
    probs=np.exp(eta-eta.max(axis=1,keepdims=True))
    probs/=probs.sum(axis=1,keepdims=True)
    cases=np.array([rng.choice(size,p=p) for p in probs],dtype=int)
    y=np.zeros((nstrata,size),dtype=int)
    y[np.arange(nstrata),cases]=1
    groups=np.repeat(np.arange(nstrata),size)
    # distinct 25-grid blocks: 480 training and 120 testing blocks.
    df=pd.DataFrame({
        "grid_id":["grid_"+str(g) for g in groups],
        "calendar_stratum_id":["stratum_"+str(g) for g in groups],
        "episode_onset":y.flatten(),
        "year":np.repeat(np.where(np.arange(nstrata)<480,2020,2024),size),
        "grid_x":np.repeat(np.arange(nstrata)*25,size),
        "grid_y":0,
        "imerg_p24_utc_mm_mean":p24,
        "imerg_p3h_max_mm_max":p3,
        "era5_api7_mm_mean":api,
        "era5_sm1_prevday_mean":sm,
    })
    return df


def test_conditional_score_and_sandwich_are_finite():
    rng=np.random.default_rng(2)
    n_groups=70
    n=4*n_groups
    X=np.column_stack([rng.normal(size=n),rng.normal(size=n)])
    y=np.tile([1.,0,0,0],n_groups)
    groups=np.repeat(np.arange(n_groups),4)
    blk=groups.copy()
    arrays=m.sorted_groups(X,y,groups,blk,np.ones(n,dtype=bool))
    beta=m.fit(arrays)
    se,cov,num=m.spatial_sandwich(arrays,beta)
    assert num==n_groups
    assert np.isfinite(beta).all()
    assert np.isfinite(se).all() and np.all(se>=0)
    assert np.allclose(cov,cov.T)


def test_disjoint_risk_stratum_split_and_gradient():
    rng=np.random.default_rng(201)
    ng=30
    X=rng.normal(size=(ng*4,2))
    y=np.tile([1,0,0,0],ng).astype(float)
    groups=np.repeat(np.arange(ng),4)
    blocks=np.repeat(np.arange(ng),4)
    subset=groups<20
    a=m.sorted_groups(X,y,groups,blocks,subset)
    b=m.sorted_groups(X,y,groups,blocks,~subset)
    assert len(a[4])==20
    assert len(b[4])==10
    beta=np.array([.1,-.22])
    loss,deriv=m.calc(beta,a,True)
    eps=1e-6
    numeric=np.array([(m.calc(beta+eps*np.eye(2)[i],a)[0]-
                       m.calc(beta-eps*np.eye(2)[i],a)[0])/(2*eps) for i in range(2)])
    assert np.allclose(deriv,numeric,rtol=1e-5,atol=1e-5)


def test_interactions_paired_heldout_and_cluster_ci(tmp_path):
    data=make_data()
    coeff,gains,manifest=m.analyze(data,tmp_path,"temporal",25,2026,99)
    assert set(gains.baseline)=={"I0_additive"}
    assert len(gains)==4
    assert len(coeff)>25
    assert (gains.test_onsets>0).all()
    assert gains.heldout_loss_gain.notna().all()
    assert (gains.gain_CI_low<=gains.gain_CI_high).all()
    assert (coeff.cluster_se>=0).all()
    assert manifest["P24"]["transform"]=="log1p"
