"""R35 frozen-grid integrity guard, independent of Google Earth Engine."""
import sys,importlib.util
from pathlib import Path
import numpy as np,pandas as pd,pytest
ROOT=Path(__file__).resolve().parents[1]/"pipelines"/"r35"
def mod():
    p=ROOT/"72_grid10_integrity_contract.py"
    z=importlib.util.spec_from_file_location("gf10_test_integrity",p)
    m=importlib.util.module_from_spec(z);sys.modules[z.name]=m;z.loader.exec_module(m)
    return m
def make(path,broken=False):
    df=pd.DataFrame({"grid_id":["0_0","1_0"],"grid_x":[0,1],
      "grid_y":[0,0],"cell_area_m2":[100000000]*2,
      "urban_fraction_2020":[.10,.20],"source_valid_fraction":[1.,1.],
      "urban_area_km2_2020":[10.,20.]})
    if broken:df.loc[0,"urban_area_km2_2020"]=45.
    df.to_parquet(path,index=False)
def test_strict_area_and_ids(tmp_path):
    p=tmp_path/"grid.parquet";make(p)
    x=mod().validate(p,[],tmp_path/"qc",max_geodesic=2)
    assert x["status"]=="PASS_GF10_V1_AREA_AND_KEYS"
    assert x["n_cells"]==2
def test_wrong_area_rejected(tmp_path):
    p=tmp_path/"grid.parquet";make(p,True)
    with pytest.raises(ValueError,match="Urban fraction"):
        mod().validate(p,[],tmp_path/"qc",max_geodesic=2)
def test_foreign_id_rejected(tmp_path):
    p=tmp_path/"grid.parquet";make(p)
    foreign=tmp_path/"other.parquet"
    pd.DataFrame({"grid_id":["0_0","34_25"]}).to_parquet(foreign,index=False)
    with pytest.raises(ValueError,match="NOT in frozen urban"):
        mod().validate(p,[foreign],tmp_path/"qc",max_geodesic=2)
def test_weather_completeness_protected():
    s=(ROOT/"65_gee_event_weather10.py").read_text(encoding="utf-8")
    assert "updateMask(count.eq(48))" in s
    assert "updateMask(sub.count().eq(6))" in s
    assert "updateMask(a7n.eq(7))" in s
