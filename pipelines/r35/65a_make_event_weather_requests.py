# -*- coding: utf-8 -*-
"""Stage65a: make exact unique GF10 grid-date weather requests from Stage63b.

The request manifest and yearly CSV assets are a stable bridge to EE.
No GEE calls. Date labels are UTC, not verified local event onset times.
"""
import argparse,json
from pathlib import Path
import duckdb,pandas as pd

def run(calendar,grid,out):
    out.mkdir(parents=True,exist_ok=True)
    con=duckdb.connect()
    con.execute("CREATE VIEW panel AS SELECT * FROM read_parquet(\x27"+str(calendar).replace("\x27","\x27\x27")+"\x27)")
    con.execute("CREATE VIEW grid AS SELECT grid_id, longitude, latitude, grid_x,grid_y FROM read_parquet(\x27"+str(grid).replace("\x27","\x27\x27")+"\x27)")
    # Avoid duplicates due to multiple strata referring to same grid and day.
    query="""SELECT DISTINCT p.grid_id,
            CAST(p.event_date AS VARCHAR) event_date,
            CAST(year(p.event_date) AS INTEGER) year,
            CAST(month(p.event_date) AS INTEGER) month,
            g.grid_x,g.grid_y,g.longitude,g.latitude,
            p.grid_id||'|'||CAST(p.event_date AS VARCHAR) climate_id
        FROM panel p JOIN grid g USING(grid_id)
        WHERE p.eligibility=1
        ORDER BY year,month,event_date,p.grid_id"""
    tbl=con.execute(query).df()
    if tbl.climate_id.duplicated().any():raise RuntimeError("Duplicate climate requests")
    if len(tbl)==0:raise ValueError("No case/reference dates")
    tbl.to_parquet(out/"65a_unique_requests_grid10.parquet",index=False)
    jobs=[]
    for year,g in tbl.groupby("year"):
        file=out/f"requests_{year}.csv"
        g.to_csv(file,index=False)
        jobs.append({"year":int(year),"rows":len(g),"dates":int(g.event_date.nunique()),
                     "path":str(file),
                     "asset_id":f"projects/eco-sector-503721-b7/assets/gf10_requests_{year}"})
    (out/"65a_asset_manifest.json").write_text(json.dumps(
        {"source_grid":str(grid),"source_calendar":str(calendar),
        "unique_requests":len(tbl),"year_assets":jobs,
        "note":"Upload CSV to GEE table as nongeometry, use fields longitude/latitude for point construction."},
        indent=2),encoding="utf-8")
    print("[DONE]",len(tbl),"unique grid-days, years",len(jobs))
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--calendar",type=Path,required=True)
    p.add_argument("--grid",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args();run(a.calendar,a.grid,a.out)
if __name__=="__main__":main()
