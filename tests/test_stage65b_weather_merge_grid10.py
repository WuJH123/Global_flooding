"""GF10 Stage65b strict weather join synthetic integration test."""
from pathlib import Path
import importlib.util,sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]/"pipelines"/"r35"
def load():
    sp=importlib.util.spec_from_file_location("gf10_stage65b",ROOT/"65b_merge_weather10.py")
    m=importlib.util.module_from_spec(sp);sys.modules[sp.name]=m;sp.loader.exec_module(m);return m

def test_weather_join_ids_and_complete_days(tmp_path):
    cal=tmp_path/"cal.parquet";requests=tmp_path/"requests.parquet"
    days=["2020-07-01","2020-07-08"]
    pd.DataFrame([{"stratum_id":"0_0|2020-07-01","grid_id":"0_0",
        "event_date":pd.Timestamp(day),"is_onset":int(i==0),"eligibility":1,
        "reported_positive":int(i==0)} for i,day in enumerate(days)]).to_parquet(cal,index=False)
    pd.DataFrame([{"climate_id":"0_0|"+day,"grid_id":"0_0","event_date":day,
        "grid_x":0,"grid_y":0,"longitude":0.,"latitude":0.,"year":2020,"month":7}
        for day in days]).to_parquet(requests,index=False)
    folder=tmp_path/"csv";folder.mkdir()
    pd.DataFrame([{"climate_id":"0_0|"+day,"grid_id":"0_0","event_date":day,
        "imerg_p24_utc_mm":15.,"imerg_p3h_rolling_mm":5.,
        "imerg_halfhour_count":48,"era5_api7_mm":20.,
        "era5_api7_valid_days":7,"era5_sm1_prevday":.25,
        "era5_temp_day_c":26.} for day in days]).to_csv(
            folder/"gf10event_v1_20200701_20200730.csv",index=False)
    out=tmp_path/"merged"
    m=load();m.merge(cal,requests,folder,out)
    d=pd.read_parquet(out/"65b_standard_daily_grid10.parquet")
    assert len(d)==2
    assert d.imerg_halfhour_count.min()==48
    assert d.era5_api7_valid_days.min()==7
