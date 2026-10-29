# -*- coding: utf-8 -*-
"""Stage80: UCDB city availability, continent/population/climate selection audit.

Only models *sample eligibility*, not flood incidence or reporting recall.
All 11,422 official cities stay in denominator. Optional GHSL E2020 context
describes assigned GF10 cell proxies, not official UCDB-city census totals.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def audit(cities,dominant=None,context=None,koppen=None,pilot_per_stratum=4):
    needed={'city_id','longitude','latitude','continent','screened','n_high_reports','n_report_years'}
    if needed-set(cities):raise ValueError('Missing Figure1 city fields: '+str(needed-set(cities)))
    c=cities.copy()
    c.city_id=c.city_id.astype('string')
    if c.city_id.duplicated().any():raise ValueError('Duplicate UCDB city id')
    if not c.screened.isin([True,False]).all():raise ValueError('Invalid screened flag')
    c['continent_category']=c.continent.fillna('Unknown')
    if (dominant is None)!=(context is None):
        raise ValueError('Supply Stage62c dominant and GHSL E2020 context TOGETHER')
    if dominant is not None:
        if dominant.grid_id.duplicated().any() or context.grid_id.duplicated().any():
            raise ValueError('Duplicate grid_id')
        required={'grid_id','population_count','built_total_m2'}
        if required-set(context):raise ValueError('GHSL context missing '+str(required-set(context)))
        d=dominant[['grid_id','city_id']].copy()
        d.city_id=d.city_id.astype('string')
        vals=d.merge(context[['grid_id','population_count','built_total_m2']],on='grid_id',
                     how='left',validate='one_to_one')
        for col in ('population_count','built_total_m2'):
            vals[col]=pd.to_numeric(vals[col],errors='coerce')
            if vals[col].dropna().lt(0).any():raise ValueError('Negative GHSL '+col)
        # Sum only observed cells, preserve number of cells with valid data.
        agg=vals.groupby('city_id').agg(
          gf10_assigned_cells=('grid_id','nunique'),
          gf10_population_proxy=('population_count','sum'),
          gf10_pop_valid_cells=('population_count','count'),
          gf10_built_m2_proxy=('built_total_m2','sum'),
          gf10_built_valid_cells=('built_total_m2','count')).reset_index()
        agg.loc[agg.gf10_pop_valid_cells.eq(0),'gf10_population_proxy']=np.nan
        agg.loc[agg.gf10_built_valid_cells.eq(0),'gf10_built_m2_proxy']=np.nan
        c=c.merge(agg,on='city_id',how='left',validate='one_to_one')
        c['population_proxy_quintile']='Unknown'
        valid=c.gf10_population_proxy.notna()
        if valid.sum()>=10:
            ranks=c.loc[valid,'gf10_population_proxy'].rank(method='first',pct=True)
            c.loc[valid,'population_proxy_quintile']=(np.ceil(ranks*5).clip(1,5).astype(int)
                                                     .map(lambda x:f'Q{x}')).to_numpy()
    if koppen is not None:
        import rasterio
        from pyproj import Transformer
        with rasterio.open(koppen) as ds:
            if ds.crs is None or ds.count!=1:
                raise ValueError('Köppen requires one band and valid raster CRS')
            if ds.width*ds.height>30_000_000:
                raise ValueError('Use 0p1 Köppen raster rather than giant 1km global raster')
            t=Transformer.from_crs('EPSG:4326',ds.crs,always_xy=True)
            X,Y=t.transform(c.longitude.to_numpy(),c.latitude.to_numpy())
            code=np.array([v[0] for v in ds.sample(zip(X,Y),masked=True)])
        code=np.ma.filled(code,0).astype(int)
        c['koppen_broad']=np.select(
            [(code>=1)&(code<=3),(code>=4)&(code<=7),
             (code>=8)&(code<=16),(code>=17)&(code<=28),
             (code>=29)&(code<=30)],
            ['A Tropical','B Arid','C Temperate','D Cold','E Polar'],default='Unknown')
    groupings={'continent':['continent_category']}
    if 'population_proxy_quintile' in c:
        groupings['population_proxy_quintile']=['population_proxy_quintile']
    if 'koppen_broad' in c:groupings['koppen_zone']=['koppen_broad']
    summaries={}
    for label,cols in groupings.items():
        grouped=c.groupby(cols,dropna=False).agg(
          official_cities=('city_id','size'),screened_cities=('screened','sum')).reset_index()
        grouped['screened_fraction']=grouped.screened_cities/grouped.official_cities
        summaries[label]=grouped
    # Sampling frame for external manual validation: includes sampled eligible
    # and ineligible cities, not claimed that all are satellite observable.
    pilot=pd.concat([part.sample(n=min(len(part),pilot_per_stratum),
                                  random_state=20261029)
           for _,part in c.groupby(['continent_category','screened'],dropna=False)],
           ignore_index=True)
    return c,summaries,pilot.reset_index(drop=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--city-audit',type=Path,required=True)
    p.add_argument('--dominant-city',type=Path)
    p.add_argument('--ghsl2020',type=Path)
    p.add_argument('--koppen',type=Path)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--pilot-per-stratum',type=int,default=4)
    a=p.parse_args()
    if a.pilot_per_stratum<1 or a.pilot_per_stratum>20:raise ValueError('Pilot 1..20')
    dom=pd.read_parquet(a.dominant_city) if a.dominant_city else None
    ctx=pd.read_parquet(a.ghsl2020) if a.ghsl2020 else None
    c,t,pilot=audit(pd.read_csv(a.city_audit,dtype={'city_id':str}),dom,ctx,a.koppen,
                    a.pilot_per_stratum)
    a.out.mkdir(parents=True,exist_ok=True)
    c.to_csv(a.out/'80_city_observation_profile.csv',index=False)
    pilot.to_csv(a.out/'80_stratified_validation_city_candidates.csv',index=False)
    for name,df in t.items():df.to_csv(a.out/f'80_{name}_screened_fraction.csv',index=False)
    z=t['continent'].sort_values('screened_fraction')
    fig,ax=plt.subplots(figsize=(9,4.6),layout='constrained')
    ax.barh(z.continent_category,z.screened_fraction*100,color='#52849B')
    ax.set(xlim=(0,100),xlabel='% official UCDB cities meeting news-report eligibility',
           title='Figure 1 | Unequal archive-eligible urban coverage')
    fig.savefig(a.out/'Figure1_city_selection_by_continent.png',dpi=300)
    fig.savefig(a.out/'Figure1_city_selection_by_continent.pdf')
    plt.close(fig)
    qc={'status':'CITY_NEWS_AVAILABILITY_NOT_FLOOD_INCIDENCE',
        'official_ucdb_cities':len(c),'screened_cities':int(c.screened.sum()),
        'unknown_continent_cities':int(c.continent.isna().sum()),
        'ghsl2020_context_available':ctx is not None,
        'koppen_context_available':a.koppen is not None,
        'pilot_stratified_cities':len(pilot),
        'caution':'GHSL proxy from dominant 10km cells is not complete UCDB-city population'}
    (a.out/'80_city_selection_qc.json').write_text(
       json.dumps(qc,indent=2),encoding='utf-8')
    print('[DONE]',qc)
if __name__=='__main__':main()
