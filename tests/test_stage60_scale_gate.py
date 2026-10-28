"""Scale gate test: no 5/10km response winner without same-event meteorology."""
import importlib.util,sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/"pipelines"/"r34"/"60_scale_decision_gate.py"
spec=importlib.util.spec_from_file_location("r34_60_test",PATH)
mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)

def test_scale_gate_does_not_invent_finer_model_winner(tmp_path):
    rows=[]
    for i in range(30):
        for scale in (5,10,20):
            for offset in (0.,.5):
                rows.append({"city_id":str(i),"event_id":str(i),
                     "scale_km":scale,"origin_offset_fraction":offset,
                     "precision_class":"small" if i%2 else "broad",
                     "touched_cells":1 if scale==20 else (1 if i%3 else 2),
                     "max_geocode_overlap_fraction":1. if scale==20 else .7})
    path=tmp_path/"location.parquet";pd.DataFrame(rows).to_parquet(path)
    bound=pd.DataFrame({"city_id":[str(i) for i in range(30)],
                        "status":["PASS_QUADRATURE"]*30,
                        "within_city_5km_sd":[.1]*30})
    result,score=mod.evaluate(path,bound,{"n_valid":300})
    assert result["operational_main_response_scale_km"]==20
    assert result["environment_native_cache_scale_km"]==5
    assert result["next_response_candidate_scale_km"]==10
    assert result["response_scale_winner"].startswith("NOT_DETERMINABLE")
    assert set(score.scale_km)=={5,10,20}
