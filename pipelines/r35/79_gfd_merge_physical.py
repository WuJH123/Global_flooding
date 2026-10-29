# -*- coding: utf-8 -*-
"""Stage79: merge GEE observed-area pixels with exact Stage77 requests.

Missing clear observations are UNOBSERVED; a valid MODIS non-detection does
not prove no urban flood, particularly for short-lived urban pluvial flooding.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd

AREA=100_000_000

def merge(req,downloads,minimum_coverage=0.2):
    if not 0 < minimum_coverage <= 1:raise ValueError('minimum_coverage in (0,1]')
    required={'request_id','gfd_id','grid_x','grid_y'}
    bands={'observed_area_m2','flood_area_m2'}
    if required-set(req):raise ValueError('Missing Stage77 join keys')
    if required.union(bands)-set(downloads):
        raise ValueError('Missing Stage78 GEE columns')
    if req.request_id.duplicated().any() or downloads.request_id.duplicated().any():
        raise ValueError('Duplicate request_id: duplicate downloads or requests')
    unknown=set(downloads.request_id)-set(req.request_id)
    missing=set(req.request_id)-set(downloads.request_id)
    if unknown or missing:
        raise ValueError(f'GEE request-ID mismatch: unknown={len(unknown)} missing={len(missing)}')
    for c in ('gfd_id','grid_x','grid_y'):
        a=req.set_index('request_id')[c]
        b=downloads.set_index('request_id')[c]
        if not a.sort_index().equals(pd.to_numeric(b,errors='raise').astype(a.dtype).sort_index()):
            raise ValueError(f'GEE geometry key {c} differs from Stage77 manifest')
    d=req.merge(downloads[['request_id',*bands]],on='request_id',validate='one_to_one')
    for c in bands:d[c]=pd.to_numeric(d[c],errors='coerce')
    if np.isinf(d[list(bands)]).any().any():
        raise ValueError('Infinite pixel area')
    if (d[list(bands)].dropna()<0).any().any():
        raise ValueError('Negative pixel area')
    if (d[list(bands)].dropna()>AREA*1.10).any().any():
        raise ValueError('Area > 10km equal-area cell (beyond sampling tolerance)')
    both=d[list(bands)].notna().all(axis=1)
    if (d.loc[both,'flood_area_m2']>d.loc[both,'observed_area_m2']+AREA*0.01).any():
        raise ValueError('Flooded area exceeds observed area')
    d['clear_coverage_fraction']=d.observed_area_m2/AREA
    d['evidence_state']='UNOBSERVED'
    adequate=both & d.clear_coverage_fraction.ge(minimum_coverage)
    d.loc[adequate,'evidence_state']='OBSERVED_NO_GFD_FLOOD_PIXEL'
    d.loc[adequate & d.flood_area_m2.gt(0),'evidence_state']='GFD_FLOOD_PIXELS_PRESENT'
    d['valid_observation']=adequate
    # Both observed classes are satellite *measurements* on a DFO-selected event
    # cell, not ground-truth labels for the news-reported flood.
    return d

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--requests',type=Path,required=True)
    p.add_argument('--csv-root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--minimum-coverage',type=float,default=0.20)
    a=p.parse_args()
    files=sorted(a.csv_root.rglob('gf10gfd_v1_dfo*.csv'))
    if not files:raise FileNotFoundError('No downloaded Stage78 GEE task CSVs')
    parts=[pd.read_csv(x,dtype={'request_id':str}) for x in files]
    d=merge(pd.read_csv(a.requests,dtype={'request_id':str}),
            pd.concat(parts,ignore_index=True),a.minimum_coverage)
    a.out.mkdir(parents=True,exist_ok=True)
    d.to_parquet(a.out/'79_gfd_physical_observation_grid10.parquet',index=False)
    counts={k:int(v) for k,v in d.evidence_state.value_counts().items()}
    qc={'status':'GFD_SATELLITE_MODALITY_PILOT_NOT_PLUVIAL_EVENT_VALIDATION',
        'requested_event_grid_pairs':len(d),
        'gfd_dfo_catalogued_events':int(d.gfd_id.nunique()),
        'adequately_observed_pairs':int(d.valid_observation.sum()),
        'states':counts,'minimum_clear_coverage':a.minimum_coverage,
        'source_files':[str(x) for x in files],
        'warning':'GFD DFO-selected 2000-2018; positive is satellite water pixels, negative is not no-flood'}
    (a.out/'79_gfd_observation_qc.json').write_text(
        json.dumps(qc,indent=2,ensure_ascii=False),encoding='utf-8')
    print('[DONE]',qc)
if __name__=='__main__':main()
