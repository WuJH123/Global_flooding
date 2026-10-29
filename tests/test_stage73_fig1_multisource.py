"""Synthetic event-matching guard tests; no private observations required."""
import importlib.util
from pathlib import Path

import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import Point, box

SCRIPT = Path(__file__).resolve().parents[1]/'pipelines'/'r35'/'73_fig1_multisource_audit.py'
spec = importlib.util.spec_from_file_location('gf10_fig1_73', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_local_point_date_distance_screen(tmp_path):
    path = tmp_path/'archive.geojson'
    gpd.GeoDataFrame(
        {'BEGAN':['2020-01-10','2020-02-10'],
         'ENDED':['2020-01-11','2020-02-11']},
        geometry=[Point(100,20),Point(50,20)],crs='EPSG:4326'
    ).to_file(path,driver='GeoJSON')
    ref,geoms,info=m.read_reference(
        {'name':'test','family':'dfo','path':str(path),
         'start_field':'BEGAN','end_field':'ENDED','point_radius_km':50},
        '2020-01-01','2020-12-31')
    reports=pd.DataFrame({
        'event_id':['A','B','C','D'],
        'start_day':pd.to_datetime(['2020-01-10','2020-01-14','2020-02-10','2020-01-10']),
        'longitude':[100,100,50,100],'latitude':[20,20,20,-35],
        'high':[True]*4,'city_id':['1','1','2','3']})
    links=m.pair_matches(reports,ref,geoms,day_window=2,batch=2)
    assert set(links.event_id)=={'A','C'}
    summary=m.summarize(links,reports,[info])
    assert summary.iloc[0].candidate_report_uuids==2


def test_polygon_overlap_is_candidate_only(tmp_path):
    path=tmp_path/'polygons.geojson'
    gpd.GeoDataFrame({'DATE':['2017-09-01']},geometry=[box(-1,-1,1,1)],
                      crs='EPSG:4326').to_file(path,driver='GeoJSON')
    ref,geo,info=m.read_reference(
        {'name':'arch','family':'dfo','path':str(path),'start_field':'DATE'},
        '2017-01-01','2017-12-31')
    x=pd.DataFrame({'event_id':['one','two'],'start_day':pd.to_datetime(['2017-09-02']*2),
                    'longitude':[0,7],'latitude':[0,0],'city_id':['1','2']})
    assert set(m.pair_matches(x,ref,geo,day_window=3).event_id)=={'one'}
    assert 'NOT validated flood footprint' in info['observation_type']


def test_duplicate_uuids_rejected_and_year_dates():
    assert str(m.dates([2000,'2022-07-03']).iloc[0].date())=='2000-01-01'
    x=pd.DataFrame({'event_id':['same','same'],'start_day':['2020-01-01']*2,
       'longitude':[0,0],'latitude':[0,0],'high':[True,True]})
    with pytest.raises(ValueError,match='Duplicate'):
        m.normalize_reports(x,'2000-01-01','2025-12-31')


def test_mixed_legacy_date_formats_not_silently_coerced():
    values = m.dates(['2020-01-01','2020-02-01 15:00:00','20200304',2021])
    assert values.notna().all()
    assert [str(x.date()) for x in values] == ['2020-01-01','2020-02-01','2020-03-04','2021-01-01']
