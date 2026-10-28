"""Stage61 small synthetic equal-area city intersections, no GEE."""
from pathlib import Path
import importlib.util,sys
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box
ROOT=Path(__file__).resolve().parents[1]
f=ROOT/"pipelines"/"r34"/"61_city_environment_scale_sensitivity.py"
spec=importlib.util.spec_from_file_location("test_r34_61",f)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

def test_city_area_weighted_cell_fraction_and_variance():
    grid=pd.DataFrame({"grid_x":[0,1],"grid_y":[0,0],
                       "built_fraction_cell":[.1,.5],
                       "source_valid_fraction":[1.,1.]})
    geom=box(0,0,10000,5000)
    summary=m.city_scale_summary(geom,grid,5)
    assert summary["status"]=="PASS_CONTEXTUAL_PROXY"
    assert summary["n_valid_cells"]==2
    assert summary["mean_built_fraction"]==pytest.approx(.3)
    assert summary["sd_built_fraction"]==pytest.approx(.2)
    assert summary["covered_grid_overlap_fraction"]==pytest.approx(1.)
    assert summary["fraction_area_built_ge20pct"]==pytest.approx(.5)

def test_city_source_coverage_is_not_imputed_zero():
    grid=pd.DataFrame({"grid_x":[0,1],"grid_y":[0,0],
                       "built_fraction_cell":[.1,.5],
                       "source_valid_fraction":[1.,0.]})
    r=m.city_scale_summary(box(0,0,10000,5000),grid,5)
    assert r["mean_built_fraction"]==pytest.approx(.1)
    assert r["source_weighted_overlap_fraction"]==pytest.approx(.5)
