"""Stage35: transparent connected-component *proxy* storm grouping.

Connect two reported onset episodes when their 20-km EASE grid indices are
8-neighbors (including same cell) and onset dates differ <= N days. Edges are
transitive; an extended connected component is not guaranteed one meteorological
storm. Export episode-to-proxy-group membership and duration/size QC.
NEVER refer to clusters as verified storms without IMERG/weather-track validation.
"""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

class DSU:
    def __init__(self,n):
        self.p=list(range(n));self.sz=[1]*n
    def find(self,i):
        while self.p[i]!=i:
            self.p[i]=self.p[self.p[i]]
            i=self.p[i]
        return i
    def union(self,a,b):
        a,b=self.find(a),self.find(b)
        if a==b:return
        if self.sz[a]<self.sz[b]:a,b=b,a
        self.p[b]=a;self.sz[a]+=self.sz[b]

def connected_proxy(df,lag_days=1):
    if lag_days<0 or lag_days>7:raise ValueError("lag-days in 0..7")
    need={"grid_id","episode_start","grid_x","grid_y"}
    if not need.issubset(df.columns):raise ValueError("episode source missing: "+str(need-set(df.columns)))
    x=df.copy()
    x["episode_start"]=pd.to_datetime(x.episode_start,errors="raise")
    x=x.reset_index(drop=True)
    keys=list(zip(x.grid_x.astype(int),x.grid_y.astype(int),
                  x.episode_start.to_numpy(dtype='datetime64[D]').astype('int64')))
    index={}
    for j,key in enumerate(keys):
        index.setdefault(key,[]).append(j)
    ds=DSU(len(x))
    # Each undirected link is found at most twice; union de-duplicates.
    for i,(gx,gy,day) in enumerate(keys):
        for xx in (gx-1,gx,gx+1):
            for yy in (gy-1,gy,gy+1):
                for delta in range(-lag_days,lag_days+1):
                    for k in index.get((xx,yy,day+delta),()):
                        if k<i:ds.union(i,k)
    root=[ds.find(i) for i in range(len(x))]
    x["_root"]=root
    first=x.groupby("_root",sort=False).agg(start=("episode_start","min"),
              grid=("grid_id","min"),members=("grid_id","size"))
    stable={r:("PROXY_"+hashlib.sha1(
        (str(v.start.date())+"|"+str(v.grid)+"|"+str(v.members)).encode()
        ).hexdigest()[:14]) for r,v in first.iterrows()}
    x["proxy_storm_id"]=x["_root"].map(stable)
    x=x.drop(columns="_root")
    summ=x.groupby("proxy_storm_id",sort=False).agg(
        onset_start=("episode_start","min"),
        onset_end=("episode_start","max"),
        episode_grid_records=("grid_id","size"),
        unique_grids=("grid_id","nunique")).reset_index()
    summ["onset_span_days"]=(summ.onset_end-summ.onset_start).dt.days+1
    return x,summ

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--episodes",type=Path,required=True)
    p.add_argument("--lag-days",type=int,default=1)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--max-rows",type=int)
    a=p.parse_args()
    if a.lag_days<0 or a.lag_days>7:raise ValueError("lag-days in 0..7")
    con=duckdb.connect()
    schema={z[0] for z in con.execute("DESCRIBE SELECT * FROM read_parquet(?)",[str(a.episodes)]).fetchall()}
    cols=["grid_id","episode_start"]
    if {"grid_x","grid_y"}.issubset(schema):
        cols+=["grid_x","grid_y"]
    elif "grid_id" not in schema:raise RuntimeError("Episode file lacks grid ID")
    q="SELECT "+",".join('"'+c+'"' for c in cols)+" FROM read_parquet(?)"
    if a.max_rows:q+=" LIMIT "+str(int(a.max_rows))
    df=con.execute(q,[str(a.episodes)]).fetchdf()
    con.close()
    if not {"grid_x","grid_y"}.issubset(df.columns):
        ids=df.grid_id.astype(str).str.extract(r"^(-?\d+)_(-?\d+)$")
        if ids.isna().any().any():raise RuntimeError("Cannot recover grid_x/grid_y from grid_id")
        df["grid_x"]=ids[0].astype("int32")
        df["grid_y"]=ids[1].astype("int32")
    members,groups=connected_proxy(df,a.lag_days)
    a.out.mkdir(parents=True,exist_ok=True)
    suffix=f"_smoke{a.max_rows}" if a.max_rows else ""
    members.to_parquet(a.out/f"35_proxy_storm_membership{suffix}.parquet",index=False)
    groups.to_csv(a.out/f"35_proxy_storm_summary{suffix}.csv",index=False)
    qc={"episodes":len(members),"proxy_components":len(groups),
        "single_grid_episode_groups":int(groups.episode_grid_records.eq(1).sum()),
        "max_component_span_days":int(groups.onset_span_days.max()),
        "largest_component_records":int(groups.episode_grid_records.max()),
        "lag_days":a.lag_days,
        "warning":"Onset adjacency yields candidate event dependence groups, NOT verified meteorological storms."}
    (a.out/f"35_manifest{suffix}.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    print("[DONE]",qc,flush=True)

if __name__=="__main__":main()
