# -*- coding: utf-8 -*-
"""Results 3.1: independent GHSL urban denominator and Beck Köppen–Geiger climate zones.

This runs locally, reads the corrected 31a universe, previously exported
31_all_reported_grids.csv, and 1991–2020 Köppen-Geiger raster (suggest 0p1).
It calculates observation coverage, not physical flood occurrence or risk.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from pyproj import Transformer
import rasterio
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ZONES={1:'A Tropical',2:'B Arid',3:'C Temperate',4:'D Cold',5:'E Polar'}
# Beck 2023 numeric encoding, broad zones inferred directly from class code.
POP_BINS=[-np.inf,100,300,1000,3000,10000,np.inf]
POP_NAMES=['<100','100–300','300–1,000','1,000–3,000','3,000–10,000','≥10,000']

def longitude_latitude(frame):
    t=Transformer.from_crs('EPSG:6933','EPSG:4326',always_xy=True)
    x=(frame.grid_x.to_numpy(dtype='float64')+0.5)*20000
    y=(frame.grid_y.to_numpy(dtype='float64')+0.5)*20000
    a,b=t.transform(x,y)
    return np.asarray(a),np.asarray(b)

def sampled_climate(lon,lat, tif:Path):
    """Vectorized sampling, fast at 0.1 degrees; returns classes and valid masks."""
    with rasterio.open(tif) as ds:
        if ds.count!=1 or ds.crs is None:raise ValueError('Require one-band, georeferenced Köppen TIFF')
        print(f'[KOPPEN] {ds.width}x{ds.height} crs={ds.crs} nodata={ds.nodata}',flush=True)
        # 0p1 is ~6.5 million pixels; reading all is cheap. For 0p00833333
        # (~933 million), disallow accidental multi-GB RAM usage.
        if ds.width*ds.height>30_000_000:
            raise ValueError('Use koppen_geiger_0p1.tif (0.1 degree), not 0p00833333 for global sampling.')
        t=Transformer.from_crs('EPSG:4326',ds.crs,always_xy=True)
        sx,sy=t.transform(lon,lat)
        inv=~ds.transform
        cc,rr=inv*(np.asarray(sx),np.asarray(sy))
        rows=np.floor(rr).astype('int64')
        cols=np.floor(cc).astype('int64')
        valid=(rows>=0)&(rows<ds.height)&(cols>=0)&(cols<ds.width)
        # Some +/-180 points may map to the far edge.
        v=np.zeros(len(rows),dtype=np.int16)
        raw=ds.read(1,masked=True)
        indices=np.flatnonzero(valid)
        if len(indices):
            picked=raw[rows[indices],cols[indices]]
            v[indices]=np.asarray(np.ma.filled(picked,0),dtype='int16')
        valid_class=(v>=1)&(v<=30)
        zone_num=np.select([valid_class&(v<=3),valid_class&(v<=7),valid_class&(v<=16),valid_class&(v<=28),valid_class],
                           [1,2,3,4,5],default=0).astype('int8')
        labels=np.asarray(['Unknown','A Tropical','B Arid','C Temperate','D Cold','E Polar'],dtype=object)
        return v,labels[zone_num]

def save(df,out,name):
    dest=out/name;df.to_csv(dest,index=False,encoding='utf-8-sig');print('[SAVED]',dest,flush=True)
    return dest

def plot_results(uni,table,by_fraction,out,epoch):
    fig,axs=plt.subplots(2,2,figsize=(13,9.5),constrained_layout=True)
    ax=axs[0,0]
    rng=np.random.default_rng(20261019)
    n=150000
    sample=uni if len(uni)<=n else uni.iloc[np.sort(rng.choice(len(uni),n,replace=False))]
    ax.scatter(sample.longitude,sample.latitude,s=.18,c='#9da4aa',alpha=.35,rasterized=True,label=f'GHSL E{epoch} broad-urban grids')
    reported=uni.loc[uni.groundsource_ever_reported]
    rep=reported if len(reported)<=n else reported.iloc[np.sort(rng.choice(len(reported),n,replace=False))]
    ax.scatter(rep.longitude,rep.latitude,s=.8,c='#bf523b',alpha=.65,rasterized=True,label='At least one Groundsource report')
    ax.set(xlim=(-180,180),ylim=(-62,82),xlabel='Longitude',ylabel='Latitude')
    ax.set_title('a  Global reporting footprint within GHSL urban domain',loc='left',fontweight='bold')
    ax.legend(loc='lower left',frameon=False,fontsize=8,markerscale=4)
    ax.text(.5,.02,'No report ≠ no flood; not an estimate of flood hazard',transform=ax.transAxes,ha='center',fontsize=8)

    tbl=table[table.zone!='Unknown'].copy()
    ax=axs[0,1]
    x=np.arange(len(tbl))
    ax.bar(x-.18,100*tbl.share_of_universe.to_numpy(),width=.36,label=f'All urban grids (E{epoch})',color='#96a7b1')
    ax.bar(x+.18,100*tbl.share_of_reported_in_universe.to_numpy(),width=.36,label='Reported urban grids',color='#bf523b')
    ax.set_xticks(x,tbl.zone.str[0].tolist());ax.set_ylabel('% within five climate zones')
    ax.set_title('b  Climate-zone composition of the sampling frame',loc='left',fontweight='bold')
    ax.legend(frameon=False,fontsize=8)

    ax=axs[1,0]
    ax.bar(tbl.zone.str[0],100*tbl.reporting_coverage_fraction,color='#4a8c89')
    ax.set_ylabel('Urban grids with ≥1 report (%)')
    ax.set_title('c  Archive reporting coverage by climate zone',loc='left',fontweight='bold')
    ax.text(.02,.94,'Descriptive sample coverage, not flood incidence',transform=ax.transAxes,va='top',fontsize=8)

    ax=axs[1,1]
    ix=np.arange(len(by_fraction))
    ax.bar(ix,100*by_fraction.coverage.to_numpy(),color='#6478a7')
    ax.set_xticks(ix,by_fraction.urban_fraction_bin.to_list(),rotation=25,ha='right')
    ax.set_ylabel('Urban grids with ≥1 report (%)')
    ax.set_title('d  Reporting coverage versus urban-area share',loc='left',fontweight='bold')
    fig.suptitle('Results 3.1 | Urban observation footprint and climate representativeness',fontsize=14,fontweight='bold')
    fig.savefig(out/f'Figure1_reference_evidence_E{epoch}.png',dpi=300,bbox_inches='tight')
    fig.savefig(out/f'Figure1_reference_evidence_E{epoch}.svg',bbox_inches='tight')
    plt.close(fig)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--universe',type=Path,required=True)
    ap.add_argument('--koppen',type=Path,required=True)
    ap.add_argument('--reported',type=Path,default=Path(r'E:\Global_flooding\results_31\31_all_reported_grids.csv'))
    ap.add_argument('--ghsl2020-positive',type=Path,default=Path(r'E:\Global_flooding\results_31\31_ghsl2020_positive_grid_attributes.csv'))
    ap.add_argument('--out',type=Path,default=Path(r'E:\Global_flooding\results_31\reference_integrated'))
    args=ap.parse_args()
    for p in (args.universe,args.koppen,args.reported):
        if not p.is_file():raise FileNotFoundError(p)
    args.out.mkdir(parents=True,exist_ok=True)
    print('[1/6] Read corrected urban universe',flush=True)
    uni=pd.read_csv(args.universe,low_memory=False)
    need={'grid_id','grid_x','grid_y','urban_fraction_20km','ghsl_epoch'}
    if not need.issubset(uni):raise ValueError(f'Wrong/unvalidated universe file; missing {need-set(uni.columns)}. Re-run FIXED 31a.')
    if uni.grid_id.duplicated().any():raise ValueError('Duplicated universe grid IDs')
    if len(uni)>650000:raise ValueError('Implausibly many urban grids; old 1.28M raster result must NOT be used')
    epoch=int(uni.ghsl_epoch.iloc[0]);
    if uni.ghsl_epoch.nunique()!=1:raise ValueError('Mixed GHSL epochs in denominator')
    obs=pd.read_csv(args.reported,usecols=['grid_id','grid_x','grid_y','n_reported_grid_days'])
    if obs.grid_id.duplicated().any():raise ValueError('Duplicated ever-reported IDs')
    obs_ids=set(obs.grid_id)
    uni['groundsource_ever_reported']=uni.grid_id.isin(obs_ids)
    n_all=len(uni);n_rep=int(uni.groundsource_ever_reported.sum())
    print(f'[CHECK] E{epoch} urban grids={n_all:,}; ever reported={n_rep:,}; no report={n_all-n_rep:,}; reported elsewhere={len(obs)-n_rep:,}',flush=True)
    if n_rep>len(obs):raise AssertionError('Reporting count > all reported grids')
    uni['longitude'],uni['latitude']=longitude_latitude(uni)
    print('[2/6] Sample climate of each urban grid center',flush=True)
    cls,zones=sampled_climate(uni.longitude.to_numpy(),uni.latitude.to_numpy(),args.koppen)
    uni['koppen_class']=cls;uni['zone']=zones
    # Unknowns are excluded from climate-specific denominators, never assigned to class A.
    print('[CLIMATE] share with known Köppen class',float((uni.zone!='Unknown').mean()),flush=True)
    print('[3/6] Calculate coverage by climate zone and city extent',flush=True)
    t=uni.groupby('zone',dropna=False).agg(all_urban_grids=('grid_id','size'),reported_urban_grids=('groundsource_ever_reported','sum')).reset_index()
    order={'A Tropical':0,'B Arid':1,'C Temperate':2,'D Cold':3,'E Polar':4,'Unknown':5}
    t['order']=t.zone.map(order).fillna(6);t=t.sort_values('order').drop(columns='order')
    known=t[t.zone!='Unknown'].copy()
    denominator=int(known.all_urban_grids.sum());rep_denominator=int(known.reported_urban_grids.sum())
    t['reporting_coverage_fraction']=t.reported_urban_grids/t.all_urban_grids
    t['share_of_universe']=t.all_urban_grids/denominator if denominator else np.nan
    t['share_of_reported_in_universe']=t.reported_urban_grids/rep_denominator if rep_denominator else np.nan
    save(t,args.out,'31b_climate_zone_reporting_coverage.csv')
    labels=['<1%','1–5%','5–10%','10–25%','25–50%','≥50%']
    frac=uni.urban_fraction_20km.to_numpy()
    uni['urban_fraction_bin']=pd.cut(frac,[-.000001,.01,.05,.10,.25,.50,1.00001],labels=labels,right=False).astype(str)
    b=uni.groupby('urban_fraction_bin').agg(all_urban_grids=('grid_id','size'),reported_urban_grids=('groundsource_ever_reported','sum')).reindex(labels).reset_index()
    b['coverage']=b.reported_urban_grids/b.all_urban_grids
    save(b,args.out,'31b_urban_fraction_reporting_coverage.csv')
    joint=uni.groupby(['zone','urban_fraction_bin'],observed=True).agg(total_grids=('grid_id','size'),reported_grids=('groundsource_ever_reported','sum')).reset_index()
    joint['coverage']=joint.reported_grids/joint.total_grids
    save(joint,args.out,'31b_zone_by_urban_fraction.csv')
    reported=uni[uni.groundsource_ever_reported].copy()
    # Reported-only climate comparison includes reported grids OUTSIDE broad E2025 urban domain.
    obs['longitude'],obs['latitude']=longitude_latitude(obs)
    cls2,zone2=sampled_climate(obs.longitude.to_numpy(),obs.latitude.to_numpy(),args.koppen)
    obs['koppen_class']=cls2;obs['zone']=zone2
    raw=obs.groupby('zone').agg(all_reported_grids=('grid_id','size'),positive_grid_days=('n_reported_grid_days','sum')).reset_index()
    save(raw,args.out,'31b_all_reported_climate_composition.csv')
    # Optional GHSL 2020 population density is only known for EVER-REPORTED grids.
    if args.ghsl2020_positive.is_file():
        gh=pd.read_csv(args.ghsl2020_positive,usecols=lambda c:c in {'grid_id','pop_density','urban_pop_share'})
        combined=obs[['grid_id','zone']].merge(gh,on='grid_id',how='left',validate='one_to_one')
        combined['pop_density']=pd.to_numeric(combined.pop_density,errors='coerce')
        combined['population_bin_2020']=pd.cut(combined.pop_density,POP_BINS,labels=POP_NAMES,right=False)
        p=combined.groupby(['zone','population_bin_2020'],observed=True).size().rename('reported_grids').reset_index()
        save(p,args.out,'31b_ghsl2020_population_by_climate_reported_only.csv')
    print('[4/6] Write complete grid table and totals',flush=True)
    try:
        uni.to_parquet(args.out/'31b_global_urban_reporting_with_climate.parquet',index=False)
    except ImportError:
        print('[WARNING] pyarrow/fastparquet not installed; CSV is still written. Install pyarrow for Parquet.',flush=True)
    save(uni[['grid_id','grid_x','grid_y','ghsl_epoch','urban_fraction_20km','groundsource_ever_reported','koppen_class','zone']],args.out,'31b_global_urban_reporting_with_climate.csv')
    totals=pd.DataFrame([{
        'GHSL_epoch':epoch,'koppen_period':'1991-2020','urban_definition':'SMOD21+22+23+30, fractional area >= threshold',
        'urban_grid_count':n_all,'urban_reported_grid_count':n_rep,'urban_no_report_count':n_all-n_rep,
        'fraction_with_report':n_rep/n_all,'ever_reported_outside_this_urban_domain':len(obs)-n_rep,
        'unknown_climate_urban_grid_count':int((uni.zone=='Unknown').sum()),
        'CAUTION':'Ever reported in 2000-2026 does not mean physically flooded / currently urban'
    }]);save(totals,args.out,'31b_global_reference_summary.csv')
    print('[5/6] Draw reference panels',flush=True)
    plot_results(uni,t,b,args.out,epoch)
    print('[6/6] Write scientifically qualified summary',flush=True)
    paragraph=(
      f'Using a static GHSL E{epoch} SMOD broad urban definition on 20-km equal-area cells, '
      f'the study identified {n_all:,} urban cells, of which {n_rep:,} ({n_rep/n_all*100:.2f}%) '
      f'had at least one Groundsource report during 2000–2026. '
      f'The remaining {n_all-n_rep:,} cells lacked a report in this archive; they must not be interpreted as flood-free. '
      f'Köppen–Geiger zones were assigned to cell centroids using the Beck 1991–2020 climate classification. '
      f'The settlement denominator (E{epoch}) and multi-year reporting window do not coincide temporally, '
      f'and results represent observational archive coverage, not physical flood frequency or risk. '
      f'GHSL population density distributions use 2020 for reported-only grid samples and must not be attributed to the {epoch} global denominator.\n'
    )
    (args.out/'Results31_reference_interpretation_EN.txt').write_text(paragraph,encoding='utf-8')
    (args.out/'31b_provenance.json').write_text(json.dumps({'universe':str(args.universe),'koppen':str(args.koppen),'reported':str(args.reported),'epoch':epoch,'grid_count':n_all,'reported':n_rep},ensure_ascii=False,indent=2),encoding='utf-8')
    print('[DONE]',args.out,flush=True)

if __name__=='__main__':main()
