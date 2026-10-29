# -*- coding: utf-8 -*-
"""Stage64: all climate/static annual/epoch metrics use the FROZEN GF10 lattice.

GEE image exports only (no cloud preview/test tasks). One stripe/year/epoch
per task by default to limit memory/timeouts. Only upstream native support;
output 10km grid does not increase observation resolution.
GHSL population + built are COUNTS/AREAS per 100m source pixel, summed;
IMERG half-hour precipitation rates mm/h * 0.5 h; ERA5 daily mm m*1000.
Future CMIP6 daily rates kg m-2 s-1 * 86400 => mm/day.
"""
from __future__ import annotations
import argparse,json,hashlib,math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"r34"))
from ee_ease_projection import EASE_WKT
from pathlib import Path

PROJECT="eco-sector-503721-b7"
GRID_SCALE=10000
TOTAL_ROWS=1464
GHSL_CONTRACT='V2_NATIVE_DENSITY_AREA'
T=[10000,0,-17360000,0,-10000,7320000]
STRIPES=((-17360000,0),(0,17360000))
YMIN,YMAX=-7320000,7320000

def parse_csv_int(v):
    if not v:return []
    vals=[int(x) for x in v.split(",")]
    if not vals or len(set(vals))!=len(vals):raise ValueError("empty/repeated period")
    return vals

def ghsl(ee, yr, region):
    """Native-pixel area density -> frozen 10km area-conservative totals.

    ee.Reducer.sum weights by OUTPUT covered fraction, not native pixel
    fraction. Divide counts/m2 by native pixel area, sum weighted density,
    then multiply by target pixel area. Separate POP/BUILT before reproj.
    """
    pop=ee.Image(f"JRC/GHSL/P2023A/GHS_POP/{yr}").select("population_count")
    built=ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{yr}")
    target_area=ee.Image.pixelArea().reproject(crs=EASE_WKT,crsTransform=T)
    def integrate(source,name):
        native=source.projection()
        area_native=ee.Image.pixelArea().setDefaultProjection(native)
        density=source.toFloat().divide(area_native).rename(name)
        reduced=(density.clip(region)
                 .reduceResolution(ee.Reducer.sum(),
                                   bestEffort=False,maxPixels=16384)
                 .reproject(crs=EASE_WKT,crsTransform=T))
        return reduced.multiply(target_area).rename(name)
    return ee.Image.cat([
        integrate(pop,"population_count"),
        integrate(built.select("built_surface"),"built_total_m2"),
        integrate(built.select("built_surface_nres"),"built_nres_m2")
    ]).toFloat()

def era5(ee,yr):
    col=ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(
        f"{yr}-01-01",f"{yr+1}-01-01")
    def daily(im):
        rain=im.select("total_precipitation_sum").multiply(1000).max(0).rename("rain_mm")
        temp=im.select("temperature_2m").subtract(273.15).rename("temp_c")
        soil=im.select("volumetric_soil_water_layer_1").rename("soil1")
        return ee.Image.cat([rain,temp,soil]).copyProperties(im,["system:time_start"])
    d=col.map(daily)
    rain=d.select("rain_mm")
    return ee.Image.cat([
        rain.sum().rename("era5_annual_rain_mm"),
        rain.max().rename("era5_p24_max_mm"),
        rain.reduce(ee.Reducer.percentile([95])).rename("era5_daily_p95_mm"),
        rain.map(lambda im:im.gte(20)).sum().rename("era5_wet20_days"),
        d.select("temp_c").mean().rename("era5_temp_mean_c"),
        d.select("soil1").mean().rename("era5_soil1_mean"),
        rain.count().rename("era5_valid_days")
    ]).toFloat()

def imerg(ee,yr):
    source=ee.ImageCollection("NASA/GPM_L3/IMERG_V07").filterDate(
        f"{yr}-01-01",f"{yr+1}-01-01").select("precipitation")
    # Source values are rate mm/hr at 30min. Sum rate * 0.5 h.
    # Max 30-minute depth, NOT 3-hour or 24-hour rolling maximum.
    depth=source.map(lambda im:im.multiply(.5).rename("depth_mm"))
    return ee.Image.cat([
        depth.sum().rename("imerg_annual_total_mm"),
        depth.max().rename("imerg_p30_max_mm"),
        depth.count().rename("imerg_valid_halfhour_steps")
    ]).toFloat()

def cmip6(ee,model,scenario,start,end):
    col=(ee.ImageCollection("NASA/GDDP-CMIP6")
          .filter(ee.Filter.eq("model",model))
          .filter(ee.Filter.eq("scenario",scenario))
          .filterDate(f"{start}-01-01",f"{end+1}-01-01"))
    def daily(im):
        rain=im.select("pr").multiply(86400).max(0).rename("rain_mm")
        temp=im.select("tas").subtract(273.15).rename("temp_c")
        return ee.Image.cat([rain,temp])
    d=col.map(daily);rain=d.select("rain_mm")
    # Historical/future comparable *daily* climate statistics only.
    return ee.Image.cat([
        rain.mean().multiply(365.25).rename("cmip_annualized_precip_mm"),
        rain.reduce(ee.Reducer.percentile([99])).rename("cmip_daily_p99_mm"),
        rain.map(lambda im:im.gte(20)).mean().rename("cmip_fraction_wet20_days"),
        d.select("temp_c").mean().rename("cmip_temp_mean_c"),
        rain.count().rename("cmip_available_days"),
    ]).toFloat()

def make_jobs(a):
    if a.source=="ghsl":
        periods=parse_csv_int(a.epochs)
        if any(y>2020 or y<1975 or y%5 for y in periods):
            raise ValueError("Historical GHSL epochs must be 1975-2020 multiples of 5")
        return [(f"E{y}",{"year":y}) for y in periods]
    if a.source in ("era5","imerg"):
        years=parse_csv_int(a.years)
        if any(y<2000 or y>2025 for y in years):
            raise ValueError("Historical analysis configured for 2000-2025; no partial 2026")
        return [(f"Y{y}",{"year":y}) for y in years]
    if a.source=="cmip6":
        if a.model is None or a.scenario not in ("historical","ssp245","ssp585"):
            raise ValueError("CMIP6 requires --model and --scenario")
        if not a.start_year or not a.end_year or a.end_year<a.start_year:
            raise ValueError("CMIP6 explicit --start-year/--end-year required")
        if a.scenario=="historical" and not (1950<=a.start_year<=a.end_year<=2014):
            raise ValueError("CMIP historical only through 2014")
        if a.scenario!="historical" and not(2015<=a.start_year<=a.end_year<=2100):
            raise ValueError("CMIP futures require 2015-2100")
        return [(f"{a.model}_{a.scenario}_{a.start_year}_{a.end_year}",
                {"model":a.model,"scenario":a.scenario,
                 "start":a.start_year,"end":a.end_year})]
    raise ValueError("Unknown source")

def validate_row_parts(source, row_parts):
    """Local hard gate: avoid the known full-stripe GHSL reprojection failure."""
    if row_parts < 1 or TOTAL_ROWS % row_parts:
        raise ValueError("row-parts must be a positive divisor of 1464")
    if source == "ghsl" and row_parts < 4:
        raise ValueError(
            "GHSL V2 requires --row-parts 4 (or 6, 8, 12, 24) to bound "
            "native 100m reprojection; unpartitioned global stripes are prohibited"
        )

def run(a):
    import importlib.util
    p=Path(__file__).with_name("62_gee_freeze_grid10.py")
    spec=importlib.util.spec_from_file_location("gf10_freeze",p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.validate_local()
    jobs=make_jobs(a)
    if not jobs:raise ValueError("No jobs")
    validate_row_parts(a.source, a.row_parts)
    selected=[i for i in range(2) if a.stripes is None or i in a.stripes]
    row_indices=range(a.row_parts) if a.rows is None else sorted(a.rows)
    if any(r<0 or r>=a.row_parts for r in row_indices):
        raise ValueError("row index outside parts")
    tasklist=[(period,s,details,r) for period,details in jobs for s in selected for r in row_indices]
    print("[PLAN]",a.source,"production tasks",len(tasklist))
    for period,s,_,r in tasklist:print("[PLAN]",a.source,period,"stripe",s,"row",r,"of",a.row_parts)
    a.out.mkdir(parents=True,exist_ok=True)
    if not a.submit:
        print("[DRY RUN] No EE task created");return
    import ee
    ee.Initialize(project=a.project)
    proj=ee.Projection(EASE_WKT)
    recent={t.get("description"):t.get("state") for t in ee.data.getTaskList()}
    ledger=a.out/"64_production_submissions.jsonl"
    previous=set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:previous.add(json.loads(line)["name"])
            except (ValueError,KeyError):pass
    for period,stripe,details,row in tasklist:
        prefix={"ghsl":"GHS10","era5":"ERA10","imerg":"IMG10","cmip6":"CMIP10"}[a.source]
        version="V2" if a.source=="ghsl" else "V1"
        suffix=f"_R{row:02d}of{a.row_parts:02d}" if a.row_parts>1 else ""
        name=f"{prefix}_{version}_{period}_S{stripe:02d}{suffix}"
        if len(name)>100:raise ValueError("EE description too long")
        if recent.get(name) in ("FAILED", "CANCELLED"):
            raise RuntimeError(
                f"Existing EE task {name} is {recent[name]}; --resume only "
                "skips submissions and cannot repair failed exports. "
                "Inspect the task error and plan a distinct recovery layout."
            )
        if name in recent or name in previous:
            if a.resume:
                print("[SKIP SUBMITTED]",name,recent.get(name,"local ledger"))
                continue
            raise RuntimeError(f"Existing EE task {name} {recent.get(name,'local ledger')}; pass --resume to SKIP")
        x0,x1=STRIPES[stripe]
        rows_per=TOTAL_ROWS//a.row_parts
        top=YMAX-row*rows_per*GRID_SCALE
        bottom=top-rows_per*GRID_SCALE
        region=ee.Geometry.Rectangle([x0,bottom,x1,top],proj,False)
        expected_pixels=((x1-x0)//GRID_SCALE)*rows_per
        if expected_pixels<=0 or expected_pixels>10000000:
            raise RuntimeError("Incorrect fixed-grid tile dimension")
        if a.source=="ghsl":
            img=ghsl(ee,details["year"],region)
        elif a.source=="era5":
            img=era5(ee,details["year"])
        elif a.source=="imerg":
            img=imerg(ee,details["year"])
        else:
            img=cmip6(ee,details["model"],details["scenario"],details["start"],details["end"])
        task=ee.batch.Export.image.toDrive(
            image=img,description=name,folder=a.folder,fileNamePrefix=name.lower(),
            region=region,crs=EASE_WKT,crsTransform=T,
            maxPixels=10000000,fileFormat="GeoTIFF")
        task.start()
        record={"name":name,"id":task.id,"source":a.source,
                "stripe":stripe,"row":row,"row_parts":a.row_parts,
                "target_pixels":expected_pixels,"details":details,
                "ghsl_contract":GHSL_CONTRACT if a.source=="ghsl" else None}
        with ledger.open("a",encoding="utf-8") as f:
            f.write(json.dumps(record)+"\n")
        print("[STARTED]",record)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True,choices=["ghsl","era5","imerg","cmip6"])
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--out",type=Path,default=Path("grid10_gee_outputs"))
    p.add_argument("--folder",default="GlobalFlood_10km_Production")
    p.add_argument("--epochs",default="2000,2005,2010,2015,2020")
    p.add_argument("--years",default="2000,2005,2010,2015,2020,2021,2022,2023,2024,2025")
    p.add_argument("--model")
    p.add_argument("--scenario",default="ssp245")
    p.add_argument("--start-year",type=int)
    p.add_argument("--end-year",type=int)
    p.add_argument("--stripes",default="")
    p.add_argument("--row-parts",type=int,default=1,
                   help="split half-globes into fixed 10km latitude tiles; recommend 4 for GHSL")
    p.add_argument("--rows",default="",help="comma-separated row tiles, optional")
    p.add_argument("--resume",action="store_true",help="skip previously submitted tasks, never resubmit")
    p.add_argument("--submit",action="store_true")
    a=p.parse_args()
    a.stripes={int(x) for x in a.stripes.split(",")} if a.stripes else None
    if a.stripes is not None and not a.stripes<={0,1}:raise ValueError("Invalid stripe")
    a.rows={int(x) for x in a.rows.split(",")} if a.rows else None
    run(a)
if __name__=="__main__":main()
