"""Offline regression tests for local nested 5km environment summaries."""
from pathlib import Path
import importlib.util
import json
import sys
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import Affine

ROOT=Path(__file__).resolve().parents[1]
R34=ROOT/"pipelines"/"r34"

def load(name):
    spec=importlib.util.spec_from_file_location("r34_"+name,R34/(name+".py"))
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

def test_5km_built_m2_conservation(tmp_path):
    m=load("54_local_fine5_built_cache")
    src=tmp_path/"test_source.tif"
    with rasterio.open(src,"w",driver="GTiff",width=20,height=20,count=1,
        dtype="float32",crs="EPSG:6933",
        transform=Affine(1000,0,0,0,-1000,20000),nodata=65535) as ds:
        ds.write(np.full((20,20),400_000,dtype="float32"),1)
    parents=pd.DataFrame([{"grid_id":"0_0","grid_x":0,"grid_y":0}])
    with rasterio.open(src) as ds:
        frame,bad=m.extract_batch(ds,parents,2020)
    assert bad==0
    assert len(frame)==16
    assert set(frame.grid5_id)=={f"{x}_{y}" for x in range(4) for y in range(4)}
    assert np.isclose(frame.built_total_m2.sum(),160_000_000,rtol=.001)
    assert np.isclose(frame.built_valid_area_m2.sum(),400_000_000,rtol=.001)
    assert np.allclose(frame.built_fraction_cell,.4,atol=.001)

def test_parent_id_validation_and_negative_children(tmp_path):
    m=load("54_local_fine5_built_cache")
    parent=tmp_path/"p.csv"
    pd.DataFrame([{"grid_id":"-1_-1","grid_x":-1,"grid_y":-1}]).to_csv(parent,index=False)
    df=m.load_parents(parent)
    assert df.grid_id.iloc[0]=="-1_-1"
    with pytest.raises(ValueError,match="grid_id"):
        pd.DataFrame([{"grid_id":"0_0","grid_x":1,"grid_y":0}]).to_csv(parent,index=False)
        m.load_parents(parent)

def test_rollup_5_to_10_20_preserves_area_and_recomputes_ratios(tmp_path):
    m=load("55_aggregate_fine5_environment")
    fine=tmp_path/"fine";fine.mkdir()
    records=[]
    for px,py in [(-1,-1),(0,0)]:
        for y in range(py*4,py*4+4):
            for x in range(px*4,px*4+4):
                records.append(dict(grid5_id=f"{x}_{y}",grid_x5=x,grid_y5=y,
                    parent_grid_id=f"{px}_{py}",parent_grid_x=px,parent_grid_y=py,
                    epoch=2020,built_total_m2=5_000_000,built_valid_area_m2=25_000_000,
                    cell_area_m2=25_000_000,built_fraction_cell=.2))
    f=fine/"fine5_parent_0000.parquet"
    pd.DataFrame(records).to_parquet(f,index=False)
    (fine/"54_fine5_manifest.json").write_text(json.dumps({
        "parts":[{"file":str(f),"parents":2,"rows":32}],
        "fine5_grids":32}),encoding="utf-8")
    report=m.rollup(fine,tmp_path/"coarse")
    a=pd.read_parquet(tmp_path/"coarse"/"environment_grid_10km_2020.parquet")
    b=pd.read_parquet(tmp_path/"coarse"/"environment_grid_20km_2020.parquet")
    assert len(a)==8 and len(b)==2
    assert set(b.grid_id)=={"-1_-1","0_0"}
    assert (a.n_children==4).all() and (b.n_children==16).all()
    assert np.isclose(a.built_total_m2.sum(),b.built_total_m2.sum())
    assert np.allclose(b.built_fraction_cell,.2)
    assert report["outputs"]["20"]["rows"]==2

def test_aggregate_excludes_nonlinear_extremes():
    m=load("55_aggregate_fine5_environment")
    import inspect
    src=inspect.getsource(m.rollup)
    assert "SUM(built_total_m2)" in src
    assert "SUM(built_valid_area_m2)" in src
    assert "built_total_m2/NULLIF(cell_area_m2,0)" in src
    assert "max_rain" not in src
