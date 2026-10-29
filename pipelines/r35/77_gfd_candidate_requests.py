# -*- coding: utf-8 -*-
"""Stage77: BOUNDED candidate GFD satellite pixels vs GF10 high-specificity reports.

A nearby DFO *centroid* and similar date only nominate satellite inspection;
neither GFD selection nor archive location validates a specific news report.
The Global Flood Database (913 MODIS-mapped large floods, 2000–2018) is
DFO-event-selected and cannot be considered independent event discovery.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
from scipy.spatial import cKDTree

def xyz(lon,lat):
    lo=np.radians(np.asarray(lon,dtype=float))
    la=np.radians(np.asarray(lat,dtype=float))
    return np.column_stack((np.cos(la)*np.cos(lo),np.cos(la)*np.sin(lo),np.sin(la)))

def candidates(reports,manifest,max_km=100,days=7,limit=200,per_event=10):
    if not (0 < max_km <= 300 and 0 <= days <= 14 and 1 <= limit <= 1000 and 1 <= per_event <= 100):
        raise ValueError('Pilot bounds: km 1..300, days 0..14, rows 1..1000, per-event 1..100')
    need_r={'event_id','grid_id','start_day','longitude','latitude','high'}
    need_m={'dfo_event_id','dfo_centroid_x','dfo_centroid_y','time_start_utc'}
    if need_r-set(reports):raise ValueError('Missing report fields: '+str(need_r-set(reports)))
    if need_m-set(manifest):raise ValueError('Missing GFD fields: '+str(need_m-set(manifest)))
    r=reports.loc[reports.high.astype(bool)].copy()
    if r.event_id.duplicated().any():raise ValueError('Duplicate report event_id')
    r.start_day=pd.to_datetime(r.start_day,errors='coerce',utc=True).dt.tz_convert(None)
    m=manifest.copy()
    m.time_start_utc=pd.to_datetime(m.time_start_utc,errors='coerce',utc=True).dt.tz_convert(None)
    for k in ('dfo_centroid_x','dfo_centroid_y'):
        m[k]=pd.to_numeric(m[k],errors='coerce')
    m=m.loc[m.dfo_centroid_x.between(-180,180)&m.dfo_centroid_y.between(-90,90)&m.time_start_utc.notna()]
    r=r.loc[r.longitude.between(-180,180)&r.latitude.between(-90,90)&r.start_day.notna()].copy()
    if not len(r) or not len(m):raise ValueError('No valid dated GFD centroids or news reports')
    if m.dfo_event_id.duplicated().any():raise ValueError('Duplicate GFD ID')
    m=m.reset_index(drop=True)
    # Exact spherical great-circle chord search; avoid inaccurate global degrees buffers.
    tree=cKDTree(xyz(m.dfo_centroid_x,m.dfo_centroid_y))
    chord=2*np.sin((max_km/6371.0088)/2)
    pairs=[]
    for part in np.array_split(r,max(1,int(np.ceil(len(r)/40000)))):
        found=tree.query_ball_point(xyz(part.longitude,part.latitude),r=chord)
        rrows=part.reset_index(drop=True)
        for i,inds in enumerate(found):
            if not inds:continue
            item=rrows.iloc[i]
            for j in inds:
                evt=m.iloc[j]
                lag=abs((item.start_day-evt.time_start_utc).total_seconds())/86400
                if lag>days:continue
                a=xyz([item.longitude],[item.latitude])[0]
                b=xyz([evt.dfo_centroid_x],[evt.dfo_centroid_y])[0]
                km=6371.0088*np.arccos(np.clip(float(a@b),-1,1))
                grid_id=str(item.grid_id)
                xy=grid_id.split('_')
                if len(xy)!=2:raise ValueError('Expected Stage62 grid_id x_y: '+grid_id)
                x,y=map(int,xy)
                pairs.append({'event_id':str(item.event_id),'grid_id':grid_id,
                              'grid_x':x,'grid_y':y,'gfd_id':int(evt.dfo_event_id),
                              'candidate_distance_km':km,'candidate_date_lag_days':lag})
    if not pairs:raise ValueError('No candidate overlap; broaden ONLY after reviewing methodology')
    df=pd.DataFrame(pairs).sort_values(
        ['gfd_id','candidate_distance_km','candidate_date_lag_days','event_id'])
    # Every event-grid sampling unit is unique, even when multiple news reports land in a grid.
    df=df.drop_duplicates(['gfd_id','grid_id'])
    df=df.groupby('gfd_id',sort=True,group_keys=False).head(per_event)
    # Stable round-robin across independent DFO event IDs; avoid one large flood dominating pilot.
    df['pick_rank']=df.groupby('gfd_id').cumcount()
    df=df.sort_values(['pick_rank','gfd_id','grid_id']).head(limit).copy()
    df=df.sort_values(['gfd_id','grid_id']).reset_index(drop=True)
    df['request_id']=['GFD%05d_X%s_Y%s'%(a,b,c) for a,b,c in zip(df.gfd_id,df.grid_x,df.grid_y)]
    if df.request_id.duplicated().any():raise ValueError('Duplicate GFD request ID')
    df['selection_rule']='DFO_CENTROID_DATE_NEARBY_CANDIDATE_NOT_VERIFIED_EVENT'
    return df.drop(columns=['pick_rank'])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reports',type=Path,required=True)
    p.add_argument('--gfd-manifest',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--max-km',type=float,default=100)
    p.add_argument('--days',type=int,default=7)
    p.add_argument('--limit',type=int,default=160)
    p.add_argument('--per-event',type=int,default=10)
    a=p.parse_args()
    df=candidates(pd.read_parquet(a.reports),pd.read_csv(a.gfd_manifest),
                  a.max_km,a.days,a.limit,a.per_event)
    a.out.mkdir(parents=True,exist_ok=True)
    df.to_csv(a.out/'77_gfd_city_grid_requests.csv',index=False)
    qc={'status':'PILOT_GFD_DFO_CENTROID_CANDIDATES_NOT_EVENT_ATTRIBUTION',
        'selected_event_grid_pairs':len(df),'unique_gfd_dfo_events':int(df.gfd_id.nunique()),
        'radius_km':a.max_km,'date_window_days':a.days,
        'note':'GFD maps are DFO-selected; no independent catalog confirmation, not urban flood footprint truth'}
    (a.out/'77_gfd_candidate_qc.json').write_text(json.dumps(qc,indent=2),encoding='utf-8')
    print('[DONE]',qc)
if __name__=='__main__':main()
