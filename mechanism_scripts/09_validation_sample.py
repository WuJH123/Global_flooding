# -*- coding: utf-8 -*-
"""Create a reproducible stratified MANUAL verification sample, without labels.

Sampling is by episode year period × duration. Inspect each sampled event
against ground-truth sources separately; an episode is a grid-level record,
not necessarily an independent storm. No claims of accuracy from this script.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    p.add_argument('--n',type=int,default=480)
    p.add_argument('--seed',type=int,default=2026)
    p.add_argument('--out',default=None)
    a=p.parse_args()
    if a.n<1:p.error('--n must be positive')
    cfg=json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    root=Path(cfg['project_root'])/'mechanism_project'
    out=Path(a.out or root/'09_manual_review');out.mkdir(parents=True,exist_ok=True)
    con=duckdb.connect()
    path=str(Path(cfg['episodes']))
    columns=con.execute('DESCRIBE SELECT * FROM read_parquet(?)',[path]).fetchdf()['column_name'].tolist()
    durcol=next((col for col in ['duration_days','episode_duration_days','episode_length_days'] if col in columns),None)
    if durcol is None:
        raise RuntimeError('Missing duration column; verify 06_build_flood_episodes_controls output')
    idcol=next((col for col in ['episode_id','flood_episode_id'] if col in columns),None)
    idsql=f'CAST("{idcol}" AS VARCHAR) AS episode_id,' if idcol else ''
    ep=con.execute(f'''SELECT {idsql} CAST(grid_id AS VARCHAR) AS grid_id,
        CAST(episode_start AS DATE) AS episode_start,
        CAST("{durcol}" AS DOUBLE) AS duration_days
        FROM read_parquet(?)''',[path]).fetchdf()
    con.close()
    if idcol is None:
        ep['episode_id']=ep.grid_id.astype(str)+'_'+ep.episode_start.astype(str)
    if ep.episode_id.isna().any() or ep.episode_id.duplicated().any():
        raise RuntimeError('Episode key not unique; manual review rows would be ambiguous')
    m=root/'01_cities'/'m3'/'grid_primary_city.csv'
    if m.is_file():
        city=pd.read_csv(m,usecols=['grid_id','city_id','city_name'],dtype={'grid_id':str,'city_id':str})
        if city.grid_id.duplicated().any():raise RuntimeError('Bad city crosswalk')
        if city.city_id.fillna('').str.startswith('ucdb_row_').any():
            raise RuntimeError('Run corrected 01 first: invalid temporary city IDs')
        ep=ep.merge(city,on='grid_id',how='left',validate='many_to_one')
    ep['year']=pd.to_datetime(ep.episode_start).dt.year
    ep['period']=pd.cut(ep.year,bins=[1999,2009,2019,2026],labels=['2000-2009','2010-2019','2020-2026'])
    ep['duration_class']=pd.cut(ep.duration_days,bins=[0,1,3,np.inf],labels=['1 day','2-3 days','4+ days'])
    ep['stratum']=ep.period.astype(str)+' | '+ep.duration_class.astype(str)
    rng=np.random.default_rng(a.seed)
    k=min(a.n,len(ep))
    # Allocate proportional quotas, adding minimal representation when possible.
    groups=ep.groupby('stratum',dropna=False,sort=True).indices
    quotas={s:0 for s in groups}
    for s in groups:
        quotas[s]=min(len(groups[s]),int(np.floor(k*len(groups[s])/len(ep))))
    remaining=k-sum(quotas.values())
    # Largest remainder with capacity; deterministic tie break.
    order=sorted(groups,key=lambda s:(-(k*len(groups[s])/len(ep)-np.floor(k*len(groups[s])/len(ep))),str(s)))
    for s in order:
        if remaining<=0:break
        if quotas[s]<len(groups[s]): quotas[s]+=1;remaining-=1
    if remaining:
        for s in sorted(groups,key=str):
            add=min(remaining,len(groups[s])-quotas[s])
            quotas[s]+=add;remaining-=add
            if remaining==0:break
    selected=[]
    for s,index in groups.items():
        n=quotas[s]
        if n: selected.extend(rng.choice(index,size=n,replace=False).tolist())
    sample=ep.iloc[selected].sort_values(['period','duration_class','episode_start','grid_id']).reset_index(drop=True)
    if len(sample)!=k or sample.episode_id.duplicated().any():raise RuntimeError('Invalid sampling count/uniqueness')
    for col in ['source_uuid_or_url','source_available','verification_reference','verified_onset_date',
                'date_error_days','verified_lon','verified_lat','flood_type',
                'location_precision','is_inundation_pluvial','adjudicator','verification_notes']:
        sample[col]=''
    sample.to_csv(out/'event_manual_validation_template.csv',index=False,encoding='utf-8-sig')
    sample.groupby(['period','duration_class'],observed=True).size().reset_index(name='sample_count').to_csv(
        out/'validation_sampling_summary.csv',index=False)
    (out/'README.txt').write_text('MANUAL REVIEW TEMPLATE ONLY; no events have been verified. '
        'The archive may lack direct article URLs; source document search and independent local records are required. '
        'Sampled grid episodes can share storms. Do not treat unmatched external archives as false events.\n',encoding='utf-8')
    print('[DONE]',len(sample),'randomly sampled grid-level episodes ->',out)

if __name__=='__main__':main()
