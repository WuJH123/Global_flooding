# -*- coding: utf-8 -*-
"""Independent one-to-one and basic physical QC for local mechanism tables.
Never labels missing data as valid, never 'corrects' data automatically.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    p.add_argument('--out',default=None)
    args=p.parse_args()
    cfg=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    root=Path(cfg['project_root'])/'mechanism_project'
    out=Path(args.out or root/'10_validation');out.mkdir(parents=True,exist_ok=True)
    paths={
      'centers':root/'01_cities'/'m3'/'grid_centers.csv',
      'primary':root/'01_cities'/'m3'/'grid_primary_city.csv',
      'crosswalk':root/'01_cities'/'m3'/'grid_city_crosswalk_all.csv',
      'basin':root/'02_basins'/'basinatlas_level07_grid_context.csv',
      'cru':root/'07_climate_background'/'cru_1991_2020_grid_climatology.csv',
      'static':root/'05_static_merged'/'grid_static_context_m3.csv',
    }
    report={}
    errors=[]
    loaded={}
    for name,path in paths.items():
        if not path.is_file():
            report[name]={'status':'missing_optional_or_pending','path':str(path)}
            continue
        df=pd.read_csv(path,dtype={'grid_id':str,'city_id':str},low_memory=False)
        loaded[name]=df
        record={'path':str(path),'rows':len(df),'unique_grid_id':int(df.grid_id.nunique()),
                'duplicate_grid_id':int(df.grid_id.duplicated().sum())}
        report[name]=record
        if name!='crosswalk' and df.grid_id.duplicated().any():errors.append(f'{name}: duplicate grid IDs')
    if 'centers' in loaded:
        g=loaded['centers']
        expected=set(g.grid_id)
        record=report['centers']
        record['invalid_coordinate_count']=int((~np.isfinite(g.center_lon.to_numpy())|~np.isfinite(g.center_lat.to_numpy())|
               (g.center_lat.abs().gt(85))).sum())
        if record['invalid_coordinate_count']:errors.append('invalid M3 geographic centers')
        for name in ('basin','cru','static'):
            if name in loaded:
                if set(loaded[name].grid_id)!=expected:
                    errors.append(f'{name}: does not cover the identical grid-id domain')
    if 'primary' in loaded:
        df=loaded['primary']
        if 'city_id' not in df:errors.append('primary missing city_id')
        elif df.city_id.fillna('').str.startswith('ucdb_row_').any():
            errors.append('primary contains temporary row-number UCDB IDs')
        else:report['primary']['mapped_cities']=int(df.city_id.nunique())
    if 'basin' in loaded:
        df=loaded['basin']
        if {'basin_slp_dg_sav','basin_slope_degrees'}.issubset(df.columns):
            residual=(pd.to_numeric(df.basin_slp_dg_sav,errors='coerce')/10.-
                      pd.to_numeric(df.basin_slope_degrees,errors='coerce')).abs()
            report['basin']['bad_slope_conversion']=int(residual.gt(1e-6).sum())
            if residual.gt(1e-6).any():errors.append('slope conversion inconsistent')
    if 'cru' in loaded:
        df=loaded['cru']
        mm=[f'cru_pre_{i:02d}_mm' for i in range(1,13)]
        if set(mm+['cru_pre_annual_mm']).issubset(df.columns):
            annual=df[mm].sum(axis=1,min_count=12)
            diff=(annual-df.cru_pre_annual_mm).abs()
            report['cru']['bad_month_sum']=int(diff.gt(1e-5).sum())
            report['cru']['annual_complete']=int(df.cru_pre_annual_mm.notna().sum())
            if diff.gt(1e-5).any():errors.append('CRU annual != sum of climatological months')
        if 'cru_pre_station_supported_fraction' in df:
            frac=df.cru_pre_station_supported_fraction.dropna()
            if frac.lt(0).any() or frac.gt(1).any():errors.append('CRU station fraction outside 0..1')
    report['errors']=errors
    report['status']='PASS' if not errors else 'FAIL'
    target=out/'static_input_validation.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('[QC]',report['status'],'errors',errors,'\n[SAVED]',target)
    if errors:raise RuntimeError('Static table QC failed; see JSON')

if __name__=='__main__':main()
