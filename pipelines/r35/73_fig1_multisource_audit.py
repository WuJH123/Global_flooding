# -*- coding: utf-8 -*-
"""Stage73: multi-archive *candidate corroboration* for GF10 Figure 1.

Never treats news reports, DFO regions, or GFID representative points as
verified urban inundation. All outputs are downstream, never edit raw inputs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely import points, STRtree
from pyproj import Transformer
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATE_NAMES = ("BEG_DATE", "START_DATE", "START", "BEGAN", "STARTDATE",
              "EVENT_DATE", "DATE", "BEGIN_DATE", "FLOOD_DATE", "BEGDATE")
END_NAMES = ("END_DATE", "ENDED", "END", "ENDDATE", "END_DT", "STOP_DATE")


def candidate_column(columns, proposals):
    index = {str(k).strip().upper(): k for k in columns}
    return next((index[k] for k in proposals if k in index), None)


def dates(values):
    x = pd.Series(values).astype('string').str.strip()
    x = x.where(~x.str.fullmatch(r"\d{4}", na=False), x + "-01-01")
    return pd.to_datetime(x, errors='coerce', utc=True).dt.tz_localize(None).dt.normalize()


def read_reference(spec, start, end):
    path = Path(spec['path'])
    if not path.is_file():
        raise FileNotFoundError(f"Enabled source {spec['name']} missing: {path}")
    kind = spec.get('format', path.suffix.lstrip('.').lower())
    if kind in ('shp', 'gpkg', 'geojson'):
        obj = gpd.read_file(path, layer=spec.get('layer'))
        if obj.crs is None:
            raise ValueError(f"{spec['name']}: missing CRS; inspect metadata")
        geo = obj.geometry.to_crs('EPSG:6933')
    elif kind in ('csv', 'xlsx'):
        obj = pd.read_csv(path, low_memory=False) if kind == 'csv' else pd.read_excel(
            path, sheet_name=spec.get('sheet', 'Aggregated'))
        lat, lon = spec.get('latitude'), spec.get('longitude')
        if not lat or not lon or lat not in obj or lon not in obj:
            raise ValueError(f"{spec['name']}: explicit latitude/longitude names required")
        la = pd.to_numeric(obj[lat], errors='coerce')
        lo = pd.to_numeric(obj[lon], errors='coerce')
        valid = la.between(-90, 90) & lo.between(-180, 180)
        geo = gpd.GeoSeries(gpd.points_from_xy(lo.where(valid), la.where(valid)),
                            crs='EPSG:4326').to_crs('EPSG:6933')
    else:
        raise ValueError(f"Unsupported format {kind}")
    s = spec.get('start_field') or candidate_column(obj.columns, DATE_NAMES)
    e = spec.get('end_field') or candidate_column(obj.columns, END_NAMES)
    if not s or s not in obj:
        raise ValueError(f"{spec['name']}: inspect start date field and set start_field")
    a = dates(obj[s]); b = dates(obj[e]) if e else a.copy()
    b = b.fillna(a)
    valid = (a.notna() & b.notna() & (b >= a) &
             (a <= pd.Timestamp(end)) & (b >= pd.Timestamp(start)) &
             geo.notna() & ~geo.is_empty)
    total = len(obj)
    if int(valid.sum()) == 0:
        raise ValueError(f"{spec['name']}: zero dated events in analysis window")
    if valid.sum() / max(total, 1) < spec.get('min_valid_fraction', 0.10):
        raise ValueError(f"{spec['name']}: low valid fraction; check dates and CRS")
    idx = np.flatnonzero(valid.to_numpy())
    out = pd.DataFrame({'source':spec['name'], 'family':spec['family'],
                        'event_key':[f"{spec['name']}:{i}" for i in idx],
                        'start_day':a.iloc[idx].to_numpy(),
                        'end_day':b.iloc[idx].to_numpy()})
    geometry = geo.iloc[idx].reset_index(drop=True)
    if kind in ('xlsx', 'csv') and spec.get('source_field'):
        field = spec['source_field']
        if field not in obj:
            raise ValueError(f"{spec['name']}: unknown source field {field}")
        family_map = spec.get('family_map', {})
        keys = obj.iloc[idx][field].astype(str).str.strip().str.upper()
        unknown = set(keys.unique()) - set(family_map)
        if unknown:
            raise ValueError(f"GFID source names require explicit mapping: {sorted(unknown)}")
        out['family'] = keys.map(family_map).to_numpy()
    ispoint = geometry.geom_type.isin(['Point', 'MultiPoint'])
    radius = float(spec.get('point_radius_km', 50.))
    if radius <= 0 or radius > 100:
        raise ValueError('point_radius_km must be in (0,100]')
    geoms = geometry.to_numpy(copy=True)
    for j in np.flatnonzero(ispoint.to_numpy()):
        geoms[j] = geoms[j].buffer(radius*1000)
    if not all(g.is_valid for g in geoms):
        raise ValueError(f"{spec['name']}: invalid archive polygons")
    info = {'source':spec['name'], 'family':spec['family'], 'raw_rows':total,
            'usable_rows':len(out), 'start_field':s, 'end_field':e,
            'temporal_envelope_start':str(a.iloc[idx].min().date()),
            'temporal_envelope_end':str(b.iloc[idx].max().date()),
            'point_buffer_km':radius, 'geometry_point_rows':int(ispoint.sum()),
            'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'observation_type':'spatiotemporal candidate, NOT validated flood footprint'}
    return out, geoms, info


def pair_matches(reports, reference, geoms, day_window=7, batch=35000,
                 max_pairs=3_000_000):
    """Bounded STRtree spatial screen followed by exact interval-date screen."""
    if not 0 <= day_window <= 30:
        raise ValueError('day_window must be 0..30')
    tree = STRtree(geoms)
    convert = Transformer.from_crs('EPSG:4326','EPSG:6933', always_xy=True)
    hits = []
    for lo in range(0, len(reports), batch):
        r = reports.iloc[lo:lo+batch]
        x, y = convert.transform(r.longitude.to_numpy(), r.latitude.to_numpy())
        ii, jj = tree.query(points(x, y), predicate='intersects')
        if len(ii) > max_pairs:
            raise RuntimeError('Excessive polygon overlaps; reduce batch size or review source geometry')
        if not len(ii):
            continue
        rr = r.start_day.to_numpy(dtype='datetime64[D]')[ii]
        aa = reference.start_day.to_numpy(dtype='datetime64[D]')[jj]
        bb = reference.end_day.to_numpy(dtype='datetime64[D]')[jj]
        tolerance = np.timedelta64(day_window,'D')
        ok = (rr >= aa-tolerance) & (rr <= bb+tolerance)
        if np.any(ok):
            i = ii[ok]; j = jj[ok]
            hits.append(pd.DataFrame({'event_id':r.event_id.to_numpy()[i],
                                      'city_id':r.city_id.to_numpy()[i],
                                      'source':reference.source.to_numpy()[j],
                                      'family':reference.family.to_numpy()[j],
                                      'reference_event':reference.event_key.to_numpy()[j]}))
    if not hits:
        return pd.DataFrame(columns=['event_id','city_id','source','family','reference_event'])
    return pd.concat(hits,ignore_index=True).drop_duplicates()


def normalize_reports(df, begin, finish):
    required = {'event_id','start_day','longitude','latitude','high'}
    if required-set(df):
        raise ValueError(f'Primary news report parquet missing {sorted(required-set(df))}')
    df = df.copy()
    df['start_day'] = dates(df.start_day)
    df = df[df.high.astype(bool) & df.start_day.between(begin, finish) &
            df.latitude.between(-90,90) & df.longitude.between(-180,180)].copy()
    if df.event_id.duplicated().any():
        raise ValueError('Duplicate report UUID: Stage63 primary links only')
    if 'city_id' not in df:
        df['city_id'] = pd.NA
    return df


def summarize(pairs, report, metadata):
    rows = []
    for entry in metadata:
        p = pairs.loc[pairs.source.eq(entry['source'])]
        rows.append({'source':entry['source'], 'family':entry['family'],
            'archive_usable_events':entry['usable_rows'],
            'candidate_report_uuids':int(p.event_id.nunique()),
            'candidate_reference_events':int(p.reference_event.nunique()),
            'high_report_denominator_full_period':len(report),
            'high_report_denominator_archive_envelope':int(report.start_day.between(
                entry['temporal_envelope_start'],entry['temporal_envelope_end']).sum()),
            'evidence':'SPATIOTEMPORAL_CANDIDATE_NOT_ACCURACY_OR_RECALL'})
    return pd.DataFrame(rows)


def render(summary,pairs,out):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,
                         'axes.spines.top':False,'axes.spines.right':False})
    fig,axs=plt.subplots(1,2,figsize=(13.5,5.3),layout='constrained')
    z=summary.sort_values('candidate_report_uuids')
    axs[0].barh(z.source,z.candidate_report_uuids,color='#447F9D')
    axs[0].set(xlabel='News UUIDs with candidate archive overlap',
               title='a  Source-specific spatiotemporal overlap')
    count=pairs.groupby('event_id').family.nunique().value_counts().sort_index()
    axs[1].bar(count.index.astype(str),count.values,color='#C06C4E')
    axs[1].set(xlabel='Number of source families with candidate overlap',
               ylabel='Unique report UUIDs',title='b  Overlap, not independent confirmations')
    fig.suptitle('Figure 1 supplementary | Multi-source reference screening',fontweight='bold')
    fig.text(.5,.015,'DFO and GFID-DFO share lineage. Neither unmatched nor matched records establish true pluvial floods.',
             ha='center',fontsize=8)
    fig.savefig(out/'Figure1_multisource_audit.png',dpi=300,bbox_inches='tight')
    fig.savefig(out/'Figure1_multisource_audit.pdf',bbox_inches='tight')
    plt.close(fig)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--reports',type=Path,required=True)
    ap.add_argument('--city-audit',type=Path,required=True)
    ap.add_argument('--dominant-city',type=Path,required=True,help='Stage62c dominant-city Parquet for primary grid_id')
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--start',default='2000-01-01')
    ap.add_argument('--end',default='2025-12-31')
    ap.add_argument('--day-window',type=int,default=7)
    ap.add_argument('--batch',type=int,default=35000)
    ap.add_argument('--inspect-only',action='store_true')
    args=ap.parse_args()
    sources=[x for x in json.loads(args.config.read_text(encoding='utf-8'))['sources']
             if x.get('enabled',False)]
    if not sources:
        raise ValueError('No enabled sources: do not report zeros as absence')
    if len(set(x['name'] for x in sources))!=len(sources):
        raise ValueError('Duplicate source names')
    if args.inspect_only:
        for s in sources:
            path=Path(s['path'])
            print('[SOURCE]',s['name'],'exists',path.is_file())
            if not path.is_file():
                continue
            kind=s.get('format',path.suffix[1:].lower())
            if kind in ('shp','gpkg','geojson'):
                import pyogrio
                info=pyogrio.read_info(path,layer=s.get('layer'))
                cols=list(info['fields'])
                print(' CRS',info['crs'],'rows',info['features'])
            elif kind=='xlsx':
                cols=list(pd.read_excel(path,sheet_name=s.get('sheet','Aggregated'),nrows=2).columns)
            else:
                cols=list(pd.read_csv(path,nrows=2).columns)
            print(' FIELDS',cols,'start',candidate_column(cols,DATE_NAMES),
                  'end',candidate_column(cols,END_NAMES))
        return
    for s in sources:
        if s['family'].lower() in ('independent','unknown'):
            raise ValueError('Document each source lineage explicitly')
    if not args.reports.is_file() or not args.city_audit.is_file():
        raise FileNotFoundError('Need original Figure1 primary_geocoded_reports.parquet and city_audit.csv')
    args.out.mkdir(parents=True,exist_ok=True)
    report=normalize_reports(pd.read_parquet(args.reports),args.start,args.end)
    if 'grid_id' not in report.columns:
        raise ValueError('Figure1 primary report output must retain grid_id to assign UCDB city')
    dom=pd.read_parquet(args.dominant_city,columns=['grid_id','city_id'])
    if dom.grid_id.duplicated().any():raise ValueError('Stage62c grid_id duplicates')
    report=report.drop(columns=['city_id'],errors='ignore').merge(
        dom,on='grid_id',how='left',validate='many_to_one')
    report['city_id']=report.city_id.astype('string')
    city=pd.read_csv(args.city_audit,dtype={'city_id':str})
    if city.city_id.duplicated().any():raise ValueError('Duplicated official UCDB city_id')
    if report.empty:raise ValueError('No high specificity primary reports')
    matches=[];metadata=[]
    for s in sources:
        print('[REFERENCE]',s['name'],flush=True)
        refs,geo,meta=read_reference(s,args.start,args.end)
        metadata.append(meta)
        matched=pair_matches(report,refs,geo,args.day_window,args.batch)
        matches.append(matched)
        print('[CANDIDATES]',s['name'],len(matched),flush=True)
    pairs=pd.concat(matches,ignore_index=True).drop_duplicates()
    pairs.to_parquet(args.out/'73_candidate_pairs.parquet',index=False)
    summary=summarize(pairs,report,metadata)
    summary.to_csv(args.out/'73_source_summary.csv',index=False)
    families=pairs.groupby('family').agg(candidate_report_uuids=('event_id','nunique'),
        candidate_reference_events=('reference_event','nunique')).reset_index()
    families.to_csv(args.out/'73_family_summary.csv',index=False)
    bycity=pairs[['event_id','city_id','family']].drop_duplicates()
    bycity=bycity[bycity.city_id.notna()].groupby('city_id').family.nunique()
    city['candidate_source_families']=city.city_id.map(bycity).fillna(0).astype(int)
    city.to_csv(args.out/'73_city_support.csv',index=False)
    render(summary,pairs,args.out)
    qc={'status':'CANDIDATE_REFERENCE_SCREENING_NOT_VALIDATION',
        'high_specificity_reports':len(report),
        'candidate_any_report_uuids':int(pairs.event_id.nunique()),
        'candidate_cities':int((city.candidate_source_families>0).sum()),
        'day_window':args.day_window,'sources':metadata,
        'source_dependence':['DFO archive/master and GFID DFO share original events',
             'GFD MODIS selection is based on DFO event list',
             'Candidate matches are not accuracy, recall, or inundation truth',
             'Unmatched records do not imply absence of real flooding',
             'EPSG:6933 distance buffers around point records are approximate']}
    (args.out/'73_multisource_qc.json').write_text(
         json.dumps(qc,indent=2,ensure_ascii=False),encoding='utf-8')
    print('[DONE]',json.dumps({k:v for k,v in qc.items() if k!='sources'},ensure_ascii=False))


if __name__=='__main__':
    main()
