"""19 — Audit the time-stratified referent pool WITHOUT new GEE extraction.

Scientific purpose:
  * Compare existing 1:4 controls with the full Groundsource positive grid-day index.
  * Construct EVERY same-grid/same-year/same-month/same-weekday referent date,
    without outcome-dependent exclusions or flood-date buffers.
  * Quantify how many referent climate exposures are ALREADY in the 2.79m-record
    climate lookup, and export an exact manifest for dates not yet extracted.

This is an audit, NOT a ready-to-fit standard case-crossover study. In particular,
reported-flood referents remain flagged (not treated as verified non-event days);
pre-existing climate lookup is outcome-selected by the original 1:4 design.

Windows example:
  E:\\Global_flooding\\.venv\\Scripts\\python.exe ^
    E:\\Global_flooding\\19_audit_time_stratified_referents.py ^
    --full-gridday "E:\\数据集\\Groundsource_全球城市内涝点位\\processed\\groundsource_gridday_20km.parquet"

Requires: duckdb, pandas, pyarrow.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

BASE = Path(r"E:\Global_flooding")
DEFAULT_GRIDDAY = Path(r"E:\数据集\Groundsource_全球城市内涝点位\processed\groundsource_gridday_20km.parquet")


def q(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=BASE)
    ap.add_argument("--full-gridday", type=Path, default=DEFAULT_GRIDDAY,
                    help="ALL Groundsource positive grid-days, BEFORE M3 urban filtering")
    ap.add_argument("--max-cases", type=int, default=0,
                    help="Use first N cases for a pipeline smoke test only")
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    base = args.base
    case_file = base / "climate_local_first" / "primary_complete_strata.parquet"
    climate_file = base / "climate_global" / "climate_features_primary.parquet"
    out = base / "referent_audit_19"
    out.mkdir(parents=True, exist_ok=True)
    for p in [case_file, climate_file, args.full_gridday]:
        if not p.exists():
            raise FileNotFoundError(f"Required file not found: {p}")

    con = duckdb.connect(str(out / "audit_work.duckdb"))
    con.execute(f"PRAGMA threads={max(1, args.threads)}")
    case_sql = q(case_file)
    clim_sql = q(climate_file)
    all_sql = q(args.full_gridday)
    lim = f"LIMIT {args.max_cases}" if args.max_cases > 0 else ""
    print("[1] Cases and full positive-date index", flush=True)

    con.execute(f"""
        CREATE OR REPLACE TABLE cases AS
        SELECT stratum_id, grid_id, grid_x, grid_y,
               CAST(sample_date AS DATE) AS case_date
        FROM read_parquet('{case_sql}')
        WHERE is_case = 1
        ORDER BY stratum_id
        {lim}
    """)
    count_cases, distinct_cases = con.execute(
        "SELECT count(*), count(DISTINCT stratum_id) FROM cases").fetchone()
    if count_cases != distinct_cases:
        raise RuntimeError("Duplicate/missing stratum_id among case rows")
    con.execute(f"""
        CREATE OR REPLACE TABLE positive_full AS
        SELECT DISTINCT grid_id, CAST(event_date AS DATE) AS flood_date
        FROM read_parquet('{all_sql}')
    """)
    bounds = con.execute("SELECT min(flood_date),max(flood_date) FROM positive_full").fetchone()
    if bounds[0] is None or bounds[1] is None:
        raise RuntimeError("Full positive index is empty")
    obs_first = str(bounds[0]); obs_last = str(bounds[1])
    print(f"    cases={count_cases:,}; observation window={obs_first}..{obs_last}", flush=True)

    print("[2] Build all same-weekday referents within each case month", flush=True)
    con.execute(f"""
        CREATE OR REPLACE TABLE referent_calendar AS
        SELECT c.stratum_id, c.grid_id, c.grid_x, c.grid_y, c.case_date,
               CAST(gs.d AS DATE) AS ref_date,
               c.grid_id || '__' || strftime(CAST(gs.d AS DATE),'%Y%m%d') AS climate_id
        FROM cases c
        CROSS JOIN LATERAL generate_series(
          GREATEST(date_trunc('month',c.case_date)::DATE, DATE '{obs_first}')::TIMESTAMP,
          LEAST((date_trunc('month',c.case_date)+ INTERVAL 1 MONTH - INTERVAL 1 DAY)::DATE,
                DATE '{obs_last}')::TIMESTAMP,
          INTERVAL 1 DAY
        ) AS gs(d)
        WHERE CAST(gs.d AS DATE) != c.case_date
          AND EXTRACT(DOW FROM CAST(gs.d AS DATE)) = EXTRACT(DOW FROM c.case_date)
    """)
    print("[3] Attach observed-positive flags and existing climate coverage", flush=True)
    con.execute(f"""
        CREATE OR REPLACE TABLE referents AS
        SELECT r.*,
          (p.flood_date IS NOT NULL) AS reported_flood_referent,
          (cl.climate_id IS NOT NULL) AS climate_id_present,
          (cl.climate_id IS NOT NULL AND
            cl.imerg_p3h_max_mm_max IS NOT NULL AND
            cl.imerg_p24_utc_mm_mean IS NOT NULL) AS imerg_present,
          (cl.climate_id IS NOT NULL AND
            cl.era5_event_p24_mm_mean IS NOT NULL AND
            cl.era5_api7_mm_mean IS NOT NULL AND
            cl.era5_sm1_prevday_mean IS NOT NULL) AS era5_present
        FROM referent_calendar r
        LEFT JOIN positive_full p ON r.grid_id=p.grid_id AND r.ref_date=p.flood_date
        LEFT JOIN (
          SELECT climate_id, imerg_p3h_max_mm_max,imerg_p24_utc_mm_mean,
                 era5_event_p24_mm_mean,era5_api7_mm_mean,era5_sm1_prevday_mean
          FROM read_parquet('{clim_sql}')
        ) cl USING (climate_id)
    """)
    con.execute("""
        CREATE OR REPLACE TABLE ref_stratum AS
        SELECT stratum_id, COUNT(*) AS n_referents,
               COUNT(*) FILTER (WHERE climate_id_present) AS n_lookup,
               COUNT(*) FILTER (WHERE imerg_present AND era5_present) AS n_complete_climate,
               COUNT(*) FILTER (WHERE reported_flood_referent) AS n_reported_flood_referents
        FROM referents GROUP BY stratum_id
    """)
    con.execute("""
        CREATE OR REPLACE TABLE ref_stratum_all AS
        SELECT c.stratum_id,c.grid_id,c.case_date,
               COALESCE(r.n_referents,0) AS n_referents,
               COALESCE(r.n_lookup,0) AS n_lookup,
               COALESCE(r.n_complete_climate,0) AS n_complete_climate,
               COALESCE(r.n_reported_flood_referents,0) AS n_reported_flood_referents
        FROM cases c LEFT JOIN ref_stratum r USING (stratum_id)
    """)
    print("[4] Audit original controls against ALL positive grid-days", flush=True)
    con.execute(f"""
        CREATE OR REPLACE TABLE old_controls_overlap AS
        SELECT COUNT(*) AS checked_controls,
               COUNT(*) FILTER (WHERE p.flood_date IS NOT NULL) AS known_positive_controls,
               COUNT(DISTINCT CASE WHEN p.flood_date IS NOT NULL THEN m.stratum_id ELSE NULL END)
                 AS affected_strata
        FROM read_parquet('{case_sql}') m
        JOIN cases c USING (stratum_id)
        LEFT JOIN positive_full p ON m.grid_id=p.grid_id
          AND CAST(m.sample_date AS DATE)=p.flood_date
        WHERE m.is_case=0
    """)
    print("[5] Export audit and missing climate requests", flush=True)
    con.execute(f"""
        COPY (SELECT * FROM referents)
        TO '{q(out/'19_standard_weekday_referent_pool.parquet')}'
        (FORMAT PARQUET,COMPRESSION ZSTD)
    """)
    con.execute(f"""
        COPY (SELECT * FROM ref_stratum_all)
        TO '{q(out/'19_referent_coverage_by_stratum.parquet')}'
        (FORMAT PARQUET,COMPRESSION ZSTD)
    """)
    con.execute(f"""
        COPY (
          SELECT DISTINCT climate_id, grid_id, grid_x,grid_y,ref_date AS sample_date,
              strftime(ref_date,'%Y-%m-%d') AS date_str,
              EXTRACT(YEAR FROM ref_date)::INT AS year,
              EXTRACT(MONTH FROM ref_date)::INT AS month,
              EXTRACT(DAY FROM ref_date)::INT AS day
          FROM referents WHERE NOT climate_id_present
        ) TO '{q(out/'19_missing_weekday_climate_requests.parquet')}'
        (FORMAT PARQUET,COMPRESSION ZSTD)
    """)
    con.execute(f"""
        COPY (
          SELECT DISTINCT climate_id, grid_id, grid_x,grid_y,ref_date AS sample_date,
             strftime(ref_date,'%Y-%m-%d') AS date_str
          FROM referents WHERE climate_id_present AND NOT era5_present
        ) TO '{q(out/'19_existing_era5_missing_weekday.parquet')}'
        (FORMAT PARQUET,COMPRESSION ZSTD)
    """)
    group_counts = con.execute("""
      SELECT COUNT(*) AS total_cases,
             SUM(CASE WHEN n_referents=0 THEN 1 ELSE 0 END) AS no_referents,
             SUM(CASE WHEN n_lookup=n_referents AND n_referents>0 THEN 1 ELSE 0 END)
               AS all_dates_in_lookup,
             SUM(CASE WHEN n_complete_climate=n_referents AND n_referents>0 THEN 1 ELSE 0 END)
               AS all_dates_complete_climate,
             SUM(CASE WHEN n_reported_flood_referents>0 THEN 1 ELSE 0 END)
               AS has_other_reported_event_day,
             AVG(n_referents) AS mean_referents,
             AVG(n_lookup::DOUBLE / NULLIF(n_referents,0)) AS mean_lookup_fraction,
             AVG(n_complete_climate::DOUBLE / NULLIF(n_referents,0)) AS mean_climate_fraction
      FROM ref_stratum_all
    """).fetchdf()
    ref_counts = con.execute("""
      SELECT COUNT(*) AS referent_rows,
        COUNT(*) FILTER(WHERE reported_flood_referent) AS referent_rows_reported_flood,
        COUNT(*) FILTER(WHERE climate_id_present) AS referent_rows_in_lookup,
        COUNT(*) FILTER(WHERE imerg_present AND era5_present) AS referent_rows_complete_climate,
        COUNT(DISTINCT climate_id) FILTER (WHERE NOT climate_id_present) AS missing_unique_climate_ids
      FROM referents
    """).fetchdf()
    old_counts=con.execute("SELECT * FROM old_controls_overlap").fetchdf()
    group_counts.to_csv(out/'19_referent_groups_summary.csv',index=False,encoding='utf-8-sig')
    ref_counts.to_csv(out/'19_referent_rows_summary.csv',index=False,encoding='utf-8-sig')
    old_counts.to_csv(out/'19_old_control_overlap.csv',index=False,encoding='utf-8-sig')
    annual = con.execute("""
      SELECT EXTRACT(YEAR FROM case_date)::INT AS case_year, COUNT(*) AS cases,
             ROUND(AVG(n_referents),3) AS mean_referents,
             ROUND(AVG((n_lookup=n_referents AND n_referents>0)::INT),5)
               AS fraction_all_dates_in_lookup,
             ROUND(AVG((n_complete_climate=n_referents AND n_referents>0)::INT),5)
               AS fraction_complete_climate,
             ROUND(AVG((n_reported_flood_referents>0)::INT),5) AS fraction_with_reported_flood_referent
      FROM ref_stratum_all GROUP BY 1 ORDER BY 1
    """).fetchdf()
    annual.to_csv(out/'19_referent_coverage_by_year.csv',index=False,encoding='utf-8-sig')
    print("\n[RESULT] Original controls versus full positive index:\n",old_counts.to_string(index=False))
    print("\n[RESULT] Standard referent pool:\n",ref_counts.to_string(index=False))
    print("\n[RESULT] Cases with all same-weekday climate dates available:\n",group_counts.to_string(index=False))
    print("\n[DONE]",out)
    print("CAUTION: these are exposure REFERENTS, not verified non-flood days.")
    print("Do NOT fit a publication model by simply dropping uncovered referent dates:")
    print("the existing 1:4 climate lookup was selected using the original case/reference design.")
    con.execute("CHECKPOINT")
    con.close()


if __name__ == "__main__":
    main()
