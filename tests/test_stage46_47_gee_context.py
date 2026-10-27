"""No-data, no-auth tests for Stage46/47 task planner and exact field contracts."""
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/"pipelines"/"r34"

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
