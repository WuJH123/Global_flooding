# -*- coding: utf-8 -*-
"""Build reproducible 20-km grid–UCDB city overlay using a STABLE UCDB ID.

No row-number city IDs. No false one-to-one assumption: full overlap table
retains all intersections and primary-city table is only a working simplification.
Requires: pandas, numpy, duckdb, geopandas, pyogrio, shapely, pyproj.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio
import shapely
from shapely.geometry import box
from pyproj import Transformer

ID_CANDIDATES = ('ID_UC_G0', 'ID_HDC_G0', 'UC_ID', 'CITY_ID', 'city_id')
NAME_CANDIDATES = ('GC_UCN_MAI_2025', 'UC_NM_MN', 'UC_NM_LST', 'CITY_NAME', 'NAME', 'city_name')


def normalized(s):
    return str(s).replace('\ufeff','').replace('ï»¿','').strip()


def find_field(fields, candidates):
    lookup = {normalized(f).casefold(): f for f in fields}
    for name in candidates:
        if name.casefold() in lookup:
            return lookup[name.casefold()]
    return None


def get_grid_table(cfg, scope):
    if scope in ('m3', 'all_positive'):
        import duckdb
        path = Path(cfg['m3_catalogue'] if scope == 'm3' else cfg['reported_gridday'])
        if not path.is_file():
            raise FileNotFoundError(path)
        con = duckdb.connect()
        try:
            data = con.execute('''SELECT DISTINCT CAST(grid_id AS VARCHAR) AS grid_id,
                CAST(grid_x AS INTEGER) AS grid_x, CAST(grid_y AS INTEGER) AS grid_y
                FROM read_parquet(?)''', [str(path)]).fetchdf()
        finally:
            con.close()
    else:
        path = Path(cfg['urban_universe'])
        if not path.is_file():
            raise FileNotFoundError(path)
        data = pd.read_csv(path, low_memory=False)
        if 'grid_id' not in data:
            data['grid_id'] = data['grid_x'].astype('Int64').astype(str)+'_'+data['grid_y'].astype('Int64').astype(str)
        # The E2025 file may contain EPSG:6933 boundary artifacts: calculate
        # valid latitudes below and discard explicitly.
        data = data[['grid_id','grid_x','grid_y']]
    if data[['grid_id','grid_x','grid_y']].isna().any().any():
        raise RuntimeError('Input grid keys contain nulls')
    if data.grid_id.duplicated().any():
        raise RuntimeError('Grid ID is not unique; resolve inconsistent coordinates')
    ids = data.grid_x.astype(int).astype(str)+'_'+data.grid_y.astype(int).astype(str)
    if not (data.grid_id.astype(str).to_numpy() == ids.to_numpy()).all():
        raise RuntimeError('grid_id inconsistent with grid_x/grid_y')
    return data.sort_values('grid_id').reset_index(drop=True)


def choose_layer(path, chosen=None):
    layers = [(str(name), str(geom)) for name, geom in pyogrio.list_layers(str(path)) if 'Polygon' in str(geom)]
    if not layers:
        raise RuntimeError('UCDB contains no polygon layer')
    candidates = []
    for name, kind in layers:
        info = pyogrio.read_info(str(path), layer=name)
        fields = list(map(str, info['fields']))
        id_field = find_field(fields, ID_CANDIDATES)
        name_field = find_field(fields, NAME_CANDIDATES)
        if chosen is not None and name != chosen:
            continue
        if id_field is None:
            if chosen == name:
                raise RuntimeError(f'Layer {name} has no stable UCDB ID. Fields: {fields[:100]}')
            continue
        score = (10 if 'GENERAL_CHARACTERISTICS' in name.upper() else 0) + (2 if name_field else 0)
        candidates.append((score, name, id_field, name_field))
    if not candidates:
        raise RuntimeError('No polygon layer with stable city ID. Inspect 00_audit metadata; do not use row indices.')
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][1:]


def clean_ids(values):
    text = values.astype('string').str.strip()
    text = text.str.replace(r'^(\d+)\.0$',r'\1',regex=True)
    text = text.replace({'': pd.NA, 'nan': pd.NA, 'None': pd.NA, '<NA>':pd.NA})
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    p.add_argument('--scope',choices=['m3','all_positive','urban_universe'],default='m3')
    p.add_argument('--city-layer',default=None)
    p.add_argument('--min-intersection-km2',type=float,default=0.01)
    p.add_argument('--chunk-size',type=int,default=5000)
    p.add_argument('--out',default=None)
    a=p.parse_args()
    if a.chunk_size<1 or a.min_intersection_km2<0: p.error('Invalid chunk-size or min-intersection')
    cfg=json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    root=Path(cfg['project_root'])
    output=Path(a.out or root/'mechanism_project'/'01_cities'/a.scope)
    output.mkdir(parents=True,exist_ok=True)
    grid=get_grid_table(cfg,a.scope)
    x=(grid.grid_x.to_numpy(dtype=np.int64)+.5)*20000
    y=(grid.grid_y.to_numpy(dtype=np.int64)+.5)*20000
    lon,lat=Transformer.from_crs('EPSG:6933','EPSG:4326',always_xy=True).transform(x,y)
    invalid=~(np.isfinite(lon)&np.isfinite(lat)&(np.abs(lat)<=85)&(np.abs(lon)<=180))
    if np.any(invalid):
        grid.loc[invalid].to_csv(output/'invalid_grid_centers.csv',index=False)
        print('[WARN] Outside supported 6933 geographic domain:',int(invalid.sum()))
        grid=grid.loc[~invalid].reset_index(drop=True)
        lon=lon[~invalid];lat=lat[~invalid]
    grid['center_lon']=lon;grid['center_lat']=lat
    grid.to_csv(output/'grid_centers.csv',index=False)
    if a.scope=='m3':
        upload=grid[['grid_id','grid_x','grid_y','center_lon','center_lat']].sort_values('grid_id').reset_index(drop=True)
        upload['chunk_id']=(np.arange(len(upload))//a.chunk_size).astype('int32')
        upload.rename(columns={'center_lon':'longitude','center_lat':'latitude'}).to_csv(output/'merit_m3_grids_gee_upload.csv',index=False)
        print('[GEE INPUT]',len(upload),'grids',upload.chunk_id.nunique(),'chunks')
    ucdb=Path(cfg['ucdb'])
    layer,field_id,field_name=choose_layer(ucdb,a.city_layer)
    print('[CITY LAYER]',layer,'[CITY FIELDS]',field_id,field_name)
    # Read only the two required attributes + geometry; UCDB contains thousands
    # of thematic fields, reading them all needlessly increases memory usage.
    wanted=list(dict.fromkeys([c for c in (field_id,field_name) if c]))
    cities=gpd.read_file(ucdb,layer=layer,columns=wanted,engine='pyogrio')
    id_col=find_field(cities.columns,ID_CANDIDATES)
    name_col=find_field(cities.columns,NAME_CANDIDATES)
    if not id_col:raise RuntimeError('Stable city ID disappeared while loading UCDB')
    cities['city_id']=clean_ids(cities[id_col])
    if cities.city_id.isna().any() or cities.city_id.duplicated().any():
        raise RuntimeError('UCDB city ID is null/duplicated: inspect source layer and ID')
    cities['city_name']=cities[name_col].astype('string').fillna('') if name_col else ''
    cities=cities.loc[cities.geometry.notna()&~cities.geometry.is_empty,['city_id','city_name','geometry']].copy()
    if cities.crs is None:raise RuntimeError('UCDB CRS missing')
    cities=cities.to_crs('EPSG:6933').reset_index(drop=True)
    polygons=[box(int(gx)*20000,int(gy)*20000,(int(gx)+1)*20000,(int(gy)+1)*20000)
              for gx,gy in grid[['grid_x','grid_y']].itertuples(index=False,name=None)]
    gg=gpd.GeoDataFrame(grid,geometry=polygons,crs='EPSG:6933')
    pairs=gpd.sjoin(gg[['grid_id','grid_x','grid_y','geometry']],cities,how='inner',predicate='intersects')
    print('[CANDIDATE LINKS]',len(pairs))
    cols=['grid_id','grid_x','grid_y','city_id','city_name','overlap_km2','grid_fraction','city_rank_for_grid']
    if len(pairs):
        lhs=gg.geometry.loc[pairs.index].to_numpy()
        rhs=cities.geometry.iloc[pairs.index_right.to_numpy(dtype=int)].to_numpy()
        pairs['overlap_km2']=shapely.area(shapely.intersection(lhs,rhs))/1e6
        pairs=pairs.loc[pairs.overlap_km2>=a.min_intersection_km2].copy()
        pairs['grid_fraction']=pairs.overlap_km2/400.0
        pairs.sort_values(['grid_id','overlap_km2','city_id'],ascending=[True,False,True],inplace=True)
        pairs['city_rank_for_grid']=pairs.groupby('grid_id').cumcount()+1
        cross=pairs[cols].copy()
    else: cross=pd.DataFrame(columns=cols)
    cross.to_csv(output/'grid_city_crosswalk_all.csv',index=False)
    primary=cross.loc[cross.city_rank_for_grid==1].copy()
    primary.to_csv(output/'grid_primary_city.csv',index=False)
    unmatched=grid.loc[~grid.grid_id.isin(primary.grid_id)]
    unmatched.to_csv(output/'grids_without_ucdb_city.csv',index=False)
    city_stats=primary.groupby(['city_id','city_name'],dropna=False).agg(
        n_grid=('grid_id','nunique'),linked_area_km2=('overlap_km2','sum')).reset_index()
    city_stats.to_csv(output/'city_summary.csv',index=False)
    qc=dict(scope=a.scope,urban_grid_rows=len(grid),city_polygons=len(cities),
            matched_grids=int(primary.grid_id.nunique()),unassigned_grids=len(unmatched),
            multi_city_grids=int(cross.groupby('grid_id').size().gt(1).sum()),
            links=len(cross),mapped_cities=int(primary.city_id.nunique()),
            city_layer=layer,city_id_field=normalized(field_id),
            city_name_field=normalized(field_name) if field_name else None,
            min_intersection_km2=a.min_intersection_km2)
    (output/'city_crosswalk_qc.json').write_text(json.dumps(qc,ensure_ascii=False,indent=2),encoding='utf-8')
    print('[QC]',qc,'\n[SAVED]',output)

if __name__=='__main__':main()
