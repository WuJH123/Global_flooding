# -*- coding: utf-8 -*-
"""Results 3.1 local correction, QC, stratification and revised diagnostic figure.

Do not interpret reporting as flood incidence. Does not modify upstream files.
Uses existing 31b global urban CSV; does not require GEE or raster resampling.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import numpy as np
import pandas as pd
from pyproj import Transformer
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ZONES=['A Tropical','B Arid','C Temperate','D Cold','E Polar','Unknown']
BINS=['<1%','1–5%','5–10%','10–25%','25–50%','≥50%']
EDGES=[0,.01,.05,.10,.25,.50,1.0000001]
THRESHOLDS=[.001,.005,.01,.05,.1,.25,.5]

def save(df, path):
    df.to_csv(path,index=False,encoding='utf-8-sig')
    print('[SAVED]',path,flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=Path(r'E:\Global_flooding\results_31\reference_integrated\31b_global_urban_reporting_with_climate.csv'))
    p.add_argument('--output',type=Path,default=Path(r'E:\Global_flooding\results_31\reference_audit_31c'))
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if not args.input.is_file():raise FileNotFoundError(args.input)
    u=pd.read_csv(args.input)
    required={'grid_id','grid_x','grid_y','ghsl_epoch','urban_fraction_20km','groundsource_ever_reported','zone'}
    if not required.issubset(u):raise ValueError(f'Missing required columns: {sorted(required-set(u.columns))}')
    if u.grid_id.duplicated().any():raise ValueError('Duplicate grid_id')
    if u.ghsl_epoch.nunique()!=1:raise ValueError('Mixed GHSL epochs')
    if not u.urban_fraction_20km.between(0,1).all():raise ValueError('Invalid urban fraction')
    if u.groundsource_ever_reported.dtype != bool:
        u.groundsource_ever_reported=u.groundsource_ever_reported.astype(str).str.lower().map({'true':True,'false':False})
        if u.groundsource_ever_reported.isna().any(): raise ValueError('Unknown reporting label values')
    u['reported']=u.groundsource_ever_reported.astype(bool)
    x=(u.grid_x.to_numpy(dtype=float)+.5)*20_000
    y=(u.grid_y.to_numpy(dtype=float)+.5)*20_000
    t=Transformer.from_crs('EPSG:6933','EPSG:4326',always_xy=True)
    lon,lat=t.transform(x,y)
    good=np.isfinite(lon)&np.isfinite(lat)&(lat>=-90)&(lat<=90)&(lon>=-180)&(lon<=180)
    u['longitude']=lon;u['latitude']=lat;u['valid_center']=good
    excluded=u.loc[~good].copy()
    save(excluded[['grid_id','grid_x','grid_y','urban_fraction_20km','reported','longitude','latitude','zone']],args.output/'31c_invalid_center_grids.csv')
    print(f'[GEOMETRY] original={len(u):,}; invalid_center={len(excluded):,}; invalid_reported={excluded.reported.sum():,}')
    v=u[u.valid_center].copy()
    v['urban_fraction_bin']=pd.cut(v.urban_fraction_20km,EDGES,labels=BINS,include_lowest=True,right=False)
    if v.urban_fraction_bin.isna().any():raise RuntimeError('Urban fraction binning error')
    v['zone']=v.zone.where(v.zone.isin(ZONES),'Unknown')
    total=len(v); reported=int(v.reported.sum()); unknown=(v.zone=='Unknown')
    overview=pd.DataFrame([{
       'original_urban_grid_count':len(u),'removed_invalid_center_grids':len(excluded),
       'removed_invalid_center_reported':int(excluded.reported.sum()),
       'corrected_urban_grid_count':total,'corrected_reported_grid_count':reported,
       'corrected_unreported_grid_count':total-reported,
       'corrected_ever_reported_fraction':reported/total,
       'known_climate_grids':int((~unknown).sum()),
       'unknown_climate_grids':int(unknown.sum()),
       'unknown_climate_reported':int(v.loc[unknown,'reported'].sum()),
       'GHSL_epoch':int(v.ghsl_epoch.iloc[0]),
       'definition':'GHSL SMOD 21/22/23/30 in >=0.1% of a 20km cell (E2025 snapshot)'
    }]);save(overview,args.output/'31c_corrected_global_summary.csv')
    z=v.groupby('zone',observed=True).agg(n_urban=('grid_id','size'),n_reported=('reported','sum')).reindex(ZONES,fill_value=0).reset_index()
    z['coverage_fraction']=z.n_reported/z.n_urban.replace(0,np.nan)
    z['share_of_all_urban']=z.n_urban/total;z['share_of_all_reported']=z.n_reported/reported
    known=z[z.zone!='Unknown'].copy()
    z['share_within_known_urban']=np.where(z.zone!='Unknown',z.n_urban/known.n_urban.sum(),np.nan)
    z['share_within_known_reported']=np.where(z.zone!='Unknown',z.n_reported/known.n_reported.sum(),np.nan)
    z['composition_difference_pp']=100*(z.share_within_known_reported-z.share_within_known_urban)
    save(z,args.output/'31c_corrected_climate_coverage.csv')
    urban=v.groupby('urban_fraction_bin',observed=False).agg(n_urban=('grid_id','size'),n_reported=('reported','sum')).reindex(BINS).reset_index()
    urban['coverage_fraction']=urban.n_reported/urban.n_urban
    save(urban,args.output/'31c_corrected_urban_fraction_coverage.csv')
    threshold=[]
    for f in THRESHOLDS:
        s=v[v.urban_fraction_20km>=f]
        threshold.append({'min_urban_fraction':f,'n_urban':len(s),'n_reported':int(s.reported.sum()),'n_no_report':int((~s.reported).sum()),'coverage_fraction':s.reported.mean()})
    th=pd.DataFrame(threshold);save(th,args.output/'31c_cumulative_threshold_sensitivity.csv')
    joint=v[v.zone!='Unknown'].groupby(['zone','urban_fraction_bin'],observed=False).agg(n_urban=('grid_id','size'),n_reported=('reported','sum')).reset_index()
    joint['coverage_fraction']=joint.n_reported/joint.n_urban.replace(0,np.nan)
    save(joint,args.output/'31c_zone_by_urban_fraction.csv')
    # Direct standardization across shared classes A-D; E has structurally empty
    # >=50% bin and tiny high-urban strata, so only report E as unadjusted.
    ad=v[v.zone.isin(ZONES[:4])]
    weights=ad.urban_fraction_bin.value_counts(normalize=True).reindex(BINS,fill_value=0)
    standardized=[]
    for zone in ZONES[:4]:
        t=joint[joint.zone==zone].set_index('urban_fraction_bin').reindex(BINS)
        assert (t.n_urban>0).all(),f'Cannot standardize {zone} due to empty bin'
        std=float((t.coverage_fraction.to_numpy()*weights.to_numpy()).sum())
        raw=float(v.loc[v.zone==zone,'reported'].mean())
        standardized.append({'zone':zone,'raw_coverage_fraction':raw,'urban_fraction_standardized_coverage':std,'standardized_minus_raw_pp':100*(std-raw)})
    std=pd.DataFrame(standardized)
    save(pd.DataFrame({'urban_fraction_bin':BINS,'reference_weight':weights.to_numpy()}),args.output/'31c_standardization_weights.csv')
    save(std,args.output/'31c_urban_adjusted_climate_comparison.csv')
    try:
        v.to_parquet(args.output/'31c_valid_global_urban_reporting.parquet',index=False)
    except ImportError:
        save(v,args.output/'31c_valid_global_urban_reporting.csv')
    # Plot a compact supplemental data-quality and coverage figure.
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(2,2,figsize=(12.2,8.4),layout='constrained')
    ax=axes[0,0]
    nr=v[~v.reported];rr=v[v.reported]
    # Rasterized overview instead of tens of thousands of vector markers.
    xe=np.linspace(-180,180,1441);ye=np.linspace(-62,82,577)
    hn=np.histogram2d(nr.latitude,nr.longitude,bins=(ye,xe))[0]
    hr=np.histogram2d(rr.latitude,rr.longitude,bins=(ye,xe))[0]
    rgba=np.ones((*hn.shape,4),dtype=np.float32)
    rgba[:,:,:3]=[1,1,1];rgba[:,:,3]=0
    rgba[hn>0,:3]=[.66,.71,.74];rgba[hn>0,3]=.60
    rgba[hr>0,:3]=[.77,.33,.25];rgba[hr>0,3]=.86
    ax.imshow(rgba,extent=[-180,180,-62,82],origin='lower',interpolation='nearest',aspect='auto')
    ax.set(xlim=(-180,180),ylim=(-62,82),xlabel='Longitude',ylabel='Latitude')
    ax.set_title('a  Observed urban grid footprint (GHSL E2025)',loc='left',fontweight='bold')
    ax.text(.01,.03,f'Urban cells: {len(v):,}; with report: {reported:,}', transform=ax.transAxes,fontsize=8,color='#3b4b55',bbox=dict(boxstyle='round',facecolor='white',alpha=.8,edgecolor='none'))
    ax=axes[0,1]
    zz=z[z.zone!='Unknown']
    xx=np.arange(len(zz));ax.bar(xx-.17,100*zz.share_within_known_urban,width=.34,color='#96a7b1',label='All urban grids')
    ax.bar(xx+.17,100*zz.share_within_known_reported,width=.34,color='#c35641',label='Grids with report')
    ax.set_xticks(xx,['A','B','C','D','E']);ax.set_ylabel('Share within known climate zones (%)')
    ax.set_title('b  Climate composition (known zones only)',loc='left',fontweight='bold');ax.legend(fontsize=8,frameon=False)
    ax=axes[1,0]
    ax.bar(np.arange(len(urban)),urban.coverage_fraction.to_numpy()*100,color='#608e8a')
    ax.set_xticks(np.arange(len(urban)),BINS,rotation=20);ax.set_ylim(0,100)
    ax.set_ylabel('Grids with ≥1 archive report (%)');ax.set_xlabel('Fraction of 20-km grid classified urban')
    ax.set_title('c  Archive footprint vs urban-area extent',loc='left',fontweight='bold')
    ax=axes[1,1]
    xx=np.arange(len(std));ax.bar(xx-.16,100*std.raw_coverage_fraction,width=.32,color='#7b9ca9',label='Raw')
    ax.bar(xx+.16,100*std.urban_fraction_standardized_coverage,width=.32,color='#d2a36e',label='Urban-fraction standardized')
    ax.set_xticks(xx,['A','B','C','D']);ax.set_ylim(0,100)
    ax.set_ylabel('Grids with ≥1 archive report (%)')
    ax.set_title('d  Climate coverage adjusted for urban extent',loc='left',fontweight='bold');ax.legend(fontsize=8,frameon=False)
    fig.suptitle('Results 3.1 — Corrected observational reference-domain diagnostics',fontsize=14,fontweight='bold')
    fig.savefig(args.output/'Figure1_31c_corrected_diagnostics.png',dpi=300,bbox_inches='tight')
    fig.savefig(args.output/'Figure1_31c_corrected_diagnostics.svg',bbox_inches='tight')
    plt.close(fig)
    metadata={'input':str(args.input),'code':'31c_observation_reference_audit.py','epoch':int(v.ghsl_epoch.iloc[0]),'threshold':0.001,
              'notes':['No-report is not no-flood','E2025 denominator spans all reporting years','Koppen assigned to centroid','Unknown climates retained for total denominator','Climatic zone standardization A-D only']}
    (args.output/'31c_methods_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print('\n[RESULT] Corrected urban=',total,'reported=',reported,'coverage=',f'{reported/total:.2%}')
    print('[RESULT] Urban-fraction-standardized A-D coverage:\n',std.to_string(index=False))
    print('[DONE]',args.output)

if __name__=='__main__':main()
