# -*- coding: utf-8 -*-
"""Stage62: PRODUCTION 10km E2020 global urban-domain raster, EE->Drive.

Exactly 2 longitude stripes, zero test tasks, one shared global 10km lattice.
Full cells are 100 km2 on EPSG:6933; 'urban area' uses measured fraction,
not 100 km2. This is a 2020 *sampling domain*, NOT flood-risk labels.
"""
from __future__ import annotations
import argparse,hashlib,json,math
from pathlib import Path
from pyproj import Transformer
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"r34"))
from ee_ease_projection import EASE_WKT

PROJECT="eco-sector-503721-b7"
VERSION="GF10_SMOD2020_V1"
CRS="EPSG:6933"
CELL_M=10000
XMIN,XMAX=-17360000,17360000
YMIN,YMAX=-7320000,7320000
WIDTH=(XMAX-XMIN)//CELL_M
HEIGHT=(YMAX-YMIN)//CELL_M
TRANSFORM=[CELL_M,0,XMIN,0,-CELL_M,YMAX]
STRIPES=((XMIN,0),(0,XMAX))
SOURCE="JRC/GHSL/P2023A/GHS_SMOD_V2-0/2020"
URBAN_CODES=(21,22,23,30)

def validate_local():
    assert WIDTH==3472 and HEIGHT==1464
    t=Transformer.from_crs(CRS,"EPSG:4326",always_xy=True)
    for x in (XMIN,XMAX):
        for y in (YMIN,YMAX):
            lo,la=t.transform(x,y)
            if not(math.isfinite(lo) and math.isfinite(la) and abs(la)<86.01 and abs(lo)<=180):
                raise ValueError(f"Outside EPSG:6933 validity: {(x,y)} -> {(lo,la)}")
    for a,b in STRIPES:
        if a>=b or a%CELL_M or b%CELL_M:raise ValueError("Stripe misaligned")
    if sum(b-a for a,b in STRIPES)!=(XMAX-XMIN):raise ValueError("Stripe gap or overlap")
    return {"crs":CRS,"transform":TRANSFORM,"width":WIDTH,"height":HEIGHT,
            "nominal_full_cell_area_m2":CELL_M**2,
            "region":(XMIN,YMIN,XMAX,YMAX),
            "stripes":STRIPES,"source":SOURCE,
            "urban_codes":URBAN_CODES,
            "edge_caveat":"EPSG:6933 valid to 86deg; projected border strips excluded"}

def smod_image(ee):
    image=ee.Image(SOURCE).select("smod_code")
    valid=image.neq(-200).unmask(0).toFloat()
    urban=(image.eq(21).Or(image.eq(22)).Or(image.eq(23)).Or(image.eq(30))
           .unmask(0).toFloat())
    # BOTH images share original source projection: weighted native-pixel mean
    # at 10km target; source valid fraction separately retained.
    def mean(x,n):
        return x.reduceResolution(ee.Reducer.mean(),maxPixels=2048).rename(n)
    return ee.Image.cat([mean(urban,"urban_fraction_fullcell"),
                         mean(valid,"source_valid_fraction")]).toFloat()

def submit(a):
    info=validate_local()
    out=a.out;out.mkdir(parents=True,exist_ok=True)
    plan={"project":a.project,"version":VERSION,"destination_folder":a.folder,**info}
    (out/"62_grid_contract.json").write_text(json.dumps(plan,indent=2),encoding="utf-8")
    print("[GRID CONTRACT]",WIDTH,HEIGHT,"cells",WIDTH*HEIGHT)
    if not a.submit:
        print("[PLAN ONLY] no GEE task");return
    import ee
    ee.Initialize(project=a.project)
    proj=ee.Projection(EASE_WKT)
    recent={str(t.get("description")):t for t in ee.data.getTaskList()}
    ledger=out/"62_gee_submissions.jsonl"
    # no small test tasks: production only; fail-fast local geometry check performed.
    for idx,(x0,x1) in enumerate(STRIPES):
        if a.stripes and idx not in a.stripes:continue
        name=f"{VERSION}_S{idx:02d}"
        if name in recent:
            raise RuntimeError(f"Task {name} already exists ({recent[name].get('state')}); verify result before resubmission")
        region=ee.Geometry.Rectangle([x0,YMIN,x1,YMAX],proj,False)
        task=ee.batch.Export.image.toDrive(
            image=smod_image(ee),description=name,folder=a.folder,
            fileNamePrefix=name.lower(),region=region,crs=EASE_WKT,
            crsTransform=TRANSFORM,maxPixels=10000000,
            fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized":True})
        task.start()
        record={"task_name":name,"task_id":task.id,"stripe":idx,"xrange_m":[x0,x1]}
        with ledger.open("a",encoding="utf-8") as f:f.write(json.dumps(record)+"\n")
        print("[SUBMITTED]",record)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--folder",default="GlobalFlood_10km_Production")
    p.add_argument("--out",type=Path,default=Path("grid10_contract"))
    p.add_argument("--stripes",default="",help="0,1 or blank for both")
    p.add_argument("--submit",action="store_true",help="Only production task(s), NO test")
    a=p.parse_args()
    a.stripes={int(x) for x in a.stripes.split(",")} if a.stripes else set()
    if a.stripes and not a.stripes<={0,1}:raise ValueError("Only stripe 0/1")
    submit(a)
if __name__=="__main__":main()
