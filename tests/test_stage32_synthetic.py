"""Synthetic-only smoke tests: no local datasets or Google credentials required."""
from __future__ import annotations
import importlib.util
import subprocess
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/"pipelines"/"r32"/"23_conditional_poisson_stage32.py"
spec=importlib.util.spec_from_file_location("stage32",PATH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def test_likelihood_strata_and_optimizer():
    rng=np.random.default_rng(20261024)
    groups=150
    size=5
    n=groups*size
    df=pd.DataFrame({"calendar_stratum_id":np.repeat(np.arange(groups).astype(str),size),
                     "episode_onset":np.tile([1,0,0,0,0],groups)})
    x={"P24":rng.normal(0,1,n)}
    train=np.repeat(np.arange(groups)<120,size)
    test=~train
    row,coefs=mod.fit_one(df,x,train,test,"D0_daily")
    assert row["train_strata"]==120
    assert row["test_strata"]==30
    assert row["test_onsets"]==30
    assert np.isfinite(row["test_conditional_logloss"])
    assert 0<=row["test_top1_onset"]<=1
    assert len(coefs)==1
    assert np.isclose(row["within_stratum_baseline"],0.2,atol=1e-12)

def test_standardization_uses_only_training_data():
    n=100
    df=pd.DataFrame({
        "imerg_p24_utc_mm_mean":np.r_[np.arange(80,dtype=float), np.full(20,10000.)],
        "era5_api7_mm_mean":np.arange(n,dtype=float),
        "era5_sm1_prevday_mean":np.linspace(.1,.9,n),
        "imerg_p3h_max_mm_max":np.arange(n,dtype=float),
    })
    train=np.arange(n)<80
    x,settings=mod.standardize(df,train)
    assert abs(x["P24"][train].mean())<1e-9
    assert "API7_sq" in x
    assert settings["P24"]["p995"]<np.log1p(10000)

def test_data_registry_manifest_is_present():
    path=ROOT/"docs"/"DATA_REGISTRY.md"
    assert path.read_text(encoding="utf-8").count("26,317")>=1

def test_calibration_preview_finds_stage21_after_relocation(tmp_path):
    script=ROOT/"pipelines"/"r32"/"21_calibrate_known.py"
    result=subprocess.run([sys.executable,str(script),"--base",str(tmp_path)],
                          capture_output=True,text=True,check=True)
    assert "21_standard_weather_submit.py" in result.stdout
    assert "Preview only" in result.stdout
