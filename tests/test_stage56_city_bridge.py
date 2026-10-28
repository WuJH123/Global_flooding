"""Stage56 city-level built5 modifiers: source fidelity and no event duplication."""
from pathlib import Path
import sys,importlib.util
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
R34=ROOT/"pipelines"/"r34"
def load(name):
    path=R34/f"{name}.py"
    spec=importlib.util.spec_from_file_location("test_"+name,path)
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return m

def test_weighted_city_bridge_preserves_city_ids_and_overlap():
    m=load("56_bridge_fine5_city_response")
    cities=pd.DataFrame([{"city_id":"A","base":3},{"city_id":"B","base":4}])
    grid=pd.DataFrame([
        {"grid_id":"0_0","epoch":2020,"child_built_sd":.1,
         "child_built_max":.5,"high_built_area_fraction":.2,
         "source_valid_fraction":1.},
        {"grid_id":"1_0","epoch":2020,"child_built_sd":.3,
         "child_built_max":.9,"high_built_area_fraction":.8,
         "source_valid_fraction":.9}])
    links=pd.DataFrame([{"city_id":"A","grid_id":"0_0","overlap_km2":1},
                        {"city_id":"A","grid_id":"1_0","overlap_km2":3},
                        {"city_id":"B","grid_id":"0_0","overlap_km2":2}])
    out,derived,joined=m.build(cities,grid,links)
    assert len(out)==2 and len(joined)==3
    assert np.isclose(out.set_index("city_id").loc["A","ghsl_built_5km_sd_wmean"],.25)
    assert out.set_index("city_id").loc["B","ghsl_built_5km_max_wmean"]==.5

def test_city_bridge_refuses_duplicate_grid_city():
    m=load("56_bridge_fine5_city_response")
    cities=pd.DataFrame({"city_id":["A"]})
    grid=pd.DataFrame([{"grid_id":"0_0","epoch":2020,"child_built_sd":.1,
         "child_built_max":.5,"high_built_area_fraction":.2,
         "source_valid_fraction":1.}])
    links=pd.DataFrame([{"city_id":"A","grid_id":"0_0","overlap_km2":1},
                        {"city_id":"A","grid_id":"0_0","overlap_km2":2}])
    with pytest.raises(ValueError,match="Repeated"):m.build(cities,grid,links)

def test_stage34b_optin_moderators():
    source=(ROOT/"pipelines"/"r33"/"34b_continuous_environment_response.py").read_text(encoding="utf-8")
    for name in ("ghsl_built_5km_sd_wmean","ghsl_built_5km_max_wmean",
                 "ghsl_built_5km_high_area_wmean"):
        assert name in source
