# -*- coding: utf-8 -*-
"""Validate the local 26,317-grid CSV, create a GEE table manifest,
optionally upload it through GCS, and verify the resulting EE Table asset.

No cloud activity unless --upload or --check or --wait is specified.
The input CSV is never modified. Uses official Earth Engine table manifest.

Examples:
  python 04a_upload_merit_asset.py
  python 04a_upload_merit_asset.py --upload
  python 04a_upload_merit_asset.py --check
  python 04a_upload_merit_asset.py --wait --wait-minutes 30
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = 'eco-sector-503721-b7'
ASSET = f'projects/{PROJECT}/assets/groundsource_merit_m3_grids'
CSV = Path(r'E:\Global_flooding\mechanism_project\01_cities\m3\merit_m3_grids_gee_upload.csv')
GCS = 'gs://groundsource-climate-503721-b7/static/merit_m3_grids_gee_upload.csv'
EXPECTED_ROWS = 26317
NEEDED = ['grid_id', 'grid_x', 'grid_y', 'longitude', 'latitude', 'chunk_id']


def command(exe: str) -> str | None:
    found = shutil.which(exe)
    if found:
        return found
    scripts = Path(sys.executable).parent
    candidate = scripts / (exe + ('.exe' if sys.platform == 'win32' else ''))
    return str(candidate) if candidate.exists() else None


def check_csv(path: Path, expected: int) -> tuple[pd.DataFrame, dict]:
    if not path.is_file():
        raise FileNotFoundError(f'Upload CSV not found: {path}. Run 01_link_cities.py --scope m3 first.')
    df = pd.read_csv(path, dtype={'grid_id': 'string'}, encoding='utf-8-sig')
    absent = set(NEEDED) - set(df.columns)
    if absent:
        raise ValueError(f'Missing columns in CSV: {sorted(absent)}')
    df = df[NEEDED].copy()
    if len(df) != expected:
        raise ValueError(f'Expected {expected:,} rows, found {len(df):,}; check the input M3 selection before upload.')
    if df.isna().any().any():
        raise ValueError('Null values in upload input are not allowed')
    if df.grid_id.duplicated().any():
        raise ValueError('Duplicate grid IDs')
    for col in ['grid_x', 'grid_y', 'chunk_id']:
        numeric = pd.to_numeric(df[col], errors='coerce')
        if numeric.isna().any() or not np.all(np.isclose(numeric, np.round(numeric))):
            raise ValueError(f'{col} must contain integer numbers')
        df[col] = numeric.astype('int64')
    df['longitude'] = pd.to_numeric(df.longitude, errors='raise')
    df['latitude'] = pd.to_numeric(df.latitude, errors='raise')
    if not np.isfinite(df[['latitude', 'longitude']].to_numpy()).all():
        raise ValueError('Nonfinite longitude/latitude')
    if not df.longitude.between(-180, 180).all() or not df.latitude.between(-86, 86).all():
        raise ValueError('Invalid longitude/latitude for EPSG:6933 input')
    expected_ids = df.grid_x.astype(str) + '_' + df.grid_y.astype(str)
    if not expected_ids.eq(df.grid_id).all():
        raise ValueError('grid_id must equal grid_x + "_" + grid_y')
    chunks = df.groupby('chunk_id').size().sort_index().to_dict()
    if list(chunks) != list(range(len(chunks))) or any(n > 5000 for n in chunks.values()):
        raise ValueError(f'Invalid chunk IDs or chunk size: {chunks}')
    result = {'rows': len(df), 'unique_grid_id': int(df.grid_id.nunique()),
              'chunk_sizes': {str(k): int(v) for k, v in chunks.items()},
              'min_lon': float(df.longitude.min()), 'max_lon': float(df.longitude.max()),
              'min_lat': float(df.latitude.min()), 'max_lat': float(df.latitude.max())}
    return df, result


def get_asset(asset: str, project: str):
    import ee
    ee.Initialize(project=project)
    try:
        return ee.data.getAsset(asset)
    except Exception as exc:
        msg = str(exc).lower()
        if 'does not exist' in msg or 'not found' in msg or '404' in msg:
            return None
        raise RuntimeError(f'Unable to check asset; check credentials and permissions: {exc}') from exc


def run(cmd: list[str]) -> None:
    print('[RUN]', subprocess.list2cmdline(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', type=Path, default=CSV)
    p.add_argument('--gcs-uri', default=GCS)
    p.add_argument('--asset', default=ASSET)
    p.add_argument('--project', default=PROJECT)
    p.add_argument('--expected-rows', type=int, default=EXPECTED_ROWS)
    p.add_argument('--manifest-dir', type=Path, default=Path(r'E:\Global_flooding\mechanism_project\04_merit_asset'))
    p.add_argument('--upload', action='store_true', help='Actually copy CSV to GCS and start EE table ingestion')
    p.add_argument('--check', action='store_true', help='Read current EE asset metadata; no upload')
    p.add_argument('--wait', action='store_true', help='Wait until the EE Table asset becomes available; no upload')
    p.add_argument('--wait-minutes', type=float, default=30)
    p.add_argument('--allow-existing', action='store_true', help='Skip upload if asset already exists; never overwrite')
    args = p.parse_args()
    if not args.gcs_uri.startswith('gs://'):
        raise ValueError('--gcs-uri must start with gs://')
    if args.wait_minutes <= 0:
        raise ValueError('--wait-minutes must be positive')
    df, qc = check_csv(args.csv, args.expected_rows)
    print('[LOCAL CSV QC]', json.dumps(qc, indent=2, ensure_ascii=False))
    args.manifest_dir.mkdir(parents=True, exist_ok=True)
    qc_path = args.manifest_dir / '04a_local_asset_input_qc.json'
    qc_path.write_text(json.dumps(qc, indent=2, ensure_ascii=False), encoding='utf-8')
    manifest = {
        'name': args.asset,
        'sources': [{'uris': [args.gcs_uri], 'crs': 'EPSG:4326',
                     'xColumn': 'longitude', 'yColumn': 'latitude'}],
        'properties': {'description': 'M3 positive grid centres; polygons reconstructed from EPSG6933 grid_x grid_y',
                       'coordinate_source': 'WGS84 points; original grid definition is EPSG6933 20km'}
    }
    manifest_path = args.manifest_dir / '04a_merit_m3_table_manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print('[MANIFEST]', manifest_path)
    print('[ASSET]', args.asset)
    print('[GCS]', args.gcs_uri)
    if not (args.upload or args.check or args.wait):
        print('[DRY RUN] No network calls. To upload: add --upload. To check: add --check.')
        return

    if args.check:
        meta = get_asset(args.asset, args.project)
        if meta is None:
            print('[NOT AVAILABLE] Asset is missing, still ingesting, or not accessible. Confirm project and permissions.')
            return
        print('[ASSET FOUND]', json.dumps(meta, indent=2, default=str))
        return

    if args.upload:
        existing = get_asset(args.asset, args.project)
        if existing is not None:
            if args.allow_existing:
                print('[SKIP] Asset already exists; no overwrite.'); return
            raise RuntimeError(f'Asset already exists: {args.asset}; refusing overwrite. Use --check or --allow-existing.')
        gcloud = command('gcloud')
        if not gcloud:
            raise RuntimeError('gcloud CLI is missing. Install Google Cloud CLI, log in and set project, or manually upload CSV from EE Assets > NEW > Table Upload > CSV, selecting the same Asset ID.')
        eecli = command('earthengine')
        if not eecli:
            raise RuntimeError('earthengine CLI not found. Install: python -m pip install earthengine-api, then authenticate with earthengine authenticate.')
        # The --project is explicit, to avoid accidentally copying to the wrong project.
        run([gcloud, '--project', args.project, 'storage', 'cp', str(args.csv), args.gcs_uri])
        run([eecli, 'upload', 'table', '--manifest', str(manifest_path)])
        print('[INGESTION SUBMITTED] This is NOT a compute export. Wait for the Table ingest task to complete.')
        print('Then: python 04a_upload_merit_asset.py --wait --wait-minutes 30')
        return

    if args.wait:
        deadline = time.monotonic() + args.wait_minutes * 60
        while time.monotonic() < deadline:
            meta = get_asset(args.asset, args.project)
            if meta is not None:
                print('[ASSET FOUND]', args.asset, 'type:', meta.get('type'))
                print('[NEXT] Run 04_submit_merit_gee.py --verify-asset then submit a 100-grid test.')
                return
            print('[WAITING] Asset not yet available. Check EE Tasks for ingestion errors.', flush=True)
            time.sleep(30)
        raise TimeoutError('EE asset not available within the wait interval. Review Tasks/ingestion status, GCS access and asset permissions.')


if __name__ == '__main__':
    main()
