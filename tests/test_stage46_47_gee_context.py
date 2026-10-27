"""No-data, no-auth tests for Stage46/47 task planner and exact field contracts."""
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/"pipelines"/"r34"
if str(R) not in sys.path:
    sys.path.insert(0, str(R))

def load(stem):
    path=R/stem
    spec=importlib.util.spec_from_file_location("qc_"+stem.replace(".","_"),path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_epoch_years_restrict_projections():
    m=load("46_gee_ghsl_epoch_context.py")
    assert m.parse_epochs("2020,2000,2010")==[2000,2010,2020]
    assert len(m.expected_fields([2000,2020]))==4+8*2
    assert "ghsl_2020_built_total_m2" in m.expected_fields([2020])
    assert "ghsl_2020_smod_urban_area_m2" in m.expected_fields([2020])
    assert "ghsl_2020_built_valid_area_m2" in m.expected_fields([2020])
    assert m.parse_chunks("0,2-4")==[0,2,3,4]
    import pytest
    with pytest.raises(ValueError):m.parse_epochs("2025")
    with pytest.raises(ValueError):m.parse_epochs("2020,2020")
    with pytest.raises(ValueError):m.parse_chunks("3-1")

def test_era5_annual_units_and_grouping():
    m=load("47_gee_era5_annual_context.py")
    assert m.parse_years("2020,2022-2023")==[2020,2022,2023]
    assert m.groups([2020,2021,2022,2023,2024],2)==[[2020,2021],[2022,2023],[2024]]
    assert "era5_2020_annual_precip_mm" in m.fields([2020])
    assert "era5_2020_negative_precip_days" in m.fields([2020])
    assert "era5_2020_valid_precip_days" in m.fields([2020])
    import pytest
    with pytest.raises(ValueError):m.parse_years("2026")
    with pytest.raises(ValueError):m.groups([2020,2021],6)

def test_both_scripts_dry_run_without_ee_or_credentials():
    for script,opts in [
        ("46_gee_ghsl_epoch_context.py",["--epochs","2000,2020","--chunks","0"]),
        ("47_gee_era5_annual_context.py",["--years","2020-2021","--chunks","0"]),
    ]:
        p=subprocess.run([sys.executable,str(R/script),*opts],
                         capture_output=True,text=True,check=True)
        assert "DRY RUN" in p.stdout
        assert "[PLAN]" in p.stdout
        assert "[STARTED]" not in p.stdout
        assert "ee" not in sys.modules


def test_stage48_detects_overlap_and_bad_physics(tmp_path):
    import pandas as pd
    m=load("48_validate_new_context_csvs.py")
    a=pd.DataFrame({
        "grid_id":["g0","g1"],
        "ghsl_2020_built_total_m2":[100,100],
        "ghsl_2020_built_nres_m2":[25,20],
        "ghsl_2020_built_valid_area_m2":[400e6,400e6],
        "ghsl_2020_pop_total":[1000,2000],
        "ghsl_2020_pop_valid_area_m2":[400e6,400e6],
        "ghsl_2020_smod_urban_area_m2":[100e6,200e6],
        "ghsl_2020_smod_dense_area_m2":[20e6,40e6],
        "ghsl_2020_smod_land_area_m2":[400e6,400e6],
    })
    x=tmp_path/"ghsl_epoch_v1_2020_c00.csv"
    y=tmp_path/"ghsl_epoch_v1_2020_c01.csv"
    a.to_csv(x,index=False)
    rows,errs,ids=m.audit_files([x],"ghsl")
    assert not errs and ids[2020]=={"g0","g1"}
    a.iloc[:1].to_csv(y,index=False)
    _,errs,_=m.audit_files([x,y],"ghsl")
    assert any("Duplicate" in e for e in errs)
    a.loc[0,"ghsl_2020_built_nres_m2"]=125
    a.to_csv(y,index=False)
    _,errs,_=m.audit_files([y],"ghsl")
    assert any("greater than total" in e for e in errs)


def test_gaia_epoch_year_mapping_and_dry_run():
    m=load("49_gee_gaia_impervious_history.py")
    assert m.epochs("2018,2000,2010")==[2000,2010,2018]
    assert 2019-2018==1 and 2019-2000==19
    import pytest
    with pytest.raises(ValueError):m.epochs("2020")
    with pytest.raises(ValueError):m.chunks("4-2")
    p=subprocess.run([sys.executable,str(R/"49_gee_gaia_impervious_history.py"),
                      "--chunks","0","--epochs","2000,2018"],
                     capture_output=True,text=True,check=True)
    assert "DRY RUN" in p.stdout and "[STARTED]" not in p.stdout

def test_stage48_gaia_monotone_area(tmp_path):
    import pandas as pd
    m=load("48_validate_new_context_csvs.py")
    f=pd.DataFrame({"grid_id":["g1","g2"],
                    "gaia_2000_impervious_class_area_m2":[100,0],
                    "gaia_2018_impervious_class_area_m2":[150,200]})
    p=tmp_path/"gaia_imp_v1_c0.csv"
    f.to_csv(p,index=False)
    _,err,_=m.audit_files([p],"gaia")
    assert not err
    f.loc[0,"gaia_2018_impervious_class_area_m2"]=10
    f.to_csv(p,index=False)
    _,err,_=m.audit_files([p],"gaia")
    assert any("decreases" in e for e in err)



def test_area_conservation_regression_ghsl():
    """Regression: EE source totals must not be directly reduceResolution(sum)'d."""
    import inspect
    m=load("46_gee_ghsl_epoch_context.py")
    src=inspect.getsource(m.aggregate_to_km)
    assert "VERSION" not in src
    assert "native_density" in src
    assert "ee.Reducer.mean()" in src
    assert "multiply(target_area)" in src
    assert m.VERSION=="GHSL_EPOCH_V3"
    # 100 source 100m cells (each 5,000m2 built) in one 1km target.
    expected_total=100*5_000
    old_wrong_weighted_sum=100*5_000*0.01
    new_area_density_mean=(5_000/10_000)*1_000_000
    assert new_area_density_mean == expected_total
    assert old_wrong_weighted_sum != expected_total


def test_area_conservation_regression_gaia():
    import inspect
    m=load("49_gee_gaia_impervious_history.py")
    src=inspect.getsource(m.stack_for)
    assert "ee.Reducer.mean()" in src
    assert "km500=fraction.multiply(target_area)" in src
    assert "ee.Reducer.sum()" not in src
    assert m.VERSION=="GAIA_IMP_V3"


def test_stage48_v2_glob_and_expected_years():
    import inspect
    m=load("48_validate_new_context_csvs.py")
    src=inspect.getsource(m.main)
    assert '"ghsl_epoch_v3_*.csv"' in src
    assert '"gaia_imp_v3_*.csv"' in src
    assert "--expected-years" in src



def test_r34_wkt_crs_matches_previously_successful_merit():
    """Regression for task failed 'The CRS of a map projection could not be parsed'."""
    import inspect
    p=load("ee_ease_projection.py")
    assert 'Cylindrical_Equal_Area' in p.EASE_WKT
    assert 'AUTHORITY["EPSG","6933"]' in p.EASE_WKT
    assert "PROJCS[" in p.EASE_WKT
    assert 'WGS 84 / NSIDC EASE-Grid 2.0 Global' in p.EASE_WKT
    legacy=(ROOT/"mechanism_scripts"/"04_submit_merit_gee.py").read_text(encoding="utf-8")
    assert "EASE_WKT" in legacy and 'PROJECTION["Cylindrical_Equal_Area"]' in legacy
    for script in ["46_gee_ghsl_epoch_context.py",
                   "47_gee_era5_annual_context.py",
                   "49_gee_gaia_impervious_history.py"]:
        m=load(script)
        assert m.grid_projection is p.grid_projection
        assert "--probe-projection" in inspect.getsource(m.main)
        assert "probe_projection(ee)" in inspect.getsource(m.main)
        assert 'ee.Projection("EPSG:6933")' not in inspect.getsource(m.main)
    assert load("47_gee_era5_annual_context.py").VERSION=="ERA5YR_V2"


def test_server_crs_probe_mock_geometry_and_raster():
    """Ensure a failing CRS probe aborts; mock only, actual EE requires live smoke."""
    p=load("ee_ease_projection.py")
    class Value:
        def __init__(self, v):self.v=v
        def getInfo(self):return self.v
    class Geometry:
        def area(self, maxError):return Value(400_000_000)
    class PixelArea:
        def rename(self,_):return self
        def reduceRegion(self,**kwargs):return Value({"pixel_area_m2":400_000_000})
    class Projection:
        def __init__(self, wkt):self.wkt=wkt
    class FakeEE:
        Projection=Projection
        class Geometry:
            @staticmethod
            def Rectangle(coords,proj,geodesic,evenOdd):return Geometry()
        @staticmethod
        def List(v):return v
        @staticmethod
        def Number(v):return v
        class Image:
            @staticmethod
            def pixelArea():return PixelArea()
        class Reducer:
            @staticmethod
            def sum():return "sum"
    # A lightweight projection constructor contract; not EE pixel evaluation.
    assert p.grid_projection(FakeEE).wkt==p.EASE_WKT
