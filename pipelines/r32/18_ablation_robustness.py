"""18 — Ablation and held-out validation for Groundsource matched climate models.

Depends on 17_matched_climate_models.py in the SAME directory. Runs only on local
Parquet; does not access Google Earth Engine.

The evidence is exploratory: existing 1:4 matched controls are not a canonical
same-weekday time-stratified design, and held-out spatial blocks may share storms.

Usage (PowerShell):
  E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\pipelines\\r32\\18_ablation_robustness.py --max-strata 5000 --spatial-seeds 2026
  E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\pipelines\\r32\\18_ablation_robustness.py
  E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\pipelines\\r32\\18_ablation_robustness.py --spatial-seeds 2026,2031,2040 --block-cells 25
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "17_matched_climate_models.py"
BASE = Path(r"E:\Global_flooding")

MODEL_SPECS = {
    "M0_daily": [1],
    "M1_daily_short": [0, 1],
    "M2a_plus_API7": [0, 1, 2],
    "M2b_plus_SM1": [0, 1, 3],
    "M2_API7_SM1": [0, 1, 2, 3],
    "M3_P3h_x_SM1": [0, 1, 2, 3, 4],
    "M4_exploratory_curvature": [0, 1, 2, 3, 5, 6, 7],
}


def load_17():
    if not SOURCE.exists():
        raise FileNotFoundError(f"Missing prerequisite script: {SOURCE}")
    spec = importlib.util.spec_from_file_location("groundsource_stage17", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import stage 17 from {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def get_extra_design(stage17, raw, train):
    base, info = stage17.transform_covariates(raw, train)
    z = base[:, :, :4]
    extras = np.stack([z[:, :, 0] ** 2, z[:, :, 2] ** 2, z[:, :, 3] ** 2], axis=2)
    design = np.concatenate([base, extras], axis=2)
    return design, info


def fit_model(stage17, xx, train):
    xtrain = xx[train]
    result = minimize(
        lambda beta: stage17.loss_grad(beta, xtrain),
        x0=np.zeros(xx.shape[2], dtype=np.float64),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 180, "ftol": 1e-10, "gtol": 1e-7},
    )
    return result


def summarize_predictions(stage17, beta, xx, test, block_id):
    # Return exact per-stratum logloss and tie-aware top1 for block-resampled CIs.
    probs = stage17.probabilities(beta, xx[test])
    losses = -np.log(np.clip(probs[:, 0], 1e-300, 1))
    mx = probs.max(axis=1, keepdims=True)
    ties = np.isclose(probs, mx, atol=1e-12, rtol=1e-12)
    wins = ties[:, 0] / ties.sum(axis=1)
    return losses, wins


def summarize_block_bootstrap(loss_a, loss_b, blocks, seed=41, repeats=600):
    """Cluster bootstrap for mean(test_loss_a-test_loss_b), resampling spatial blocks."""
    codes, inverse = np.unique(blocks, return_inverse=True)
    n = len(codes)
    counts = np.bincount(inverse).astype(np.float64)
    sum_diff = np.bincount(inverse, weights=loss_a - loss_b)
    rng = np.random.default_rng(seed)
    boot = np.empty(repeats, dtype=np.float64)
    for r in range(repeats):
        samples = rng.integers(0, n, size=n)
        boot[r] = sum_diff[samples].sum() / counts[samples].sum()
    return float(np.mean(loss_a - loss_b)), float(np.quantile(boot, .025)), float(np.quantile(boot, .975)), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=BASE / "climate_local_first" / "primary_complete_strata.parquet")
    ap.add_argument("--outdir", type=Path, default=BASE / "climate_mechanism_models" / "robustness_18")
    ap.add_argument("--max-strata", type=int, default=0, help="First N groups ONLY: smoke test, not scientific analysis")
    ap.add_argument("--spatial-seeds", type=str, default="2026,2031", help="Comma-separated integer spatial holdout seeds")
    ap.add_argument("--block-cells", type=int, default=25, help="Each block is ~20 km * this number on projected grid")
    ap.add_argument("--test-share", type=float, default=.20)
    ap.add_argument("--temporal-test-start", type=int, default=2023)
    ap.add_argument("--temporal-test-end", type=int, default=2025)
    ap.add_argument("--bootstrap", type=int, default=600)
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    if not 0.01 < args.test_share < 0.5:
        raise ValueError("test-share must be between .01 and .5")
    if args.temporal_test_end < args.temporal_test_start:
        raise ValueError("temporal-test-end must >= temporal-test-start")
    stage17 = load_17()
    raw, gx, gy, year = stage17.load_strata(args.input, args.max_strata)
    if len(raw) < 100:
        raise RuntimeError("Need at least 100 strata")

    spatial_keys = np.column_stack((np.floor_divide(gx, args.block_cells),
                                    np.floor_divide(gy, args.block_cells)))
    _, spatial_inv = np.unique(spatial_keys, axis=0, return_inverse=True)
    folds = []
    for seed in [int(v.strip()) for v in args.spatial_seeds.split(",") if v.strip()]:
        train, test, inv, keys = stage17.spatial_split(gx, gy, args.block_cells, args.test_share, seed)
        folds.append((f"spatial_seed_{seed}", train, test, spatial_inv))
    temporal_train = year < args.temporal_test_start
    temporal_test = (year >= args.temporal_test_start) & (year <= args.temporal_test_end)
    if temporal_train.sum() > 100 and temporal_test.sum() > 100:
        folds.append((f"temporal_{args.temporal_test_start}_{args.temporal_test_end}",
                      temporal_train, temporal_test, spatial_inv))
    else:
        print("[WARN] Temporal test skipped: insufficient groups in test or train. (Expected on --max-strata small)")

    metrics, contrasts, coef_rows = [], [], []
    for fold, train, test, inv in folds:
        print(f"\n[FOLD] {fold}: train={train.sum():,}, test={test.sum():,}", flush=True)
        design, tinfo = get_extra_design(stage17, raw, train)
        # Optional audit of shifted soil humidity distributions can be done separately.
        pred = {}
        for name, cols in MODEL_SPECS.items():
            xx = design[:, :, cols]
            res = fit_model(stage17, xx, train)
            losses, wins = summarize_predictions(stage17, res.x, xx, test, inv)
            pred[name] = losses
            m = {
                "fold": fold, "model": name, "n_train": int(train.sum()), "n_test": int(test.sum()),
                "test_logloss": float(losses.mean()), "top1_fraction": float(wins.mean()),
                "mean_case_probability": float(np.exp(-losses).mean()),
                "converged": bool(res.success), "optimization_message": str(res.message),
            }
            metrics.append(m)
            for c, b in zip(cols, res.x):
                coef_rows.append({"fold": fold, "model": name,
                                  "term": stage17.FEATURE_LABELS[c] if c < 4
                                  else {4: "P3h_z_X_SM1_z", 5: "P3h_z_squared", 6: "API7_z_squared", 7: "SM1_z_squared"}[c],
                                  "estimate": float(b)})
            print(f"  {name:<27} loss={m['test_logloss']:.6f}, top1={m['top1_fraction']:.4f} {res.success}", flush=True)

        comparisons = [
            ("M1_daily_short", "M0_daily", "added_P3h_vs_daily"),
            ("M2a_plus_API7", "M1_daily_short", "added_API7_alone"),
            ("M2b_plus_SM1", "M1_daily_short", "added_SM1_alone"),
            ("M2_API7_SM1", "M2a_plus_API7", "added_SM1_given_API7"),
            ("M2_API7_SM1", "M2b_plus_SM1", "added_API7_given_SM1"),
            ("M3_P3h_x_SM1", "M2_API7_SM1", "added_interaction"),
            ("M4_exploratory_curvature", "M2_API7_SM1", "added_quadratic_terms"),
        ]
        for newer, baseline, label in comparisons:
            delta, low, high, nblocks = summarize_block_bootstrap(
                pred[baseline], pred[newer], inv[test], seed=41,
                repeats=args.bootstrap)
            # Positive delta = lower logloss for newer model.
            contrasts.append({"fold": fold, "comparison": label,
                              "baseline": baseline, "new_model": newer,
                              "logloss_gain_new_model": delta,
                              "block_bootstrap_ci_low": low,
                              "block_bootstrap_ci_high": high,
                              "n_test_spatial_blocks": nblocks})
        for fn, data in [("18_model_folds.csv", metrics), ("18_ablation_deltas.csv", contrasts),
                         ("18_coefficients_by_fold.csv", coef_rows)]:
            pd.DataFrame(data).to_csv(args.outdir / fn, index=False, encoding="utf-8-sig")
        del design, pred
    print(f"\n[DONE] Saved to: {args.outdir}")
    print("IMPORTANT: block bootstrap here is exploratory; same storms can span adjacent blocks.")
    print("Positive logloss_gain_new_model favors the new model. Compare all folds; do not interpret coefficients as causality.")


if __name__ == "__main__":
    main()
