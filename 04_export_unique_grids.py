from pathlib import Path
import shutil

import numpy as np
import duckdb
import geopandas as gpd
from shapely.geometry import box


# =========================================================
# PATHS
# =========================================================

INPUT = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位"
    r"\processed\groundsource_gridday_20km.parquet"
)

OUT_DIR = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位"
    r"\processed\groundsource_positive_grids_20km"
)

ZIP_BASE = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位"
    r"\processed\groundsource_positive_grids_20km"
)

OUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

GRID_SIZE = 20_000.0


# =========================================================
# READ UNIQUE GRIDS
# =========================================================

con = duckdb.connect()

df = con.execute(
    f"""
    SELECT DISTINCT
        grid_id,
        grid_x,
        grid_y
    FROM read_parquet('{INPUT.as_posix()}')
    ORDER BY grid_x, grid_y
    """
).fetchdf()

con.close()

print("Unique grids:", len(df))


# =========================================================
# OPTIONAL CHUNK ID
#
# 后面如果 GEE 一次处理 94,738 grids 太大，
# 可以按 chunk_id 分批。
# =========================================================

df["chunk_id"] = (
    np.arange(len(df)) // 5000
).astype("int16")


# =========================================================
# BUILD 20 km × 20 km SQUARES IN EPSG:6933
# =========================================================

xmin = df["grid_x"].to_numpy() * GRID_SIZE
ymin = df["grid_y"].to_numpy() * GRID_SIZE

geometries = [
    box(
        x,
        y,
        x + GRID_SIZE,
        y + GRID_SIZE
    )
    for x, y in zip(xmin, ymin)
]


gdf = gpd.GeoDataFrame(
    df,
    geometry=geometries,
    crs="EPSG:6933"
)


# =========================================================
# VALIDATION
# =========================================================

print("Invalid EPSG:6933 geometries:",
      (~gdf.geometry.is_valid).sum())

print(
    "Mean grid area km2:",
    gdf.geometry.area.mean() / 1e6
)


# =========================================================
# TRANSFORM TO EPSG:4326 FOR GEE
# =========================================================

gdf = gdf.to_crs("EPSG:4326")

print("Invalid WGS84 geometries:",
      (~gdf.geometry.is_valid).sum())

print(gdf.head())


# =========================================================
# WRITE SHAPEFILE
# =========================================================

shp_path = OUT_DIR / "grids20km.shp"

gdf.to_file(
    shp_path,
    driver="ESRI Shapefile",
    index=False
)

print("Shapefile written:")
print(shp_path)


# =========================================================
# ZIP ALL SHAPEFILE COMPONENTS
# =========================================================

zip_path = shutil.make_archive(
    str(ZIP_BASE),
    "zip",
    root_dir=OUT_DIR
)

print("\nZIP created:")
print(zip_path)