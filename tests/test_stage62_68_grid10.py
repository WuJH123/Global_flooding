"""GF10 reproducibility tests: grid, geometry, calendar, units, model, QC."""
from pathlib import Path
import importlib.util,sys
import numpy as np,pandas as pd,pytest
from shapely.geometry import box
from shapely.ops import transform
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]/"pipelines"/"r35"
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT.parent/"r34"))
def get(module):
    spec=importlib.util.spec_from_file_location("gf10_"+module,ROOT/(module+".py"))
    m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return m

def test_global_grid_lock_and_areas():
    g=get("62_gee_freeze_grid10")
    assert g.validate_local()["width"]==3472
    assert g.TRANSFORM==[10000,0,-17360000,0,-10000,7320000]
    a=get("62b_freeze_grid10_local")
    assert abs(a.audit_geodetic_cell(0,0)/1e8-1)<.001
    assert abs(a.audit_geodetic_cell(200,400)/1e8-1)<.005

def test_geometry_primary_and_not_extrapolating():
    m=get("63_event_geometry_grid10")
    t=Transformer.from_crs(6933,4326,always_xy=True)
    g=transform(t.transform,box(100,100,1000,1000))
    pairs,status=m.event_candidate(g,{"0_0"},Transformer.from_crs(4326,6933,always_xy=True))
    assert status=="MATCHED" and len(pairs)==1 and pairs[0][0]=="0_0"
    pairs,status=m.event_candidate(g,{"1_1"},Transformer.from_crs(4326,6933,always_xy=True))
    assert status=="NOT_IN_URBAN_2020_DOMAIN"
    g=transform(t.transform,box(0,0,200000,200000))
    pairs,status=m.event_candidate(g,{"0_0"},Transformer.from_crs(4326,6933,always_xy=True))
    assert status=="GEOCODE_TOO_BROAD"

def test_time_stratified_calendar_no_positive_controls(tmp_path):
    m=get("63b_standard_calendar10")
    root=tmp_path/"links";root.mkdir()
    pd.DataFrame([
      {"event_id":"a","grid_id":"0_0","start_date":"2020-07-01",
       "end_date":"2020-07-02","primary":True,"is_high_geocode_specificity":True},
      {"event_id":"b","grid_id":"0_0","start_date":"2020-07-15",
       "end_date":"2020-07-15","primary":True,"is_high_geocode_specificity":False}
    ]).to_parquet(root/"63_event_grid_links_00000.parquet",index=False)
    out=tmp_path/"calendar";m.build(root,out,"2020-12-31")
    f=pd.read_parquet(out/"63b_calendar_grid10.parquet")
    assert f.is_onset.sum()==1
    assert not f.event_date.astype(str).str.contains("2020-07-15").any()
    assert f.stratum_id.nunique()==1

def test_conditional_softmax_finite_and_matched_group():
    m=get("66_shared_response10")
    rng=np.random.default_rng(2026)
    x=rng.normal(size=(200,4))
    y=np.zeros(200);y[np.arange(0,200,4)]=1
    starts=np.arange(0,200,4)
    res=m.fit_softmax(x,y,starts)
    assert res.success
    losses=m.eval_softmax(x,y,starts,res.x)
    assert len(losses)==50 and np.isfinite(losses).all()

def test_annual_dataset_input_and_physical_bounds():
    m=get("64_gee_context10_exports")
    from types import SimpleNamespace
    bad=SimpleNamespace(source="era5",years="1999",epochs="",model=None,
       scenario="ssp245",start_year=None,end_year=None)
    with pytest.raises(ValueError):m.make_jobs(bad)
    good=SimpleNamespace(source="ghsl",epochs="2000,2010,2020",years="",model=None,
       scenario="ssp245",start_year=None,end_year=None)
    assert len(m.make_jobs(good))==3
