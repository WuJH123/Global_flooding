"""Create exploratory Figure 2 diagnostics from Stage23 outputs (not a publication figure)."""
import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ORDER=["D0_daily","D1_antecedent","D2_plus_soil","D3_short_daily_API","D4_exploratory_curvature"]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    a=p.parse_args()
    root=a.base/"stage32_results"
    comparison=root/"23_model_comparison.csv"
    coefficients=root/"23_coefficients_exploratory.csv"
    if not comparison.is_file() or not coefficients.is_file():
        raise FileNotFoundError("Run full Stage23 before drawing. Smoke/sensitivity outputs intentionally excluded.")
    df=pd.read_csv(comparison)
    coef=pd.read_csv(coefficients)
    expected={"validation","model","test_conditional_logloss","test_top1_onset"}
    if expected-set(df.columns):
        raise ValueError(f"Missing Stage23 output columns: {sorted(expected-set(df.columns))}")
    baseline=df[df.model.eq("D0_daily")][["validation","test_conditional_logloss"]].rename(
        columns={"test_conditional_logloss":"baseline"})
    gains=df.merge(baseline,on="validation",validate="many_to_one")
    gains["gain_over_D0"]=gains.baseline-gains.test_conditional_logloss
    gains.to_csv(root/"24_incremental_logloss.csv",index=False)
    fig, axes=plt.subplots(2,2,figsize=(12,8),layout="constrained")
    ax=axes[0,0]
    for val,g in gains.groupby("validation"):
        g=g.set_index("model").reindex(ORDER)
        ax.plot(range(len(ORDER)),g.gain_over_D0,marker="o",label=val)
    ax.axhline(0,linestyle="--",linewidth=1)
    ax.set_xticks(range(len(ORDER)),["D0","D1","D2","D3","D4"])
    ax.set_ylabel("Held-out conditional log-loss gain")
    ax.set_title("(a) Incremental information")
    ax.legend()
    ax=axes[0,1]
    for val,g in df.groupby("validation"):
        g=g.set_index("model").reindex(ORDER)
        ax.plot(range(len(ORDER)),g.test_top1_onset,marker="o",label=val)
    ax.set_xticks(range(len(ORDER)),["D0","D1","D2","D3","D4"])
    ax.set_ylabel("Within-stratum top-ranked onset")
    ax.set_title("(b) Ranking; NOT flood accuracy")
    ax=axes[1,0]
    for val,g in coef[coef.model.eq("D2_plus_soil")].groupby("validation"):
        g=g.set_index("feature").reindex(["P24","API7","SM1"])
        ax.plot(["P24","API7","SM1"],g.standardized_beta,marker="o",label=val)
    ax.axhline(0,linestyle="--",linewidth=1)
    ax.set_title("(c) Relative associations (exploratory)")
    ax.set_ylabel("Standardized coefficient")
    ax=axes[1,1]
    source=root/"23_eligibility_by_year.csv"
    if source.is_file():
        yearly=pd.read_csv(source)
        ax.plot(yearly.year,yearly.onsets,marker=".",linewidth=1)
        ax.set_ylabel("Retained reported onsets")
    ax.set_title("(d) Eligible sample composition")
    for axis in axes.flat:
        axis.tick_params(labelsize=8)
    fig.suptitle("Shared hydroclimatic signatures — exploratory only",fontsize=14)
    fig.savefig(root/"Figure2_stage32_EXPLORATORY.png",dpi=300)
    fig.savefig(root/"Figure2_stage32_EXPLORATORY.svg")
    plt.close(fig)
    print("[SAVED] Figure2_stage32_EXPLORATORY.png/.svg and 24_incremental_logloss.csv")

if __name__=="__main__":
    main()
