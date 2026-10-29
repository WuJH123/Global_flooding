# -*- coding: utf-8 -*-
"""Optional GFD/MODIS event catalogue manifest; NO GEE export or GEE raster download.

The 913 MODIS-mapped events were selected from Dartmouth Flood Observatory.
Thus this supplies an independent satellite *measurement modality*, NOT an
independent original event catalogue. Does not imply observed pluvial floods.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd

COLLECTION='GLOBAL_FLOOD_DB/MODIS_EVENTS/V1'
FIELDS=('id','dfo_centroid_x','dfo_centroid_y','dfo_country','dfo_main_cause',
        'dfo_validation_type','system:time_start')

def run(project,out):
    import ee
    ee.Initialize(project=project)
    col=ee.ImageCollection(COLLECTION)
    n=int(col.size().getInfo())
    data={k:col.aggregate_array(k).getInfo() for k in FIELDS}
    if any(len(v)!=n for v in data.values()):
        raise RuntimeError('GFD property arrays incomplete')
    frame=pd.DataFrame(data).rename(columns={'id':'dfo_event_id'})
    if frame.dfo_event_id.isna().any() or frame.dfo_event_id.duplicated().any():
        raise ValueError('GFD MODIS DFO event IDs missing or duplicated')
    frame['time_start_utc']=pd.to_datetime(frame['system:time_start'],
        errors='coerce',unit='ms',utc=True)
    out.mkdir(parents=True,exist_ok=True)
    frame.to_csv(out/'76_gfd_dfo_selected_satellite_event_manifest.csv',index=False)
    info={'status':'SATELLITE_MODALITY_DFO_SELECTED_EVENTS_NOT_INDEPENDENT_EVENTS',
          'collection':COLLECTION,'images':n,
          'coverage':'2000-2018, DFO-selected 913 mapped flood events',
          'caution':'Centroids/metadata are not confirmed city pluvial inundation; GFD flood pixels require independent QC',
          'submission':'none; only small Earth Engine metadata collection getInfo calls'}
    (out/'76_gfd_metadata_qc.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    print('[DONE]',info)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--project',required=True)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    run(a.project,a.out)
