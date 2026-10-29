# -*- coding: utf-8 -*-
"""Stage75: cheap local evidence-readiness inventory, NO GEE and no data mutation."""
import argparse,json
from pathlib import Path
import pandas as pd

def inspect(config):
    import pyogrio
    result=[]
    for s in config['sources']:
        p=Path(s['path'])
        row={'source':s['name'],'family':s['family'],'enabled':bool(s.get('enabled',False)),
             'path':str(p),'exists':p.is_file(),'status':'ABSENT',
             'crs':None,'fields':[],'sidecars_missing':[],'date_field':None}
        if not p.is_file():
            row['status']='MISSING_ENABLED_INPUT' if row['enabled'] else 'DISABLED_NOT_DOWNLOADED'
            result.append(row);continue
        kind=s.get('format',p.suffix[1:]).lower()
        if kind in ('shp','gpkg','geojson'):
            if kind=='shp':
                row['sidecars_missing']=[str(p.with_suffix(ext))
                                        for ext in ('.shx','.dbf','.prj')
                                        if not p.with_suffix(ext).exists()]
            try:
                info=pyogrio.read_info(p,layer=s.get('layer'))
                row['crs']=info.get('crs')
                row['record_count']=int(info.get('features') or 0)
                row['fields']=list(map(str,info['fields']))
            except Exception as e:
                row['status']='UNREADABLE_'+type(e).__name__
                row['error']=str(e)[:500]
                result.append(row);continue
        elif kind in ('xlsx','csv'):
            try:
                sample=(pd.read_excel(p,sheet_name=s.get('sheet','Aggregated'),nrows=3)
                        if kind=='xlsx' else pd.read_csv(p,nrows=3))
                row['fields']=list(map(str,sample.columns))
                row['crs']='EPSG:4326 coordinates from explicit LAT/LON' if s.get('latitude') else 'UNKNOWN'
            except Exception as e:
                row['status']='UNREADABLE_'+type(e).__name__
                row['error']=str(e)[:500]
                result.append(row);continue
        else:
            row['status']='UNSUPPORTED_FORMAT';result.append(row);continue
        for name in ('start_field','end_field','latitude','longitude','source_field'):
            field=s.get(name)
            if field and field not in row['fields']:
                row.setdefault('missing_declared_fields',[]).append(field)
        from_candidates=next((x for x in row['fields'] if x.strip().upper() in
             {'BEG_DATE','START_DATE','START','BEGAN','EVENT_DATE','DATE','BEGIN_DATE','FLOOD_DATE'}),None)
        row['date_field']=s.get('start_field') or from_candidates
        row['status']='READY_FOR_DATE_PRECISION_CHECK'
        if row['sidecars_missing'] and any(x.endswith(('.shx','.dbf')) for x in row['sidecars_missing']):
            row['status']='MISSING_REQUIRED_SHP_SIDECAR'
        elif not row['crs'] and not (s.get('confirmed_crs') and s.get('crs_evidence')):
            row['status']='BLOCKED_CRS_METADATA_REQUIRED'
        elif row.get('missing_declared_fields'):
            row['status']='BLOCKED_WRONG_FIELD_NAMES'
        elif not row['date_field']:
            row['status']='BLOCKED_DATE_FIELD_UNKNOWN'
        if not row['enabled'] and row['status']=='READY_FOR_DATE_PRECISION_CHECK':
            row['status']='READY_BUT_DISABLED'
        result.append(row)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    cfg=json.loads(a.config.read_text(encoding='utf-8'))
    out=inspect(cfg)
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/'75_reference_readiness.json').write_text(
       json.dumps({'sources':out,'warning':'CRS/date metadata still require human review; READY is not validated flood truth'},
                  ensure_ascii=False,indent=2),encoding='utf-8')
    pd.DataFrame([{k:v for k,v in item.items() if k not in ('fields','sidecars_missing')}
                  for item in out]).to_csv(a.out/'75_reference_readiness.csv',index=False)
    for x in out:
        print('[INSPECT]',x['name'] if 'name' in x else x['source'],x['status'],
              'CRS',x['crs'],'DATE',x['date_field'],'MISSING',x['sidecars_missing'])
    if any(x['enabled'] and x['status']!='READY_FOR_DATE_PRECISION_CHECK' for x in out):
        raise SystemExit('Some ENABLED sources not ready. Fix paths/CRS/fields before Stage73.')

if __name__=='__main__':main()
