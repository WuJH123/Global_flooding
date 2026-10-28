"""Offline regressions for GEE Stage45, 46b, 50 and 53 modules (no EE calls)."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import Affine

ROOT=Path(__file__).resolve().parents[1]
R34=ROOT/"pipelines"/"r34"
if str(R34) not in sys.path:sys.path.insert(0,str(R34))

def load(stem):
    path=R34/(stem+".py")
    spec=importlib.util.spec_from_file_location("test_"+stem,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_annual_parsers_and_versioned_filenames():
    mod=load("53_gee_annual_raster_exports")
    assert mod.parse_years("2000,2002-2003")==[2000,2002,2003]
    assert mod.parse_years("2020")==[2020]
    with pytest.raises(ValueError):mod.parse_years("2026")
    assert mod.SOURCES["era5"][2]=="ERA5RASTER_V2"
    assert mod.SOURCES["imerg"][2]=="IMERGRASTER_V2"
    assert load("50_imerg_annual_daily_extremes").parse_range("0-3,5",0,999)==[0,1,2,3,5]
    assert load("46b_gee_built_only").parse_epochs("2000,2010")==[2000,2010]

def test_smod_20km_source_crs_contract():
    mod=load("45_smod2020_export")
    assert mod.TRANSFORM==[20000,0,-17400000,0,-20000,7400000]
    assert mod.VERSION=="SMOD2020_20KM_V2"

def test_smod_local_conversion():
    mod=load("45b_smod2020_to_asset")
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)
        tif=path/"smod.tif";csv=path/"urban.csv"
        urban=np.zeros((740,1740),dtype="float32")
        valid=np.ones_like(urban)
        urban[200:210,500:710]=.20
        with rasterio.open(tif,"w",driver="GTiff",width=1740,height=740,
             count=2,dtype="float32",crs="EPSG:6933",
             transform=Affine(20000,0,-17400000,0,-20000,7400000)) as ds:
            ds.write(urban,1);ds.write(valid,2)
        qc=mod.convert(tif,csv,.001,.95,5000)
        frame=pd.read_csv(csv)
        assert qc["rows"]==2100
        assert frame.grid_id.nunique()==2100
        assert (frame.chunk_id==0).all()
        assert (frame.latitude.abs()<=90).all()

def test_annual_parquet_requires_all_26_years(tmp_path,monkeypatch):
    script=load("53b_raster_to_grid_parquet")
    assert script.PAT["era5"].match("era5raster_v2_y2020.tif")
    assert script.PAT["imerg"].match("imergraster_v2_y2020_test.tif")
    grid=tmp_path/"grid.csv"
    pd.DataFrame([{"grid_id":"0_0","grid_x":0,"grid_y":0}]).to_csv(grid,index=False)
    root=tmp_path/"rasters";root.mkdir()
    (root/"era5raster_v2_y2020.tif").write_bytes(b"not-read-due-to-year-gate")
    monkeypatch.setattr(sys,"argv",["53b","--source","era5","--tif-root",str(root),
        "--grid-csv",str(grid),"--out",str(tmp_path/"out"),"--require-full"])
    with pytest.raises(RuntimeError,match="Missing/extra year"):
        script.main()

def test_offline_csv_auditor_flags_bad_physics(tmp_path):
    module=load("50b_qc_new_exports")
    p=tmp_path/"built_hist_v2_y00_c00.csv"
    pd.DataFrame([{"grid_id":"0_0","built_2000_built_total_m2":1,
       "built_2000_built_nres_m2":3,"built_2000_source_valid_area_m2":10}]).to_csv(p,index=False)
    report,_=module.inspect("built",[p],[2000],{"0_0"},True)
    assert report["status"]=="FAIL"
