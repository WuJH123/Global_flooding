# -*- coding: utf-8 -*-
"""Stage78: bounded GFD/MODIS *physical water-observation* checks on 10km cells.

At most 8 DFO-selected events and 160 event/cell requests per invocation.
Default DRY RUN. Cloud export only with explicit --submit.
GFD is not a global pluvial flood validation census. Treat no clear pixels
as UNOBSERVED, not as a verified no-flood.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd

COLLECTION='GLOBAL_FLOOD_DB/MODIS_EVENTS/V1'
AREA_M2=100_000_000
def validate(req):
    required={'request_id','gfd_id','grid_x','grid_y'}
    if required-set(req):raise ValueError(f'Missing requests: {required-set(req)}')
    if req.request_id.duplicated().any() or req.duplicated(['gfd_id','grid_x','grid_y']).any():
        raise ValueError('Duplicated GFD event-grid requests')
    if req[['grid_x','grid_y']].isna().any().any():raise ValueError('Null grid coords')
    if (req[['grid_x','grid_y']].to_numpy() != req[['grid_x','grid_y']].to_numpy().astype(int)).any():
        raise ValueError('Nonintegral grid coords')
    if req.gfd_id.isna().any():raise ValueError('Null GFD ID')
    return req

def run(a):
    req=validate(pd.read_csv(a.requests,dtype={'request_id':str}))
    if len(req)>a.max_rows or req.gfd_id.nunique()>a.max_events:
        raise ValueError(f'Exceeds safe pilot bounds: max {a.max_rows} rows, {a.max_events} GFD events. Rebuild smaller Stage77 pilot.')
    if a.max_events>8 or a.max_rows>160:
        raise ValueError('Hard cloud safety maximum: 8 events, 160 event-grid rows')
    plan={'status':'DRY_RUN' if not a.submit else 'SUBMISSION_ATTEMPT',
          'gfd_events':int(req.gfd_id.nunique()),'event_grid_pairs':len(req),
          'dataset':COLLECTION,'cloud_tasks_if_submitted':int(req.gfd_id.nunique()),
          'units':'m2','observed_area_rule':'clear_views>0',
          'satellite_flood_rule':'flooded=1 AND jrc_perm_water=0 AND clear_views>0',
          'warning':'GFD DFO event selection; non-detection is not no flood'}
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/'78_gfd_export_plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    print('[PLAN]',plan)
    if not a.submit:return
    import ee
    ee.Initialize(project=a.project)
    col=ee.ImageCollection(COLLECTION)
    existing={str(t.get('description')):t.get('state') for t in ee.data.getTaskList()}
    journal=a.out/'78_submissions.jsonl'
    previous=set()
    if journal.exists():
        for line in journal.read_text(encoding='utf-8').splitlines():
            if line.strip():previous.add(json.loads(line)['name'])
    for gfd_id,part in req.groupby('gfd_id'):
        name=f'GF10GFD_V1_DFO{int(gfd_id):05d}'
        if name in existing or name in previous:
            raise RuntimeError(f'{name} already in task list or submission journal; inspect and do not duplicate')
        selected=col.filter(ee.Filter.eq('id',int(gfd_id)))
        if int(selected.size().getInfo())!=1:
            raise RuntimeError(f'GFD image id {gfd_id} absent or duplicated')
        im=ee.Image(selected.first())
        clear=im.select('clear_views').gt(0)
        notperm=im.select('jrc_perm_water').eq(0)
        inundated=im.select('flooded').eq(1).And(notperm).And(clear)
        observed=ee.Image.pixelArea().updateMask(clear).rename('observed_area_m2')
        flood=ee.Image.pixelArea().updateMask(inundated).rename('flood_area_m2')
        data=ee.Image.cat([observed,flood])
        features=[]
        proj=ee.Projection('EPSG:6933')
        for r in part.itertuples(index=False):
            x0=int(r.grid_x)*10000;y0=int(r.grid_y)*10000
            polygon=ee.Geometry.Rectangle([x0,y0,x0+10000,y0+10000],proj,False)
            measure=data.reduceRegion(reducer=ee.Reducer.sum(),
                geometry=polygon,scale=250,crs='EPSG:6933',
                maxPixels=40000,tileScale=4)
            features.append(ee.Feature(None,measure).set({
                'request_id':str(r.request_id),'gfd_id':int(gfd_id),
                'grid_x':int(r.grid_x),'grid_y':int(r.grid_y)}))
        task=ee.batch.Export.table.toDrive(collection=ee.FeatureCollection(features),
            description=name,folder=a.folder,fileNamePrefix=name.lower(),
            fileFormat='CSV',
            selectors=['request_id','gfd_id','grid_x','grid_y',
                       'observed_area_m2','flood_area_m2'])
        task.start()
        record={'name':name,'task_id':task.id,'gfd_id':int(gfd_id),
                'requests':len(part),'source':COLLECTION}
        with journal.open('a',encoding='utf-8') as f:
            f.write(json.dumps(record)+'\n')
        print('[SUBMITTED]',record)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',default='eco-sector-503721-b7')
    p.add_argument('--requests',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--folder',default='GlobalFlood_10km_Production')
    p.add_argument('--max-events',type=int,default=8)
    p.add_argument('--max-rows',type=int,default=160)
    p.add_argument('--submit',action='store_true')
    run(p.parse_args())
if __name__=='__main__':main()
