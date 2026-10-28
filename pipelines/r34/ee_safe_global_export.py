# -*- coding: utf-8 -*-
"""Conservative EE global EPSG:6933 export geometry aligned to the 20km lattice.

DO NOT use +/-17,400,000 / +/-7,400,000: latitude of 7,400,000 in
EPSG:6933 is undefined (beyond pole-valid y), so EE Export can fail with
"Failed to compute bounds of <Geometry>". These safe bounds omit only the
outer projected edge slivers. This is NOT mathematically complete coverage
of the poles or exact +/-180 meridian. Report this limitation.
"""
from __future__ import annotations
import math

CELL_M = 20000
# 1736 x 732 cells. All corners map to finite geographic coordinates.
SAFE_XMIN = -17_360_000
SAFE_XMAX = 17_360_000
SAFE_YMIN = -7_320_000
SAFE_YMAX = 7_320_000
SAFE_TRANSFORM = [CELL_M, 0, SAFE_XMIN, 0, -CELL_M, SAFE_YMAX]
SAFE_WIDTH = (SAFE_XMAX - SAFE_XMIN) // CELL_M
SAFE_HEIGHT = (SAFE_YMAX - SAFE_YMIN) // CELL_M
SAFE_BOUNDS = (SAFE_XMIN, SAFE_YMIN, SAFE_XMAX, SAFE_YMAX)

# 10x10 test pixels (same 20km alignment), in northern India.
TEST_BOUNDS = (7_400_000, 2_800_000, 7_600_000, 3_000_000)

def validate_bounds(bounds=SAFE_BOUNDS):
    from pyproj import Transformer
    if len(bounds) != 4:
        raise ValueError("Export extent must have four coordinates")
    x0, y0, x1, y1 = map(float, bounds)
    if not (x0<x1 and y0<y1):
        raise ValueError("Invalid extent ordering")
    if any(abs(q % CELL_M) > 1e-5 for q in (x0,y0,x1,y1)):
        raise ValueError("Extent not aligned with 20km project lattice")
    t = Transformer.from_crs("EPSG:6933", "EPSG:4326", always_xy=True)
    for x in (x0,x1):
        for y in (y0,y1):
            lon,lat=t.transform(x,y)
            if not (math.isfinite(lon) and math.isfinite(lat)
                    and abs(lat)<=85.6 and abs(lon)<=180):
                raise ValueError(f"Invalid EPSG:6933 export corner {(x,y)} => {(lon,lat)}")
    return int((x1-x0)/CELL_M), int((y1-y0)/CELL_M)

def make_region(ee, proj, test=False):
    # Rectangle defined directly in *validated projected coordinates*.
    b = TEST_BOUNDS if test else SAFE_BOUNDS
    return ee.Geometry.Rectangle(list(b), proj, False)

def server_probe_region(ee, proj, test=False):
    region = make_region(ee, proj, test)
    expected = TEST_BOUNDS if test else SAFE_BOUNDS
    area = float(region.area(maxError=100).getInfo())
    width,height=validate_bounds(expected)
    square_area=float(width*height*CELL_M*CELL_M)
    if not math.isfinite(area) or abs(area-square_area)/square_area>.03:
        raise RuntimeError(f"Unexpected EE region area {area} vs {square_area}")
    print("[BOUNDS PASS] grid shape",width,height,"region area",area,
          "edge clipping: outside +/-179.922 lon and +/-85.56 lat omitted")
    return region
