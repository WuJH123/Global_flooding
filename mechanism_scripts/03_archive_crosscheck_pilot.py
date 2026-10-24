# -*- coding: utf-8 -*-
"""Event archive cross-check PILOT (not independent truth validation).
Samples M3 onset events, intersects external footprints or uses 50 km radius
around archive points, and filters on available date fields ±7 days.
"""
import argparse,json
from pathlib import Path
import pandas as pd
import numpy as np
import geopandas as gpd
import pyogrio
import duckdb

KEYS=['flood_archive_region','dfo_master','flood_1989_2017']
DATE_CANDIDATES=['BEG_DATE','START_DATE','START','BEGAN','STARTDATE','START_DATE_','EVENT_DATE','DATE','BEGDATE',
                 'BEGIN','BEGIN_DATE','BEG','FLOOD_DATE','S_DATE','START_DT']
END_CANDIDATES=['END_DATE','END','ENDDATE','END_DT','STOP_DATE','END_DATE_','E_DATE']

def candidate_field(fields, preferred):
    if preferred:
        if preferred not in fields: raise ValueError(f'Field {preferred} absent; available: {fields}')
        return preferred
    lk={f.upper():f for f in fields}
    for c in DATE_CANDIDATES:
        if c in lk:return lk[c]
    return None


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    ap.add_argument('--source',choices=KEYS,default='flood_archive_region')
    ap.add_argument('--start-field',default=None)
    ap.add_argument('--end-field',default=None)
    ap.add_argument('--n',type=int,default=20000,help='pilot sample only')
    ap.add_argument('--day-window',type=int,default=7)
    ap.add_argument('--radius-km',type=float,default=50.0)
    ap.add_argument('--out',default=None)
    a=ap.parse_args()
    cfg=json.loads(Path(a.config).read_text(encoding='utf-8'))
    out=Path(a.out or Path(cfg['project_root'])/'mechanism_project'/'03_archive_pilot'/a.source)
    out.mkdir(parents=True,exist_ok=True)
    source=cfg[a.source]
    fields=list(map(str,pyogrio.read_info(source)['fields']))
    start=candidate_field(fields,a.start_field)
    end=a.end_field or next((f for f in fields if f.upper() in END_CANDIDATES),None)
    if not start:
        (out/'requires_date_field.txt').write_text('No date field recognized. Inspect 00_audit dataset_metadata.json. Fields:\n'+str(fields),encoding='utf-8')
        raise RuntimeError('No reliable event date column identified. Re-run with --start-field FIELD; do not claim validation without date.')
    read_fields=[start]+([end] if end and end!=start else [])
    arch=gpd.read_file(source,columns=read_fields,engine='pyogrio')
    if arch.crs is None: raise RuntimeError('Archive missing CRS; manually verify original source metadata')
    arch=arch[arch.geometry.notna() & ~arch.geometry.is_empty].copy()
    arch['archive_start']=pd.to_datetime(arch[start],errors='coerce',utc=True).dt.tz_localize(None)
    arch['archive_end']=pd.to_datetime(arch[end],errors='coerce',utc=True).dt.tz_localize(None) if end else arch.archive_start
    arch.loc[arch.archive_end.isna(),'archive_end']=arch.archive_start
    arch=arch[arch.archive_start.notna() & (arch.archive_end>=arch.archive_start)].copy()
    if not arch.empty and (arch.archive_start.dt.year.lt(1900) | arch.archive_start.dt.year.gt(2030)).mean() > 0.05:
        raise RuntimeError('More than 5% archive start dates outside 1900..2030. Inspect date field/format; do not guess.')
    if len(arch)==0: raise RuntimeError('Parsed 0 valid dates. Check date format manually.')
    arch=arch.to_crs('EPSG:6933')
    geom_types=arch.geometry.geom_type
    points=geom_types.isin(['Point','MultiPoint'])
    if points.any():
        # EPSG:6933 radius is an approximate projected distance, not exact geodesic distance.
        arch.loc[points,'geometry']=arch.loc[points].geometry.buffer(a.radius_km*1000)
    if not arch.geometry.is_valid.all():
        arch.loc[~arch.geometry.is_valid,'geometry']=arch.loc[~arch.geometry.is_valid].geometry.make_valid()
    # Do not process every episode until the pilot has been reviewed.
    c=pd.read_csv(Path(cfg['project_root'])/'mechanism_project'/'01_cities'/'m3'/'grid_centers.csv',
                  usecols=['grid_id','center_lon','center_lat'])
    con=duckdb.connect()
    ep=con.execute('SELECT grid_id, CAST(episode_start AS DATE) AS onset_date FROM read_parquet(?)',
                   [cfg['episodes']]).fetchdf()
    con.close()
    ep=ep.sample(n=min(a.n,len(ep)),random_state=2026).merge(c,on='grid_id',how='inner')
    ep['onset_date']=pd.to_datetime(ep['onset_date'])
    g=gpd.GeoDataFrame(ep,geometry=gpd.points_from_xy(ep.center_lon,ep.center_lat),crs='EPSG:4326').to_crs('EPSG:6933')
    match=gpd.sjoin(g,arch[['archive_start','archive_end','geometry']],how='left',predicate='intersects')
    lag=pd.Timedelta(days=a.day_window)
    match['date_window_match']=(match.onset_date>=match.archive_start-lag)&(match.onset_date<=match.archive_end+lag)
    match['spatial_overlap']=match.index_right.notna()
    # Only spatiotemporal candidate matches; cannot classify unmatched as false or fake events.
    accepted=match.loc[match.date_window_match & match.spatial_overlap].copy()
    accepted=accepted.reset_index(drop=False).rename(columns={'index':'episode_index'})
    accepted=accepted[['grid_id','onset_date','archive_start','archive_end','center_lon','center_lat']].drop_duplicates()
    accepted.to_csv(out/'archive_spatiotemporal_candidates.csv',index=False)
    n_matched=int(accepted[['grid_id','onset_date']].drop_duplicates().shape[0])
    qc={'dataset':a.source,'sampled_episodes':len(ep),'archive_parsed_events':len(arch),
        'spatiotemporal_candidate_episodes':n_matched,'start_field':start,'end_field':end,
        'time_window_days':a.day_window,'point_buffer_km_projected':a.radius_km,
        'interpretation':'candidate co-location only; DFO large-flood footprints are not urban pluvial-flood ground truth'}
    (out/'archive_pilot_qc.json').write_text(json.dumps(qc,ensure_ascii=False,indent=2),encoding='utf-8')
    print('[QC]',qc)

if __name__=='__main__':main()
