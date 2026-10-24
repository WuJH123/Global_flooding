# -*- coding: utf-8 -*-
"""CRU TS v4.09 precipitation climatology for M3 20-km grid centres.

This is a STATIC monthly climate-background product, NOT flood-event rainfall.

Key safeguards:
  * Use scipy for the user's CRU NetCDF3 file (works with Chinese Windows paths).
  * Decode CF NoData; don't turn ocean/invalid precipitation into zero.
  * Verify an exactly complete monthly time axis and monthly sampling support.
  * Use a 1991–2020 reference period, nearest CRU 0.5-degree pixel centre.
  * Report station support (CRU 'stn', 0 = climatology substituted).
  * Report source-grid centre, distance, and optional coastal proxy provenance.
  * Preserve prior output field names for downstream script 05.

Default: NO spatial filling. --fallback-km is OPTIONAL sensitivity only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

EARTH_RADIUS_KM = 6371.0088


def nearest_index(axis: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Nearest index on an ascending or descending regularly spaced axis."""
    if axis.ndim != 1 or axis.size < 2 or not np.all(np.isfinite(axis)):
        raise ValueError('Invalid latitude/longitude coordinate axis')
    delta = np.diff(axis)
    if not (np.all(delta > 0) or np.all(delta < 0)):
        raise ValueError('Coordinate axis is not strictly monotonic')
    ascending = delta[0] > 0
    ordered = axis if ascending else axis[::-1]
    idx = np.searchsorted(ordered, values)
    idx = np.clip(idx, 1, len(ordered) - 1)
    idx = np.where(
        np.abs(ordered[idx] - values) < np.abs(ordered[idx - 1] - values),
        idx, idx - 1,
    )
    return idx if ascending else len(ordered) - 1 - idx


def great_circle_km(lat1, lon1, lat2, lon2):
    a1, a2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dlat = a2 - a1
    dlon = (np.deg2rad(lon2 - lon1) + np.pi) % (2 * np.pi) - np.pi
    h = np.sin(dlat / 2) ** 2 + np.cos(a1) * np.cos(a2) * np.sin(dlon / 2) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def spherical_xyz(lat, lon):
    lat = np.deg2rad(lat)
    lon = np.deg2rad(lon)
    return np.column_stack((
        np.cos(lat) * np.cos(lon),
        np.cos(lat) * np.sin(lon),
        np.sin(lat),
    ))


def calc_climatology(ds, variable, start, end, min_years):
    """Stream exactly one year at a time; no Dask or full 6 GB load needed."""
    if variable not in ds:
        raise KeyError(f'{variable!r} not in CRU file; available: {list(ds.data_vars)}')
    precip = ds[variable]
    units = str(precip.attrs.get('units', '')).strip().lower()
    if units not in {'mm/month', 'mm per month', 'mm'}:
        raise ValueError(f'Unexpected precipitation unit {units!r}; expected mm/month')
    if not {'time', 'lat', 'lon'} <= set(precip.dims):
        raise ValueError(f'Unexpected CRU dimensions: {precip.dims}')

    dates = pd.DatetimeIndex(ds.time.values)
    expected = pd.period_range(f'{start}-01', f'{end}-12', freq='M')
    selected_indices = np.flatnonzero((dates.year >= start) & (dates.year <= end))
    actual = pd.PeriodIndex(dates[selected_indices], freq='M')
    if len(actual) != len(expected) or not actual.equals(expected):
        raise ValueError('CRU time axis is not exactly the requested continuous monthly period')

    lat = np.asarray(ds.lat.values, dtype=float)
    lon = np.asarray(ds.lon.values, dtype=float)
    shape = (12, len(lat), len(lon))
    total = np.zeros(shape, dtype=np.float64)
    count = np.zeros(shape, dtype=np.uint16)
    stn_total = np.zeros((len(lat), len(lon)), dtype=np.float64)
    stn_count = np.zeros((len(lat), len(lon)), dtype=np.uint32)
    stn_supported_count = np.zeros((len(lat), len(lon)), dtype=np.uint32)
    has_stations = ('stn' in ds and set(ds['stn'].dims) == set(precip.dims))
    if not has_stations:
        print('[WARN] CRU station-support field absent or has unexpected dimensions')

    print('[CRU] variable=', variable, 'units=', units,
          'months=', len(selected_indices), 'lat/lon=', len(lat), len(lon))
    print('[CRU] streaming precipitation and station-support, one year per read...')

    for y in range(start, end + 1):
        # Data are chronological, precisely 12 monthly records per year.
        block = selected_indices[(y - start) * 12:(y - start + 1) * 12]
        year_pre = np.asarray(
            precip.isel(time=block).transpose('time', 'lat', 'lon').values,
            dtype=np.float64)
        valid = np.isfinite(year_pre) & (year_pre >= 0)
        total += np.where(valid, year_pre, 0.0)
        count += valid.astype(np.uint16)
        if has_stations:
            station = np.asarray(ds['stn'].isel(time=block).transpose(
                'time', 'lat', 'lon').values, dtype=np.float64)
            st_valid = valid & np.isfinite(station) & (station >= 0)
            stn_total += np.where(st_valid, station, 0.0).sum(axis=0)
            stn_count += st_valid.sum(axis=0).astype(np.uint32)
            stn_supported_count += (st_valid & (station > 0)).sum(axis=0).astype(np.uint32)
        if (y - start + 1) % 5 == 0 or y == end:
            print(f'[CRU] processed {y - start + 1}/{end - start + 1} years, through {y}')

    monthly_mean = np.full(shape, np.nan, dtype=np.float64)
    np.divide(total, count, out=monthly_mean, where=count >= min_years)
    coords = {'month': np.arange(1, 13), 'lat': lat, 'lon': lon}
    means = xr.DataArray(monthly_mean, dims=('month', 'lat', 'lon'), coords=coords)
    counts = xr.DataArray(count, dims=('month', 'lat', 'lon'), coords=coords)
    stn_mean = stn_supported = None
    if has_stations:
        m = np.full(stn_total.shape, np.nan, dtype=np.float64)
        f = np.full(stn_total.shape, np.nan, dtype=np.float64)
        np.divide(stn_total, stn_count, out=m, where=stn_count > 0)
        np.divide(stn_supported_count, stn_count, out=f, where=stn_count > 0)
        stn_mean = xr.DataArray(m, dims=('lat', 'lon'), coords={'lat': lat, 'lon': lon})
        stn_supported = xr.DataArray(f, dims=('lat', 'lon'), coords={'lat': lat, 'lon': lon})
    return means, counts, stn_mean, stn_supported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path(__file__).with_name('project_paths.json')))
    parser.add_argument('--centers', default=None,
                        help='Override input grid_centers.csv')
    parser.add_argument('--cru', default=None, help='Override CRU NetCDF file')
    parser.add_argument('--out', default=None)
    parser.add_argument('--var', default='pre')
    parser.add_argument('--base-start', type=int, default=1991)
    parser.add_argument('--base-end', type=int, default=2020)
    parser.add_argument('--min-years-per-month', type=int, default=24)
    parser.add_argument('--fallback-km', type=float, default=0.0,
                        help='Optional spatial proxy for ocean-centre grids, OFF by default')
    args = parser.parse_args()
    if args.base_end < args.base_start:
        parser.error('base-end must be >= base-start')
    years = args.base_end - args.base_start + 1
    if not 1 <= args.min_years_per_month <= years:
        parser.error('min-years-per-month must be within 1..number of years')
    if args.fallback_km < 0:
        parser.error('fallback-km cannot be negative')

    config_path = Path(args.config)
    cfg = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    root = Path(cfg.get('project_root', r'E:\Global_flooding'))
    input_csv = Path(args.centers) if args.centers else root / 'mechanism_project' / '01_cities' / 'm3' / 'grid_centers.csv'
    nc_path = Path(args.cru) if args.cru else Path(cfg.get('cru_precip',
        r'E:\数据集\CRU TS v4.09全球降水数据集\cru_ts4.09.1901.2024.pre.dat.nc'))
    output = Path(args.out) if args.out else root / 'mechanism_project' / '07_climate_background'
    output.mkdir(parents=True, exist_ok=True)
    for path in (input_csv, nc_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    centers = pd.read_csv(input_csv, dtype={'grid_id': str}, encoding='utf-8-sig')
    expected_fields = {'grid_id', 'center_lon', 'center_lat'}
    if not expected_fields.issubset(centers.columns):
        raise ValueError(f'grid_centers.csv missing: {expected_fields - set(centers.columns)}')
    if centers.grid_id.isna().any() or centers.grid_id.duplicated().any():
        raise ValueError('Invalid/duplicate grid_id in grid_centers.csv')
    lat0 = pd.to_numeric(centers.center_lat, errors='coerce').to_numpy(dtype=float)
    lon0 = pd.to_numeric(centers.center_lon, errors='coerce').to_numpy(dtype=float)
    good_coords = (np.isfinite(lat0) & np.isfinite(lon0) &
                   (lat0 >= -90) & (lat0 <= 90) &
                   (lon0 >= -180) & (lon0 <= 180))
    if not good_coords.all():
        raise ValueError(f'{np.count_nonzero(~good_coords)} grid centres have invalid coordinates')
    print('[INPUT] grid centres=', len(centers), 'CRU=', nc_path)

    with xr.open_dataset(
        nc_path, engine='scipy', decode_times=True, mask_and_scale=True,
        drop_variables=['mae', 'maea'],
    ) as ds:
        means, counts, stn_mean, stn_supported = calc_climatology(
            ds, args.var, args.base_start, args.base_end, args.min_years_per_month)
        src_lat = means.lat.to_numpy().astype(float)
        src_lon = means.lon.to_numpy().astype(float)

    longitude_query = lon0 % 360.0 if np.max(src_lon) > 180 else lon0
    li = nearest_index(src_lat, lat0)
    lj = nearest_index(src_lon, longitude_query)
    monthly_cube = means.to_numpy().astype(float)
    n_cube = counts.to_numpy().astype(float)
    monthly = monthly_cube[:, li, lj].T.copy()
    valid_year_counts = n_cube[:, li, lj].T.copy()
    sampled_lat = src_lat[li].copy()
    sampled_lon = src_lon[lj].copy()
    source_distance = great_circle_km(lat0, lon0, sampled_lat, sampled_lon)
    station_mean_cube = None if stn_mean is None else stn_mean.to_numpy().astype(float)
    station_support_cube = None if stn_supported is None else stn_supported.to_numpy().astype(float)
    support_n = np.full(len(centers), np.nan)
    support_frac = np.full(len(centers), np.nan)
    if station_mean_cube is not None:
        support_n[:] = station_mean_cube[li, lj]
        support_frac[:] = station_support_cube[li, lj]

    valid_monthly = np.isfinite(monthly).all(axis=1)
    method = np.where(valid_monthly, 'nearest_cru_cell', 'nearest_cru_cell_missing').astype(object)
    before = int((~valid_monthly).sum())
    print(f'[QC] nearest CRU cells: complete={valid_monthly.sum()}, missing={before}')

    if args.fallback_km > 0 and before:
        # Proxy only; do not use it silently as if it were the target grid observation.
        from scipy.spatial import cKDTree
        source_valid = np.isfinite(monthly_cube).all(axis=0)
        vr, vc = np.nonzero(source_valid)
        source_xyz = spherical_xyz(src_lat[vr], src_lon[vc])
        missing_idx = np.flatnonzero(~valid_monthly)
        kd = cKDTree(source_xyz)
        chord, best = kd.query(spherical_xyz(lat0[missing_idx], lon0[missing_idx]), k=1)
        dist = 2 * EARTH_RADIUS_KM * np.arcsin(np.clip(chord / 2, 0, 1))
        good = dist <= args.fallback_km
        targets = missing_idx[good]
        rr = vr[best[good]]
        cc = vc[best[good]]
        monthly[targets] = monthly_cube[:, rr, cc].T
        valid_year_counts[targets] = n_cube[:, rr, cc].T
        sampled_lat[targets], sampled_lon[targets] = src_lat[rr], src_lon[cc]
        source_distance[targets] = dist[good]
        method[targets] = 'nearest_valid_cru_spatial_proxy'
        if station_mean_cube is not None:
            support_n[targets] = station_mean_cube[rr, cc]
            support_frac[targets] = station_support_cube[rr, cc]
        print(f'[FALLBACK] resolved {len(targets)} of {before} missing cells within {args.fallback_km} km')

    monthly_cols = [f'cru_pre_{i:02d}_mm' for i in range(1, 13)]
    result = pd.DataFrame(monthly, columns=monthly_cols)
    result.insert(0, 'grid_id', centers.grid_id.to_numpy())
    result['cru_pre_annual_mm'] = result[monthly_cols].sum(axis=1, min_count=12)
    mean_month = result[monthly_cols].mean(axis=1)
    result['cru_pre_peak_to_mean_month_ratio'] = (
        result[monthly_cols].max(axis=1) / mean_month.replace(0, np.nan))
    annual = result['cru_pre_annual_mm'].to_numpy(dtype=float)
    # Top 3 climatological months. No implicit NA=>zero for invalid grid cells.
    three = np.sort(np.where(np.isfinite(monthly), monthly, -np.inf), axis=1)[:, -3:]
    numerator = three.sum(axis=1)
    ratio = np.full(len(result), np.nan)
    use = np.isfinite(annual) & (annual > 0) & np.isfinite(numerator)
    ratio[use] = numerator[use] / annual[use]
    result['cru_pre_wettest3_fraction'] = ratio

    result['cru_pre_valid_years_min'] = np.min(valid_year_counts, axis=1).astype(int)
    result['cru_pre_station_mean_count'] = support_n
    result['cru_pre_station_supported_fraction'] = support_frac
    result['cru_pre_stationless_all_flag'] = np.where(
        np.isfinite(support_frac), support_frac == 0, False)
    result['cru_pre_source_lat'] = sampled_lat
    result['cru_pre_source_lon'] = (sampled_lon + 180) % 360 - 180
    result['cru_pre_source_distance_km'] = source_distance
    result['cru_pre_sampling_method'] = method
    result['cru_pre_base_start'] = args.base_start
    result['cru_pre_base_end'] = args.base_end

    suffix = f'{args.base_start}_{args.base_end}'
    csv_file = output / f'cru_{suffix}_grid_climatology.csv'
    parquet_file = output / f'cru_{suffix}_grid_climatology.parquet'
    result.to_csv(csv_file, index=False, encoding='utf-8-sig')
    parquet_ok = False
    try:
        result.to_parquet(parquet_file, index=False)
        parquet_ok = True
    except ImportError:
        print('[WARN] pyarrow/fastparquet unavailable; CSV and QC JSON were saved, Parquet skipped')
    complete = int(result['cru_pre_annual_mm'].notna().sum())
    proxy = int((result.cru_pre_sampling_method == 'nearest_valid_cru_spatial_proxy').sum())
    no_stations = int(result['cru_pre_stationless_all_flag'].sum())
    qc = {
        'cru_file': str(nc_path), 'grid_centers': str(input_csv),
        'period': suffix, 'n_input_grids': len(result), 'n_unique_ids': result.grid_id.nunique(),
        'n_complete_climatology': complete, 'n_missing_climatology': len(result) - complete,
        'n_spatial_proxy_grids': proxy, 'n_zero_station_support_grids': no_stations,
        'min_years_per_month': args.min_years_per_month,
        'max_sampling_distance_km': float(result.cru_pre_source_distance_km.max()),
        'annual_precip_min_mm': float(result.cru_pre_annual_mm.min()),
        'annual_precip_median_mm': float(result.cru_pre_annual_mm.median()),
        'annual_precip_max_mm': float(result.cru_pre_annual_mm.max()),
        'note': 'CRU 0.5-degree monthly climatology, not event precipitation. Station support 0 indicates climatology inserted; spatial proxy is sensitivity only.',
    }
    (output / f'cru_{suffix}_qc.json').write_text(
        json.dumps(qc, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[QC]', json.dumps(qc, ensure_ascii=False, indent=2))
    print('[SAVED]', csv_file)
    if parquet_ok:
        print('[SAVED]', parquet_file)
    print('[SAVED]', output / f'cru_{suffix}_qc.json')


if __name__ == '__main__':
    main()
