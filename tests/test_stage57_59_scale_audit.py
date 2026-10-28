"""No-data-cloud Stage57-59 regressions; missing precision never yields scale winner."""
from pathlib import Path
import importlib.util,sys
import numpy as np,pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]/"pipelines"/"r34"
def get(name):
    sp=importlib.util.spec_from_file_location("module_"+name,ROOT/(name+".py"))
    m=importlib.util.module_from_spec(sp);sys.modules[sp.name]=m;sp.loader.exec_module(m);return m

def test_stratified_selection_balances_categories():
    s=get("59_stratified_scale_decision")
    z=pd.DataFrame([{"city_id":f"{c}_{i}","climate_zone":c,"location_quality":q}
      for i in range(40) for c,q in [("A","precise"),("B","coarse")]])
    x=s.select_cities(z,12)
    assert len(x)==12 and len(set(x.city_id))==12
    assert set(x.climate_zone)=={"A","B"}

def test_no_fake_precision_or_scale_winner():
    s=get("59_stratified_scale_decision")
    b=pd.DataFrame([{"city_id":"x","scale_km":k,"city_built_fraction":.2,
       "city_built_sd":.03,"coverage_fraction":1.} for k in [5,10,20]])
    q=s.evaluate(b)
    assert q["scale_winner"] is None and "NO_SCALE" in q["status"]
    with pytest.raises(ValueError):
        s.select_cities(pd.DataFrame([{"city_id":"x","climate_zone":"A",
           "location_quality":"not_verified"}]),1)

def test_native_pixel_audit_simple_raster(tmp_path):
    import rasterio
    from affine import Affine
    a=get("57_native_source_audit")
    tif=tmp_path/"r.tif"
    with rasterio.open(tif,"w",driver="GTiff",width=20,height=20,count=1,
      dtype="float32",crs="EPSG:6933",transform=Affine(1000,0,0,0,-1000,20000),
      nodata=65535) as dst:
        dst.write(np.full((20,20),400000,dtype="float32"),1)
    with rasterio.open(tif) as ds:
        mid,up,bound,valid=a.inspect(ds,0,0)
    assert abs(mid-.4)<1e-6 and abs(up-.4)<1e-6
    assert valid==1.0

def test_city_boundary_intersect_weighted_cell():
    from shapely.geometry import box
    m=get("58_city_boundary_multiscale")
    f=pd.DataFrame([{"grid_x":0,"grid_y":0,"built_fraction_cell":.2,"source_valid_fraction":1.},
                    {"grid_x":1,"grid_y":0,"built_fraction_cell":.8,"source_valid_fraction":1.}])
    res=m.city_scale(box(0,0,7500,5000),f,5)
    assert abs(res["city_built_fraction"]-.4)<1e-6
    assert res["cells_intersected"]==2
