# -*- coding: utf-8 -*-
"""Read metadata only; do not scan or load full global rasters/vectors."""
import argparse
import json
import pathlib
import traceback
from collections import Counter

VECTOR_KEYS = ('ucdb', 'flood_archive_region', 'dfo_master', 'flood_1989_2017')
RASTER_KEYS = ('population_2025', 'human_footprint_2024', 'wsf_2019')
NC_KEYS = ('cama_nc', 'cru_precip')


def audit_vector(path):
    import pyogrio
    layers = pyogrio.list_layers(str(path))
    arr = []
    for lname, geom_type in layers:
        meta = pyogrio.read_info(str(path), layer=lname)
        arr.append({
            'layer': str(lname), 'geom_type': str(geom_type),
            'feature_count': int(meta.get('features', -1)),
            'crs': str(meta.get('crs')), 'bounds': str(meta.get('total_bounds')),
            'fields': [str(x) for x in meta.get('fields', [])],
            'field_types': [str(x) for x in meta.get('dtypes', [])]
        })
    return arr


def audit_raster(path):
    import rasterio
    with rasterio.open(path) as src:
        return {'crs': str(src.crs), 'width': src.width, 'height': src.height,
                'bands': src.count, 'bounds': list(src.bounds),
                'nodata': src.nodata, 'dtypes': src.dtypes,
                'units': src.units, 'descriptions': src.descriptions,
                'resolution': list(src.res), 'tags': src.tags()}


def audit_nc(path):
    import xarray as xr
    # The user's files were verified to read with scipy (CRU NetCDF3)
    # and h5netcdf (CaMa HDF5) on Windows Chinese-language paths.
    from pathlib import Path
    engine = 'scipy' if 'cru_ts' in Path(path).name.lower() else 'h5netcdf'
    ds = xr.open_dataset(path, engine=engine, decode_times=False, chunks=None)
    try:
        return {'engine': engine, 'sizes': dict(ds.sizes),
                'data_vars': {v: {'dims': list(ds[v].dims), 'dtype': str(ds[v].dtype),
                                  'attrs': {k: str(val)[:500] for k, val in ds[v].attrs.items()}}
                              for v in ds.data_vars},
                'coords': {v: {'dims': list(ds[v].dims), 'attrs': {k: str(z) for k, z in ds[v].attrs.items()}}
                           for v in ds.coords},
                'attrs': {k: str(val)[:500] for k, val in ds.attrs.items()}}
    finally:
        ds.close()


def audit_parquet(path):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    return {'rows': pf.metadata.num_rows, 'row_groups': pf.metadata.num_row_groups,
            'schema': str(pf.schema_arrow)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default=str(pathlib.Path(__file__).with_name('project_paths.json')))
    p.add_argument('--out', default=None)
    a = p.parse_args()
    cfg = json.loads(pathlib.Path(a.config).read_text(encoding='utf-8-sig'))
    out = pathlib.Path(a.out or pathlib.Path(cfg['project_root']) / 'mechanism_project' / '00_audit')
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for key, path_value in cfg.items():
        if key == 'project_root': continue
        path = pathlib.Path(path_value)
        entry = {'file': str(path), 'exists': path.exists()}
        if path.exists():
            try:
                if key in VECTOR_KEYS: entry['metadata'] = audit_vector(path)
                elif key in RASTER_KEYS: entry['metadata'] = audit_raster(path)
                elif key in NC_KEYS: entry['metadata'] = audit_nc(path)
                elif path.suffix.lower() == '.parquet': entry['metadata'] = audit_parquet(path)
                elif path.suffix.lower() == '.csv':
                    import pandas as pd
                    entry['metadata'] = {'columns': pd.read_csv(path, nrows=2).columns.tolist()}
                else: entry['metadata'] = {'note': 'unknown metadata reader'}
            except Exception as exc:
                entry['error'] = repr(exc)
        report[key] = entry
        print(f"[{('OK' if path.exists() else 'MISSING')}] {key}")
        if 'error' in entry: print(' ERROR:', entry['error'])
        if key == 'ucdb' and 'metadata' in entry:
            for layer in entry['metadata']: print(' UCDB LAYER:', layer['layer'], layer['feature_count'], layer['fields'][:25])
        if key == 'cama_nc' and 'metadata' in entry:
            print(' CAMA VARIABLES:', list(entry['metadata']['data_vars']))
    basin_path = pathlib.Path(cfg['basin_root'])
    basins = sorted(basin_path.rglob('*.shp')) if basin_path.exists() else []
    basin_report = []
    for f in basins:
        e = {'path': str(f)}
        try:
            d = audit_vector(f)
            e['layers'] = d
            print('[BASIN]', f.name, d[0]['feature_count'], 'features')
        except Exception as exc: e['error'] = repr(exc)
        basin_report.append(e)
    report['basinatlas_shapefiles'] = basin_report
    path = out / 'dataset_metadata.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print('\n[SAVED]', path)
    print('NEXT: verify UCDB layer/id field and BasinATLAS level/fields in the JSON before spatial joins.')


if __name__ == '__main__': main()
