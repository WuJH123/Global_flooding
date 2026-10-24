# -*- coding: utf-8 -*-
"""MERIT Hydro -> 1 km summaries -> 20 km EPSG:6933 M3 grids -> Drive CSV.

IMPORTANT scientific correction (2026): MERIT Hydro band 'hnd' is
'hydrologically adjusted elevation', NOT Height Above Nearest Drainage (HAND).
This program does NOT produce any true HAND metric or low-HAND fraction.

Default: DRY RUN (no Earth Engine API calls / no tasks started).
Before --submit, upload 26,317-point CSV via 04a_upload_merit_asset.py.

Examples:
  python 04_submit_merit_gee.py
  python 04_submit_merit_gee.py --verify-asset
  python 04_submit_merit_gee.py --submit --test --chunks 0 --max-tasks 1
  python 04_submit_merit_gee.py --submit --chunks 0 --max-tasks 1
  python 04_submit_merit_gee.py --submit --chunks 1-5 --max-tasks 0
  python 04_submit_merit_gee.py --submit --chunks 3 --subdivisions 2 --max-tasks 0
"""
from __future__ import annotations
import argparse
import re
import time

PROJECT = 'eco-sector-503721-b7'
GRID_ASSET = f'projects/{PROJECT}/assets/groundsource_merit_m3_grids'
GRID_M = 20000
EXPECTED_M3_GRIDS = 26317
# Same EPSG:6933 WKT form previously used successfully in this project.
EASE_WKT = (
 'PROJCS["WGS 84 / NSIDC EASE-Grid 2.0 Global",'
 'GEOGCS["WGS 84",DATUM["WGS_1984",'
 'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
 'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
 'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]],'
 'PROJECTION["Cylindrical_Equal_Area"],PARAMETER["standard_parallel_1",30],'
 'PARAMETER["central_meridian",0],PARAMETER["false_easting",0],'
 'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
 'AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","6933"]]'
)

# Export exact properties; no misleading HAND proxy.
OUTPUT_FIELDS = [
 'grid_id','grid_x','grid_y','chunk_id',
 'merit_elv_1km_mean','merit_elv_1km_min','merit_elv_1km_max',
 'merit_local_relief_m_approx',
 'merit_slope_deg_1km_mean','merit_slope_deg_1km_max',
 'merit_hydro_adjusted_elev_1km_mean',
 'merit_stream_upa100_fraction_1km_mean',
 'merit_upa_km2_1km_max'
]


def parse_chunks(spec: str) -> list[int]:
    chunks = set()
    for token in spec.split(','):
        token = token.strip()
        if re.fullmatch(r'\d+', token):
            chunks.add(int(token))
        elif re.fullmatch(r'\d+-\d+', token):
            a, b = map(int, token.split('-'))
            if a > b:
                raise ValueError(f'Reverse range {token!r}')
            chunks.update(range(a, b + 1))
        else:
            raise ValueError(f'Bad chunks token {token!r}; e.g. 0,2-5')
    if not chunks:
        raise ValueError('No chunks selected')
    return sorted(chunks)


def task_descriptions(chunk: int, sub: int, subdivisions: int, test: bool) -> tuple[str, str]:
    suffix = f'c{chunk:02d}_s{sub:02d}of{subdivisions:02d}'
    desc = 'MERIT_M3_V3_' + suffix.upper() + ('_TEST100' if test else '')
    filename = 'merit_m3_' + suffix + ('_test100' if test else '')
    return desc, filename


def reconstruct(f, proj, ee):
    f = ee.Feature(f)
    x = ee.Number(f.get('grid_x')).multiply(GRID_M)
    y = ee.Number(f.get('grid_y')).multiply(GRID_M)
    rect = ee.Geometry.Rectangle(
        ee.List([x, y, x.add(GRID_M), y.add(GRID_M)]), proj, False, True)
    return ee.Feature(rect, f.toDictionary(['grid_id','grid_x','grid_y','chunk_id']))


def build_metrics(proj, scale, max_agg_pixels, ee):
    m = ee.Image('MERIT/Hydro/v1_0_1')
    elv = m.select('elv')
    # NOTE: this 'hnd' is adjusted elevation, not real HAND!
    adjusted_elev = m.select('hnd')
    upa = m.select('upa')  # units: km2 (official dataset catalog)
    slope_deg = ee.Terrain.slope(elv)
    reducer_mean = ee.Reducer.mean()
    def aggregate(img, reducer, name):
        return (ee.Image(img)
                .reduceResolution(reducer=reducer, maxPixels=max_agg_pixels)
                .reproject(crs=proj, scale=scale).rename(name))
    bands = ee.Image.cat([
        aggregate(elv, reducer_mean, 'merit_elv_1km'),
        aggregate(slope_deg, reducer_mean, 'merit_slope_deg_1km'),
        aggregate(adjusted_elev, reducer_mean, 'merit_hydro_adjusted_elev_1km'),
        aggregate(upa.gte(100).updateMask(upa.mask()), reducer_mean,
                  'merit_stream_upa100_fraction_1km'),
        aggregate(upa, ee.Reducer.max(), 'merit_upa_km2_1km')
    ])
    return bands


def existing_task_states(ee):
    result = {}
    for task in ee.data.getTaskList():
        desc = str(task.get('description', ''))
        if desc.startswith('MERIT_M3_V3_'):
            result.setdefault(desc, set()).add(str(task.get('state','UNKNOWN')).upper())
    return result


def check_asset(ee, asset: str, require_count=False):
    try:
        meta = ee.data.getAsset(asset)
    except Exception as exc:
        s = str(exc).lower()
        if '404' in s or 'does not exist' in s or 'not found' in s:
            raise RuntimeError(
                f'EE M3 Table asset is not available: {asset}\n'
                'The CSV exists locally but has not been ingested, the asset name is different, '
                'or your account lacks permission.\n'
                'First run: python 04a_upload_merit_asset.py --upload\n'
                'Wait for Table ingestion, then: python 04a_upload_merit_asset.py --check\n'
                'Do not confuse this 404 with noncommercial compute quota warnings.'
            ) from exc
        raise
    kind = str(meta.get('type','')).upper()
    if kind not in ('TABLE', 'FEATURE_COLLECTION'):
        raise RuntimeError(f'Expected EE TABLE asset, got {kind!r}: {asset}')
    print('[ASSET FOUND]', asset, 'type=', kind)
    if require_count:
        n = ee.FeatureCollection(asset).size().getInfo()
        print('[ASSET ROW COUNT]', n)
        if n != EXPECTED_M3_GRIDS:
            raise RuntimeError(f'Wrong EE Table rows: {n}; expected {EXPECTED_M3_GRIDS}. '
                               'Likely using the 94,738 positive grid asset or a partial CSV.')
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', default=PROJECT)
    ap.add_argument('--asset', default=GRID_ASSET)
    ap.add_argument('--submit', action='store_true', help='Actually create and start Batch tasks')
    ap.add_argument('--verify-asset', action='store_true', help='Verify remote TABLE and count; does not start tasks')
    ap.add_argument('--test', action='store_true', help='Limit to first 100 grids in selected chunk')
    ap.add_argument('--chunks', default='0-5')
    ap.add_argument('--subdivisions', type=int, default=1,
                    help='Split an original chunk only if that whole-chunk task failed; NEVER merge parent and child outputs')
    ap.add_argument('--max-tasks', type=int, default=1, help='0 = all selected tasks')
    ap.add_argument('--scale', type=int, default=1000)
    ap.add_argument('--tile-scale', type=int, default=8)
    ap.add_argument('--max-pixels-per-region', type=int, default=100000)
    ap.add_argument('--max-aggregate-pixels', type=int, default=512)
    ap.add_argument('--retry-failed', action='store_true')
    ap.add_argument('--pause-seconds', type=float, default=1.0)
    ap.add_argument('--drive-folder', default='Groundsource_Static_MERIT')
    args = ap.parse_args()

    if not 1 <= args.tile_scale <= 16:
        raise ValueError('--tile-scale must be between 1 and 16')
    if not 1 <= args.max_aggregate_pixels <= 1024:
        raise ValueError('--max-aggregate-pixels must be between 1 and 1024')
    if args.subdivisions < 1 or args.scale < 100 or args.max_pixels_per_region <= 0:
        raise ValueError('Invalid subdivision, scale or pixel limit')
    if args.max_tasks < 0:
        raise ValueError('--max-tasks must be >= 0')
    chunks = parse_chunks(args.chunks)
    plan = [(c, s) for c in chunks for s in range(args.subdivisions)]
    print(f'[PLAN] {len(plan)} task(s); chunk ids={chunks}; splits={args.subdivisions}; '
          f'scale={args.scale}; tileScale={args.tile_scale}')
    print('[TEST]', args.test, '[SUBMIT]', args.submit, '[VERIFY]', args.verify_asset)
    if not (args.submit or args.verify_asset):
        for chunk, sub in plan:
            desc, prefix = task_descriptions(chunk, sub, args.subdivisions, args.test)
            print('DRY RUN', desc, '->', prefix+'.csv')
        print('[NO TASK SUBMITTED] Upload the point CSV as an EE TABLE before --submit.')
        return

    import ee
    ee.Initialize(project=args.project)
    check_asset(ee, args.asset, require_count=args.verify_asset)
    if args.verify_asset and not args.submit:
        return
    if args.test and args.max_tasks == 0:
        print('[CAUTION] --test with --max-tasks 0 would submit one 100-grid test per chunk')
    raw = ee.FeatureCollection(args.asset)
    proj = ee.Projection(EASE_WKT)
    metric_stack = build_metrics(proj, args.scale, args.max_aggregate_pixels, ee)
    reducer = (ee.Reducer.mean()
               .combine(reducer2=ee.Reducer.max(), sharedInputs=True)
               .combine(reducer2=ee.Reducer.min(), sharedInputs=True))
    remote = existing_task_states(ee)
    started = 0
    for c, sub in plan:
        desc, filename = task_descriptions(c, sub, args.subdivisions, args.test)
        states = remote.get(desc, set())
        if states.intersection({'READY','RUNNING','COMPLETED','SUCCEEDED'}):
            print('[SKIP ALREADY PRESENT]', desc, sorted(states)); continue
        if 'FAILED' in states and not args.retry_failed:
            print('[SKIP FAILED]', desc, 'Use --subdivisions 2 or --retry-failed'); continue

        fc = raw.filter(ee.Filter.eq('chunk_id', c))
        if args.subdivisions > 1:
            fc = (fc.randomColumn('merit_fixed_random', seed=2026)
                  .filter(ee.Filter.gte('merit_fixed_random', sub/args.subdivisions))
                  .filter(ee.Filter.lt('merit_fixed_random', (sub+1)/args.subdivisions)))
        if args.test:
            fc = fc.limit(100)
        polygons = fc.map(lambda f: reconstruct(f, proj, ee))
        results = metric_stack.reduceRegions(
            collection=polygons, reducer=reducer,
            scale=args.scale, crs=proj,
            tileScale=args.tile_scale,
            maxPixelsPerRegion=args.max_pixels_per_region
        )

        def remove_geometry_and_add_relief(f):
            f = ee.Feature(f)
            lo = f.get('merit_elv_1km_min')
            hi = f.get('merit_elv_1km_max')
            relief = ee.Algorithms.If(
                ee.Algorithms.IsEqual(lo, None), None,
                ee.Algorithms.If(
                    ee.Algorithms.IsEqual(hi, None), None,
                    ee.Number(hi).subtract(ee.Number(lo))))
            f = f.set('merit_local_relief_m_approx', relief)
            return ee.Feature(None, f.toDictionary(OUTPUT_FIELDS))

        results = results.map(remove_geometry_and_add_relief)
        task = ee.batch.Export.table.toDrive(
            collection=results,
            description=desc,
            folder=args.drive_folder,
            fileNamePrefix=filename,
            fileFormat='CSV',
            selectors=OUTPUT_FIELDS
        )
        task.start()
        print('[TASK STARTED]', desc, 'id:', task.id, 'Drive CSV:', filename+'.csv')
        started += 1
        if args.max_tasks and started >= args.max_tasks:
            break
        if args.pause_seconds > 0:
            time.sleep(args.pause_seconds)
    print('[SUBMISSION COMPLETE] started:', started, 'of', len(plan))
    print('Check https://code.earthengine.google.com/tasks and inspect output CSV before larger batches.')
    print('NOTE: MERIT hnd is hydrologically adjusted elevation; NO actual HAND is computed.')


if __name__ == '__main__':
    main()
