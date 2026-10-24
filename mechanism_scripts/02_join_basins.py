# -*- coding: utf-8 -*-
"""Assign BasinATLAS Level 07/08 basin CONTEXT to each M3 grid centre.

A centre-in-polygon match is not an area-weighted grid mean or urban
stormwater drainage basin. HydroATLAS slp_dg_sav is stored in 0.1 degrees.
Unmatched coastal/island grid centres are retained, never silently filled.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio

CANDIDATES=['HYBAS_ID','PFAF_ID','NEXT_DOWN','SUB_AREA','UP_AREA',
            'ele_mt_sav','slp_dg_sav','pre_mm_syr','run_mm_syr',
            'dis_m3_pyr','urb_pc_sse','pop_ct_usu']

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    ap.add_argument('--level',type=int,default=7)
    ap.add_argument('--city-centers',default=None)
    ap.add_argument('--out',default=None)
    args=ap.parse_args()
    cfg=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    root=Path(cfg['project_root'])
    centers=Path(args.city_centers or root/'mechanism_project'/'01_cities'/'m3'/'grid_centers.csv')
    out=Path(args.out or root/'mechanism_project'/'02_basins');out.mkdir(parents=True,exist_ok=True)
    candidates=[p for p in Path(cfg['basin_root']).rglob('*.shp')
                if re.search(r'lev0?%d$'%args.level,p.stem,re.I)]
    if len(candidates)!=1:raise RuntimeError(f'Expected one global BasinATLAS Level{args.level}, found {candidates}')
    f=candidates[0]
    info=pyogrio.read_info(f)
    fields=[str(x) for x in info['fields']]
    cols=[c for c in CANDIDATES if c in fields]
    if 'HYBAS_ID' not in cols:raise RuntimeError('HYBAS_ID missing in BasinATLAS')
    basin=gpd.read_file(f,columns=cols,engine='pyogrio')
    if basin.crs is None:raise RuntimeError('BasinATLAS CRS not defined')
    basin=basin.loc[basin.geometry.notna()&~basin.geometry.is_empty].copy()
    c=pd.read_csv(centers,dtype={'grid_id':str})
    if c.grid_id.duplicated().any():raise RuntimeError('Input grid IDs duplicated')
    need={'grid_id','center_lon','center_lat'}
    if not need.issubset(c.columns):raise ValueError(f'Grid file missing {need-set(c.columns)}')
    p=gpd.GeoDataFrame(c,geometry=gpd.points_from_xy(c.center_lon,c.center_lat),crs='EPSG:4326').to_crs(basin.crs)
    j=gpd.sjoin(p,basin,how='left',predicate='intersects')
    j['basin_boundary_multiple_hits']=j.groupby('grid_id').grid_id.transform('size').gt(1)
    if 'SUB_AREA' in j.columns:
        j.sort_values(['grid_id','SUB_AREA','HYBAS_ID'],ascending=True,na_position='last',inplace=True)
    else:j.sort_values(['grid_id','HYBAS_ID'],ascending=True,na_position='last',inplace=True)
    j=j.drop_duplicates('grid_id')
    res=j[['grid_id']+cols+['basin_boundary_multiple_hits']].rename(columns={f:f'basin_{f}' for f in cols}).copy()
    # Sentinel values have no physical meaning. Preserve raw fields otherwise.
    for field in cols:
        name='basin_'+field
        arr=pd.to_numeric(res[name],errors='coerce')
        res[name]=arr.mask(arr.isin([-9999,-999,-99]))
    if 'basin_slp_dg_sav' in res:
        res['basin_slope_degrees']=res['basin_slp_dg_sav']/10.0
    res['basin_data_available']=res.basin_HYBAS_ID.notna()
    if len(res)!=len(c) or res.grid_id.duplicated().any():raise RuntimeError('One row per input grid not preserved')
    path=out/f'basinatlas_level{args.level:02d}_grid_context.csv'
    res.to_csv(path,index=False)
    qc={'n_grids':len(res),'n_assigned':int(res.basin_data_available.sum()),
        'n_missing':int((~res.basin_data_available).sum()),
        'n_boundary_multi_hits':int(res.basin_boundary_multiple_hits.sum()),
        'level':args.level,'source':str(f),'original_attribute_fields':cols,
        'slope_units':'basin_slp_dg_sav raw degree*10, basin_slope_degrees is degree',
        'spatial_definition':'grid centre intersects one basin; NOT 20km grid-area mean'}
    (out/'basin_join_qc.json').write_text(json.dumps(qc,ensure_ascii=False,indent=2),encoding='utf-8')
    print('[QC]',qc,'\n[SAVED]',path)

if __name__=='__main__':main()
