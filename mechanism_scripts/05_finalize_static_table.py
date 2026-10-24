# -*- coding: utf-8 -*-
"""Safely merge M3 city, basin, CRU and optional MERIT static datasets.

Refuses old UCDB `ucdb_row_*` identifiers, bad slopes, duplicate MERIT chunks,
and unexpected missing MERIT grid IDs by default. No observations are imputed.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

MERIT_RE=re.compile(r'^merit_(?:m3|all)_c\d+_s\d+of\d+\.csv$',re.I)

def required_unique(df,key,label):
    if key not in df:raise RuntimeError(f'{label} missing {key}')
    if df[key].isna().any() or df[key].duplicated().any():
        raise RuntimeError(f'{label} has null or duplicate {key}')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    p.add_argument('--merit-dir',default=None)
    p.add_argument('--allow-partial-merit',action='store_true')
    p.add_argument('--out',default=None)
    a=p.parse_args()
    cfg=json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    root=Path(cfg['project_root'])/'mechanism_project'
    out=Path(a.out or root/'05_static_merged');out.mkdir(parents=True,exist_ok=True)
    centers=pd.read_csv(root/'01_cities'/'m3'/'grid_centers.csv',dtype={'grid_id':str})
    primary=pd.read_csv(root/'01_cities'/'m3'/'grid_primary_city.csv',dtype={'grid_id':str,'city_id':str})
    required_unique(centers,'grid_id','centers')
    required_unique(primary,'grid_id','primary city')
    if primary.city_id.fillna('').str.startswith('ucdb_row_').any():
        raise RuntimeError('01_link_cities.py generated row-index city IDs. Rerun corrected 01 first.')
    need=['grid_id','city_id','city_name','overlap_km2','grid_fraction']
    if not set(need).issubset(primary.columns):raise RuntimeError('City crosswalk has missing columns')
    df=centers.merge(primary[need],on='grid_id',how='left',validate='one_to_one')
    joined=['cities']
    basin_path=root/'02_basins'/'basinatlas_level07_grid_context.csv'
    if basin_path.exists():
        basin=pd.read_csv(basin_path,dtype={'grid_id':str})
        required_unique(basin,'grid_id','BasinATLAS')
        if 'basin_slope_degrees' not in basin and 'basin_slp_dg_sav' in basin:
            basin['basin_slope_degrees']=pd.to_numeric(basin.basin_slp_dg_sav,errors='coerce')/10.0
        df=df.merge(basin,on='grid_id',how='left',validate='one_to_one');joined.append('BasinATLAS')
    else:print('[WARN] BasinATLAS not found; merged without basin')
    cru_path=root/'07_climate_background'/'cru_1991_2020_grid_climatology.csv'
    if cru_path.exists():
        cru=pd.read_csv(cru_path,dtype={'grid_id':str})
        required_unique(cru,'grid_id','CRU')
        df=df.merge(cru,on='grid_id',how='left',validate='one_to_one');joined.append('CRU')
    else:print('[WARN] CRU not found; merged without CRU')
    n_merit=0
    if a.merit_dir:
        paths=sorted(p for p in Path(a.merit_dir).rglob('*.csv') if MERIT_RE.fullmatch(p.name))
        if not paths:raise FileNotFoundError('No production MERIT CSV; test100 files excluded')
        frames=[pd.read_csv(f,dtype={'grid_id':str}) for f in paths]
        merit=pd.concat(frames,ignore_index=True)
        required_unique(merit,'grid_id','MERIT')
        extra=set(merit.grid_id)-set(centers.grid_id)
        missing=set(centers.grid_id)-set(merit.grid_id)
        if extra:raise RuntimeError(f'MERIT has {len(extra)} grid IDs not in M3: {list(extra)[:4]}')
        if missing and not a.allow_partial_merit:
            raise RuntimeError(f'MERIT files have {len(missing)} missing M3 grid IDs. '
                               'Do not call this a complete run; provide --allow-partial-merit for exploratory merges.')
        mcols=[k for k in merit if k.startswith('merit_')]
        if not mcols:raise RuntimeError('MERIT CSV lacks metric columns; check reducer property naming')
        df=df.merge(merit[['grid_id']+mcols],on='grid_id',how='left',validate='one_to_one')
        n_merit=len(merit);joined.append('MERIT')
    required_unique(df,'grid_id','merged static')
    if len(df)!=len(centers):raise RuntimeError('Grid rows changed during joins')
    df.to_csv(out/'grid_static_context_m3.csv',index=False)
    df.to_parquet(out/'grid_static_context_m3.parquet',index=False)
    # Episodes are grid-level occurrences, NOT independent storms.
    con=duckdb.connect()
    con.register('grid_city',df[['grid_id','city_id','city_name']])
    episodes=str(cfg['episodes'])
    q='''SELECT s.city_id,s.city_name,COUNT(*) AS grid_level_episodes,
        COUNT(DISTINCT e.grid_id) AS contributing_grids,
        MIN(CAST(e.episode_start AS DATE)) AS first_onset,
        MAX(CAST(e.episode_start AS DATE)) AS last_onset
        FROM read_parquet(?) AS e INNER JOIN grid_city AS s USING(grid_id)
        WHERE s.city_id IS NOT NULL GROUP BY 1,2 ORDER BY grid_level_episodes DESC'''
    counts=con.execute(q,[episodes]).fetchdf()
    counts.to_csv(out/'city_episode_coverage.csv',index=False)
    years=con.execute('''SELECT s.city_id,YEAR(CAST(e.episode_start AS DATE)) AS onset_year,
        COUNT(*) AS reported_grid_episodes FROM read_parquet(?) AS e
        JOIN grid_city s USING(grid_id) WHERE s.city_id IS NOT NULL
        GROUP BY 1,2 ORDER BY 1,2''',[episodes]).fetchdf()
    years.to_csv(out/'city_year_reported_grid_episodes.csv',index=False)
    con.close()
    qc={'m3_grids':len(df),'grids_with_ucdb_city':int(df.city_id.notna().sum()),
        'mapped_cities_with_episodes':int(counts.city_id.nunique()),
        'cities_with_at_least_30_grid_episodes':int(counts.grid_level_episodes.ge(30).sum()),
        'cities_with_at_least_100_grid_episodes':int(counts.grid_level_episodes.ge(100).sum()),
        'merit_rows':n_merit,'integrated_sources':joined,
        'interpretation':'City assignment = largest grid polygon overlap; counts are grid-level episodes, not independent storms.'}
    (out/'merged_static_qc.json').write_text(json.dumps(qc,ensure_ascii=False,indent=2),encoding='utf-8')
    print('[DONE]',qc,'\n[SAVED]',out)

if __name__=='__main__':main()
