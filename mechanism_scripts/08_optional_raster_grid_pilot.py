# -*- coding: utf-8 -*-
"""Optional raster-grid pilot. Avoid treating 10 m global WSF as a cheap full run.
For population outputs, mean native raster value is NOT population sum unless
source pixel units, nodata and support are documented.
"""
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

KEYS=('population_2025','human_footprint_2024','wsf_2019')

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    a.add_argument('--dataset',choices=KEYS,required=True)
    a.add_argument('--limit',type=int,default=200,help='0=all grids, costly for WSF 10m')
    a.add_argument('--out',default=None)
    ar=a.parse_args()
    cfg=json.loads(Path(ar.config).read_text(encoding='utf-8'))
    centers=Path(cfg['project_root'])/'mechanism_project'/'01_cities'/'m3'/'grid_centers.csv'
    c=pd.read_csv(centers)
    if ar.limit>0 and ar.limit<len(c): c=c.sample(n=ar.limit,random_state=2026).sort_values('grid_id')
    out=Path(ar.out or Path(cfg['project_root'])/'mechanism_project'/'08_raster_pilot');out.mkdir(parents=True,exist_ok=True)
    result=[]
    with rasterio.open(cfg[ar.dataset]) as src:
        print('[RASTER]',src.width,src.height,src.crs,'nodata',src.nodata,'dtype',src.dtypes)
        tf,w,h=calculate_default_transform(src.crs,'EPSG:6933',src.width,src.height,*src.bounds,resolution=1000)
        kw={'crs':'EPSG:6933','transform':tf,'width':w,'height':h,
            'resampling':Resampling.average,'dtype':'float32','nodata':-9999.0}
        if ar.dataset=='wsf_2019':
            # DLR WSF2019: 255=settlement, 0=nonsettlement; both valid classes.
            kw['src_nodata']=-9999.0
        with WarpedVRT(src,**kw) as vrt:
            for n,r in enumerate(c.itertuples(index=False)):
                x,y=int(r.grid_x),int(r.grid_y)
                win=from_bounds(x*20000,y*20000,(x+1)*20000,(y+1)*20000,transform=vrt.transform)
                vals=vrt.read(1,window=win,masked=True)
                a_vals=np.asarray(vals.compressed(),dtype=float)
                a_vals=a_vals[np.isfinite(a_vals)]
                entry={'grid_id':r.grid_id,'n_valid_1km_cells':len(a_vals),'mean_src_pixel_value':float(a_vals.mean()) if len(a_vals) else np.nan}
                if ar.dataset=='wsf_2019':
                    entry['wsf_settlement_fraction_estimate']=float(a_vals.mean()/255.0) if len(a_vals) else np.nan
                result.append(entry)
                if (n+1)%100==0: print('[PROGRESS]',n+1,'/',len(c))
    pd.DataFrame(result).to_csv(out/f'{ar.dataset}_1km_grid_pilot.csv',index=False)
    print('[SAVED]',out)

if __name__=='__main__':main()
