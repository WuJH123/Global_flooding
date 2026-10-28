# -*- coding: utf-8 -*-
"""Stage45: export complete E2020 GHSL-SMOD urban coverage to exact 20km EASE grid.

This derives the urban universe independently of Groundsource positive reports.
Dry-run by default. Source fractions are area-weighted, not flood probabilities.
"""
import argparse
from ee_ease_projection import EASE_WKT, probe_projection

PROJECT = "eco-sector-503721-b7"
VERSION = "SMOD2020_20KM_V2"
TRANSFORM = [20000, 0, -17400000, 0, -20000, 7400000]

def source_image(ee):
    smod = ee.Image("JRC/GHSL/P2023A/GHS_SMOD_V2-0/2020").select("smod_code")
    valid = smod.mask().And(smod.neq(-200)).unmask(0)
    urban = (smod.eq(21).Or(smod.eq(22)).Or(smod.eq(23)).Or(smod.eq(30))).unmask(0).And(valid)
    target = ee.Projection(EASE_WKT, TRANSFORM)
    def weighted_fraction(mask, name):
        return (mask.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024)
                .reproject(crs=target).rename(name).toFloat())
    return ee.Image.cat([weighted_fraction(urban, "urban_fraction_wholecell"),
                         weighted_fraction(valid, "valid_fraction")])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--folder",default="GlobalFlood_E2020_Urban")
    p.add_argument("--probe",action="store_true")
    p.add_argument("--submit",action="store_true")
    a=p.parse_args()
    print("[PLAN]",VERSION,"one 1740x740 E2020 GeoTIFF; no Task without --submit")
    if not (a.submit or a.probe): return
    import ee
    ee.Initialize(project=a.project)
    proj=probe_projection(ee)
    if not a.submit: return
    previous={t.get("description"):t.get("state") for t in ee.data.getTaskList()}
    if VERSION in previous:
        raise RuntimeError("Existing task "+VERSION+" ("+str(previous[VERSION])+"); inspect history before retry")
    region=ee.Geometry.Rectangle([-17400000,-7400000,17400000,7400000],proj,False)
    task=ee.batch.Export.image.toDrive(
        image=source_image(ee),description=VERSION,folder=a.folder,
        fileNamePrefix="smod2020_20km_v2",region=region,crs=EASE_WKT,
        crsTransform=TRANSFORM,maxPixels=2_000_000,fileFormat="GeoTIFF")
    task.start()
    print("[STARTED]",VERSION,task.id)
if __name__=="__main__":main()
