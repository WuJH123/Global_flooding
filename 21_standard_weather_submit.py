"""Groundsource 21: plan and optionally submit ONLY missing calendar weather requests.

Requires previously generated files:
  E:/Global_flooding/standard_calendar_20/20_missing_climate_requests_chunked.parquet
  E:/Global_flooding/standard_calendar_20/20_missing_task_manifest.csv
  Uploaded Earth Engine assets: projects/eco-sector-503721-b7/assets/standard_requests_YYYY

PLAN-ONLY by default; never contacts Earth Engine until --submit.
Uses the SAME IMERG V07 30-min and ERA5-Land HOURLY definitions as the
previous production workflow. Do not interpret selected reference days as
verified absence of a flood.

Authoring note: select FIRST one task to compare with existing climate outputs,
and inspect task runtime/errors before submitting more.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import hashlib
import time
from pathlib import Path

import duckdb
import pandas as pd


PROJECT = 'eco-sector-503721-b7'
ASSET_ROOT = f'projects/{PROJECT}/assets'
BASE = Path(r'E:\Global_flooding')

IMERG_FIELDS = [
    'imerg_p30_max_mm', 'imerg_p1h_max_mm', 'imerg_p3h_max_mm',
    'imerg_p6h_max_mm', 'imerg_p12h_max_mm', 'imerg_p24_utc_mm'
]
ERA_FIELDS = [
    'era5_event_p24_mm', 'era5_event_runoff24_mm',
    'era5_event_surface_runoff24_mm', 'era5_ant_p3d_mm',
    'era5_ant_p7d_mm', 'era5_ant_runoff3d_mm',
    'era5_ant_runoff7d_mm', 'era5_api3_mm', 'era5_api7_mm',
    'era5_sm1_prevday', 'era5_sm2_prevday'
]
WEATHER_COLS = [f'{name}_{suffix}' for name in IMERG_FIELDS + ERA_FIELDS
                for suffix in ('mean', 'max')]
REQUEST_COLS = ['climate_id', 'grid_id', 'grid_x', 'grid_y', 'sample_date',
                'date_str', 'year', 'month', 'day', 'request_chunk']
OUTPUT_COLS = REQUEST_COLS + WEATHER_COLS

# Identical CRS WKT to previously validated GEE workflow.
EASE6933_WKT = (
    'PROJCS["WGS 84 / NSIDC EASE-Grid 2.0 Global",'
    'GEOGCS["WGS 84",DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
    'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4326"]],PROJECTION["Cylindrical_Equal_Area"],'
    'PARAMETER["standard_parallel_1",30],PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","6933"]]'
)


def qpath(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def get_plan(base: Path, max_requests: int, max_dates: int) -> pd.DataFrame:
    parquet = base / '20_missing_climate_requests_chunked.parquet'
    monthly = base / '20_missing_task_manifest.csv'
    for p in (parquet, monthly):
        if not p.exists():
            raise FileNotFoundError(p)
    con = duckdb.connect()
    parts = con.execute(f"""
      SELECT year::INTEGER AS year, month::INTEGER AS month,
             request_chunk::INTEGER AS request_chunk,
             COUNT(*)::INTEGER AS requests,
             LIST_SORT(LIST(DISTINCT date_str)) AS dates
      FROM read_parquet('{qpath(parquet)}')
      GROUP BY 1,2,3 ORDER BY 1,2,3
    """).fetchdf()
    con.close()
    manifest = pd.read_csv(monthly)
    source_keys = {tuple(x) for x in manifest[['year','month','request_chunk']].itertuples(index=False, name=None)}
    actual_keys = {tuple(x) for x in parts[['year','month','request_chunk']].itertuples(index=False, name=None)}
    if source_keys != actual_keys or int(parts.requests.sum()) != int(manifest.requests.sum()):
        raise RuntimeError('Missing request parquet does not match original manifest')
    # The Stage20b publication cohort can have a different missing-ID count.
    # Manifest/parquet identity is verified above; do not hard-code a total.
    records = []
    for year, subset in parts.groupby('year', sort=True):
        members, dates, count, ordinal = [], set(), 0, 0
        def emit_group(members, dates, count, ordinal):
            if not members: return
            records.append({
                'year': int(year), 'group': int(ordinal),
                'requests': int(count), 'distinct_dates': len(dates),
                'members_json': json.dumps(members, separators=(',', ':')),
                'first_date': min(dates), 'last_date': max(dates),
            })
        for r in subset.itertuples(index=False):
            new = {'month': int(r.month), 'chunk': int(r.request_chunk)}
            row_dates = set(r.dates)
            n = int(r.requests)
            if n > max_requests or len(row_dates) > max_dates:
                raise RuntimeError(f'Single 20-month-chunk exceeds plan caps: {year}-{r.month} C{r.request_chunk}; '
                                   f'{n} rows, {len(row_dates)} dates. Increase caps or rerun 20 with smaller --chunk-size.')
            if members and (count + n > max_requests or len(dates | row_dates) > max_dates):
                emit_group(members, dates, count, ordinal)
                members, dates, count = [], set(), 0
                ordinal += 1
            members.append(new)
            dates |= row_dates
            count += n
        emit_group(members, dates, count, ordinal)
    plan = pd.DataFrame.from_records(records)
    if int(plan.requests.sum()) != int(parts.requests.sum()):
        raise RuntimeError('Planned request count differs from actual request count')
    expected_members = {(int(r.year), int(r.month), int(r.request_chunk)) for r in parts.itertuples()}
    actual_members = [(int(r.year), x['month'], x['chunk'])
                      for r in plan.itertuples() for x in json.loads(r.members_json)]
    if len(actual_members) != len(set(actual_members)) or set(actual_members) != expected_members:
        raise RuntimeError('Task plan must cover each original (year,month,chunk) exactly once')
    return plan


def make_ee_functions():
    import ee
    raw_imerg = ee.ImageCollection('NASA/GPM_L3/IMERG_V07').select('precipitation')
    raw_era = ee.ImageCollection('ECMWF/ERA5_LAND/HOURLY')
    grid_proj = ee.Projection(EASE6933_WKT)
    reducer = ee.Reducer.mean().combine(reducer2=ee.Reducer.max(), sharedInputs=True)

    def rebuild_grid(f):
        f = ee.Feature(f)
        gx = ee.Number(f.get('grid_x'))
        gy = ee.Number(f.get('grid_y'))
        x0 = gx.multiply(20000)
        y0 = gy.multiply(20000)
        geom = ee.Geometry.Rectangle(ee.List([x0,y0,x0.add(20000),y0.add(20000)]),grid_proj,False,True)
        return ee.Feature(geom, f.toDictionary())

    def imerg30(date):
        def convert(img):
            img = ee.Image(img)
            return img.max(0).multiply(0.5).rename('p').copyProperties(img,['system:time_start'])
        return raw_imerg.filterDate(date,date.advance(1,'day')).map(convert)

    def rolling_max(date, coll, steps, name):
        def one_window(i):
            start = date.advance(ee.Number(i).multiply(30),'minute')
            end = start.advance(steps*30,'minute')
            return coll.filterDate(start,end).sum().rename(name)
        return ee.ImageCollection.fromImages(ee.List.sequence(0,48-steps).map(one_window)).max().rename(name)

    def imerg_metrics(date):
        p = imerg30(date)
        return ee.Image.cat([
            p.max().rename('imerg_p30_max_mm'),
            rolling_max(date,p,2,'imerg_p1h_max_mm'),
            rolling_max(date,p,6,'imerg_p3h_max_mm'),
            rolling_max(date,p,12,'imerg_p6h_max_mm'),
            rolling_max(date,p,24,'imerg_p12h_max_mm'),
            p.sum().rename('imerg_p24_utc_mm')
        ])

    def era_flow(start,end,band,name):
        return (raw_era.filterDate(start,end).select(band)
                .map(lambda img: ee.Image(img).max(0).multiply(1000))
                .sum().rename(name))

    def build_api(date,n,name):
        def weighted(k):
            k = ee.Number(k)
            start = date.advance(k.multiply(-1),'day')
            return era_flow(start,start.advance(1,'day'),'total_precipitation_hourly',name).multiply(
                ee.Number(0.85).pow(k.subtract(1))).rename(name)
        return ee.ImageCollection.fromImages(ee.List.sequence(1,n).map(weighted)).sum().rename(name)

    def era_metrics(date):
        prev = raw_era.filterDate(date.advance(-1,'day'),date)
        return ee.Image.cat([
            era_flow(date,date.advance(1,'day'),'total_precipitation_hourly','era5_event_p24_mm'),
            era_flow(date,date.advance(1,'day'),'runoff_hourly','era5_event_runoff24_mm'),
            era_flow(date,date.advance(1,'day'),'surface_runoff_hourly','era5_event_surface_runoff24_mm'),
            era_flow(date.advance(-3,'day'),date,'total_precipitation_hourly','era5_ant_p3d_mm'),
            era_flow(date.advance(-7,'day'),date,'total_precipitation_hourly','era5_ant_p7d_mm'),
            era_flow(date.advance(-3,'day'),date,'runoff_hourly','era5_ant_runoff3d_mm'),
            era_flow(date.advance(-7,'day'),date,'runoff_hourly','era5_ant_runoff7d_mm'),
            build_api(date,3,'era5_api3_mm'),
            build_api(date,7,'era5_api7_mm'),
            prev.select('volumetric_soil_water_layer_1').mean().rename('era5_sm1_prevday'),
            prev.select('volumetric_soil_water_layer_2').mean().rename('era5_sm2_prevday')
        ])

    def one_date(ds, subset):
        ds=ee.String(ds)
        date=ee.Date.parse('YYYY-MM-dd',ds)
        day_features=subset.filter(ee.Filter.eq('date_str',ds)).map(rebuild_grid)
        imerg=imerg_metrics(date)
        era=era_metrics(date)
        with_p=imerg.reduceRegions(collection=day_features,reducer=reducer,scale=11132,
                                     crs=imerg.select(0).projection(),tileScale=4)
        return era.reduceRegions(collection=with_p,reducer=reducer,scale=11132,
                                 crs=era.select(0).projection(),tileScale=4)

    def process(subset):
        dates=ee.List(subset.aggregate_array('date_str')).distinct().sort()
        nested=dates.map(lambda ds: one_date(ds,subset))
        result=ee.FeatureCollection(nested).flatten()
        return result.map(lambda f: ee.Feature(None,ee.Feature(f).toDictionary(OUTPUT_COLS)))

    def group_subset(raw, members):
        filters=[ee.Filter.And(ee.Filter.eq('month',int(x['month'])),
                               ee.Filter.eq('request_chunk',int(x['chunk']))) for x in members]
        if len(filters)==1:
            return raw.filter(filters[0])
        return raw.filter(ee.Filter.Or(*filters))
    return process, group_subset


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,default=BASE, help='Project root E:\\Global_flooding')
    p.add_argument('--calendar-dir',type=Path,default=None,help='Stage20/20b output directory; defaults to standard_calendar_20b')
    p.add_argument('--max-requests',type=int,default=20000)
    p.add_argument('--max-dates',type=int,default=45)
    p.add_argument('--submit',action='store_true',help='Start EE tasks; without this only produce local plan')
    p.add_argument('--max-tasks',type=int,default=1,help='Maximum NEW tasks to submit; 0 means all, only with --submit')
    p.add_argument('--year',type=int,default=None,help='Restrict submission to one year; plan remains global')
    p.add_argument('--retry-failed',action='store_true',help='Allow retry of FAILED, after examining error')
    p.add_argument('--delay',type=float,default=1.0)
    a=p.parse_args()
    calendar_dir=a.calendar_dir or (a.base/'standard_calendar_20b')
    plan=get_plan(calendar_dir,a.max_requests,a.max_dates)
    target=calendar_dir/'21_standard_climate_optimized_task_plan.csv'
    plan_signature=hashlib.sha256(plan[['year','group','requests','distinct_dates','members_json']].to_csv(index=False).encode('utf-8')).hexdigest()
    frozen=calendar_dir/'21_frozen_plan_sha256.txt'
    if frozen.exists() and frozen.read_text(encoding='utf-8').strip()!=plan_signature:
        raise RuntimeError('Task plan changed since submission began. Do not change caps/groups after starting tasks; resolve outstanding tasks first.')
    plan.to_csv(target,index=False,encoding='utf-8-sig')
    print(f'[PLAN] {len(plan):,} tasks, {int(plan.requests.sum()):,} requests, '
          f'max requests={plan.requests.max():,}, max distinct dates={plan.distinct_dates.max()}',flush=True)
    print(f'[SAVED] {target}',flush=True)
    print(plan.groupby('year').agg(tasks=('group','count'),requests=('requests','sum')).to_string(),flush=True)
    if not a.submit:
        print('PLAN ONLY: no EE calls and no tasks started. Review plan and upload yearly standard_requests_YYYY assets first.')
        return
    if a.max_tasks<0: raise ValueError('--max-tasks must be >=0')
    if not frozen.exists(): frozen.write_text(plan_signature,encoding='utf-8')
    import ee
    ee.Initialize(project=PROJECT)
    selected=plan[plan.year.eq(a.year)] if a.year is not None else plan
    years=sorted(int(y) for y in selected.year.unique())
    for year in years:
        asset=f'{ASSET_ROOT}/standard_requests_{year}'
        data=ee.data.getAsset(asset)
        if data.get('type') != 'TABLE':
            raise RuntimeError(f'Asset not a TABLE: {asset}')
        print(f'[ASSET OK] {asset}',flush=True)
    tasks=ee.data.getTaskList()
    remote={}
    for t in tasks:
        name=t.get('description','')
        if not name.startswith('STDClimate_'):
            continue
        remote.setdefault(name,[]).append(t.get('state','UNKNOWN'))
    process,group_subset=make_ee_functions()
    log=calendar_dir/'21_standard_climate_submissions.jsonl'
    submitted=0
    for r in selected.itertuples(index=False):
        name=f'STDClimate_{int(r.year)}_G{int(r.group):03d}'
        statuses=remote.get(name,[])
        if any(s in ('READY','RUNNING','COMPLETED','PENDING','SUCCEEDED') for s in statuses):
            print(f'[SKIP active/completed] {name}: {statuses}',flush=True)
            continue
        if statuses and not a.retry_failed:
            print(f'[SKIP existing terminal task] {name}: {statuses}',flush=True)
            continue
        members=json.loads(r.members_json)
        raw=ee.FeatureCollection(f'{ASSET_ROOT}/standard_requests_{int(r.year)}')
        subset=group_subset(raw,members)
        output=process(subset)
        task=ee.batch.Export.table.toDrive(
            collection=output,description=name,
            folder=f'Groundsource_Standard_Climate_{int(r.year)}',
            fileNamePrefix=f'stdclimate_{int(r.year)}_g{int(r.group):03d}',
            fileFormat='CSV',selectors=OUTPUT_COLS
        )
        task.start()
        record=dict(name=name,id=task.id,year=int(r.year),group=int(r.group),
                    requests=int(r.requests),distinct_dates=int(r.distinct_dates),
                    submitted_at=dt.datetime.now().isoformat(timespec='seconds'))
        with log.open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n')
        print(f'[STARTED] {name}: {r.requests:,} requests, {r.distinct_dates} dates; {task.id}',flush=True)
        submitted+=1
        if a.max_tasks and submitted>=a.max_tasks:
            break
        time.sleep(a.delay)
    print(f'[DONE] newly submitted {submitted}; local log {log}',flush=True)


if __name__=='__main__':
    main()
