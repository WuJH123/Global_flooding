# -*- coding: utf-8 -*-
"""Validated Earth Engine CRS for the project's 20km EPSG:6933 grid.

Earth Engine currently has a reported execution-time parser issue with
the bare "EPSG:6933" authority string. The explicit WKT1 below is copied
from mechanism_scripts/04_submit_merit_gee.py, which produced successful
M3 outputs. Keep grid_x/grid_y in this exact CRS, never substitute EPSG:3857.

Public bug report: https://github.com/google/earthengine-community/issues/971
"""
from __future__ import annotations

GRID_M = 20000

EASE_WKT = (
    'PROJCS["WGS 84 / NSIDC EASE-Grid 2.0 Global",'
    'GEOGCS["WGS 84",DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
    'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]],'
    'PROJECTION["Cylindrical_Equal_Area"],PARAMETER["standard_parallel_1",30],'
    'PARAMETER["central_meridian",0],PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","6933"]]'
)

def grid_projection(ee):
    """Return EE Projection using WKT, not its unsupported bare EPSG alias."""
    return ee.Projection(EASE_WKT)


def rectangular_grid(ee, x, y, projection=None):
    """Reconstruct a 20km cell from integer grid_x/grid_y in native CRS."""
    proj = projection if projection is not None else grid_projection(ee)
    x0 = ee.Number(x).multiply(GRID_M)
    y0 = ee.Number(y).multiply(GRID_M)
    return ee.Geometry.Rectangle(
        ee.List([x0, y0, x0.add(GRID_M), y0.add(GRID_M)]),
        proj, False, True,
    )


def probe_projection(ee, *, require_raster=True):
    """Evaluate 1 bounded EE request to catch parser failures before export.

    A square 20 km equal-area cell must be ~400km2. This is not a GHSL
    science parity test: native raster sampling still requires a pilot CSV.
    """
    proj = grid_projection(ee)
    geom = rectangular_grid(ee, 0, 0, proj)
    area = float(geom.area(maxError=1).getInfo())
    if not 390_000_000 <= area <= 410_000_000:
        raise RuntimeError(f"Bad EPSG:6933 test square area: {area} m2")
    if require_raster:
        # Exercise pixel access in the WKT projection, not just client-side
        # JSON serialization. ~400 cells; no batch EE task is created.
        pixel_area = ee.Image.pixelArea().rename("pixel_area_m2")
        stats = pixel_area.reduceRegion(
            reducer=ee.Reducer.sum(), geometry=geom, crs=proj,
            scale=1000, maxPixels=10000, tileScale=4,
        ).getInfo()
        image_area = float(stats.get("pixel_area_m2", float("nan")))
        if not 390_000_000 <= image_area <= 410_000_000:
            raise RuntimeError(f"Bad EPSG:6933 pixel-area test: {image_area} m2")
    else:
        image_area = None
    print("[CRS PASS] 20km square m2", area, "raster m2", image_area)
    return proj
