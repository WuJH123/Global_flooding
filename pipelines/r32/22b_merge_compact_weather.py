"""Groundsource 22b: verify compact cross-year weather exports and join standard grid-day calendar.

Input: Stage20 2,663,528-row standard calendar, Stage11 original 2,789,073-row
climate lookup, Stage21 optimized plan, and all NEW weather CSVs.

This script FAILS rather than silently dropping missing dates, duplicate weather
IDs, or incomplete data. Does not treat no-report days as verified flood-free.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import duckdb
import pandas as pd

BASE=Path(r'E:\Global_flooding')
WEATHER_NAMES=[
 'imerg_p30_max_mm','imerg_p1h_max_mm','imerg_p3h_max_mm',
 'imerg_p6h_max_mm','imerg_p12h_max_mm','imerg_p24_utc_mm',
 'era5_event_p24_mm','era5_event_runoff24_mm','era5_event_surface_runoff24_mm',
 'era5_ant_p3d_mm','era5_ant_p7d_mm','era5_ant_runoff3d_mm','era5_ant_runoff7d_mm',
 'era5_api3_mm','era5_api7_mm','era5_sm1_prevday','era5_sm2_prevday'
]
WCOLS=[f'{name}_{stat}' for name in WEATHER_NAMES for stat in ('mean','max')]


def qp(p:Path)->str: return p.as_posix().replace("'","''")

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--base',type=Path,default=BASE)
 p.add_argument('--calendar-dir',type=Path,default=None,help='Stage20b output, defaults to standard_calendar_20b')
 p.add_argument('--csv-root',type=Path,default=BASE/'Standard_Climate_Compact_all')
 p.add_argument('--rescue-plan',type=Path,default=None,help='Optional immutable Stage21g rescue plan')
 p.add_argument('--rescue-csv-root',type=Path,default=None,help='Downloaded STD32R files; required with --rescue-plan')
 a=p.parse_args()
 root=a.calendar_dir or (a.base/'standard_calendar_20b')
 panel=root/'20_standard_daily_panel.parquet'
 miss=root/'20_missing_climate_requests_chunked.parquet'
 plan_path=root/'21_compact'/'compact_task_plan.csv'
 primary=a.base/'climate_global'/'climate_features_primary.parquet'
 for f in [panel,miss,plan_path,primary,a.csv_root]:
  if not f.exists():raise FileNotFoundError(f)
 plan=pd.read_csv(plan_path)
 if len(plan)==0:raise RuntimeError('Empty plan')
 task_records = [
  {"block":int(r.block), "parent":int(r.block),
   "requests":int(r.requests), "filename":str(r.filename)}
  for r in plan.itertuples(index=False)
 ]
 scan_dirs=[a.csv_root]
 if (a.rescue_plan is None)!=(a.rescue_csv_root is None):
  raise ValueError('--rescue-plan and --rescue-csv-root must be specified together')
 if a.rescue_plan is not None:
  if not a.rescue_plan.exists():raise FileNotFoundError(a.rescue_plan)
  if not a.rescue_csv_root.exists():raise FileNotFoundError(a.rescue_csv_root)
  rescue=pd.read_csv(a.rescue_plan)
  required={'parent_block','recovery_part','requests','filename','members_json'}
  if required-set(rescue.columns):
   raise RuntimeError('Recovery plan missing columns: '+str(sorted(required-set(rescue.columns))))
  parents=set(map(int,rescue.parent_block))
  if not parents or not parents.issubset(set(map(int,plan.block))):
   raise RuntimeError('Rescue parents not a subset of original plan')
  for block in sorted(parents):
   p_record=plan.loc[plan.block==block]
   old=json.loads(p_record.iloc[0].members_json)
   replacement=rescue.loc[rescue.parent_block==block].sort_values('recovery_part')
   new=[m for rec in replacement.itertuples(index=False) for m in json.loads(rec.members_json)]
   if old!=new or int(replacement.requests.sum())!=int(p_record.iloc[0].requests):
    raise RuntimeError(f'Rescue plan B{block:03d} has missing, duplicated or re-ordered source chunks')
   if replacement.recovery_part.duplicated().any():
    raise RuntimeError(f'Rescue plan B{block:03d} repeats a child part')
  task_records=[r for r in task_records if r['parent'] not in parents]
  task_records.extend([
   {"block":int(r.recovery_part),"parent":int(r.parent_block),
    "requests":int(r.requests),"filename":str(r.filename)}
   for r in rescue.itertuples(index=False)
  ])
  scan_dirs.append(a.rescue_csv_root)
 pattern=re.compile(r'^(std32(?:c|r)_[a-f0-9]{8}_(?:b\d{3}|b\d{3}_s\d{2}))\.csv$', re.I)
 found={}
 for folder in scan_dirs:
  for f in folder.rglob('*.csv'):
   m=pattern.fullmatch(f.name)
   if m:found.setdefault(m.group(1).lower(),[]).append(f)
 expected={r['filename'].lower() for r in task_records}
 if len(expected)!=len(task_records):raise RuntimeError('Duplicate selected export filename in plan')
 missing=expected-set(found)
 duplicate={key:fs for key,fs in found.items() if key in expected and len(fs)!=1}
 extra=set(found)-expected
 if missing or duplicate or extra:
  print('Missing selected task outputs:',sorted(missing))
  print('Duplicate task outputs:',duplicate)
  print('Unrecognized/originally failed export files:',sorted(extra))
  raise RuntimeError('Selected compact/rescue exports do not match authoritative plan')
 con=duckdb.connect()
 con.execute('PRAGMA threads=8')
 outdir=a.base/'standard_calendar_22_compact'
 outdir.mkdir(parents=True,exist_ok=True)
 tasks=[]
 for r in task_records:
  f=found[r['filename'].lower()][0]
  count=con.execute(f"SELECT COUNT(*) FROM read_csv_auto('{qp(f)}',header=true,all_varchar=true)").fetchone()[0]
  tasks.append(dict(parent_block=r['parent'],part=r['block'],
                    expected=int(r['requests']),actual=int(count),file=str(f)))
 taskdf=pd.DataFrame(tasks)
 taskdf.to_csv(outdir/'22_export_task_qc.csv',index=False,encoding='utf-8-sig')
 bad=taskdf[taskdf.actual!=taskdf.expected]
 if len(bad):
  print(bad.to_string(index=False))
  raise RuntimeError('Some task outputs have unexpected row counts')
 files=[found[r['filename'].lower()][0] for r in task_records]
 src='['+','.join("'"+qp(f)+"'" for f in files)+']'
 con.execute(f"""
 CREATE OR REPLACE TEMP VIEW new_csv AS
 SELECT * FROM read_csv_auto({src},header=true,union_by_name=true)
 """)
 cols={row[0] for row in con.execute('DESCRIBE SELECT * FROM new_csv').fetchall()}
 must=set(WCOLS)|{'climate_id','date_str','grid_id'}
 if must-cols:raise RuntimeError('New CSV weather columns missing: '+repr(sorted(must-cols)))
 newqc=con.execute(f"""
 SELECT COUNT(*) n, COUNT(DISTINCT climate_id) unique_ids,
        COUNT(*)-COUNT(DISTINCT climate_id) duplicate_ids
 FROM new_csv
 """).fetchdf()
 count=int(newqc.n.iloc[0])
 expected_new=con.execute(f"SELECT COUNT(*) FROM read_parquet('{qp(miss)}')").fetchone()[0]
 if count!=expected_new or int(newqc.duplicate_ids.iloc[0]):
  raise RuntimeError(f'Bad new CSV coverage: {newqc.to_dict("records")}')
 # ID set equality: BOTH ways, not merely equal row counts.
 sets=con.execute(f"""
 WITH m AS (SELECT climate_id FROM read_parquet('{qp(miss)}')),
      n AS (SELECT climate_id FROM new_csv)
 SELECT (SELECT COUNT(*) FROM m LEFT JOIN n USING(climate_id) WHERE n.climate_id IS NULL) missing_ids,
        (SELECT COUNT(*) FROM n LEFT JOIN m USING(climate_id) WHERE m.climate_id IS NULL) extra_ids
 """).fetchone()
 if sets!=(0,0):raise RuntimeError(f'New CSV ID coverage differs from Stage20: {sets}')
 # Validate the returned feature's date/grid, not merely matching climate_id.
 mismatch=con.execute(f"""
 SELECT COUNT(*) FROM new_csv n JOIN read_parquet('{qp(miss)}') m USING(climate_id)
 WHERE CAST(n.grid_id AS VARCHAR) <> CAST(m.grid_id AS VARCHAR)
    OR TRY_CAST(n.date_str AS DATE) IS NULL
    OR TRY_CAST(n.date_str AS DATE) <> CAST(m.sample_date AS DATE)
 """).fetchone()[0]
 if mismatch:raise RuntimeError(f'GEE export has {mismatch} wrong grid/date rows')
 # Avoid implicit string inference differences for numeric climate values.
 weather_sql=',\n'.join('TRY_CAST("'+c+'" AS DOUBLE) AS "'+c+'"' for c in WCOLS)
 con.execute(f"""
 CREATE OR REPLACE TEMP VIEW new_weather AS
 SELECT climate_id, {weather_sql} FROM new_csv
 """)
 existing_weather_sql=', '.join(WCOLS)
 con.execute(f"""
 CREATE OR REPLACE TEMP VIEW combined AS
 SELECT climate_id,{existing_weather_sql}
 FROM read_parquet('{qp(primary)}')
 UNION ALL
 SELECT climate_id,{existing_weather_sql} FROM new_weather
 """)
 pcols={row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{qp(panel)}')").fetchall()}
 if 'climate_id' not in pcols:raise RuntimeError('Calendar missing climate_id')
 out=outdir/'22_standard_daily_with_climate.parquet'
 if out.exists():out.unlink()
 con.execute(f"""
 COPY (
 SELECT p.*, {', '.join('w.'+x for x in WCOLS)},
        CASE WHEN p.reported_non_onset_day=1 THEN 0 ELSE 1 END AS eligible_risk_day,
        CASE WHEN p.episode_onset=1 THEN 1 ELSE 0 END AS y_episode_onset
 FROM read_parquet('{qp(panel)}') p
 LEFT JOIN combined w USING(climate_id)
 ORDER BY p.calendar_stratum_id,p.day
 ) TO '{qp(out)}' (FORMAT PARQUET,COMPRESSION ZSTD)
 """)
 null_expr=' OR '.join(f'{c} IS NULL' for c in WCOLS)
 qc=con.execute(f"""
 SELECT COUNT(*) AS rows,COUNT(DISTINCT climate_id) AS unique_dates,
        COUNT(DISTINCT calendar_stratum_id) AS strata,
        SUM(y_episode_onset) AS onset_days,
        SUM(reported_non_onset_day) AS excluded_continuing_days,
        SUM(CASE WHEN {null_expr} THEN 1 ELSE 0 END) AS rows_any_weather_missing,
        COUNT(*) FILTER(WHERE eligible_risk_day=1) AS eligible_days
 FROM read_parquet('{qp(out)}')
 """).fetchdf()
 qc.to_csv(outdir/'22_standard_daily_qc.csv',index=False,encoding='utf-8-sig')
 print(qc.to_string(index=False))
 expected_n,expected_case=con.execute(f"SELECT COUNT(*),SUM(episode_onset) FROM read_parquet('{qp(panel)}')").fetchone()
 if int(qc.rows.iloc[0])!=expected_n or int(qc.unique_dates.iloc[0])!=expected_n:
  raise RuntimeError('Stage22 output must preserve unique panel grid-dates exactly')
 if int(qc.onset_days.iloc[0])!=expected_case:
  raise RuntimeError('Stage22 must preserve every original panel onset')
 print('[SAVED]',out)
 print('NOTE: missing ERA5 values can remain for offshore grids. Do not replace nulls with zero.')
 if int(qc.rows_any_weather_missing.iloc[0]):
  print('[WARNING] Weather missing; perform strict model eligibility/coverage QC before inference.')
 con.close()

if __name__=='__main__':main()
