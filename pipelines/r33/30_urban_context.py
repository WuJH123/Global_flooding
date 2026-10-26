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
    """Read both the exported legacy GEE schema and newer GHSL tables.

    Older project CSVs exported built_m2, grid_area_m2 / grid_area_km2,
    but NOT necessarily built_fraction. Calculate built_fraction only when
    the original built area and actual grid area are BOTH available.
    urban_area_fraction is a GHS-SMOD class share and MUST NOT be renamed
    or copied to GHSL built surface fraction.
    """
    frames=[]
    qc=[]
    core=set(GHSL)-{"built_fraction"}
    for path in paths:
        match=re.search(r"ghsl_(\d{4})_chunk_",path.name,re.I)
        if match is None:
            raise ValueError(f"Unrecognized GHSL filename: {path}")
        epoch=int(match.group(1))
        raw=pd.read_csv(path,low_memory=False,dtype={"grid_id":"string"})
        raw.columns=[str(c).lstrip("\ufeff").strip() for c in raw.columns]
        needed={"grid_id",*core}
        if not needed.issubset(raw.columns):
            raise ValueError(f"{path} missing GHSL fields: {sorted(needed-set(raw.columns))}")
        if "epoch" in raw.columns:
            values=pd.to_numeric(raw.epoch,errors="coerce")
            if ((values.notna())&(values.ne(epoch))).any():
                raise RuntimeError(f"Epoch values disagree with filename {path}")
        if raw.grid_id.isna().any() or raw.grid_id.duplicated().any():
            raise RuntimeError(f"Duplicate/null IDs in GHSL chunk {path}")

        raw=raw.loc[raw.grid_id.isin(valid_grid_ids)].copy()
        source="not_available"
        available_area=raw.get("grid_area_m2")
        if available_area is None:
            area=pd.Series(np.nan,index=raw.index,dtype=float)
        else:
            area=pd.to_numeric(available_area,errors="coerce").astype(float)
        if "grid_area_km2" in raw.columns:
            from_km2=pd.to_numeric(raw["grid_area_km2"],errors="coerce")*1e6
            area=area.where(np.isfinite(area)&area.gt(0),from_km2)

        derived=pd.Series(np.nan,index=raw.index,dtype=float)
        if "built_m2" in raw.columns:
            built=pd.to_numeric(raw["built_m2"],errors="coerce").astype(float)
            if built.lt(0).any():
                raise ValueError(f"Negative built_m2 in {path}")
            mask=np.isfinite(built)&np.isfinite(area)&area.gt(0)
            derived.loc[mask]=built.loc[mask]/area.loc[mask]
            if not mask.all():
                source="built_m2_unusable_area_for_some_rows"
            else:
                source="built_m2_over_actual_grid_area"

        if "built_fraction" in raw.columns:
            original=pd.to_numeric(raw["built_fraction"],errors="coerce").astype(float)
            overlap=original.notna()&derived.notna()
            if overlap.any():
                delta=(original.loc[overlap]-derived.loc[overlap]).abs()
                if delta.gt(0.02).any():
                    raise ValueError(
                        f"Conflicting supplied built_fraction vs built_m2/area in {path}; "
                        f"maximum absolute difference={delta.max():.6f}"
                    )
            raw["built_fraction"]=original.fillna(derived)
            source=("existing_built_fraction" if derived.notna().sum()==0
                    else "existing_or_derived_built_fraction")
        else:
            raw["built_fraction"]=derived

        if "built_m2" not in raw.columns and "built_fraction" not in pd.read_csv(path,nrows=0).columns:
            source="missing_built_m2_and_built_fraction"

        for name in GHSL:
            raw[name]=pd.to_numeric(raw[name],errors="coerce")
        for name in FRACTIONS:
            bad=raw[name].notna()&~raw[name].between(-1e-6,1.01)
            if bad.any():
                raise ValueError(f"{name} outside [0,1] in {path}: {int(bad.sum())}")
        for name in ["pop_total","pop_density"]:
            if raw[name].lt(0).any():
                raise ValueError(f"Negative population in {path}")
        raw["epoch"]=epoch
        qc.append({"file":str(path),"epoch":epoch,"m3_rows":len(raw),
                   "built_fraction_source":source,
                   "built_fraction_valid":int(raw.built_fraction.notna().sum()),
                   "built_fraction_missing":int(raw.built_fraction.isna().sum())})
        frames.append(raw[["grid_id",*GHSL,"epoch"]])

    result=pd.concat(frames,ignore_index=True)
    if result.duplicated(["grid_id","epoch"]).any():
        raise RuntimeError("Duplicate grid-epoch across CSV chunks")
    return result,pd.DataFrame(qc)


def validate_epoch_coverage(ghsl,valid_ids,allow_incomplete=False):
    """Six epochs of exactly the M3 master grid set are expected for production."""
    expected=set(valid_ids)
    errors=[]
    for year in YEARS:
        actual=set(ghsl.loc[ghsl.epoch.eq(year),"grid_id"])
        missing=expected-actual
        extra=actual-expected
        if missing or extra:
            errors.append({
                "year":year,"expected":len(expected),"found":len(actual),
                "missing":len(missing),"extra":len(extra),
                "example_missing_ids":sorted(missing)[:5]
            })
    if errors and not allow_incomplete:
        raise RuntimeError(
            "Historical GHSL grid-epoch coverage is incomplete: "
            +json.dumps(errors,ensure_ascii=False)
            +". Use --allow-incomplete-epochs ONLY for exploratory QC."
        )
    return errors


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
    p.add_argument("--allow-incomplete-epochs",action="store_true")
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
    epoch_errors=validate_epoch_coverage(ghsl,set(static.grid_id),a.allow_incomplete_epochs)
    if not ghsl.built_fraction.notna().any():
        print("[WARNING] No valid GHSL built_fraction source: leaving NaN; "
              "urban_area_fraction is NOT built_fraction.")
    else:
        print("[QC] built_fraction valid grid-epochs:",int(ghsl.built_fraction.notna().sum()))
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
              "epoch_coverage_issues":epoch_errors,
              "built_fraction_valid_grid_epochs":int(ghsl.built_fraction.notna().sum()),
              "built_fraction_absent_means_not_measured":True,
              "note":"Population and built_fraction are context indicators. No inferred drainage, HAND, or city-population totals from 20km overlap."}
    (out/"30_manifest.json").write_text(json.dumps(overview,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",overview,out,flush=True)

if __name__=="__main__":main()
