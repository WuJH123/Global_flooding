# -*- coding: utf-8 -*-
"""Stage45 V3: E2020 GHSL-SMOD broad urban *denominator* on global 20km EASE grid.

V2 failed with 'Failed to compute bounds of <Geometry>' because the requested
projected rectangle extended past the valid EPSG:6933 Y domain.
V3 uses a numerically verified, 20km-aligned conservative extent.
No GEE call without --probe or --submit; no Task without --submit.
The output is not a city flood-risk or absolute probability surface.
"""
from __future__ import annotations
import argparse
from ee_ease_projection import EASE_WKT, probe_projection
from ee_safe_global_export import (
    SAFE_BOUNDS, SAFE_TRANSFORM, SAFE_WIDTH, SAFE_HEIGHT,
    make_region,server_probe_region,validate_bounds,
)

PROJECT = "eco-sector-503721-b7"
VERSION = "SMOD2020_20KM_V3"
TRANSFORM = SAFE_TRANSFORM
SMOD_URBAN = (21,22,23,30)

def source_image(ee):
    smod = ee.Image("JRC/GHSL/P2023A/GHS_SMOD_V2-0/2020").select("smod_code")
    valid = smod.neq(-200).unmask(0)
    urban = smod.eq(21).Or(smod.eq(22)).Or(smod.eq(23)).Or(smod.eq(30)).unmask(0)
    # Native 1km categorical fractions are averaged using overlap weights
    # only when EE Export asks for 20km target pixels. Do not force a
    # full-globe .reproject() or derive a new EPSG:6933 geometry from source.
    def fraction(image, name):
        return (image.toFloat()
                .reduceResolution(reducer=ee.Reducer.mean(),maxPixels=2048)
                .rename(name))
    return ee.Image.cat(
        [fraction(urban.And(valid), "urban_fraction_wholecell"),
         fraction(valid, "valid_fraction")]
    ).toFloat()

def task_name(test=False):
    return VERSION + ("_TEST" if test else "")

def check_history(ee,name):
    tasks=[t for t in ee.data.getTaskList()
           if str(t.get("description",""))==name]
    if tasks:
        raise RuntimeError(
            f"Task {name} exists in recent EE history, states="
            f"{[t.get('state') for t in tasks]}. No automatic resubmission. "
            "Inspect Tasks/Drive and choose a new version for a deliberate retry."
        )

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--folder",default="GlobalFlood_E2020_Urban")
    p.add_argument("--probe",action="store_true",help="Check full export region on EE server; no task")
    p.add_argument("--test",action="store_true",help="Export only 10x10 target cells near India")
    p.add_argument("--submit",action="store_true")
    a=p.parse_args()
    validate_bounds()
    name=task_name(a.test)
    print("[PLAN]",name,"full aligned shape",SAFE_WIDTH,SAFE_HEIGHT,
          "test",a.test,"old V2 is obsolete")
    print("[EXTENT]",SAFE_BOUNDS,"EPSG:6933 conservative domain; polar/dateline slivers excluded")
    if not (a.probe or a.submit):
        print("[DRY RUN] 0 EE calls, 0 tasks");return
    import ee
    ee.Initialize(project=a.project)
    proj=probe_projection(ee)
    region=server_probe_region(ee,proj,test=a.test)
    if a.probe or a.submit:
        # Physically exercise 20km reprojection at a small inhabited sample,
        # without exporting any global map or materializing all 1.27m pixels.
        probe=source_image(ee).reduceRegion(
            reducer=ee.Reducer.mean(),geometry=make_region(ee,proj,True),
            crs=proj,scale=20000,maxPixels=300, tileScale=4).getInfo()
        urban=probe.get("urban_fraction_wholecell")
        valid=probe.get("valid_fraction")
        if urban is None or valid is None or not (0<=float(urban)<=1 and 0<=float(valid)<=1):
            raise RuntimeError("SMOD2020 pixel probe did not produce physical fractions: "+str(probe))
        print("[SMOD PROBE PASS]",probe)
    if not a.submit:
        print("[PROBE ONLY] No batch Task created");return
    check_history(ee,name)
    task=ee.batch.Export.image.toDrive(
        image=source_image(ee),description=name,folder=a.folder,
        fileNamePrefix=name.lower(),region=region,crs=EASE_WKT,
        crsTransform=TRANSFORM,maxPixels=2_000_000,fileFormat="GeoTIFF")
    task.start()
    print("[SUBMITTED]",name,task.id,
          "download only the COMPLETED V3 GeoTIFF; NEVER merge failed V2")
if __name__=="__main__":main()
