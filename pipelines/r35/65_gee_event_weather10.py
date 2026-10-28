# -*- coding: utf-8 -*-
"""Stage65: production event/referent weather from ORIGINAL GF10 grid-date requests.

No small TEST tasks; user-specified 30-90 calendar-day batches.
UTC event date, not localized flood onset. P3h is TRUE rolling 6x30min
maximum, not annual raster P30 or interpolated 20km statistic.
CRS/transform EXACTLY frozen Stage62. Nulls stay null.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from datetime import date,timedelta
from ee_ease_projection import EASE_WKT

T=[10000,0,-17360000,0,-10000,7320000]
PROJECT="eco-sector-503721-b7"
def daily_image(ee,day):
    nextday=day.advance(1,"day")
    imerg=(ee.ImageCollection("NASA/GPM_L3/IMERG_V07")
           .filterDate(day,nextday).select("precipitation"))
    p24=imerg.sum().multiply(.5).rename("imerg_p24_utc_mm")
    count=imerg.count().rename("imerg_halfhour_count")
    # Rolling 3h: 43 starts from 00:00 through 21:00; six 30-minute steps.
    def window(k):
        start=day.advance(ee.Number(k).multiply(30),"minute")
        return imerg.filterDate(start,start.advance(180,"minute")).sum().multiply(.5).rename("p3h")
    p3h=ee.ImageCollection.fromImages(ee.List.sequence(0,42).map(window)).max().rename("imerg_p3h_rolling_mm")
    era=ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
    era7=era.filterDate(day.advance(-7,"day"),day).select("total_precipitation_sum")
    a7=era7.sum().multiply(1000).max(0).rename("era5_api7_mm")
    a7n=era7.count().rename("era5_api7_valid_days")
    previous=ee.Image(era.filterDate(day.advance(-1,"day"),day).first())
    soil=previous.select("volumetric_soil_water_layer_1").rename("era5_sm1_prevday")
    temp=ee.Image(era.filterDate(day,nextday).first()).select("temperature_2m").subtract(273.15).rename("era5_temp_day_c")
    return ee.Image.cat([p24,p3h,count,a7,a7n,soil,temp]).toFloat()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--asset",required=True,help="GEE uploaded yearly request CSV table")
    p.add_argument("--start",required=True,help="YYYY-MM-DD UTC start inclusive")
    p.add_argument("--days",type=int,default=30,help="Calendar days, max 90")
    p.add_argument("--folder",default="GlobalFlood_10km_Production")
    p.add_argument("--out",type=Path,default=Path("grid10_weather_jobs"))
    p.add_argument("--submit",action="store_true")
    a=p.parse_args()
    d=date.fromisoformat(a.start)
    if not 1<=a.days<=90:raise ValueError("1..90 day batches; avoid EE timeouts")
    last=d+timedelta(days=a.days)
    if d.year!=(last-timedelta(days=1)).year:
        raise ValueError("Calendar block must lie within one year")
    if not a.asset.startswith(f"projects/{a.project}/assets/"):
        raise ValueError("Asset must be in selected GEE project")
    a.out.mkdir(parents=True,exist_ok=True)
    name=f"GF10EVENT_V1_{d.strftime('%Y%m%d')}_{(last-timedelta(days=1)).strftime('%Y%m%d')}"
    print("[PLAN]",name,"production",a.asset)
    if not a.submit:print("[DRY RUN] No GEE Task");return
    import ee
    ee.Initialize(project=a.project)
    recent={str(t.get("description")):t.get("state") for t in ee.data.getTaskList()}
    if name in recent:raise RuntimeError(f"Existing EE Task {name} {recent[name]}: do not duplicate")
    table=ee.FeatureCollection(a.asset)
    proj=ee.Projection(EASE_WKT)
    def per_day(k):
        day=ee.Date(d.isoformat()).advance(ee.Number(k),"day")
        label=day.format("YYYY-MM-dd")
        subset=table.filter(ee.Filter.eq("event_date",label))
        img=daily_image(ee,day)
        def enrich(feat):
            x=ee.Number(feat.get("grid_x")).add(.5).multiply(10000)
            y=ee.Number(feat.get("grid_y")).add(.5).multiply(10000)
            point=ee.Geometry.Point([x,y],proj)
            values=img.reduceRegion(reducer=ee.Reducer.first(),
                geometry=point,crs=EASE_WKT,crsTransform=T,maxPixels=4)
            return ee.Feature(None,values).set({
                "climate_id":feat.get("climate_id"),
                "grid_id":feat.get("grid_id"),
                "event_date":label})
        return subset.map(enrich)
    result=ee.FeatureCollection(ee.List.sequence(0,a.days-1).map(per_day)).flatten()
    task=ee.batch.Export.table.toDrive(
        collection=result,description=name,folder=a.folder,
        fileNamePrefix=name.lower(),fileFormat="CSV",
        selectors=["climate_id","grid_id","event_date",
          "imerg_p24_utc_mm","imerg_p3h_rolling_mm","imerg_halfhour_count",
          "era5_api7_mm","era5_api7_valid_days","era5_sm1_prevday","era5_temp_day_c"])
    task.start()
    record={"name":name,"task_id":task.id,"asset":a.asset,"start":a.start,"days":a.days,
            "contract":"GF10_EPSG6933_NATIVE_WEATHER_V1"}
    with (a.out/"65_submissions.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(record)+"\n")
    print("[SUBMITTED]",record)

if __name__=="__main__":main()
