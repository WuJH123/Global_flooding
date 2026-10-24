#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge quarter-wise +15km DAILY_AGGR fallback CSVs into the original
ERA5-Land hourly climate lookup and all matched case-control rows.

This is a *candidate* mixed-product dataset requiring overlap validation.
Complete-case version (hourly-only) from 14_local_first_climate.py
should remain the primary analysis.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(r"E:\Global_flooding")
PRIMARY = ROOT / "climate_global" / "climate_features_primary.parquet"
MATCHED = ROOT / "episode_control" / "matched_case_control_m3.parquet"
MANIFEST = ROOT / "climate_global" / "era5_fallback_manifest.csv"
CSV_ROOT = ROOT / "ERA5_fallback_daily_all"
OUT = ROOT / "climate_daily_fallback_final"
EXPECTED_PRIMARY = 2_789_073
EXPECTED_MATCHED = 3_250_710
EXPECTED_STRATA = 650_142
FILENAME = re.compile(r"era5fb_daily_b15_(\d{4})_q([1-4])\.csv$", re.I)


def q(path):
    return Path(path).as_posix().replace("'", "''")


def export(con, sql, filename):
    dest = OUT / filename
    if dest.exists():
        dest.unlink()
    con.execute(f"COPY ({sql}) TO '{q(dest)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print("[SAVED]", dest)
    return dest


def main():
    for p in (PRIMARY, MATCHED, MANIFEST, CSV_ROOT):
        if not p.exists():
            raise FileNotFoundError(p)
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(OUT / "fallback_merge_work.duckdb"))
    con.execute(f"PRAGMA threads={min(os.cpu_count() or 4, 8)}")
    m = pd.read_csv(MANIFEST)
    m["year"] = m.year.astype(int)
    m["month"] = m.month.astype(int)
    m["quarter"] = (m.month - 1) // 3 + 1
    expected = m.groupby(["year", "quarter"], as_index=False).requests.sum()
    fmap = {}
    for file in CSV_ROOT.rglob("*.csv"):
        match = FILENAME.fullmatch(file.name)
        if match:
            key = (int(match.group(1)), int(match.group(2)))
            fmap.setdefault(key, []).append(file)
    missing = [key for key in zip(expected.year, expected.quarter) if key not in fmap]
    dup = {k:v for k,v in fmap.items() if len(v)>1}
    if missing or dup:
        raise RuntimeError(f"Missing quarterly fallback outputs: {missing[:30]}; duplicate keys: {list(dup)[:30]}")
    selected = [fmap[(int(row.year), int(row.quarter))][0] for row in expected.itertuples(index=False)]
    file_sql = "[" + ",".join("'"+q(p)+"'" for p in selected) + "]"
    con.execute(f"CREATE OR REPLACE VIEW fb AS SELECT * FROM read_csv_auto({file_sql}, header=true, union_by_name=true)")
    pcols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{q(PRIMARY)}')").fetchall()]
    eracols = [x for x in pcols if x.startswith("era5_")]
    imergcols = [x for x in pcols if x.startswith("imerg_")]
    fcols = [r[0] for r in con.execute("DESCRIBE SELECT * FROM fb").fetchall()]
    if len(eracols)!=22 or len(imergcols)!=12 or not set(eracols).issubset(fcols):
        raise RuntimeError("Unexpected fallback/primary climate schema")
    # Match exact quarterly row counts before merging.
    task_qc = con.execute("SELECT CAST(year AS INTEGER) year, ((CAST(month AS INTEGER)-1)//3+1) quarter, COUNT(*) rows FROM fb GROUP BY 1,2 ORDER BY 1,2").fetchdf()
    task_qc = expected.merge(task_qc, on=["year", "quarter"], how="left")
    task_qc.to_csv(OUT / "fallback_quarter_qc.csv", index=False, encoding="utf-8-sig")
    if task_qc.rows.isna().any() or (task_qc.rows != task_qc.requests).any():
        raise RuntimeError("Fallback quarterly row-count QC failed")
    counts = con.execute("SELECT COUNT(*), COUNT(DISTINCT climate_id) FROM fb").fetchone()
    expected_n = int(m.requests.sum())
    if counts != (expected_n, expected_n):
        raise RuntimeError(f"Expected {expected_n} unique fallback requests, got {counts}")
    unknown = con.execute(f"""
       SELECT COUNT(*) FROM fb f LEFT JOIN read_parquet('{q(PRIMARY)}') p USING (climate_id)
       WHERE p.climate_id IS NULL""").fetchone()[0]
    if unknown:
        raise RuntimeError(f"{unknown} fallback keys not in primary")
    nonempty = " AND ".join(f"f.{x} IS NOT NULL" for x in eracols)
    valid = con.execute(f"SELECT COUNT(*) FILTER (WHERE {nonempty}), COUNT(*) FILTER (WHERE NOT({nonempty})) FROM fb f").fetchone()
    print("[FALLBACK] requested, complete, unresolved:", expected_n, *valid)
    # Mixed DAILY_AGGR vs HOURLY only affects missing primary ERA records.
    primary_ok = " AND ".join(f"p.{x} IS NOT NULL" for x in eracols)
    replacements = ",\n             ".join(f"COALESCE(p.{x}, f.{x}) AS {x}" for x in eracols)
    cli = f"""
       SELECT p.* REPLACE ({replacements}),
          CASE WHEN ({primary_ok}) THEN 0 WHEN ({nonempty}) THEN 1 ELSE 9 END AS era5_fallback_level,
          CASE WHEN ({primary_ok}) THEN 'primary_hourly' WHEN ({nonempty}) THEN 'buffer15_daily_aggregate'
               ELSE 'unresolved' END AS era5_source_method,
          CAST(NULL AS VARCHAR) AS era5_donor_grid_id,
          CAST(NULL AS DOUBLE) AS era5_donor_distance_km
       FROM read_parquet('{q(PRIMARY)}') p LEFT JOIN fb f USING (climate_id)
    """
    lookup = export(con, cli, "climate_mixed_daily_fallback_candidate.parquet")
    features = imergcols + eracols
    climate_select = ",\n              ".join(f"c.{x}" for x in features)
    key = "m.grid_id || '__' || STRFTIME(CAST(m.sample_date AS DATE), '%Y%m%d')"
    join_sql = f"""
      SELECT m.*, c.climate_id, {climate_select}, c.era5_fallback_level,
             c.era5_source_method, c.era5_donor_grid_id, c.era5_donor_distance_km
      FROM read_parquet('{q(MATCHED)}') m
      LEFT JOIN read_parquet('{q(lookup)}') c ON {key}=c.climate_id
    """
    full = export(con, join_sql, "matched_case_control_mixed_daily_all.parquet")
    fstats = con.execute(f"""
      SELECT COUNT(*), COUNT(DISTINCT stratum_id), SUM(is_case),
             COUNT(*) FILTER (WHERE climate_id IS NULL),
             COUNT(*) FILTER (WHERE era5_fallback_level=9)
      FROM read_parquet('{q(full)}')""").fetchone()
    print("[MATCHED] rows, strata, cases, missing lookup, ERA unresolved:", fstats)
    if fstats[0]!=EXPECTED_MATCHED or fstats[1]!=EXPECTED_STRATA or fstats[2]!=EXPECTED_STRATA or fstats[3]:
        raise RuntimeError("Final join structural QC failed")
    valid_climate = " AND ".join(f"{x} IS NOT NULL" for x in features)
    keep_sql = f"""
      WITH valid AS (
         SELECT stratum_id FROM read_parquet('{q(full)}')
         GROUP BY stratum_id HAVING COUNT(*)=5 AND SUM(is_case)=1
         AND COUNT(*) FILTER (WHERE NOT({valid_climate}))=0
      )
      SELECT m.* FROM read_parquet('{q(full)}') m INNER JOIN valid USING (stratum_id)
    """
    good = export(con, keep_sql, "matched_case_control_mixed_daily_complete_strata.parquet")
    good_stats = con.execute(f"SELECT COUNT(*),COUNT(DISTINCT stratum_id) FROM read_parquet('{q(good)}')").fetchone()
    print("[COMPLETE MATCHED] rows, strata:", good_stats)
    pd.DataFrame([{"metric": "fallback_requested", "value": expected_n},
                  {"metric": "fallback_complete_daily", "value": valid[0]},
                  {"metric": "fallback_still_missing", "value": valid[1]},
                  {"metric": "matched_rows", "value": fstats[0]},
                  {"metric": "matched_complete_strata", "value": good_stats[1]}]).to_csv(
        OUT / "final_fallback_qc.csv", index=False, encoding="utf-8-sig")
    print("WARNING: mixed DAILY_AGGR fallback is a sensitivity product until hourly-vs-daily consistency is validated.")
    con.execute("CHECKPOINT")
    con.close()


if __name__ == "__main__":
    main()
