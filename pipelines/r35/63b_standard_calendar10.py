# -*- coding: utf-8 -*-
"""Stage63b: time-stratified same-month/same-weekday 10km calendar.

Geocoded polygons are NOT flood footprint. Primary high-specificity
onsets only; flag all overlapping/continuing reports as 'unknown'
and never assume unreported dates have true zero flood occurrence.
Produce one record per case-stratum & day, with 1+ onsets possible.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import duckdb

def build(links_root:Path,out:Path,analysis_end="2025-12-31"):
    files=str(links_root/"63_event_grid_links_*.parquet")
    if not list(links_root.glob("63_event_grid_links_*.parquet")):
        raise FileNotFoundError(files)
    out.mkdir(parents=True,exist_ok=True)
    con=duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute(f"""CREATE TEMP VIEW links AS
      SELECT grid_id,event_id,TRY_CAST(start_date AS DATE) date0,
             TRY_CAST(end_date AS DATE) date1,"primary" AS is_primary,is_high_geocode_specificity
      FROM read_parquet('{files.replace(chr(39),chr(39)*2)}')
      WHERE grid_id IS NOT NULL""")
    e=analysis_end
    # Full positive date mask from all mappable geo polygons, not only
    # high-precision primary cases. A report is not verified flood absence.
    con.execute(f"""CREATE TEMP TABLE positives AS
       WITH input AS (
         SELECT DISTINCT grid_id, date0,
           CASE WHEN date1 IS NULL OR date1<date0 THEN date0
                WHEN date1>date0+INTERVAL '7 days' THEN date0+INTERVAL '7 days'
                ELSE date1 END date1
         FROM links WHERE date0 IS NOT NULL AND date0<=DATE '{e}'
       )
       SELECT DISTINCT grid_id, CAST(d AS DATE) event_date
       FROM input, LATERAL generate_series(date0,date1,INTERVAL '1 day') t(d)
       WHERE d<=DATE '{e}'""")
    con.execute(f"""CREATE TEMP TABLE onsets AS
       SELECT DISTINCT grid_id,date0 event_date
       FROM links WHERE is_primary AND is_high_geocode_specificity
         AND date0 IS NOT NULL AND date0 BETWEEN DATE '2000-01-01' AND DATE '{e}'""")
    # date calendar per (grid_id,onset date), all same-month same-weekday
    con.execute("""CREATE TEMP TABLE calendar AS
       WITH base AS (
         SELECT grid_id,event_date onset_date,
           date_trunc('month',event_date)::DATE month_start
         FROM onsets
       ),
       candidates AS (
         SELECT grid_id,onset_date,CAST(month_start+CAST(i AS INTEGER) AS DATE) ref_date
         FROM base,range(0,31) x(i)
       )
       SELECT c.grid_id,c.onset_date,c.ref_date,
         (c.onset_date=c.ref_date) is_index_day,
         (p.grid_id IS NOT NULL) is_reported_positive,
         (o.grid_id IS NOT NULL) is_onset
       FROM candidates c
       LEFT JOIN positives p ON p.grid_id=c.grid_id AND p.event_date=c.ref_date
       LEFT JOIN onsets o ON o.grid_id=c.grid_id AND o.event_date=c.ref_date
       WHERE month(c.ref_date)=month(c.onset_date)
         AND year(c.ref_date)=year(c.onset_date)
         AND dayofweek(c.ref_date)=dayofweek(c.onset_date)""")
    # Strict exposure-referent choice: a case day always retained,
    # other known-positive days excluded, no unknown zeros asserted.
    con.execute("""CREATE TEMP TABLE eligible AS
      SELECT grid_id||'|'||CAST(onset_date AS VARCHAR) stratum_id,
       grid_id,onset_date,ref_date event_date,
       CAST(is_index_day AS INTEGER) is_onset,
       CAST(is_reported_positive AS INTEGER) reported_positive,
       CAST(1 AS INTEGER) eligibility
      FROM calendar
      WHERE is_index_day OR NOT is_reported_positive""")
    # Exclude strata with no available matched day; keep all same-weekday
    con.execute("""CREATE TEMP TABLE final AS
       SELECT * FROM eligible
       WHERE stratum_id IN
         (SELECT stratum_id FROM eligible GROUP BY stratum_id
          HAVING SUM(is_onset)>=1 AND COUNT(*)>=2)""")
    dest=out/"63b_calendar_grid10.parquet"
    con.execute(f"COPY final TO '{str(dest).replace(chr(39),chr(39)*2)}' (FORMAT PARQUET,COMPRESSION ZSTD)")
    stats=con.execute("""SELECT COUNT(*) days,COUNT(DISTINCT stratum_id) strata,
        SUM(is_onset) onsets, COUNT(DISTINCT grid_id) grids,
        MIN(event_date) min_day, MAX(event_date) max_day FROM final""").fetchone()
    s={"rows":stats[0],"strata":stats[1],"onsets":stats[2],"grids":stats[3],
       "min_date":str(stats[4]),"max_date":str(stats[5]),
       "event_definition":"primary high-specificity NEWS geocoding date, not observed inundation extent",
       "referent_definition":"same month/year, same weekday, all known positive grid dates excluded; unreported is NOT verified non-flood",
       "note":"Non-independent repeated storms/cities; meteorology not yet joined"}
    (out/"63b_calendar_qc.json").write_text(json.dumps(s,indent=2,ensure_ascii=False),encoding="utf-8")
    con.close();print("[DONE]",s)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--links-root",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--analysis-end",default="2025-12-31")
    a=p.parse_args();build(a.links_root,a.out,a.analysis_end)
if __name__=="__main__":main()
