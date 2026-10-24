"""
Groundsource matched-climate baseline (exploratory, not causal or publication-final).

Fits exact 1-case/4-control conditional softmax likelihood, equivalent to
conditional logistic regression for one case per stratum.  Model comparisons use
held-out *spatial blocks*, not random matched rows.

Requirements (install in your own .venv):
    python -m pip install -U duckdb pyarrow pandas numpy scipy

Default input:
    E:\\Global_flooding\\climate_local_first\\primary_complete_strata.parquet

Usage:
    python 17_matched_climate_models.py
    python 17_matched_climate_models.py --input E:\\path\\file.parquet --outdir E:\\path\\results
    python 17_matched_climate_models.py --max-strata 5000  # pipeline smoke test only

Note: Previous case/control matching may be biased; its nearby-day selection is
not a canonical time-stratified same-weekday design. These results are interim.
"""
from __future__ import annotations

import argparse
import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp

BASE = Path(r"E:\Global_flooding")
FEATURES = [
    "imerg_p3h_max_mm_max",
    "imerg_p24_utc_mm_mean",
    "era5_api7_mm_mean",
    "era5_sm1_prevday_mean",
]
FEATURE_LABELS = ["log1p(P3h_max)", "log1p(P24_mean)", "log1p(API7_mean)", "SM1_previous_day"]
MODEL_SPECS = {
    "M0_daily_only": [1],
    "M1_short_plus_daily": [0, 1],
    "M2_plus_antecedent": [0, 1, 2, 3],
    "M3_plus_interaction": [0, 1, 2, 3, 4],
}


def load_strata(path: Path, max_strata: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Stream a deterministic sorted scan into compact numeric arrays."""
    import duckdb

    if not path.is_file():
        raise FileNotFoundError(path)
    con = duckdb.connect()
    path_sql = path.as_posix().replace("'", "''")
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path_sql}')").fetchall()}
    needed = set(FEATURES) | {"stratum_id", "is_case", "control_rank", "grid_x", "grid_y", "sample_date"}
    if needed - cols:
        raise ValueError(f"Missing input columns: {sorted(needed - cols)}")

    qc = con.execute(f"""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT stratum_id) AS strata,
               SUM(CAST(is_case AS INTEGER)) AS cases,
               SUM(CASE WHEN is_case=0 THEN 1 ELSE 0 END) AS controls
        FROM read_parquet('{path_sql}')
    """).fetchone()
    n_rows, n_strata, n_cases, n_controls = map(int, qc)
    if n_rows != 5 * n_strata or n_cases != n_strata or n_controls != 4 * n_strata:
        raise RuntimeError(f"Invalid 1:4 structure: {qc}")
    if max_strata > 0:
        n_strata = min(max_strata, n_strata)
    n_expected = n_strata * 5
    print(f"[READ] reading {n_strata:,} strata ({n_expected:,} rows) from {path}", flush=True)

    # Data are read in globally sorted stratum order and validated after loading.
    # Materialized ranking is a deliberate tradeoff for deterministic 1:4 grouping.
    cte = f"""
        WITH ranked AS (
            SELECT
                DENSE_RANK() OVER(ORDER BY stratum_id) - 1 AS sid,
                ROW_NUMBER() OVER(PARTITION BY stratum_id ORDER BY is_case DESC, control_rank) - 1 AS pos,
                CAST(is_case AS INTEGER) AS label,
                CAST(grid_x AS INTEGER) AS gx,
                CAST(grid_y AS INTEGER) AS gy,
                EXTRACT(YEAR FROM sample_date)::INTEGER AS sample_year,
                {', '.join('CAST(' + c + ' AS DOUBLE) AS ' + c for c in FEATURES)}
            FROM read_parquet('{path_sql}')
        )
        SELECT * FROM ranked
        {f'WHERE sid < {n_strata}' if max_strata > 0 else ''}
        ORDER BY sid, pos
    """
    # 10 total numeric columns including four source variables.
    A = np.empty((n_expected, 6 + len(FEATURES)), dtype=np.float64)
    offset = 0
    reader = con.execute(cte).fetch_record_batch(rows_per_batch=65536)
    for batch in reader:
        chunk = np.column_stack([np.asarray(batch.column(j).to_numpy(zero_copy_only=False), dtype=np.float64)
                                 for j in range(batch.num_columns)])
        count = len(chunk)
        if offset + count > n_expected:
            raise RuntimeError("More sorted rows than expected")
        A[offset:offset + count, :] = chunk
        offset += count
        if offset and (offset // 500000 != (offset - count) // 500000):
            print(f"[READ] {offset:,}/{n_expected:,}", flush=True)
    con.close()
    if offset != n_expected:
        raise RuntimeError(f"Expected {n_expected}, retrieved {offset} rows")
    sids = A[:, 0].reshape(-1, 5)
    pos = A[:, 1].reshape(-1, 5)
    labels = A[:, 2].reshape(-1, 5)
    if not np.all(sids == np.arange(n_strata, dtype=np.float64)[:, None]):
        raise RuntimeError("Stratum IDs are not five contiguous ordered rows")
    if not np.all(pos == np.arange(5)[None, :]):
        raise RuntimeError("Unexpected within-stratum positions")
    if not np.all(labels[:, 0] == 1) or not np.all(labels[:, 1:] == 0):
        raise RuntimeError("Case must be at index zero followed by four controls")
    for col in (3, 4, 5):
        vals = A[:, col].reshape(-1, 5)
        if not np.all(vals == vals[:, :1]):
            raise RuntimeError("Grid coordinates/year vary within a matched group")
    x_raw = A[:, 6:].reshape(n_strata, 5, len(FEATURES)).astype(np.float32)
    if not np.isfinite(x_raw).all():
        raise RuntimeError("Missing or non-finite climate predictors in supposedly complete strata")
    gx = A[:, 3].reshape(-1, 5)[:, 0].astype(np.int32)
    gy = A[:, 4].reshape(-1, 5)[:, 0].astype(np.int32)
    year = A[:, 5].reshape(-1, 5)[:, 0].astype(np.int16)
    return x_raw, gx, gy, year


def spatial_split(gx: np.ndarray, gy: np.ndarray, cells: int, test_share: float, seed: int):
    """Assign entire approximate projected-space blocks to train or holdout."""
    blocks = np.column_stack((np.floor_divide(gx, cells), np.floor_divide(gy, cells)))
    keys, inv = np.unique(blocks, axis=0, return_inverse=True)
    if len(keys) < 6:
        raise ValueError("Fewer than six spatial blocks; try smaller --block-cells for test")
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(keys))
    n_test = max(1, int(round(len(keys) * test_share)))
    is_test_block = np.zeros(len(keys), dtype=bool)
    is_test_block[perm[:n_test]] = True
    mask = is_test_block[inv]
    if mask.sum() < 5 or (~mask).sum() < 5:
        raise RuntimeError("Spatial holdout too small")
    return ~mask, mask, inv, keys


def transform_covariates(raw: np.ndarray, train: np.ndarray):
    raw = raw.astype(np.float64)
    for col in range(3):
        if np.any(raw[:, :, col] < -1e-6):
            raise ValueError(f"Materially negative precipitation-like variable {FEATURES[col]}")
        raw[:, :, col] = np.log1p(np.maximum(raw[:, :, col], 0.0))
    if np.any((raw[:, :, 3] < -1e-4) | (raw[:, :, 3] > 1.05)):
        raise ValueError("Unexpected SM1 values")
    raw[:, :, 3] = np.maximum(raw[:, :, 3], 0.0)
    train_flat = raw[train].reshape(-1, 4)
    lo, hi = np.percentile(train_flat, [0.5, 99.5], axis=0)
    clipped_train = np.clip(train_flat, lo, hi)
    mean, std = clipped_train.mean(axis=0), clipped_train.std(axis=0)
    if np.any(std < 1e-9):
        raise RuntimeError("Near-zero variance in a covariate")
    z = (np.clip(raw, lo, hi) - mean) / std
    interaction = (z[:, :, 0] * z[:, :, 3])[:, :, None]
    design = np.concatenate((z, interaction), axis=2).astype(np.float64)
    info = {
        "source_features": FEATURES,
        "transformed_feature_names": FEATURE_LABELS + ["P3h_z_X_SM1_z"],
        "lower_0p5pct_train": lo.tolist(),
        "upper_99p5pct_train": hi.tolist(),
        "train_mean_after_clipping": mean.tolist(),
        "train_sd_after_clipping": std.tolist(),
        "note": "First three transformed by log1p(max(x,0)); SM1 clipped at 0; scaling fitted on train only."
    }
    return design, info


def loss_grad(beta: np.ndarray, x: np.ndarray):
    logits = np.einsum("ijk,k->ij", x, beta, optimize=True)
    logz = logsumexp(logits, axis=1)
    p = np.exp(logits - logz[:, None])
    mean_x = np.einsum("ij,ijk->ik", p, x, optimize=True)
    return (float(np.mean(logz - logits[:, 0])),
            np.mean(mean_x - x[:, 0, :], axis=0))


def probabilities(beta: np.ndarray, x: np.ndarray) -> np.ndarray:
    logits = np.einsum("ijk,k->ij", x, beta, optimize=True)
    return np.exp(logits - logsumexp(logits, axis=1)[:, None])


def cluster_robust_se(beta: np.ndarray, x: np.ndarray, grid_inverse: np.ndarray):
    """Grid-clustered sandwich SE; shared storms across grids remain unaccounted for."""
    p = probabilities(beta, x)
    group_avg = np.einsum("ij,ijk->ik", p, x, optimize=True)
    k = x.shape[2]
    hess = np.zeros((k, k), dtype=np.float64)
    for start in range(0, len(x), 40000):
        stop = min(start + 40000, len(x))
        xb = x[start:stop]
        pb = p[start:stop]
        mu = group_avg[start:stop]
        hess += np.einsum("ni,nij,nik->jk", pb, xb, xb, optimize=True)
        hess -= mu.T @ mu
    scores = x[:, 0, :] - group_avg
    ng = int(grid_inverse.max()) + 1
    u = np.zeros((ng, k), dtype=np.float64)
    np.add.at(u, grid_inverse, scores)
    meat = u.T @ u
    cov = np.linalg.pinv(hess) @ meat @ np.linalg.pinv(hess)
    d = np.maximum(np.diag(cov), 0.0)
    return np.sqrt(d), hess


def run_model(name, spec, design, train, test, grid_inv, outdir):
    xx = design[:, :, spec]
    res = minimize(lambda b: loss_grad(b, xx[train]),
                   x0=np.zeros(len(spec), dtype=np.float64),
                   method="L-BFGS-B", jac=True,
                   options={"maxiter": 160, "ftol": 1e-10, "gtol": 1e-7})
    if not res.success:
        warnings.warn(f"{name} optimizer returned: {res.message}")
    p_train = probabilities(res.x, xx[train])
    p_test = probabilities(res.x, xx[test])
    # Tie-aware top-1 accuracy: when several choices have equal model score,
    # randomly breaking ties would yield a 1 / (#ties) case success chance.
    max_test_p = p_test.max(axis=1, keepdims=True)
    tied_for_top = np.isclose(p_test, max_test_p, rtol=1e-12, atol=1e-12)
    top1_tie_aware = np.mean(tied_for_top[:, 0] / tied_for_top.sum(axis=1))
    pred_rows = {
        "model": name,
        "n_train_strata": int(train.sum()),
        "n_test_strata": int(test.sum()),
        "n_train_grid_clusters": int(np.unique(grid_inv[train]).size),
        "train_negative_log_likelihood_per_stratum": float(-np.log(np.maximum(p_train[:, 0], 1e-300)).mean()),
        "test_negative_log_likelihood_per_stratum": float(-np.log(np.maximum(p_test[:, 0], 1e-300)).mean()),
        "test_logloss_improvement_vs_random": float(math.log(5) + np.log(np.maximum(p_test[:, 0], 1e-300)).mean()),
        "test_case_top1_fraction_tie_aware": float(top1_tie_aware),
        "test_mean_case_probability": float(p_test[:, 0].mean()),
        "converged": bool(res.success),
        "iterations": int(getattr(res, "nit", 0)),
    }
    se, hess = cluster_robust_se(res.x, xx[train], grid_inv[train])
    coef_rows = []
    for j, orig_col in enumerate(spec):
        b = float(res.x[j])
        s = float(se[j])
        coef_rows.append({
            "model": name,
            "term": (FEATURE_LABELS + ["P3h_z_X_SM1_z"])[orig_col],
            "coefficient_per_training_SD": b,
            "grid_clustered_se": s,
            "OR_exp_coefficient": float(np.exp(b)),
            "OR_95CI_low": float(np.exp(b - 1.96*s)),
            "OR_95CI_high": float(np.exp(b + 1.96*s)),
            "interpretation": "Relative within-stratum odds, NOT an absolute flooding probability. Interaction term is on standardized covariate scale.",
        })
    return pred_rows, coef_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=BASE / "climate_local_first" / "primary_complete_strata.parquet")
    ap.add_argument("--outdir", type=Path, default=BASE / "climate_mechanism_models")
    ap.add_argument("--max-strata", type=int, default=0, help="Smoke test only; uses first N strata, nonrepresentative")
    ap.add_argument("--block-cells", type=int, default=25, help="Projected-grid cells per side of spatial holdout block")
    ap.add_argument("--test-share", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    if not 0 < args.test_share < 0.5:
        raise ValueError("--test-share must lie between 0 and 0.5")
    args.outdir.mkdir(parents=True, exist_ok=True)
    raw, gx, gy, year = load_strata(args.input, args.max_strata)
    train, test, block_inv, unique_blocks = spatial_split(gx, gy, args.block_cells, args.test_share, args.seed)
    print(f"[SPLIT] blocks={len(unique_blocks):,}; train strata={train.sum():,}; test strata={test.sum():,}", flush=True)
    design, transform = transform_covariates(raw, train)
    (args.outdir / "feature_transform.json").write_text(json.dumps(transform, indent=2, ensure_ascii=False), encoding="utf-8")
    # Cluster SE by exact 20-km grid, not coarse spatial holdout block.
    _, exact_grid_inv = np.unique(np.column_stack((gx, gy)), axis=0, return_inverse=True)
    metrics, coefs = [], []
    for name, spec in MODEL_SPECS.items():
        print(f"[FIT] {name} {len(spec)} predictors", flush=True)
        result, terms = run_model(name, spec, design, train, test, exact_grid_inv, args.outdir)
        metrics.append(result)
        coefs.extend(terms)
        print(f"  held-out logloss={result['test_negative_log_likelihood_per_stratum']:.5f}; top1={result['test_case_top1_fraction_tie_aware']:.3f}; converged={result['converged']}", flush=True)
    pd.DataFrame(metrics).to_csv(args.outdir / "spatial_holdout_model_comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(coefs).to_csv(args.outdir / "conditional_logit_coefficients.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({
        "year": year,
        "spatial_holdout": np.where(test, "test", "train"),
    }).groupby(["year", "spatial_holdout"], observed=True).size().reset_index(name="strata").to_csv(
        args.outdir / "year_by_split.csv", index=False, encoding="utf-8-sig")
    print("\n[DONE]", args.outdir, flush=True)
    print("CAUTION: current control selection is not standard same-weekday referent sampling; time-boundary and reporting biases remain.")
    print("Grid-clustered SE do not fully address storm events shared by adjacent grids. No CMIP6/absolute flood probability inference yet.")


if __name__ == "__main__":
    main()
