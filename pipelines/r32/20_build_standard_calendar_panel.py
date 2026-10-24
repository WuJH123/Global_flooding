"""20: Canonical standard time-stratified calendar panel, NO Earth Engine computation.

Build one row per grid x calendar day from the exact case/referent domain
established by audit 19. Keep all known Groundsource-positive days and
all M3 episode onsets distinctly labelled. Generate de-duplicated missing
climate requests and an upload/compute manifest for LATER EE extraction.

Important:
* `reported_flood_day=0` means NO RECORD, not proven absence of flood.
* `episode_onset=0` on a continuing flood day does not mean an at-risk day.
* Do NOT fit a final conditional-Poisson model using only available exposure
  dates: those dates were chosen using the earlier outcome-aware scheme.
* This script does not make any GEE/network calls.

Requires: duckdb pandas pyarrow pyproj numpy
"""
from __future__ import annotations
import argparse
from pathlib import Path
import duckdb
import pandas as pd
import numpy as np
from pyproj import Transformer


def qp(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def export(con: duckdb.DuckDBPyConnection, sql: str, path: Path) -> None:
    if path.exists():
        path.unlink()
    con.execute(f"COPY ({sql}) TO '{qp(path)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"[SAVED] {path}", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, default=Path(r'E:\Global_flooding'))
    p.add_argument('--full-gridday', type=Path,
                   default=Path(r'E:\数据集\Groundsource_全球城市内涝点位\processed\groundsource_gridday_20km.parquet'))
    p.add_argument('--chunk-size', type=int, default=15000)
    p.add_argument('--threads', type=int, default=8)
    p.add_argument('--skip-csv', action='store_true', help='Do not create yearly GEE import CSVs')
    a = p.parse_args()
    if not 100 <= a.chunk_size <= 100000:
        raise ValueError('--chunk-size must be 100..100000')
    base = a.base
    cases = base/'climate_local_first'/'primary_complete_strata.parquet'
    refs = base/'referent_audit_19'/'19_standard_weekday_referent_pool.parquet'
    climate = base/'climate_global'/'climate_features_primary.parquet'
    episodes = base/'episode_control'/'flood_episodes_m3.parquet'
    outputs = base/'standard_calendar_20'
    outputs.mkdir(parents=True, exist_ok=True)
    for file in [cases, refs, climate, episodes, a.full_gridday]:
        if not file.exists():
            raise FileNotFoundError(file)

    con = duckdb.connect(str(outputs/'calendar_work.duckdb'))
    con.execute(f'PRAGMA threads={max(1, a.threads)}')
    con.execute("PRAGMA memory_limit='12GB'")
    print('[1/6] Canonical case/reference calendar, de-duplicated grid-date', flush=True)
    con.execute(f"""
      CREATE OR REPLACE TABLE base_cases AS
      SELECT DISTINCT grid_id,grid_x,grid_y,CAST(sample_date AS DATE) AS case_date
      FROM read_parquet('{qp(cases)}') WHERE is_case=1
    """)
    n_cases = con.execute('SELECT COUNT(*) FROM base_cases').fetchone()[0]
    if n_cases != 646270:
        print(f'WARNING: expected 646270 baseline case rows; got {n_cases}',flush=True)

    # Note: the repeated referent rows in audit 19 are deliberately collapsed.
    con.execute(f"""
      CREATE OR REPLACE TABLE calendar_base AS
      SELECT grid_id,MIN(grid_x)::INTEGER grid_x, MIN(grid_y)::INTEGER grid_y,
        day, (grid_id || '__' || strftime(day,'%Y%m%d')) AS climate_id
      FROM (
        SELECT grid_id, grid_x, grid_y, case_date AS day FROM base_cases
        UNION ALL
        SELECT grid_id, grid_x, grid_y, CAST(ref_date AS DATE) AS day
        FROM read_parquet('{qp(refs)}')
      ) q
      GROUP BY grid_id,day
    """)

    print('[2/6] Attach reported-positive, episode onset and climate coverage flags',flush=True)
    con.execute(f"""
      CREATE OR REPLACE TABLE daily_panel AS
      WITH p AS (
        SELECT DISTINCT grid_id,CAST(event_date AS DATE) AS day
        FROM read_parquet('{qp(a.full_gridday)}')
      ),
      e AS (
        SELECT DISTINCT grid_id, CAST(episode_start AS DATE) AS day
        FROM read_parquet('{qp(episodes)}')
      ),
      cl AS (
        SELECT climate_id,
          (imerg_p24_utc_mm_mean IS NOT NULL AND imerg_p3h_max_mm_max IS NOT NULL) AS has_imerg,
          (era5_api7_mm_mean IS NOT NULL AND era5_sm1_prevday_mean IS NOT NULL
           AND era5_event_p24_mm_mean IS NOT NULL) AS has_era5
        FROM read_parquet('{qp(climate)}')
      )
      SELECT b.*,
        (b.grid_id || '__' || strftime(b.day,'%Y%m') || '__W' ||
           CAST(EXTRACT(DOW FROM b.day) AS VARCHAR)) AS calendar_stratum_id,
        EXTRACT(YEAR FROM b.day)::INTEGER AS year,
        EXTRACT(MONTH FROM b.day)::INTEGER AS month,
        EXTRACT(DAY FROM b.day)::INTEGER AS day_of_month,
        EXTRACT(DOW FROM b.day)::INTEGER AS weekday,
        (bc.case_date IS NOT NULL)::INTEGER AS is_initial_case,
        (p.day IS NOT NULL)::INTEGER AS reported_flood_day,
        (e.day IS NOT NULL)::INTEGER AS episode_onset,
        CASE WHEN p.day IS NOT NULL AND e.day IS NULL THEN 1 ELSE 0 END AS reported_non_onset_day,
        (cl.climate_id IS NOT NULL)::INTEGER AS climate_in_lookup,
        CASE WHEN cl.climate_id IS NOT NULL AND cl.has_imerg THEN 1 ELSE 0 END AS imerg_available,
        CASE WHEN cl.climate_id IS NOT NULL AND cl.has_era5 THEN 1 ELSE 0 END AS era5_available
      FROM calendar_base b
      LEFT JOIN base_cases bc ON b.grid_id=bc.grid_id AND b.day=bc.case_date
      LEFT JOIN p ON b.grid_id=p.grid_id AND b.day=p.day
      LEFT JOIN e ON b.grid_id=e.grid_id AND b.day=e.day
      LEFT JOIN cl USING(climate_id)
    """)
    panel_parquet = outputs/'20_standard_daily_panel.parquet'
    export(con,'SELECT * FROM daily_panel ORDER BY grid_id,day',panel_parquet)

    print('[3/6] QC: calendar structure and alternative outcome definitions', flush=True)
    qc = con.execute("""
      SELECT COUNT(*) AS unique_grid_dates, COUNT(DISTINCT climate_id) AS unique_ids,
        COUNT(DISTINCT calendar_stratum_id) AS calendar_strata,
        SUM(is_initial_case) AS initial_cases,
        SUM(episode_onset) AS onset_days,
        SUM(reported_flood_day) AS reported_positive_days,
        SUM(reported_non_onset_day) AS reported_non_onset_days,
        SUM(CASE WHEN episode_onset=1 AND reported_flood_day=0 THEN 1 ELSE 0 END) AS onset_not_positive,
        SUM(CASE WHEN climate_in_lookup=0 THEN 1 ELSE 0 END) AS unique_missing_climate,
        SUM(CASE WHEN climate_in_lookup=1 AND era5_available=0 THEN 1 ELSE 0 END) AS existing_era_incomplete
      FROM daily_panel
    """).fetchdf()
    qc.to_csv(outputs/'20_calendar_qc.csv',index=False,encoding='utf-8-sig')
    print(qc.to_string(index=False),flush=True)
    if int(qc.loc[0,'unique_grid_dates']) != int(qc.loc[0,'unique_ids']):
        raise RuntimeError('Repeated grid × day after de-duplication')
    if int(qc.loc[0,'initial_cases']) != int(n_cases):
        raise RuntimeError('A baseline case day was lost in calendar build')
    if int(qc.loc[0,'onset_not_positive']) > 0:
        raise RuntimeError('M3 episode onset absent from full positive gridday index')

    annual = con.execute("""
      SELECT year,COUNT(*) AS days,
        COUNT(DISTINCT calendar_stratum_id) AS strata,
        SUM(is_initial_case) AS initial_cases,
        SUM(episode_onset) AS episode_onsets,
        SUM(reported_non_onset_day) AS reported_non_onset_days,
        SUM(CASE WHEN climate_in_lookup=0 THEN 1 ELSE 0 END) AS missing_climate_days,
        ROUND(AVG(climate_in_lookup::DOUBLE),6) AS climate_coverage_fraction
      FROM daily_panel GROUP BY year ORDER BY year
    """).fetchdf()
    annual.to_csv(outputs/'20_calendar_coverage_by_year.csv',index=False,encoding='utf-8-sig')

    strata = con.execute("""
      SELECT calendar_stratum_id,MIN(grid_id) AS grid_id,
        COUNT(*) AS observed_days, SUM(is_initial_case) AS initial_cases,
        SUM(episode_onset) AS episode_onsets,
        SUM(reported_flood_day) AS reported_flood_days,
        SUM(reported_non_onset_day) AS reported_non_onset_days,
        SUM(climate_in_lookup) AS lookup_days,
        SUM(era5_available) AS era_available_days
      FROM daily_panel GROUP BY calendar_stratum_id
    """).fetchdf()
    strata.to_csv(outputs/'20_calendar_strata_summary.csv',index=False,encoding='utf-8-sig')
    print('[4/6] Generate exact remaining unique climate requests', flush=True)
    # Determine deterministic chunks within year x month, not by case groups.
    con.execute(f"""
      CREATE OR REPLACE TABLE missing_requests AS
      WITH numbered AS (
        SELECT grid_id,grid_x,grid_y,day AS sample_date,
               climate_id,year,month,day_of_month AS day,
               strftime(day,'%Y-%m-%d') AS date_str,
               ROW_NUMBER() OVER(PARTITION BY year,month ORDER BY climate_id)-1 AS rn
        FROM daily_panel WHERE climate_in_lookup=0
      )
      SELECT * EXCLUDE(rn), FLOOR(rn / {a.chunk_size})::INTEGER AS request_chunk
      FROM numbered
    """)
    export(con, 'SELECT * FROM missing_requests',outputs/'20_missing_climate_requests_chunked.parquet')
    manifest = con.execute("""
      SELECT year,month,request_chunk,COUNT(*) AS requests
      FROM missing_requests GROUP BY year,month,request_chunk
      ORDER BY year,month,request_chunk
    """).fetchdf()
    manifest.to_csv(outputs/'20_missing_task_manifest.csv',index=False,encoding='utf-8-sig')
    print(f'[REQUESTS] {int(manifest.requests.sum()):,} unique missing grid-days; '
          f'{len(manifest):,} planned year-month-chunk tasks (NOT submitted)',flush=True)

    if not a.skip_csv:
        print('[5/6] Yearly upload CSV with grid-centre lon/lat',flush=True)
        transformer = Transformer.from_crs('EPSG:6933','EPSG:4326',always_xy=True)
        upload_dir = outputs/'gee_upload_standard'
        upload_dir.mkdir(parents=True,exist_ok=True)
        years = con.execute('SELECT DISTINCT year FROM missing_requests ORDER BY year').fetchall()
        for (year,) in years:
            df = con.execute('SELECT * FROM missing_requests WHERE year=? ORDER BY month,request_chunk,climate_id',[year]).fetchdf()
            xs=(df.grid_x.to_numpy(dtype=np.float64)+0.5)*20000
            ys=(df.grid_y.to_numpy(dtype=np.float64)+0.5)*20000
            lons,lats=transformer.transform(xs,ys)
            df['longitude']=lons
            df['latitude']=lats
            df['sample_date']=pd.to_datetime(df['sample_date']).dt.strftime('%Y-%m-%d')
            out=upload_dir/f'standard_requests_{year}.csv'
            df.to_csv(out,index=False,encoding='utf-8')
            print(f'  {year}: {len(df):,} records -> {out.name}',flush=True)
    else:
        print('[5/6] Skipped upload CSV generation',flush=True)

    con.execute('CHECKPOINT')
    con.close()
    print('[6/6] COMPLETE: no GEE calls made',flush=True)
    print('Core panel:',panel_parquet,flush=True)
    print('Coverage by year:',outputs/'20_calendar_coverage_by_year.csv',flush=True)
    print('Task manifest:',outputs/'20_missing_task_manifest.csv',flush=True)
    print('IMPORTANT: the daily panel has missing weather dates. Do not fit final inference before completing exposure.',flush=True)


if __name__=='__main__':
    main()
