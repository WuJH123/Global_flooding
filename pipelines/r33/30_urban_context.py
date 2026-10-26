"""Stage30: audited GHSL grid epochs and city-year context.

Only copies ACTUAL exported GHSL values (no synthetic imperviousness,
drainage capacity or HAND). Input is previously downloaded
Groundsource_GHSL_YYYY/...csv with grid_id and epoch.
City aggregation uses verified UCDB links and overlap_km2 if supplied;
otherwise the PRIMARY city crosswalk is explicitly labeled a proxy.
GHS-BUILT-S built surface != impervious cover.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

GHSL=["pop_total","pop_density","urban_pop_share","dense_pop_share",
      "built_fraction","urban_area_fraction","dense_area_fraction"]
FRACTIONS=["urban_pop_share","dense_pop_share","built_fraction",
           "urban_area_fraction","dense_area_fraction"]
YEARS=(2000,2005,2010,2015,2020,2025)

def discover(root):
    paths=[]
    for p in root.rglob("*.csv"):
        # Prevent unrelated CSVs inadvertently contaminating production files
        if re.match(r"groundsource_ghsl_(?:2000|2005|2010|2015|2020|2025)_chunk_\d+\.csv$",p.name,re.I):
            paths.append(p)
    if not paths:raise FileNotFoundError(f"No production groundsource_ghsl_*_chunk_N.csv under {root}")
    return sorted(paths)

def read_ghsl(paths,valid_grid_ids):
    frames=[]
    qc=[]
    for path in paths:
        match=re.search(r"ghsl_(\d{4})_chunk_",path.name,re.I)
        epoch=int(match.group(1))
        raw=pd.read_csv(path,low_memory=False,dtype={"grid_id":str})
        needed={"grid_id",*GHSL}
        if not needed.issubset(raw.columns):
            raise ValueError(f"{path} missing GHSL fields: {sorted(needed-set(raw.columns))}")
        if "epoch" in raw and not raw.epoch.dropna().eq(epoch).all():
            raise RuntimeError(f"Incorrect epoch in {path}")
        if raw.grid_id.isna().any() or raw.grid_id.duplicated().any():
            raise RuntimeError(f"Duplicate/null IDs in GHSL chunk {path}")
        raw=raw.loc[raw.grid_id.isin(valid_grid_ids),["grid_id",*GHSL]].copy()
        raw["epoch"]=epoch
        for name in GHSL:
            raw[name]=pd.to_numeric(raw[name],errors="coerce")
        for name in FRACTIONS:
            bad=raw[name].notna()&~raw[name].between(-1e-6,1.01)
            if bad.any():raise ValueError(f"{name} outside [0,1] in {path}: {int(bad.sum())}")
        for name in ["pop_total","pop_density"]:
            if raw[name].lt(0).any():raise ValueError(f"Negative population in {path}")
        qc.append({"file":str(path),"epoch":epoch,"m3_rows":len(raw)})
        frames.append(raw)
    result=pd.concat(frames,ignore_index=True)
    if result.duplicated(["grid_id","epoch"]).any():
        raise RuntimeError("Duplicate grid-epoch across CSV chunks")
    return result,pd.DataFrame(qc)

def grid_to_city(static,links=None):
    if links is None:
        link=static[["grid_id","city_id"]].dropna().copy()
        link["overlap_km2"]=1.0
        label="PRIMARY_UCDB_CITY_EQUAL_GRID_WEIGHT"
    else:
        link=pd.read_csv(links,dtype={"grid_id":str,"city_id":str})
        needed={"grid_id","city_id","overlap_km2"}
        if not needed.issubset(link):raise ValueError(f"Overlap links need {needed}")
        link=link[list(needed)].dropna().copy()
        link["overlap_km2"]=pd.to_numeric(link.overlap_km2,errors="coerce")
        link=link[link.overlap_km2>0]
        if link.duplicated(["grid_id","city_id"]).any():
            raise RuntimeError("Duplicate city-grid overlap links")
        label="GRID_CITY_INTERSECTION_AREA_WEIGHT"
    if link.empty:raise RuntimeError("No UCDB city associations")
    return link,label

def aggregate_cities(table,links):
    aligned=table.merge(links,on="grid_id",how="inner",validate="many_to_many")
    if aligned.empty:raise RuntimeError("No GHSL rows could be matched to cities")
    aligned["w"]=aligned.overlap_km2
    # do not sum pop_total for overlapped city-grid: centroid values are NOT
    # spatially allocated by overlap; publish area-weighted contextual means only
    rows=[]
    for (city,epoch),g in aligned.groupby(["city_id","epoch"],sort=True,observed=True):
        row={"city_id":str(city),"epoch":int(epoch),
             "n_20km_grids":int(g.grid_id.nunique()),
             "overlap_weight_sum":float(g.w.sum())}
        for col in GHSL:
            ok=np.isfinite(g[col].to_numpy(float))
            row["ghsl_"+col+"_wmean"]=(
                float(np.average(g.loc[ok,col],weights=g.loc[ok,"w"]))
                if ok.any() else np.nan)
            row["coverage_"+col]=float(g.loc[ok,"w"].sum()/g.w.sum())
        rows.append(row)
    return pd.DataFrame(rows)

def load_reference(path,id_col,code_col):
    ref=pd.read_csv(path,dtype={id_col:str})
    if not {id_col,code_col}.issubset(ref.columns):
        raise ValueError(f"Koppen reference lacks exact columns {id_col}, {code_col}")
    ref=ref[[id_col,code_col]].rename(columns={id_col:"grid_id",code_col:"koppen_code"})
    if ref.grid_id.duplicated().any():raise RuntimeError("Koppen grid_id duplicate")
    # Categorical string, no numeric linear regression interpretation
    ref["koppen_code"]=ref.koppen_code.astype("string")
    return ref

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--ghsl-root",type=Path)
    p.add_argument("--static-path",type=Path)
    p.add_argument("--city-overlap-csv",type=Path)
    p.add_argument("--koppen-grid-csv",type=Path)
    p.add_argument("--koppen-id-column",default="grid_id")
    p.add_argument("--koppen-code-column",default="koppen_code")
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    root=a.ghsl_root or a.base/"Groundsource_all"
    static_file=a.static_path or a.base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet"
    con=duckdb.connect()
    static=con.execute("SELECT grid_id,city_id,city_name FROM read_parquet(?)",[str(static_file)]).fetchdf()
    con.close()
    static.grid_id=static.grid_id.astype(str)
    if static.grid_id.isna().any() or static.grid_id.duplicated().any():
        raise RuntimeError("Static M3 grid_id null/duplicate")
    ghsl,byfile=read_ghsl(discover(root),set(static.grid_id))
    links,mode=grid_to_city(static,a.city_overlap_csv)
    out=a.out or a.base/"mechanism_project"/"30_urban_context"
    out.mkdir(parents=True,exist_ok=True)
    if a.koppen_grid_csv:
        ref=load_reference(a.koppen_grid_csv,a.koppen_id_column,a.koppen_code_column)
        ghsl=ghsl.merge(ref,on="grid_id",how="left",validate="many_to_one")
    city=aggregate_cities(ghsl,links)
    if a.koppen_grid_csv:
        z=ghsl[["grid_id","epoch","koppen_code"]].merge(links,on="grid_id")
        z=z.dropna(subset=["koppen_code"])
        if len(z):
            votes=(z.groupby(["city_id","epoch","koppen_code"],observed=True)
                     .overlap_km2.sum().reset_index(name="w"))
            votes=votes.sort_values(["city_id","epoch","w","koppen_code"],
                                    ascending=[True,True,False,True])
            majority=votes.drop_duplicates(["city_id","epoch"])[["city_id","epoch","koppen_code"]]
            city=city.merge(majority,on=["city_id","epoch"],how="left",validate="one_to_one")
    ghsl.to_parquet(out/"30_ghsl_grid_epochs.parquet",index=False)
    city.to_parquet(out/"30_city_ghsl_epochs.parquet",index=False)
    city.to_csv(out/"30_city_ghsl_epochs.csv",index=False)
    byfile.to_csv(out/"30_source_files_qc.csv",index=False)
    overview={"input_m3_grids":len(static),"grid_epoch_records":len(ghsl),
              "epochs":sorted(ghsl.epoch.unique().tolist()),
              "mapped_cities":int(city.city_id.nunique()),
              "city_weight_mode":mode,
              "note":"Population and built_fraction are context indicators. No inferred drainage, HAND, or city-population totals from 20km overlap."}
    (out/"30_manifest.json").write_text(json.dumps(overview,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",overview,out,flush=True)

if __name__=="__main__":main()
