"""Offline numerical and fail-closed tests for Stage57/58/59 and 34b ablation."""
from __future__ import annotations
import importlib.util,sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import Affine
from pyproj import Transformer
from shapely.geometry import box

ROOT=Path(__file__).resolve().parents[1]
R34=ROOT/"pipelines"/"r34"
def load(name):
    path=R34/f"{name}.py"
    spec=importlib.util.spec_from_file_location("test_r34_"+name,path)
    m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m
    spec.loader.exec_module(m)
    return m

def make_raster(path,built_value=6000.,nodata=False):
    arr=np.full((200,200),built_value,dtype="float32")
    if nodata:arr[0:5,0:5]=65535
    with rasterio.open(path,"w",driver="GTiff",width=200,height=200,count=1,
             dtype="float32",crs="EPSG:6933",nodata=65535,
             transform=Affine(100,0,0,0,-100,20000)) as d:
        d.write(arr,1)

def test_native_source_20km_independent_quadrature(tmp_path):
    m=load("57_independent_built_area_audit")
    tif=tmp_path/"native.tif";make_raster(tif)
    with rasterio.open(tif) as ds:
        tr=Transformer.from_crs(ds.crs,"EPSG:6933",always_xy=True)
        for q in [1,4]:
            value,coverage,n=m.native_quadrature(ds,tr,0,0,q)
            assert value==pytest.approx(.6,abs=.002)
            assert coverage==pytest.approx(1,abs=.002)
            assert n==40000

def test_source_audit_stratifies_outliers_and_rejects_ids():
    m=load("57_independent_built_area_audit")
    data=pd.DataFrame([
        {"grid_id":"0_0","built_fraction_cell":.12,"built_fraction":.2,"abs_diff":.08,"source_valid_fraction":1},
        {"grid_id":"1_0","built_fraction_cell":.12,"built_fraction":.13,"abs_diff":.01,"source_valid_fraction":1},
        {"grid_id":"2_0","built_fraction_cell":.19,"built_fraction":.2,"abs_diff":.01,"source_valid_fraction":1},
    ])
    x=m.choose(data,threshold=.03,controls=2,seed=42)
    assert x.audit_group.eq("outlier").sum()==1
    assert x.grid_id.nunique()==len(x)
    data.loc[0,"grid_id"]="not_a_grid"
    with pytest.raises(ValueError,match="Bad grid IDs"):m.choose(data,.03,2)

def test_source_city_clipping_differs_from_whole_grid(tmp_path):
    m=load("58_city_boundary_fine5_audit")
    tif=tmp_path/"native.tif";make_raster(tif)
    city=box(0,0,10000,10000)
    with rasterio.open(tif) as ds:
        summary,cells=m.source_city_cells(ds,city,max_pixels=100000)
    assert summary["status"]=="PASS_QUADRATURE"
    assert summary["valid_5km_cells"]==4
    assert summary["within_city_5km_sd"]==pytest.approx(0,abs=.001)
    assert summary["city_built_fraction"]==pytest.approx(.6,abs=.002)
    assert len(cells)==4

def test_geocode_polygon_scale_and_shift():
    m=load("59_multiscale_location_pilot")
    small=box(1000,1000,4000,4000)
    p5=m.footprint_metrics(small,5,offset=0)
    p10=m.footprint_metrics(small,10,offset=0)
    assert p5["touched_cells"]==1 and p10["touched_cells"]==1
    assert p5["max_geocode_overlap_fraction"]==pytest.approx(1)
    offset=m.footprint_metrics(small,5,offset=.5)
    assert offset["touched_cells"]>1
    assert m.area_precision(1)=="small_0_5km2"
    assert m.area_precision(30)=="medium_5_100km2"
    assert m.area_precision(500)=="broad_gt100km2"
    wide=m.footprint_metrics(box(0,0,500_000,500_000),5,max_cells=10)
    assert wide["status"]=="TOO_BROAD"

def test_34b_direct_within_fold_paired_ablation():
    s=(ROOT/"pipelines"/"r33"/"34b_continuous_environment_response.py").read_text(encoding="utf8")
    assert '("S_"+ALLOWED[f],"E2_continuous_P24")' in s
    assert 'st27.paired_gain(A["per"],B["per"]' in s
    assert 'np.array_equal(reference[7],result["test"][7])' in s
