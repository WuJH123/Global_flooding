"""Stage64 GHSL V2: physical conservation, fixed stripe/row mosaicking."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
import rasterio
from affine import Affine

ROOT=Path(__file__).resolve().parents[1]/"pipelines"/"r35"
def load(stem):
    p=ROOT/stem
    spec=importlib.util.spec_from_file_location(stem.replace(".","_"),p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def test_output_weight_sum_is_not_input_sum():
    # Four input cells, each 100 m2, one output 400 m2.
    values=np.array([1.,2.,3.,4.])
    incorrect=(values*np.full(4,0.25)).sum()
    correct=((values/100)*0.25).sum()*400
    assert correct==pytest.approx(10.)
    assert incorrect==pytest.approx(2.5)
    s=(ROOT/"64_gee_context10_exports.py").read_text(encoding="utf-8")
    for token in ("source.toFloat().divide(area_native)",
                  "reduceResolution(ee.Reducer.sum()",
                  "multiply(target_area)",
                  "density.clip(region)",
                  'version="V2"'):
        assert token in s

def test_stitch_ghsl_v2_exact_window(tmp_path):
    m=load("64c_stitch_ghsl_v2_tiles.py")
    src=tmp_path/"input";dst=tmp_path/"output"
    src.mkdir()
    rows=4
    h=m.tile_height(rows)
    for stripe in range(2):
        for row in range(rows):
            filename=f"ghs10_v2_e2000_s{stripe:02d}_r{row:02d}of{rows:02d}.tif"
            t=Affine(10000,0,m.STRIPE_X[stripe],0,-10000,m.GLOBAL_TOP-row*h*10000)
            with rasterio.open(src/filename,"w",driver="GTiff",width=1736,height=h,
                               count=3,dtype="float32",crs="EPSG:6933",
                               transform=t,nodata=-9999,compress="deflate") as ds:
                data=np.full((h,1736),float(stripe*10+row+1),dtype="float32")
                for band in range(1,4):ds.write(data*band,band)
    qc=m.merge(src,dst,2000,4)
    assert qc["status"]=="PASS_GHSL_V2_MOSAIC"
    for stripe in (0,1):
        p=dst/f"ghs10_v2_e2000_s{stripe:02d}.tif"
        with rasterio.open(p) as raster:
            assert raster.crs.to_epsg()==6933
            assert (raster.width,raster.height,raster.count)==(1736,1464,3)
            for row in range(rows):
                assert raster.read(1,window=rasterio.windows.Window(7,row*h,1,1))[0,0]==stripe*10+row+1
