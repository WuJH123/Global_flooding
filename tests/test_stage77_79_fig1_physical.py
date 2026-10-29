"""Stage77–79 synthetic: no GEE, network, or Windows files required."""
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]/'pipelines'/'r35'
def mod(stem):
    p=ROOT/stem
    spec=importlib.util.spec_from_file_location('fig1_'+stem.replace('.','_'),p)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

def test_gfd_candidates_time_distance_and_dateline():
    m=mod('77_gfd_candidate_requests.py')
    events=pd.DataFrame({
      'event_id':['A','B','C','D'],
      'grid_id':['100_100','-100_20','1_1','0_0'],
      'start_day':['2017-09-01','2017-09-02','2017-09-01','2018-10-01'],
      'longitude':[179.95,-179.96,90,179.95],
      'latitude':[5,5,20,5],
      'high':[True,True,True,True]})
    gfd=pd.DataFrame({'dfo_event_id':[1],'dfo_centroid_x':[-179.97],
                      'dfo_centroid_y':[5],'time_start_utc':['2017-09-01T00:00:00Z']})
    result=m.candidates(events,gfd,max_km=30,days=3,limit=20,per_event=20)
    assert set(result.event_id)=={'A','B'}
    assert result.request_id.is_unique
    assert result.candidate_distance_km.max() <= 30

def test_area_merge_distinguishes_unobserved_non_detection_positive():
    m=mod('79_gfd_merge_physical.py')
    req=pd.DataFrame({'request_id':['a','b','c'],'gfd_id':[10,10,10],
          'grid_x':[1,2,3],'grid_y':[1,2,3]})
    got=req.copy()
    got['observed_area_m2']=[None,25_000_000,50_000_000]
    got['flood_area_m2']=[None,0,2_000_000]
    d=m.merge(req,got,minimum_coverage=.2)
    assert d.evidence_state.tolist()==['UNOBSERVED','OBSERVED_NO_GFD_FLOOD_PIXEL',
                                      'GFD_FLOOD_PIXELS_PRESENT']
    with pytest.raises(ValueError,match='request-ID'):
        m.merge(req,got.iloc[1:].copy())
    bad=got.copy();bad.loc[2,'flood_area_m2']=90_000_000
    with pytest.raises(ValueError,match='exceeds observed'):
        m.merge(req,bad)

def test_stage73_needs_evidenced_crs_override(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point
    m=mod('73_fig1_multisource_audit.py')
    # A shapefile without .prj has no trustworthy CRS; never assume EPSG:4326.
    p=tmp_path/'external.shp'
    gpd.GeoDataFrame({'DATE':['2020-01-05','2020'],'geometry':[Point(1,1),Point(2,2)]},
                     crs='EPSG:4326').to_file(p)
    p.with_suffix('.prj').unlink()
    base={'name':'external','family':'dfo','path':str(p),'start_field':'DATE'}
    with pytest.raises(ValueError,match='missing CRS'):
        m.read_reference(base,'2020-01-01','2020-12-31')
    spec={**base,'confirmed_crs':'EPSG:4326',
          'crs_evidence':'Explicit official metadata, citation inspected'}
    d,geo,q=m.read_reference(spec,'2020-01-01','2020-12-31')
    assert len(d)==1 and q['partial_date_rows_excluded']==1

def test_gfd_gee_hard_request_limit_and_duplicate_guard():
    m=mod('78_gfd_gee_physical_pilot.py')
    good=pd.DataFrame({'request_id':['A'],'gfd_id':[10],
                       'grid_x':[5],'grid_y':[4]})
    assert len(m.validate(good))==1
    with pytest.raises(ValueError,match='Duplicated'):
        m.validate(pd.concat([good,good],ignore_index=True))
    s=(ROOT/'78_gfd_gee_physical_pilot.py').read_text()
    assert 'if not a.submit:return' in s
    assert 'jrc_perm_water' in s and "clear_views" in s


def test_stage80_city_denominator_and_strata_are_preserved():
    m=mod('80_fig1_city_selection.py')
    c=pd.DataFrame({'city_id':['1','2','3','4'],'longitude':[10,11,20,21],
      'latitude':[10,11,20,21],'continent':['Europe','Europe','Africa',None],
      'screened':[True,False,False,True],'n_high_reports':[12,0,0,9],
      'n_report_years':[4,0,0,3]})
    result,t,pilot=m.audit(c,pilot_per_stratum=2)
    assert len(result)==4 and sum(t['continent'].official_cities)==4
    assert int(t['continent'].screened_cities.sum())==2
    assert len(pilot)==4 and 'screened' in pilot and 'continent_category' in pilot
