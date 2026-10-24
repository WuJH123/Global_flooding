# -*- coding: utf-8 -*-
"""Build a defensible global GHSL broad urban-domain denominator on exact 20 km EPSG:6933 cells.

Unlike the earlier binary/max version, zero is a VALID non-urban observation;
255 is the ONLY NoData in the intermediate binary raster.  The source is
GHSL-SMOD R2023A, classes 21,22,23,30, World Mollweide (ESRI:54009).
The result is a STATIC 2025 comparator if the input is E2025, not M3.
"""
from __future__ import annotations
import argparse
import re
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterio.enums import Resampling

GX_MIN,GX_MAX = -870,869
GY_MIN,GY_MAX = -370,369
CELL=20000
WIDTH=GX_MAX-GX_MIN+1
HEIGHT=GY_MAX-GY_MIN+1
TRANSFORM=from_origin(GX_MIN*CELL,(GY_MAX+1)*CELL,CELL,CELL)
URBAN=(21,22,23,30)


def _run(src_path:Path,out:Path,threads:int,min_fraction:float):
    out.parent.mkdir(parents=True,exist_ok=True)
    with rasterio.open(src_path) as ds:
        if not ds.crs:
            raise RuntimeError('SMOD GeoTIFF missing CRS')
        if ds.width < 10000 or ds.height < 5000:
            raise RuntimeError('Expected FULL global 1 km SMOD rather than one tile')
        print(f'[SOURCE] size={ds.width}x{ds.height}, CRS={ds.crs}, source nodata={ds.nodata}',flush=True)
        year_m=re.search(r'E(20\d\d)', src_path.name)
        year=int(year_m.group(1)) if year_m else 2025
        with tempfile.TemporaryDirectory(prefix='smod31fixed_',dir=str(out.parent)) as temp_dir:
            binary_path=Path(temp_dir)/'urban_binary_valid0_nodata255.tif'
            profile={
              'driver':'GTiff','width':ds.width,'height':ds.height,'count':1,
              'dtype':'uint8','crs':ds.crs,'transform':ds.transform,
              'nodata':255,'tiled':True,'blockxsize':512,'blockysize':512,
              'compress':'DEFLATE','BIGTIFF':'IF_SAFER'
            }
            seen=set();total=0
            with rasterio.open(binary_path,'w',**profile) as target:
                for _,win in ds.block_windows(1):
                    a=ds.read(1,window=win,masked=True)
                    valid=(~np.ma.getmaskarray(a)) & (np.asarray(a)!=-200)
                    raw=np.asarray(a)
                    # Crucial: 0 is non-urban, NOT nodata. NoData=255.
                    b=np.where(valid,np.isin(raw,URBAN).astype(np.uint8),255).astype(np.uint8)
                    target.write(b,1,window=win)
                    if total%1000==0: print('[PREPARE] source blocks:',total,flush=True)
                    total+=1
                    if len(seen)<100: seen.update(np.unique(raw).tolist())
            print('[PREPARE] source category sample:',sorted(seen)[:40],flush=True)
            # Aggregate the binary urban mask to 20 km equal-area cells. AVERAGE
            # estimates urban area fraction. Target NoData=-9999, distinct from 0.
            fraction=np.full((HEIGHT,WIDTH),-9999,dtype='float32')
            with rasterio.open(binary_path) as bi:
                reproject(
                    source=rasterio.band(bi,1),destination=fraction,
                    src_transform=bi.transform,src_crs=bi.crs,src_nodata=255,
                    dst_transform=TRANSFORM,dst_crs='EPSG:6933',dst_nodata=-9999,
                    resampling=Resampling.average,num_threads=max(1,threads),
                    init_dest_nodata=True,warp_mem_limit=256,
                )
    valid=(fraction>=0)&(fraction<=1+1e-5)
    urban=valid&(fraction>=min_fraction)
    n=int(urban.sum())
    print(f'[QC] Global destination cells={WIDTH*HEIGHT:,}; valid={int(valid.sum()):,}; urban={n:,}; fraction={n/(WIDTH*HEIGHT):.3%}',flush=True)
    if not (1000<n<WIDTH*HEIGHT*.50):
        raise RuntimeError('Urban fraction implausible. Check NoData/resampling and source categories before using this output.')
    rr,cc=np.nonzero(urban)
    gx=GX_MIN+cc;gy=GY_MAX-rr
    urban_frac=np.clip(fraction[rr,cc],0,1)
    out_df=pd.DataFrame({
        'grid_x':gx.astype('int32'), 'grid_y':gy.astype('int32'),
        'urban_fraction_20km':urban_frac.astype('float32'),
        'urban_area_km2_est':(urban_frac*400).astype('float32'),
        'ghsl_epoch':year,
        'definition':f'GHSL_SMOD_E{year}_broad_codes_21_22_23_30_fraction_gte_{min_fraction:g}'
    })
    out_df.insert(0,'grid_id',out_df.grid_x.astype(str)+'_'+out_df.grid_y.astype(str))
    if out_df.grid_id.duplicated().any():raise AssertionError('Duplicate 20km grid IDs')
    out.parent.mkdir(parents=True,exist_ok=True)
    out_df.to_csv(out,index=False,encoding='utf-8-sig')
    print('[SAVED]',out,flush=True)
    # Sensitivity can be computed cheaply from the same output, no new warp.
    result=[]
    for th in [min_fraction,.005,.01,.05,.10,.25,.50]:
        result.append({'ghsl_epoch':year,'min_urban_fraction':th,'qualifying_grid_count':int((out_df.urban_fraction_20km>=th).sum())})
    summary=out.parent/f'31a_urban_threshold_counts_E{year}.csv'
    pd.DataFrame(result).to_csv(summary,index=False,encoding='utf-8-sig')
    print('[SAVED]',summary,flush=True)
    return n


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--smod',required=True,type=Path,help='Full GHSL-SMOD E2025 GeoTIFF or E2020')
    ap.add_argument('--out',type=Path,default=Path(r'E:\Global_flooding\results_31\global_urban_universe_2025_CORRECTED.csv'))
    ap.add_argument('--threads',type=int,default=4)
    ap.add_argument('--min-urban-fraction',type=float,default=.001,help='Area fraction >=0.001 avoids machine-epsilon positives; broad any-urban proxy')
    args=ap.parse_args()
    if not args.smod.is_file():raise FileNotFoundError(args.smod)
    if not 0<args.min_urban_fraction<=1:raise ValueError('min-urban-fraction must be (0,1]')
    _run(args.smod,args.out,args.threads,args.min_urban_fraction)

if __name__=='__main__':main()
