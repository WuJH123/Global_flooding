"""Synthetic pixel-area conservation and temporal-leakage tests for Stage30c."""
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
def module(path):
    spec=importlib.util.spec_from_file_location("test_stage30c_"+path.stem,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def write_raster(tmp_path,year=2020,short=False,invalid=False):
    import rasterio
    from rasterio.transform import from_origin
    name=f"GHS_BUILT_S_E{year}_GLOBE_R2023A_54009_100_V1_0.tif"
    path=tmp_path/name
    size=200 if short else 400
    data=np.zeros((size,size),dtype="float32")
    data[:200,:200]=2500
    if not short:
        data[:200,200:]=5000
        data[200:,200:]=10000
    if invalid:data[0,0]=12000.
    with rasterio.open(path,"w",driver="GTiff",height=size,width=size,
                       count=1,dtype="float32",crs="EPSG:6933",
                       transform=from_origin(0,size*100,100,100),
                       nodata=-9999.,tiled=True,blockxsize=128,blockysize=128) as dst:
        dst.write(data,1)
    return path

def make_centers(tmp_path):
    c=pd.DataFrame({"grid_id":["0_0","1_0","0_1","1_1"],
                    "grid_x":[0,1,0,1],"grid_y":[0,0,1,1]})
    p=tmp_path/"centers.csv"
    c.to_csv(p,index=False)
    return p,c

def test_30c_additive_fraction_and_scenario(tmp_path):
    import rasterio
    m=module(ROOT/"pipelines"/"r33"/"30c_extract_built_surface.py")
    raster=write_raster(tmp_path,2030)
    info=m.inspect_file(raster,2030)
    assert info["kind"]=="scenario"
    centers,table=make_centers(tmp_path)
    with rasterio.open(raster) as src:
        ans=m.compute_grid(src,m.read_centers(centers),tile_cells=2,log_every=0)
    actual=dict(zip(ans.grid_id,ans.built_fraction))
    assert abs(actual["0_0"]-0.25)<0.00001
    assert abs(actual["1_0"]-1.00)<0.00001
    assert abs(actual["0_1"]-0.25)<0.00001
    assert abs(actual["1_1"]-0.50)<0.00001
    assert np.allclose(ans.built_m2,ans.built_fraction*400_000_000)
    assert ans.source_coverage_fraction.min()>.99
    assert abs(ans.built_m2.sum()-800_000_000)<1 # fractions 0.25+1+.25+.5=2.0
    overlap=pd.DataFrame({"grid_id":["0_0","1_0"],"city_id":["A","A"],
                          "overlap_km2":[1.0,3.0]})
    op=tmp_path/"crosswalk.csv"
    overlap.to_csv(op,index=False)
    city=m.aggregate_city_context(ans,op)
    assert abs(city.iloc[0].overlap_weighted_grid_built_fraction-.8125)<1e-5
    assert city.iloc[0].overlap_coverage_fraction==1

def test_30c_source_partial_coverage_not_zero_assumed(tmp_path):
    import rasterio
    m=module(ROOT/"pipelines"/"r33"/"30c_extract_built_surface.py")
    raster=write_raster(tmp_path,2020,short=True)
    centers,_=make_centers(tmp_path)
    with rasterio.open(raster) as src:
        ans=m.compute_grid(src,m.read_centers(centers),tile_cells=2,log_every=0)
    # Small source is only one 20km cell; others must be NaN.
    assert ans.built_fraction.notna().sum()==1
    assert ans.loc[ans.grid_id.eq("0_0"),"source_coverage_fraction"].iloc[0]>.99
    assert ans.loc[ans.grid_id.eq("1_1"),"source_coverage_fraction"].iloc[0]==0

def test_30c_reject_invalid_physical_pixel(tmp_path):
    import rasterio
    m=module(ROOT/"pipelines"/"r33"/"30c_extract_built_surface.py")
    raster=write_raster(tmp_path,2020,invalid=True)
    centers,_=make_centers(tmp_path)
    with rasterio.open(raster) as src:
        with pytest.raises(ValueError,match="greater than"):
            m.compute_grid(src,m.read_centers(centers),tile_cells=2,log_every=0)

def test_30_epoch_guard_and_exact_grid_join(tmp_path):
    m=module(ROOT/"pipelines"/"r33"/"30_urban_context.py")
    base=pd.DataFrame({"grid_id":["0_0","1_0"],
                       "epoch":[2020,2020],
                       "built_fraction":[np.nan,np.nan]})
    def raster_table(year):
        x=base.copy()
        x["epoch"]=year;x["built_fraction"]=[.4,.6]
        x["temporal_kind"]="historical_reconstruction" if year<=2020 else "scenario"
        x["source_coverage_fraction"]=1.0
        return x
    path=tmp_path/"30c_built_grid_2020.parquet"
    raster_table(2020).to_parquet(path,index=False)
    joined,qc=m.merge_raster_built(base,[path])
    assert joined.built_fraction.tolist()==[.4,.6]
    assert qc["n_filled_from_raster"]==2
    projection=tmp_path/"30c_built_grid_2030.parquet"
    raster_table(2030).to_parquet(projection,index=False)
    with pytest.raises(ValueError,match="PROJECTION"):
        m.merge_raster_built(base,[projection])
    wrong=tmp_path/"30c_built_grid_2020_SMOKE5.parquet"
    raster_table(2020).to_parquet(wrong,index=False)
    with pytest.raises(ValueError,match="smoke-test"):
        m.merge_raster_built(base,[wrong])
    short=tmp_path/"30c_built_grid_2020_short.parquet"
    raster_table(2020).iloc[[0]].to_parquet(short,index=False)
    with pytest.raises(RuntimeError,match="incomplete epoch"):
        m.merge_raster_built(base,[short])

def test_30c_filename_year_guard(tmp_path):
    m=module(ROOT/"pipelines"/"r33"/"30c_extract_built_surface.py")
    p=write_raster(tmp_path,2030)
    with pytest.raises(ValueError,match="disagree"):
        m.inspect_file(p,2020)
