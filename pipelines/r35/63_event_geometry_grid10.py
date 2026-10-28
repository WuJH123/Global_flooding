# -*- coding: utf-8 -*-
"""Stage63: assign ORIGINAL news-geocoded event geometries to frozen 10km cells.

No copying from 20km positives. One primary eligible 10km grid per raw
geocode (largest overlap), but preserve all overlapping candidate cells and
geocoding uncertainty; NO claim that geocoding geometry is inundation footprint.
No GEE. Streaming parquet batches.
"""
from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
import numpy as np,pandas as pd,pyarrow.parquet as pq,pyarrow as pa
from shapely import wkb,wkt
from shapely.geometry import box
from shapely.ops import transform
from pyproj import Transformer

CELL=10000
AREA=100. # km2
def to_geom(value):
    if value is None:return None
    if hasattr(value,"geom_type"):return value
    if isinstance(value,(bytes,bytearray,memoryview)):return wkb.loads(bytes(value))
    if isinstance(value,str):
        if value.startswith(("POLYGON","MULTIPOLYGON","POINT","GEOMETRYCOLLECTION")):return wkt.loads(value)
        try:return wkb.loads(bytes.fromhex(value))
        except ValueError:pass
    return None

def event_candidate(raw,valid_ids,transformer,max_candidate=64):
    """Return (links,status) for an original geocoded geometry."""
    g=to_geom(raw)
    if g is None or g.is_empty:return [],"EMPTY_GEOMETRY"
    try:
        g=transform(transformer.transform,g)
    except Exception:return [],"TRANSFORM_FAILED"
    if g.is_empty or not g.is_valid or g.area<=0:
        return [],"NOT_VALID_AREA_GEOMETRY"
    x0,y0,x1,y1=g.bounds
    ix0=int(np.floor(x0/CELL));ix1=int(np.ceil(x1/CELL))-1
    iy0=int(np.floor(y0/CELL));iy1=int(np.ceil(y1/CELL))-1
    if (ix1-ix0+1)*(iy1-iy0+1)>max_candidate:
        return [],"GEOCODE_TOO_BROAD"
    if ix1<ix0 or iy1<iy0:return [],"NO_CELLS"
    links=[]
    for x in range(ix0,ix1+1):
        for y in range(iy0,iy1+1):
            grid_id=f"{x}_{y}"
            if grid_id not in valid_ids:continue
            area=g.intersection(box(x*CELL,y*CELL,(x+1)*CELL,(y+1)*CELL)).area
            if area>0:links.append((grid_id,x,y,area/g.area))
    if not links:return [],"NOT_IN_URBAN_2020_DOMAIN"
    links.sort(key=lambda z:(-z[3],z[0]))
    return links,"MATCHED"

def run(events,grid,out,batch_size=10000,max_candidate=64,first_batches=0):
    urban=pd.read_parquet(grid,columns=["grid_id"])
    valid_ids=set(urban.grid_id.astype(str))
    if len(valid_ids)!=len(urban):raise RuntimeError("Duplicate grid IDs")
    out.mkdir(parents=True,exist_ok=True)
    trans=Transformer.from_crs("EPSG:4326","EPSG:6933",always_xy=True)
    reader=pq.ParquetFile(events)
    cols=set(reader.schema_arrow.names)
    required={"uuid","geometry","start_date"}
    if not required<=cols:raise ValueError(f"Event parquet lacks {required-cols}")
    keep=["uuid","geometry","start_date"]+(["end_date"] if "end_date" in cols else [])
    stats={};parts=[];n=0
    for i,b in enumerate(reader.iter_batches(batch_size=batch_size,columns=keep)):
        if first_batches and i>=first_batches:break
        frame=b.to_pandas()
        rec=[];n+=len(frame)
        for r in frame.itertuples(index=False):
            base={"event_id":str(r.uuid),"start_date":str(r.start_date)[:10],
                  "end_date":str(getattr(r,"end_date",r.start_date))[:10]}
            cand,status=event_candidate(r.geometry,valid_ids,trans,max_candidate)
            stats[status]=stats.get(status,0)+1
            if status=="MATCHED":
                # Record all overlaps, primary takes largest geometric
                # fraction; only primary is eligible for one-cell onset,
                # multi-cell / broad labels explicitly retained for audits.
                for rank,(id,x,y,frac) in enumerate(cand):
                    rec.append({**base,"grid_id":id,"grid_x":x,"grid_y":y,
                        "geocode_overlap_fraction":frac,
                        "n_geocode_urban_cells":len(cand),
                        "primary":rank==0,
                        "is_high_geocode_specificity":len(cand)<=4 and cand[0][3]>=.5,
                        "source_geometry_not_inundation":True})
        part=out/f"63_event_grid_links_{i:05d}.parquet"
        pd.DataFrame(rec,columns=["event_id","start_date","end_date","grid_id",
            "grid_x","grid_y","geocode_overlap_fraction",
            "n_geocode_urban_cells","primary","is_high_geocode_specificity",
            "source_geometry_not_inundation"]).to_parquet(part,index=False)
        parts.append(str(part))
        if (i+1)%10==0:print("[EVENTS]",n,"batches",i+1,stats)
    meta={"status":"GEOCODE_RELATIONSHIPS_NOT_FLOOD_FOOTPRINTS",
          "events_processed":n,"status_counts":stats,
          "links_parts":len(parts),"max_bbox_candidates":max_candidate,
          "geometry_source":str(events),"urban_grid":str(grid),
          "max_area":100000000,"grid_size_m":10000,
          "note":"High-specificity is a GEOMETRY PROXY only, not verified location precision"}
    (out/"63_event_grid_qc.json").write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[DONE]",meta)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--events",type=Path,required=True)
    p.add_argument("--grid",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--batch-size",type=int,default=10000)
    p.add_argument("--max-candidate-cells",type=int,default=64)
    p.add_argument("--first-batches",type=int,default=0,help="Optional local-only smoke")
    a=p.parse_args()
    run(a.events,a.grid,a.out,a.batch_size,a.max_candidate_cells,a.first_batches)
if __name__=="__main__":main()
