#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add EPSG:4326 point geometry to 16,975 fallback requests for EE table upload.
GEE later IGNORES these points and rebuilds EPSG:6933 polygons from grid_x/y.
"""
from pathlib import Path
import pandas as pd
from pyproj import Transformer

ROOT = Path(r"E:\Global_flooding")
SOURCE = ROOT / "climate_global" / "era5_fallback_requests.csv"
OUTPUT = ROOT / "climate_global" / "era5_fallback_requests_upload.csv"
if not SOURCE.exists():
    raise FileNotFoundError(SOURCE)
df = pd.read_csv(SOURCE, dtype={"climate_id": str, "grid_id": str})
required = {"grid_x", "grid_y", "climate_id", "date_str", "year", "month"}
if not required.issubset(df):
    raise RuntimeError(f"Missing source fields: {required-set(df)}")
if df.climate_id.isna().any() or df.climate_id.duplicated().any():
    raise RuntimeError("climate_id invalid or duplicated")
x = (pd.to_numeric(df.grid_x, errors="raise") + 0.5) * 20000.0
y = (pd.to_numeric(df.grid_y, errors="raise") + 0.5) * 20000.0
lon, lat = Transformer.from_crs("EPSG:6933", "EPSG:4326", always_xy=True).transform(x.to_numpy(), y.to_numpy())
df["longitude"] = lon
df["latitude"] = lat
if df[["longitude", "latitude"]].isna().any().any():
    raise RuntimeError("Invalid uploaded coordinates")
df.to_csv(OUTPUT, index=False, encoding="utf-8")
print(f"Fallback requests: {len(df):,}; unique grids: {df.grid_id.nunique():,}")
print(f"Upload CSV: {OUTPUT}")
print("Use longitude/latitude for CSV point geometry; polygon rebuilding uses grid_x/grid_y.")
