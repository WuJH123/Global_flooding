"""Results 3.1: Global evidence and observational representativeness.

Entirely LOCAL. Reuses Stage 1 / 3 / 6 and GHSL files; NO Earth Engine.
Creates tables, a six-panel publication-style Figure 1 (PNG+SVG), a
cautiously worded Results 3.1 draft, and manual validation worksheet.

Honest denominators:
- Without --universe-csv, the 94,738 grids are ONLY grids EVER reported
  by Groundsource. No globally unreported urban-grid denominator is possible.
- With --universe-csv generated from a full global GHS-SMOD 2020 raster,
  a BROAD 2020 urban-grid reporting-coverage statistic becomes available.
  The 2020 broad SMOD definition is not the time-varying M3-50 definition.
- area_km2 is a REPORT POLYGON footprint, NOT observed inundated area
  or validated spatial localization error.
- Manual validation statistics are produced only from completed audit forms.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import duckdb
from pyproj import Transformer
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

POP_BINS = [-np.inf,100,300,1000,3000,10000,np.inf]
POP_LABELS = ['<100','100–300','300–1,000','1,000–3,000','3,000–10,000','≥10,000']


def qp(p:Path) -> str:
    return p.resolve().as_posix().replace("'", "''")

def sq(s): return "'"+str(s).replace("'", "''")+"'"

def csv_save(frame, folder, name):
    dest=folder/name
    frame.to_csv(dest,index=False,encoding='utf-8-sig')
    print('[SAVED]',dest,flush=True)
    return dest

def cols_of(con,view):
    return {x[0] for x in con.execute(f'DESCRIBE SELECT * FROM {view}').fetchall()}

def fmt(n): return f'{int(n):,}'

def read_grid_ids(con,gridday):
    return con.execute(f"""
     SELECT grid_id,MIN(grid_x)::INTEGER AS grid_x,
        MIN(grid_y)::INTEGER AS grid_y, COUNT(*) AS n_reported_grid_days,
        MIN(CAST(event_date AS DATE)) AS first_report_date,
        MAX(CAST(event_date AS DATE)) AS last_report_date
     FROM read_parquet('{qp(gridday)}') GROUP BY grid_id
    """).fetchdf()

def with_lonlat(df):
    if len(df)==0:
        df=df.copy();df['longitude']=[];df['latitude']=[];return df
    tf=Transformer.from_crs('EPSG:6933','EPSG:4326',always_xy=True)
    d=df.copy()
    d['longitude'],d['latitude']=tf.transform((d.grid_x.to_numpy(float)+.5)*20000,
                                           (d.grid_y.to_numpy(float)+.5)*20000)
    return d

def climate_at_centers(grid_df,path):
    import rasterio
    from rasterio.warp import transform as proj_transform
    from itertools import chain
    with rasterio.open(path) as ds:
        xs=(grid_df.grid_x.to_numpy(float)+.5)*20000
        ys=(grid_df.grid_y.to_numpy(float)+.5)*20000
        cx,cy=proj_transform('EPSG:6933',ds.crs,xs.tolist(),ys.tolist())
        vals=np.empty(len(xs),dtype='int16')
        for i, pix in enumerate(ds.sample(zip(cx,cy),indexes=1,masked=True)):
            value=pix[0]
            vals[i] = -1 if np.ma.is_masked(value) else int(value)
    cat=np.full(len(vals),'Unknown',dtype=object)
    cat[(vals>=1)&(vals<=3)]='A Tropical'
    cat[(vals>=4)&(vals<=7)]='B Arid'
    cat[(vals>=8)&(vals<=16)]='C Temperate'
    cat[(vals>=17)&(vals<=28)]='D Cold'
    cat[(vals>=29)&(vals<=30)]='E Polar'
    return cat

def fmt_percent(v):return f'{v*100:.1f}%'

def make_manual_sheet(con, raw_path, outdir, n_per_bin):
    if raw_path is None or not raw_path.exists():
        print('[MANUAL] Stage1 records not found; no review worksheet generated.');return None,None
    con.execute(f"CREATE OR REPLACE VIEW stage1 AS SELECT * FROM read_parquet('{qp(raw_path)}')")
    columns=cols_of(con,'stage1')
    if not {'uuid','start_date','area_km2'} <= columns:
        print('[MANUAL] Missing uuid, start_date or area_km2; manual template skipped.');return None,None
    coord=['grid_id','grid_x','grid_y']
    optional=', '+', '.join(c for c in coord if c in columns) if any(c in columns for c in coord) else ''
    query=f"""
      WITH categorized AS (
       SELECT uuid, CAST(start_date AS DATE) AS reported_start_date,
          TRY_CAST(area_km2 AS DOUBLE) AS reported_polygon_area_km2
          {optional},
          CASE WHEN CAST(start_date AS DATE)<DATE '2010-01-01' THEN '2000–2009'
               WHEN CAST(start_date AS DATE)<DATE '2020-01-01' THEN '2010–2019'
               ELSE '2020–2026' END AS period_bin,
          CASE WHEN area_km2<0.1 THEN '<0.1 km2'
               WHEN area_km2<5 THEN '0.1–5 km2'
               WHEN area_km2<100 THEN '5–100 km2'
               ELSE '>=100 km2' END AS footprint_bin
       FROM stage1 WHERE uuid IS NOT NULL
      ), ranked AS (
       SELECT *,ROW_NUMBER() OVER(
        PARTITION BY period_bin,footprint_bin ORDER BY HASH(CAST(uuid AS VARCHAR))) AS rn
       FROM categorized
      )
      SELECT * EXCLUDE(rn) FROM ranked WHERE rn <= {int(n_per_bin)}
      ORDER BY period_bin,footprint_bin,uuid
    """
    df=con.execute(query).fetchdf()
    for c in ['source_article_url','reviewer_id','reviewed_yes_no',
              'date_correct_yes_no','location_correct_yes_no',
              'urban_flood_type_confirmed_yes_no','event_type',
              'evidence_date','evidence_location','review_notes']:
        df[c]=''
    dest=csv_save(df,outdir,'31_manual_validation_template.csv')
    return df,dest

def existing_manual_review(path,outdir):
    if not path or not path.exists():
        (outdir/'31_manual_validation_status.txt').write_text(
            'No completed manual validation was supplied. The template is an UNREVIEWED sample; '
            'no measured accuracy can be inferred. Groundsource creator-wide validation (60% strict, '
            '82% practically usable) is an EXTERNAL figure, not this project’s estimate.\n',encoding='utf-8')
        return None
    df=pd.read_csv(path,dtype=str).fillna('')
    required={'reviewed_yes_no','date_correct_yes_no','location_correct_yes_no'}
    if not required.issubset(df):
        raise ValueError(f'Manual review file needs columns: {required}')
    f=df[df.reviewed_yes_no.str.strip().str.lower().isin(['yes','y','1','true'])].copy()
    if len(f)==0:
        print('[MANUAL] No completed reviews; no accuracy estimates.');return None
    records=[]
    for name in ['date_correct_yes_no','location_correct_yes_no','urban_flood_type_confirmed_yes_no']:
        if name not in f:continue
        answers=f[name].str.strip().str.lower()
        valid=answers.isin(['yes','y','1','true','no','n','0','false'])
        correct=answers.isin(['yes','y','1','true'])
        records.append({'dimension':name,'reviewed_with_answer':int(valid.sum()),
                        'proportion_correct':float(correct[valid].mean()) if valid.any() else np.nan})
    out=pd.DataFrame(records)
    csv_save(out,outdir,'31_manual_review_accuracy.csv')
    return out

def _plot(ax,title):
    ax.set_title(title,loc='left',fontweight='bold',fontsize=10,pad=10)
    ax.grid(alpha=.13,zorder=0)
    ax.tick_params(labelsize=8)

def make_figure(outdir,obs,m3,annual,defs,ghsl2020,universe,climate_obs,climate_uni,area_stats,duration_stats,world_shapefile=None):
    fig,axs=plt.subplots(3,2,figsize=(14,13),constrained_layout=True)
    a,b,c,d,e,f=axs.ravel()
    # A map: never conflate reported sample frame with complete world urban denominator.
    _plot(a,'a  Global 20-km urban reporting coverage')
    if world_shapefile is not None:
        try:
            import geopandas as gpd
            land = gpd.read_file(world_shapefile).to_crs('EPSG:4326')
            land.plot(ax=a, color='#eceeed', edgecolor='#b9bdb9', linewidth=.28, zorder=0)
        except Exception as ex:
            print('[WARNING] Could not render optional land basemap:',ex,flush=True)
    if universe is not None:
        u=universe
        a.scatter(u.longitude,u.latitude,s=.15,alpha=.20,c='#8d939d',rasterized=True,label='Urban grids, GHSL 2020')
        obs_grid=u[u.grid_id.isin(set(obs.grid_id))]
        a.scatter(obs_grid.longitude,obs_grid.latitude,s=.35,alpha=.52,c='#b4472e',rasterized=True,label='Grid with any report')
        a.legend(loc='lower left',fontsize=7,markerscale=5,frameon=False)
    else:
        a.scatter(obs.longitude,obs.latitude,s=.25,alpha=.45,c='#b4472e',rasterized=True,label='Reported grids only')
        a.legend(loc='lower left',fontsize=7,markerscale=5,frameon=False)
        a.text(.5,.04,'GLOBAL ZERO-REPORT DENOMINATOR NOT PROVIDED',
               transform=a.transAxes,ha='center',fontsize=8,color='#a21e1e',weight='bold')
    a.set_xlim(-180,180);a.set_ylim(-70,83);a.set_xticks([-180,-90,0,90,180]);a.set_yticks([-60,-30,0,30,60]);a.set_xlabel('Longitude');a.set_ylabel('Latitude')
    # B report counts and episode counts 2000-2025 full calendar years
    _plot(b,'b  Annual report coverage (not flood incidence)')
    df=annual[annual.year<=2025]
    b.plot(df.year,df.grid_days,label='Positive grid-days',color='#bf4c37',lw=2)
    b.plot(df.year,df.episodes,label='M3 grid episodes',color='#2d677c',lw=2)
    b.set_yscale('symlog',linthresh=500);b.set_xlabel('Year');b.set_ylabel('Count (symlog)');b.legend(frameon=False,fontsize=8)
    # C sample among reported (when density known)
    _plot(c,'c  Observed-grid composition by GHSL 2020 population density')
    if ghsl2020 is not None and 'pop_density' in ghsl2020:
        density=pd.to_numeric(ghsl2020.pop_density,errors='coerce')
        dist=pd.cut(density,bins=POP_BINS,labels=POP_LABELS,right=False)
        counts=dist.value_counts().reindex(POP_LABELS,fill_value=0)
        c.bar(range(len(counts)),counts.values,color='#607d99')
        c.set_xticks(range(len(counts)),labels=counts.index,rotation=22,ha='right',fontsize=8)
        c.set_ylabel('Grids with any report')
        c.text(.02,.97,'Positive-report grids only\nNOT a population-adjusted reporting rate',transform=c.transAxes,va='top',fontsize=7,color='#6a3b3b')
    else:
        c.text(.5,.5,'GHSL 2020 grid CSV not found',ha='center',va='center',transform=c.transAxes)
    # D climate zones: observed and urban universe, if available
    _plot(d,'d  Köppen–Geiger climate-zone composition')
    names=['A Tropical','B Arid','C Temperate','D Cold','E Polar']
    if climate_obs is not None:
        vals=pd.Series(climate_obs).value_counts().reindex(names,fill_value=0)
        d.bar(np.arange(len(names))-.19,100*vals.values/len(climate_obs),width=.38,color='#a34d38',label='Reported grids')
        if climate_uni is not None:
            uv=pd.Series(climate_uni).value_counts().reindex(names,fill_value=0)
            d.bar(np.arange(len(names))+.19,100*uv.values/len(climate_uni),width=.38,color='#999ea9',label='All urban grids (2020)')
        d.set_xticks(range(len(names)),[n[0] for n in names]);d.set_ylabel('% of grids');d.legend(frameon=False,fontsize=8)
        d.text(.02,.95,'Centre-point climate classification',transform=d.transAxes,va='top',fontsize=7)
    else:
        d.text(.5,.5,'Optional Köppen–Geiger GeoTIFF not provided',ha='center',va='center',transform=d.transAxes)
        d.set_xticks([])
    # E distribution: do not label footprint polygon area as inundation area
    _plot(e,'e  Report polygon footprint and episode duration')
    if area_stats is not None and duration_stats is not None:
        x=['p50','p90','p99']
        vals1=np.array([area_stats[f'area_{s}'] for s in x],float)
        vals2=np.array([duration_stats[f'duration_{s}'] for s in x],float)
        ix=np.arange(3)
        e.bar(ix-.19, vals1+.0001, width=.38,color='#805d86',label='Source polygon footprint (km²)')
        e.bar(ix+.19, vals2+.0001,width=.38,color='#d6aa61',label='Grid episode duration (days)')
        e.set_yscale('log');e.set_xticks(ix,['P50','P90','P99']);e.legend(frameon=False,fontsize=7)
        e.set_ylabel('Value (log scale; DIFFERENT UNITS)')
    else:
        e.text(.5,.5,'Stage1 or episode data unavailable',ha='center',va='center',transform=e.transAxes)
    # F thresholds
    _plot(f,'f  Sensitivity to urban inclusion definitions')
    if defs is not None and {'definition','share_of_all_grid_days'} <= set(defs):
        plot_defs=defs.copy()
        plot_defs['share_of_all_grid_days']=pd.to_numeric(plot_defs.share_of_all_grid_days,errors='coerce')
        f.barh(np.arange(len(plot_defs)),100*plot_defs.share_of_all_grid_days.to_numpy(),color='#4f8b7b')
        f.set_yticks(np.arange(len(plot_defs)),labels=plot_defs.definition,fontsize=7)
        f.set_xlabel('% of positive grid-days retained');f.set_xlim(0,100);f.invert_yaxis()
    else:
        f.text(.5,.5,'Urban sensitivity file not found',ha='center',va='center',transform=f.transAxes)
    fig.suptitle('Figure 1 | Observational evidence and representativeness of reported urban flooding',fontweight='bold',fontsize=14)
    png=outdir/'Figure1_observational_coverage.png';svg=outdir/'Figure1_observational_coverage.svg'
    fig.savefig(png,dpi=320,bbox_inches='tight',facecolor='white')
    fig.savefig(svg,bbox_inches='tight',facecolor='white')
    plt.close(fig)
    print('[FIGURE]',png,'and',svg,flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,default=Path(r'E:\Global_flooding'))
    ap.add_argument('--processed',type=Path,default=Path(r'E:\数据集\Groundsource_全球城市内涝点位\processed'))
    ap.add_argument('--ghsl-root',type=Path,default=None)
    ap.add_argument('--urban-universe-csv',type=Path,default=None,
                    help='Independent BROAD 2020 urban universe from 31a, not M3')
    ap.add_argument('--koppen-tif',type=Path,default=None,
                    help='Beck et al 2023 1991–2020 (not future) classification GeoTIFF')
    ap.add_argument('--manual-reviewed-csv',type=Path,default=None)
    ap.add_argument('--world-shapefile',type=Path,default=None,help='Optional local Natural Earth land/country geometry for map basemap')
    ap.add_argument('--manual-per-bin',type=int,default=25)
    ap.add_argument('--threads',type=int,default=8)
    args=ap.parse_args()
    out=args.base/'results_31';out.mkdir(parents=True,exist_ok=True)
    ghsl_root=args.ghsl_root or args.base/'Groundsource_all'
    stage1=args.processed/'groundsource_stage1_records_20km.parquet'
    gridday=args.processed/'groundsource_gridday_20km.parquet'
    episodes=args.base/'episode_control'/'flood_episodes_m3.parquet'
    m3=args.base/'processed_local'/'urban_catalogue_m3_pop100_share50.parquet'
    for name,path in [('positive grid-day',gridday),('M3 flood episodes',episodes)]:
        if not path.exists():raise FileNotFoundError(f'{name}: {path}')
    con=duckdb.connect(str(out/'31_work.duckdb'))
    con.execute(f'PRAGMA threads={max(1,args.threads)}')
    con.execute("PRAGMA memory_limit='12GB'")
    print('[1/8] Audit source, grid-day and episodes',flush=True)
    grid_index=with_lonlat(read_grid_ids(con,gridday))
    csv_save(grid_index,out,'31_all_reported_grids.csv')
    annual_days=con.execute(f"""
     SELECT YEAR(CAST(event_date AS DATE))::INTEGER AS year,
            COUNT(*) AS grid_days,COUNT(DISTINCT grid_id) AS positive_grids
     FROM read_parquet('{qp(gridday)}') GROUP BY 1 ORDER BY 1
    """).fetchdf()
    epcols=cols_of(con,f"read_parquet('{qp(episodes)}')") if False else None
    con.execute(f"CREATE OR REPLACE VIEW ep AS SELECT * FROM read_parquet('{qp(episodes)}')")
    epcols=cols_of(con,'ep')
    if 'episode_start' not in epcols:raise ValueError(f'Episode file lacks episode_start: {epcols}')
    eps=con.execute("""
       SELECT YEAR(CAST(episode_start AS DATE))::INTEGER AS year,
              COUNT(*) AS episodes,COUNT(DISTINCT grid_id) AS episode_grids
       FROM ep GROUP BY 1 ORDER BY 1
    """).fetchdf()
    annual=annual_days.merge(eps,on='year',how='outer').fillna(0).sort_values('year')
    if stage1.exists():
        con.execute(f"CREATE OR REPLACE VIEW stage1 AS SELECT * FROM read_parquet('{qp(stage1)}')")
        annual_rec=con.execute("""
          SELECT YEAR(CAST(start_date AS DATE))::INTEGER AS year, COUNT(*) AS raw_records
          FROM stage1 GROUP BY 1 ORDER BY 1
        """).fetchdf()
        annual=annual.merge(annual_rec,on='year',how='outer').fillna(0).sort_values('year')
    annual['partial_year']=annual.year.eq(2026)
    csv_save(annual,out,'31_annual_observation_counts.csv')
    counts={'raw_records':int(con.execute('SELECT COUNT(*) FROM stage1').fetchone()[0]) if stage1.exists() else None,
            'all_positive_grid_days':int(annual_days.grid_days.sum()),
            'all_reported_grid_count':len(grid_index),
            'm3_episodes':int(eps.episodes.sum())}
    print('[COUNTS]',counts,flush=True)
    # M3 positive grid stats (not necessarily complete urban universe)
    if m3.exists():
        m3v=f"read_parquet('{qp(m3)}')"
        mc=con.execute(f"SELECT COUNT(*),COUNT(DISTINCT grid_id) FROM {m3v}").fetchone()
        counts.update(m3_positive_grid_days=int(mc[0]),m3_positive_grids=int(mc[1]))
        m3grids=con.execute(f"SELECT DISTINCT grid_id FROM {m3v}").fetchdf()
    else:
        m3grids=con.execute('SELECT DISTINCT grid_id FROM ep').fetchdf()
        counts['m3_positive_grid_days']=None;counts['m3_positive_grids']=len(m3grids)
    print('[M3]',counts.get('m3_positive_grid_days'),counts['m3_positive_grids'],flush=True)
    csv_save(pd.DataFrame([counts]),out,'31_core_qc.csv')
    # Episode duration (raw episode durations, not independent storm count)
    durcol=next((c for c in ['duration_days','episode_duration_days'] if c in epcols),None)
    if durcol:
        r=con.execute(f"""
            SELECT MIN({durcol}) AS duration_min,MAX({durcol}) AS duration_max,
              APPROX_QUANTILE({durcol},.5) AS duration_p50,
              APPROX_QUANTILE({durcol},.9) AS duration_p90,
              APPROX_QUANTILE({durcol},.99) AS duration_p99
            FROM ep
        """).fetchdf(); dur_stats=r.iloc[0].to_dict()
    else:
        dur_stats=None
    # source polygon area is a source-location polygon footprint, NOT inundation
    if stage1.exists() and 'area_km2' in cols_of(con,'stage1'):
        r=con.execute("""
           SELECT MIN(area_km2) AS area_min,MAX(area_km2) AS area_max,
             APPROX_QUANTILE(area_km2,.5) AS area_p50,
             APPROX_QUANTILE(area_km2,.9) AS area_p90,
             APPROX_QUANTILE(area_km2,.99) AS area_p99
           FROM stage1 WHERE area_km2 > 0
        """).fetchdf();area_stats=r.iloc[0].to_dict()
    else:area_stats=None
    csv_save(pd.DataFrame([area_stats]) if area_stats else pd.DataFrame(),out,'31_report_polygon_area_summary.csv')
    csv_save(pd.DataFrame([dur_stats]) if dur_stats else pd.DataFrame(),out,'31_grid_episode_duration_summary.csv')
    # 2 Load previously vetted sensitivity table (does not require GHSL reload)
    print('[2/8] Urban definition sensitivity',flush=True)
    candidates=[args.base/'processed_local'/'urban_definition_sensitivity.csv',
                args.base/'urban_definition_sensitivity.csv',args.base/'Groundsource_all'/'urban_definition_sensitivity.csv']
    defs=None
    for path in candidates:
        if path.exists():defs=pd.read_csv(path,encoding='utf-8-sig');break
    # 3 Read GHSL positive grids; only an observed-grid sample frame
    print('[3/8] GHSL positive-report grid attributes',flush=True)
    ghsl_files=sorted(ghsl_root.rglob('groundsource_ghsl_*_chunk_*.csv')) if ghsl_root.exists() else []
    ghsl2020=None
    if ghsl_files:
        choices=[p for p in ghsl_files if 'groundsource_ghsl_2020_chunk_' in p.name.lower()]
        if not choices:print('[WARNING] GHSL 2020 CSV chunks not found')
        else:
            pathlist='['+','.join(sq(qp(p)) for p in choices)+']'
            con.execute(f"CREATE OR REPLACE VIEW ghsl20 AS SELECT * FROM read_csv_auto({pathlist}, header=true, union_by_name=true)")
            cs=cols_of(con,'ghsl20')
            if not {'grid_id','pop_density','urban_pop_share'}<=cs:
                print('[WARNING] GHSL columns not found; got',sorted(cs))
            else:
                cols=['grid_id','TRY_CAST(pop_density AS DOUBLE) AS pop_density',
                      'TRY_CAST(urban_pop_share AS DOUBLE) AS urban_pop_share']
                if 'urban_area_fraction' in cs:cols.append('TRY_CAST(urban_area_fraction AS DOUBLE) AS urban_area_fraction')
                ghsl2020=con.execute('SELECT '+','.join(cols)+' FROM ghsl20').fetchdf()
                if ghsl2020.grid_id.duplicated().any():
                    raise RuntimeError('GHSL 2020 CSV duplicates grid_id: do not trust population distributions')
                ghsl2020=ghsl2020[ghsl2020.grid_id.isin(set(grid_index.grid_id))].copy()
                csv_save(ghsl2020,out,'31_ghsl2020_positive_grid_attributes.csv')
                ghsl2020['pop_bin_2020']=pd.cut(ghsl2020.pop_density,POP_BINS,labels=POP_LABELS,right=False)
                popstats=ghsl2020.groupby('pop_bin_2020',observed=True).agg(n_reported_grids=('grid_id','size')).reset_index()
                csv_save(popstats,out,'31_population_composition_reported_only.csv')
                shares=pd.cut(ghsl2020.urban_pop_share,[-.00001,.25,.5,.75,1.000001],labels=['<25%','25–50%','50–75%','≥75%'])
                csv_save(pd.DataFrame({'share_bin':shares}).value_counts().rename('n_reported_grids').reset_index(),out,'31_urban_share_composition_reported_only.csv')
    else:print('[WARNING] GHSL CSV chunks unavailable. Definition sensitivity requires supplied summary.')
    if defs is not None:
        csv_save(defs,out,'31_urban_definition_sensitivity.csv')
        print('[DEFINITIONS]',len(defs),'thresholds',flush=True)
    # 4 Independent all-urban denominator
    print('[4/8] Compare to independent global urban universe (if supplied)',flush=True)
    uni=None;coverage={}
    if args.urban_universe_csv:
        if not args.urban_universe_csv.is_file():raise FileNotFoundError(args.urban_universe_csv)
        uni=pd.read_csv(args.urban_universe_csv,low_memory=False)
        if 'grid_id' not in uni and {'grid_x','grid_y'}<=set(uni):
            uni['grid_id']=uni.grid_x.astype(int).astype(str)+'_'+uni.grid_y.astype(int).astype(str)
        if not {'grid_id','grid_x','grid_y'}<=set(uni):raise ValueError('Urban universe must include grid_id,grid_x,grid_y')
        if uni.grid_id.duplicated().any():raise RuntimeError('Duplicate urban universe grid IDs')
        uni=with_lonlat(uni)
        reports=set(grid_index.grid_id)
        uni['groundsource_ever_reported']=uni.grid_id.isin(reports)
        n_report=int(uni.groundsource_ever_reported.sum())
        coverage={'universe_name':'2020 broad urban SMOD any urban 1km pixel',
                  'all_urban_grids_2020':len(uni),'ever_reported_grids_2000_2026':n_report,
                  'without_report_in_archive':len(uni)-n_report,
                  'ever_reported_share':n_report/len(uni) if len(uni) else np.nan}
        csv_save(pd.DataFrame([coverage]),out,'31_global_urban_reporting_coverage.csv')
        csv_save(uni[['grid_id','grid_x','grid_y','groundsource_ever_reported']],out,'31_global_urban_coverage_grid.csv')
        print('[GLOBAL URBAN DENOMINATOR]',coverage,flush=True)
    else:
        (out/'31_global_urban_denominator_WARNING.txt').write_text(
            'Not available: all GHSL chunks used here cover ONLY grids with a Groundsource report. '
            'A full worldwide 2020 SMOD urban raster or independently constructed global urban-grid universe '
            'is needed to quantify urban grids with ZERO Groundsource reports. '
            'Do not call 94,738 the number of all urban grids.\n',encoding='utf-8')
        print('[WARNING] No universe => cannot quantify global never-reported urban grids.')
    # 5 optional Köppen zones
    print('[5/8] Köppen–Geiger categories (optional)',flush=True)
    climates_obs=None;climates_uni=None
    if args.koppen_tif:
        if not args.koppen_tif.is_file():raise FileNotFoundError(args.koppen_tif)
        climates_obs=climate_at_centers(grid_index,args.koppen_tif)
        grid_index['climate_zone']=climates_obs
        csv_save(grid_index[['grid_id','climate_zone']],out,'31_reported_grid_climate_zone.csv')
        df=grid_index.groupby('climate_zone').agg(reported_grids=('grid_id','size'),grid_days=('n_reported_grid_days','sum')).reset_index()
        csv_save(df,out,'31_climate_composition_observed.csv')
        if uni is not None:
            climates_uni=climate_at_centers(uni,args.koppen_tif)
            uni['climate_zone']=climates_uni
            table=uni.groupby('climate_zone').agg(all_urban_grids=('grid_id','size'),grids_with_report=('groundsource_ever_reported','sum')).reset_index()
            table['reporting_coverage_fraction']=table.grids_with_report/table.all_urban_grids
            csv_save(table,out,'31_climate_zone_reporting_coverage.csv')
    # 6 sampling worksheet, not synthetic validation
    print('[6/8] Create manual review design',flush=True)
    _,template=make_manual_sheet(con,stage1 if stage1.exists() else None,out,args.manual_per_bin)
    manual=existing_manual_review(args.manual_reviewed_csv,out)
    # 7 figure
    print('[7/8] Build publication-style descriptive figure',flush=True)
    make_figure(out,grid_index,m3grids,annual,defs,ghsl2020,uni,climates_obs,climates_uni,area_stats,dur_stats,
                args.world_shapefile)
    # 8 conservative results
    print('[8/8] Write evidence-based draft',flush=True)
    lines=[
      '# Results 3.1 — Global evidence and observational representativeness',
      '',
      '> Draft based on local computed results. This is an observational coverage analysis, not a flood-frequency trend.',
      '',
      f'The Groundsource audit comprises **{fmt(counts["raw_records"])} location records**.' if counts['raw_records'] is not None else 'Raw record count unavailable.',
      f'After expansion and aggregation, **{fmt(counts["all_positive_grid_days"])} reported grid-days** were identified over **{fmt(counts["all_reported_grid_count"])} distinct 20-km cells**. '
      'These cells are report-positive locations and are not the full global urban population of grid cells.',
      f'Under the population-density and urban-share (M3-50) criterion, the retained set contains **{fmt(counts["m3_positive_grid_days"])} reported grid-days** '
      f'across **{fmt(counts["m3_positive_grids"])} urban-positive grid cells**.' if counts.get('m3_positive_grid_days') is not None else 'The M3 reported grid selection is derived from the episode file.',
      f'Adjacent positive dates in the same cell yielded **{fmt(counts["m3_episodes"])} grid-level episodes**. '
      'The episodes are not independent meteorological storms or unique regional flood disasters.',
      'Observed counts increase over the historical period, but these should not be interpreted as a change in the physical frequency of floods because digitized-news coverage and ascertainment are time-heterogeneous.',
      '',
      '## Urban-grid reporting denominator',
      (f'An independent 2020 GHSL SMOD urban universe contains **{fmt(coverage["all_urban_grids_2020"])} broad-definition urban cells**, '
       f'of which **{fmt(coverage["ever_reported_grids_2000_2026"])}** have at least one Groundsource report in the archive '
       f'({fmt_percent(coverage["ever_reported_share"])}). The remaining cells have **no Groundsource report**, not evidence of no flooding. '
       'This is a 2020 broad-urban-domain versus 2000–2026 ever-reported comparison, not a year-specific M3 rate.')
       if coverage else 'A worldwide denominator of urban grids is **not available** from report-positive GHSL exports alone. The global fraction of never-reported urban cells is deliberately not estimated.',
      '',
      '## Location accuracy and validation',
      'The source report polygon area measures the footprint of the extracted location geometry; it is **not flooded area** and is not a direct measure of positional error.',
      'Groundsource authors report an external manual validation (60% strict location-and-time agreement; 82% adequate for practical analysis). These are **source-wide external estimates** and not estimates computed on this project subset.',
      ('This project has an independently completed manual review summary in 31_manual_review_accuracy.csv.' if manual is not None else
       'A stratified manual-review worksheet has been generated, but no completed project-specific manual accuracy estimates were available. Do not report an invented project validation rate.'),
      '',
      '## Interpretive boundary',
      'Geographical reporting density is jointly influenced by actual hazard, exposed settlements, media/reporting processes, and spatial-temporal extraction precision. It must not be labelled physical flood risk without independent calibration.',
      'Köppen–Geiger classes are classified at the 20-km cell centroid; mixed-climate cells can be misclassified at this aggregation level.',
      '2026 is a partial observational year and is excluded from the full-year temporal panel.',
      '',
      '## Manuscript Figure 1',
      'Panel a: reported cells over the independent 2020 broad urban universe if available; otherwise reported cells only.',
      'Panel b: annual reported grid-days and M3 grid episodes.',
      'Panel c: 2020 GHSL population-density composition within report-positive grid cells only.',
      'Panel d: observed climate classification and optional full urban-grid comparison.',
      'Panel e: source-location polygon footprint and grid-level episode duration.',
      'Panel f: sample retention under urban inclusion thresholds.',
      '',
      'All interpretation should distinguish observation representativeness from physical flood hazard.'
    ]
    (out/'Results_3_1_draft.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (out/'31_metadata.json').write_text(json.dumps({'summary':counts,'coverage':coverage,
      'ghsl_2020_present':ghsl2020 is not None,'koppen_present':climates_obs is not None,
      'manual_validation_completed':manual is not None,
      'warning':'ALL GHSL local CSV report grids only; without independent urban universe no global zero-report coverage'},
      ensure_ascii=False,indent=2),encoding='utf-8')
    con.close()
    print('\n[FINISHED]',out,flush=True)
    print('No GEE calls. No local manual-validation rate is invented.',flush=True)

if __name__=='__main__':main()
