"""Rebuild canonical same-weekday daily calendar from ALL M3 grid-level episodes.

Unlike Stage20, this does NOT select cases based on the earlier 1:4 control
matching and ERA5 completeness. Use before further expensive GEE extraction.

Fixes DuckDB SQL day alias parse issue; validates complete calendar.
Defaults to last COMPLETE month (2026-01-31), explicitly excluding partial
February 2026. Existing ERA5-null lookup dates are flagged, NOT set to zero.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import duckdb
import pandas as pd
import numpy as np
from pyproj import Transformer

BASE=Path(r'E:\Global_flooding')
FULL=Path(r'E:\数据集\Groundsource_全球城市内涝点位\processed\groundsource_gridday_20km.parquet')

def qp(p:Path)->str:return p.as_posix().replace("'","''")
def save_parquet(con, sql: str, p:Path):
    if p.exists():p.unlink()
    con.execute(f"COPY ({sql}) TO '{qp(p)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print('[SAVED]',p,flush=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,default=BASE)
    ap.add_argument('--full-gridday',type=Path,default=FULL)
    ap.add_argument('--analysis-end',default='2026-01-31',help='Default excludes partial 2026 Feb')
    ap.add_argument('--chunk-size',type=int,default=15000)
    ap.add_argument('--threads',type=int,default=8)
    ap.add_argument('--skip-csv',action='store_true')
    a=ap.parse_args()
    if a.chunk_size<100 or a.chunk_size>100000:raise ValueError('Bad chunk size')
    out=a.base/'standard_calendar_20b'; out.mkdir(parents=True,exist_ok=True)
    episodes=a.base/'episode_control'/'flood_episodes_m3.parquet'
    climate=a.base/'climate_global'/'climate_features_primary.parquet'
    original=a.base/'climate_local_first'/'primary_complete_strata.parquet'
    for f in [episodes,climate,a.full_gridday,original]:
        if not f.exists():raise FileNotFoundError(f)
    con=duckdb.connect(str(out/'20b_work.duckdb'))
    con.execute(f'PRAGMA threads={max(a.threads,1)}')
    con.execute("PRAGMA memory_limit='12GB'")
    obs=con.execute(f"SELECT MIN(event_date)::DATE, MAX(event_date)::DATE FROM read_parquet('{qp(a.full_gridday)}')").fetchone()
    print('[SOURCE OBSERVATION]',obs,'requested analysis end',a.analysis_end,flush=True)
    end_date = pd.to_datetime(a.analysis_end).strftime('%Y-%m-%d')
    if pd.to_datetime(end_date).date() > obs[1]:raise ValueError('Analysis end extends beyond report window')
    if pd.to_datetime(end_date).date() < obs[0]:raise ValueError('Analysis end is before observation window')
    con.execute(f"""
      CREATE OR REPLACE TABLE all_cases AS
      SELECT DISTINCT grid_id,grid_x::INTEGER grid_x,grid_y::INTEGER grid_y,
             CAST(episode_start AS DATE) case_date
      FROM read_parquet('{qp(episodes)}')
      WHERE CAST(episode_start AS DATE) <= DATE '{end_date}'
    """)
    counts=con.execute('SELECT COUNT(*),COUNT(DISTINCT grid_id) FROM all_cases').fetchone()
    print('[ALL M3 CASES]',counts,flush=True)
    print('[BUILD] full month × weekday referents, de-duplicated grid × day',flush=True)
    con.execute(f"""
      CREATE OR REPLACE TABLE calendar_base AS
      SELECT c.grid_id, MIN(c.grid_x)::INTEGER AS grid_x,
             MIN(c.grid_y)::INTEGER AS grid_y,
             CAST(g.dt AS DATE) AS "day",
             c.grid_id || '__' || STRFTIME(CAST(g.dt AS DATE),'%Y%m%d') AS climate_id
      FROM all_cases c
      CROSS JOIN LATERAL GENERATE_SERIES(
        DATE_TRUNC('month',c.case_date)::TIMESTAMP,
        (DATE_TRUNC('month',c.case_date) + INTERVAL 1 MONTH - INTERVAL 1 DAY)::TIMESTAMP,
        INTERVAL 1 DAY
      ) AS g(dt)
      WHERE EXTRACT(DOW FROM g.dt)=EXTRACT(DOW FROM c.case_date)
        AND CAST(g.dt AS DATE) BETWEEN DATE '{obs[0]}' AND DATE '{end_date}'
      GROUP BY c.grid_id,CAST(g.dt AS DATE)
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE daily_panel AS
    WITH positive AS (
      SELECT DISTINCT grid_id, CAST(event_date AS DATE) AS reported_date
      FROM read_parquet('{qp(a.full_gridday)}')
    ), original AS (
      SELECT DISTINCT grid_id,CAST(sample_date AS DATE) AS original_date
      FROM read_parquet('{qp(original)}') WHERE is_case=1
    ), weather AS (
      SELECT climate_id,
       (imerg_p30_max_mm_max IS NOT NULL AND imerg_p3h_max_mm_max IS NOT NULL AND
        imerg_p24_utc_mm_mean IS NOT NULL) imerg_ok,
       (era5_event_p24_mm_mean IS NOT NULL AND era5_api7_mm_mean IS NOT NULL AND
        era5_sm1_prevday_mean IS NOT NULL) era5_ok
      FROM read_parquet('{qp(climate)}')
    )
    SELECT b.*,
       (b.grid_id || '__' || STRFTIME(b.day,'%Y%m') || '__W' ||
        CAST(EXTRACT(DOW FROM b.day) AS VARCHAR)) AS calendar_stratum_id,
       EXTRACT(YEAR FROM b.day)::INTEGER AS year,
       EXTRACT(MONTH FROM b.day)::INTEGER AS month,
       EXTRACT(DAY FROM b.day)::INTEGER AS day_of_month,
       EXTRACT(DOW FROM b.day)::INTEGER AS weekday,
       (ac.case_date IS NOT NULL)::INTEGER is_initial_case,
       (ac.case_date IS NOT NULL)::INTEGER episode_onset,
       (o.original_date IS NOT NULL)::INTEGER in_original_primary_complete,
       (p.reported_date IS NOT NULL)::INTEGER reported_flood_day,
       CASE WHEN p.reported_date IS NOT NULL AND ac.case_date IS NULL THEN 1 ELSE 0 END reported_non_onset_day,
       (w.climate_id IS NOT NULL)::INTEGER climate_in_lookup,
       CASE WHEN w.climate_id IS NOT NULL AND w.imerg_ok THEN 1 ELSE 0 END imerg_available,
       CASE WHEN w.climate_id IS NOT NULL AND w.era5_ok THEN 1 ELSE 0 END era5_available
    FROM calendar_base b
    LEFT JOIN all_cases ac ON b.grid_id=ac.grid_id AND b.day=ac.case_date
    LEFT JOIN original o ON b.grid_id=o.grid_id AND b.day=o.original_date
    LEFT JOIN positive p ON b.grid_id=p.grid_id AND b.day=p.reported_date
    LEFT JOIN weather w USING(climate_id)
    """)
    save_parquet(con,'SELECT * FROM daily_panel ORDER BY grid_id,day',out/'20_standard_daily_panel.parquet')
    qc=con.execute('''
       SELECT COUNT(*) unique_grid_dates, COUNT(DISTINCT climate_id) unique_ids,
         COUNT(DISTINCT calendar_stratum_id) calendar_strata,
         SUM(is_initial_case) initial_cases, SUM(episode_onset) onset_days,
         SUM(in_original_primary_complete) cases_in_previous_646270,
         SUM(reported_flood_day) reported_positive_days,
         SUM(reported_non_onset_day) reported_non_onset_days,
         SUM(CASE WHEN episode_onset=1 AND reported_flood_day=0 THEN 1 ELSE 0 END) onset_not_positive,
         SUM(CASE WHEN climate_in_lookup=0 THEN 1 ELSE 0 END) unique_missing_climate,
         SUM(CASE WHEN climate_in_lookup=1 AND era5_available=0 THEN 1 ELSE 0 END) existing_era_incomplete
       FROM daily_panel
    ''').fetchdf()
    qc.to_csv(out/'20_calendar_qc.csv',index=False,encoding='utf-8-sig')
    print('[QC]',qc.to_string(index=False),flush=True)
    if int(qc.unique_grid_dates.iloc[0])!=int(qc.unique_ids.iloc[0]):raise RuntimeError('Non-unique grid-date')
    if int(qc.initial_cases.iloc[0])!=counts[0]:raise RuntimeError('Cases lost in calendar')
    if int(qc.onset_not_positive.iloc[0]):raise RuntimeError('Episode onset not in full positive index')
    if int(qc.reported_non_onset_days.iloc[0]) != int(qc.reported_positive_days.iloc[0]) - int(qc.onset_days.iloc[0]):
        raise RuntimeError('Positive-day / onset decomposition mismatch')
    prior_n = con.execute(f"""SELECT COUNT(*) FROM (
       SELECT DISTINCT grid_id,CAST(sample_date AS DATE) AS original_date
       FROM read_parquet('{qp(original)}') WHERE is_case=1 AND CAST(sample_date AS DATE)<=DATE '{end_date}'
    )""").fetchone()[0]
    if int(qc.cases_in_previous_646270.iloc[0]) != int(prior_n):
        raise RuntimeError(f'Previously validated cases lost: {int(qc.cases_in_previous_646270.iloc[0])} vs {prior_n}')
    annual = con.execute("""
        SELECT
            year,
            COUNT(*) AS n_calendar_days,
            COUNT(DISTINCT calendar_stratum_id) AS n_strata,
            SUM(episode_onset) AS episode_onsets,
            SUM(reported_non_onset_day) AS reported_non_onset_days,
            SUM(CASE WHEN climate_in_lookup = 0 THEN 1 ELSE 0 END)
                AS missing_climate_days,
            ROUND(AVG(CAST(climate_in_lookup AS DOUBLE)), 6)
                AS climate_coverage_fraction
        FROM daily_panel
        GROUP BY year
        ORDER BY year
    """).fetchdf()
    annual.to_csv(out/'20_calendar_coverage_by_year.csv',index=False,encoding='utf-8-sig')
    strata=con.execute('''
     SELECT calendar_stratum_id, MIN(grid_id) grid_id,COUNT(*) observed_days,
      SUM(episode_onset) episode_onsets,SUM(reported_flood_day) reported_flood_days,
      SUM(reported_non_onset_day) reported_non_onset_days,
      SUM(climate_in_lookup) lookup_days,SUM(era5_available) era_available_days
     FROM daily_panel GROUP BY calendar_stratum_id
    ''').fetchdf()
    strata.to_csv(out/'20_calendar_strata_summary.csv',index=False,encoding='utf-8-sig')
    con.execute(f'''
      CREATE OR REPLACE TABLE missing_requests AS
      WITH numbered AS (
        SELECT grid_id,grid_x,grid_y,day AS sample_date,climate_id,year,month,
         day_of_month AS "day", STRFTIME(day,'%Y-%m-%d') AS date_str,
         ROW_NUMBER() OVER(PARTITION BY year,month ORDER BY climate_id)-1 rn
        FROM daily_panel WHERE climate_in_lookup=0
      ) SELECT * EXCLUDE(rn), FLOOR(rn/{a.chunk_size})::INTEGER request_chunk
      FROM numbered
    ''')
    save_parquet(con,'SELECT * FROM missing_requests',out/'20_missing_climate_requests_chunked.parquet')
    manifest=con.execute('''
      SELECT year,month,request_chunk,COUNT(*) requests
      FROM missing_requests GROUP BY year,month,request_chunk ORDER BY year,month,request_chunk
    ''').fetchdf()
    manifest.to_csv(out/'20_missing_task_manifest.csv',index=False,encoding='utf-8-sig')
    print(f'[MISSING] {manifest.requests.sum():,} requests, {len(manifest)} month-chunk rows',flush=True)
    if int(manifest.requests.sum()) != int(qc.unique_missing_climate.iloc[0]):
        raise RuntimeError("Missing request manifest does not match panel coverage")
    if not a.skip_csv:
        directory=out/'gee_upload_standard'; directory.mkdir(parents=True,exist_ok=True)
        tf=Transformer.from_crs('EPSG:6933','EPSG:4326',always_xy=True)
        for (year,) in con.execute('SELECT DISTINCT year FROM missing_requests ORDER BY year').fetchall():
            df=con.execute('SELECT * FROM missing_requests WHERE year=? ORDER BY month,request_chunk,climate_id',[year]).fetchdf()
            x=(df.grid_x.to_numpy(dtype=float)+0.5)*20000
            y=(df.grid_y.to_numpy(dtype=float)+0.5)*20000
            lon,lat=tf.transform(x,y)
            df['longitude']=lon;df['latitude']=lat
            df['sample_date']=pd.to_datetime(df['sample_date']).dt.strftime('%Y-%m-%d')
            dest=directory/f'standard_requests_{year}.csv'
            df.to_csv(dest,index=False,encoding='utf-8')
            print(f'[CSV] {year}: {len(df):,}',flush=True)
    con.execute('CHECKPOINT');con.close()
    print('[DONE] Full M3 onset cohort calendar, no GEE calls; use THIS folder in Stage21/22.',flush=True)

if __name__=='__main__':main()
