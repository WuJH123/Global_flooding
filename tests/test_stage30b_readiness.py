"""Stage30b/readiness no external data smoke tests."""
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]

def module(p):
    spec=importlib.util.spec_from_file_location("test_new_"+p.stem,p)
    out=importlib.util.module_from_spec(spec);spec.loader.exec_module(out)
    return out

def test_readiness_without_data(tmp_path):
    m=module(ROOT/"tools"/"stage29_35_readiness.py")
    status,errors=m.checks(tmp_path)
    assert "climate_panel" in status
    assert any("absent" in s for s in errors)

def test_city_assemble_no_duplicate_ids(tmp_path):
    m=module(ROOT/"pipelines"/"r33"/"30b_assemble_city_environment.py")
    n=60
    gh=pd.DataFrame({"city_id":["c"+str(i) for i in range(n)],
                     "epoch":2020,
                     "ghsl_built_fraction_wmean":np.linspace(.1,.8,n)})
    st=pd.DataFrame({"grid_id":["g"+str(i) for i in range(n)],
                     "city_id":["c"+str(i) for i in range(n)],
                     "merit_local_relief_m_approx":np.linspace(5,300,n),
                     "basin_slope_degrees":np.linspace(1,15,n)})
    table=m.assemble(gh,st,epoch=2020)
    assert len(table)==n
    assert table.grid_count_primary.eq(1).all()
    with pytest.raises(RuntimeError):
        m.assemble(pd.concat([gh,gh.iloc[[0]]]),st,epoch=2020)
