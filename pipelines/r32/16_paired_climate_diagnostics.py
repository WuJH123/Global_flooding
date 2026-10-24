#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Groundsource: matched-stratum climate diagnostic (non-causal, descriptive).

Uses all 5 rows in each *complete* 1:4 stratum. For each climate variable,
calculates case value and the mean of its four matched controls, then the
within-stratum difference. Does not fit inferential models or claim independence.

Default Windows command:
 E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\pipelines\\r32\\16_paired_climate_diagnostics.py

Optional explicit input/output paths:
 python 16_paired_climate_diagnostics.py --primary PATH --neighbor PATH --all-strata PATH --out DIR
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path(r"E:\Global_flooding")
FEATURES = {
    "imerg_p30_max_mm_max": "30-minute precipitation maximum (mm)",
    "imerg_p1h_max_mm_max": "1-hour rolling precipitation maximum (mm)",
    "imerg_p3h_max_mm_max": "3-hour rolling precipitation maximum (mm)",
    "imerg_p6h_max_mm_max": "6-hour rolling precipitation maximum (mm)",
    "imerg_p24_utc_mm_mean": "24-hour UTC precipitation, grid mean (mm)",
    "era5_ant_p3d_mm_mean": "Antecedent 3-day precipitation, grid mean (mm)",
    "era5_ant_p7d_mm_mean": "Antecedent 7-day precipitation, grid mean (mm)",
    "era5_api7_mm_mean": "Antecedent precipitation index 7-day (mm)",
    "era5_sm1_prevday_mean": "Previous-day volumetric soil moisture 0-7 cm",
    "era5_sm2_prevday_mean": "Previous-day volumetric soil moisture 7-28 cm",
    "era5_event_runoff24_mm_mean": "Event-day reanalysis runoff (diagnostic only; mm)",
}


def qp(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, default=ROOT / "climate_local_first" / "primary_complete_strata.parquet")
    parser.add_argument("--neighbor", type=Path, default=ROOT / "climate_local_first" / "neighbor_candidate_complete_strata.parquet")
    parser.add_argument("--all-strata", type=Path, default=ROOT / "climate_local_first" / "primary_all_strata.parquet")
    parser.add_argument("--out", type=Path, default=ROOT / "climate_mechanism_diagnostics")
    args = parser.parse_args()
    if not args.primary.exists():
        raise FileNotFoundError(args.primary)
    args.out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET threads = {min(os.cpu_count() or 4, 8)}")
    rows = []
    for label, path in [("primary", args.primary), ("neighbor_sensitivity", args.neighbor)]:
        if not path.exists():
            print(f"[SKIP] {label}: {path} not found")
            continue
        src = qp(path)
        columns = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()}
        required = {"stratum_id", "grid_id", "is_case", "case_year"} | set(FEATURES)
        missing = required - columns
        if missing:
            raise RuntimeError(f"{label} missing columns: {sorted(missing)}")
        integrity = con.execute(f"""
            WITH g AS (
              SELECT stratum_id, COUNT(*) AS n, SUM(is_case) AS cases
              FROM read_parquet('{src}') GROUP BY stratum_id
            )
            SELECT COUNT(*) AS strata, SUM(n) AS rows,
              COUNT(*) FILTER (WHERE n<>5 OR cases<>1) AS invalid_strata
            FROM g
        """).fetchone()
        print(f"[{label.upper()}] strata={integrity[0]:,}; rows={integrity[1]:,}; invalid={integrity[2]:,}")
        if integrity[2]:
            raise RuntimeError("Matched design integrity failed")
        cols_sql = ",\n".join(
            f"MAX(CASE WHEN is_case=1 THEN \"{col}\" END) AS \"{col}_case\", "
            f"AVG(CASE WHEN is_case=0 THEN \"{col}\" END) AS \"{col}_ctrl\""
            for col in FEATURES
        )
        contrast_path = args.out / f"matched_contrasts_{label}.parquet"
        con.execute(f"""
            COPY (
              SELECT stratum_id, MIN(grid_id) AS grid_id,
                MAX(case_year) AS year,
                {cols_sql}
              FROM read_parquet('{src}')
              GROUP BY stratum_id
            ) TO '{qp(contrast_path)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        print(f"[SAVED] {contrast_path}")
        for feature, description in FEATURES.items():
            c, r = f'"{feature}_case"', f'"{feature}_ctrl"'
            v = con.execute(f"""
                SELECT COUNT(*) AS n,
                  MEDIAN({c}) AS median_case,
                  MEDIAN({r}) AS median_control_mean,
                  MEDIAN({c}-{r}) AS median_within_stratum_difference,
                  AVG({c}-{r}) AS mean_within_stratum_difference,
                  AVG(CASE WHEN {c}>{r} THEN 1.0 WHEN {c}<={r} THEN 0.0 END) AS fraction_case_above_control_mean,
                  QUANTILE_CONT({c}-{r}, 0.05) AS delta_p05,
                  QUANTILE_CONT({c}-{r}, 0.95) AS delta_p95
                FROM read_parquet('{qp(contrast_path)}')
            """).fetchone()
            rows.append(dict(data_version=label, feature=feature, description=description,
                             n_strata=v[0], median_case=v[1], median_control_mean=v[2],
                             median_paired_difference=v[3], mean_paired_difference=v[4],
                             case_above_control_mean_fraction=v[5], delta_p05=v[6], delta_p95=v[7]))
        if label == "primary" and args.all_strata.exists():
            all_path = qp(args.all_strata)
            retention = con.execute(f"""
              WITH all_cases AS (
                SELECT stratum_id, case_year AS year FROM read_parquet('{all_path}')
                WHERE is_case=1
              ), complete_cases AS (
                SELECT stratum_id FROM read_parquet('{src}') WHERE is_case=1
              )
              SELECT a.year, COUNT(*) AS n_original_strata,
                 COUNT(c.stratum_id) AS n_complete_strata,
                 ROUND(COUNT(c.stratum_id)::DOUBLE / COUNT(*),6) AS retained_fraction
              FROM all_cases a LEFT JOIN complete_cases c USING(stratum_id)
              GROUP BY a.year ORDER BY a.year
            """).fetchdf()
            outfile = args.out / "primary_retention_by_year.csv"
            retention.to_csv(outfile, index=False, encoding="utf-8-sig")
            print(f"[SAVED] {outfile}")
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("No contrasts computed")
    outfile = args.out / "paired_feature_summary.csv"
    df.to_csv(outfile, index=False, encoding="utf-8-sig")
    print(f"[SAVED] {outfile}")
    print("\nPRIMARY PAIRWISE CONTRAST SUMMARY (DESCRIPTIVE, not causal):")
    print(df.loc[df.data_version.eq("primary"), ["feature", "median_paired_difference", "case_above_control_mean_fraction"]].to_string(index=False))
    print("\nWARNING: Shared storms, repeated grid observations, labeling error, and temporal mismatch prevent interpreting these descriptive differences as causal effects or independent evidence. Event-day runoff is diagnostic, not a pre-event predictor.")
    con.close()

if __name__ == "__main__":
    run()
