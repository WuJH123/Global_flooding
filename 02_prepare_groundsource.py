from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from pyproj import Transformer


# =========================================================
# PATHS
# =========================================================

SRC = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位\groundsource_2026.parquet"
)

OUT_DIR = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位\processed"
)

OUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OUT = OUT_DIR / "groundsource_stage1_records_20km.parquet"


# =========================================================
# PARAMETERS
# =========================================================

GRID_SIZE = 20_000.0

transformer = Transformer.from_crs(
    "EPSG:4326",
    "EPSG:6933",
    always_xy=True
)


# =========================================================
# OPEN PARQUET
# =========================================================

pf = pq.ParquetFile(SRC)

writer = None

total_in = 0
total_out = 0
invalid_geometry = 0
invalid_date = 0
invalid_coord = 0


# =========================================================
# BATCH PROCESSING
# =========================================================

for batch_id, batch in enumerate(
    pf.iter_batches(
        batch_size=100_000,
        columns=[
            "uuid",
            "area_km2",
            "geometry",
            "start_date",
            "end_date"
        ]
    )
):

    df = batch.to_pandas()

    total_in += len(df)

    # -----------------------------------------------------
    # 1. Parse dates
    # -----------------------------------------------------

    df["start_date"] = pd.to_datetime(
        df["start_date"],
        errors="coerce"
    )

    df["end_date"] = pd.to_datetime(
        df["end_date"],
        errors="coerce"
    )

    good_date = (
        df["start_date"].notna()
        & df["end_date"].notna()
        & (df["end_date"] >= df["start_date"])
    )

    invalid_date += int((~good_date).sum())

    df = df.loc[good_date].copy()

    if len(df) == 0:
        continue

    # -----------------------------------------------------
    # 2. Decode WKB
    # -----------------------------------------------------

    raw_wkb = df["geometry"].to_numpy()

    geoms = shapely.from_wkb(
        raw_wkb,
        on_invalid="ignore"
    )

    good_geom = ~shapely.is_missing(geoms)

    invalid_geometry += int(
        (~good_geom).sum()
    )

    df = df.loc[good_geom].copy()
    geoms = geoms[good_geom]

    if len(df) == 0:
        continue

    # -----------------------------------------------------
    # 3. Representative point
    #
    # IMPORTANT:
    # geometry = reported place boundary,
    # not actual flooded area.
    # -----------------------------------------------------

    points = shapely.point_on_surface(
        geoms
    )

    lon = shapely.get_x(points)
    lat = shapely.get_y(points)

    good_coord = (
        np.isfinite(lon)
        & np.isfinite(lat)
        & (lon >= -180)
        & (lon <= 180)
        & (lat >= -86)
        & (lat <= 86)
    )

    invalid_coord += int(
        (~good_coord).sum()
    )

    df = df.loc[good_coord].copy()

    lon = np.asarray(lon)[good_coord]
    lat = np.asarray(lat)[good_coord]

    df["lon"] = lon
    df["lat"] = lat

    # -----------------------------------------------------
    # 4. Transform to equal-area CRS
    # -----------------------------------------------------

    x, y = transformer.transform(
        lon,
        lat
    )

    x = np.asarray(x)
    y = np.asarray(y)

    df["x6933"] = x
    df["y6933"] = y

    # -----------------------------------------------------
    # 5. 20 km grid
    # -----------------------------------------------------

    grid_x = np.floor(
        x / GRID_SIZE
    ).astype(np.int32)

    grid_y = np.floor(
        y / GRID_SIZE
    ).astype(np.int32)

    df["grid_x"] = grid_x
    df["grid_y"] = grid_y

    df["grid_id"] = (
        df["grid_x"].astype(str)
        + "_"
        + df["grid_y"].astype(str)
    )

    # -----------------------------------------------------
    # 6. Event duration
    # -----------------------------------------------------

    df["duration_days"] = (
        (
            df["end_date"]
            - df["start_date"]
        ).dt.days
        + 1
    ).astype("int16")

    # -----------------------------------------------------
    # 7. GHSL epoch
    # -----------------------------------------------------

    year = df["start_date"].dt.year

    epoch = (
        np.floor(year / 5) * 5
    ).astype(int)

    epoch = np.clip(
        epoch,
        2000,
        2025
    )

    df["ghsl_epoch"] = epoch.astype(
        "int16"
    )

    # -----------------------------------------------------
    # 8. Save only analytical fields
    #
    # Raw geometry remains safely stored in original file.
    # -----------------------------------------------------

    out_df = df[
        [
            "uuid",
            "area_km2",
            "start_date",
            "end_date",
            "duration_days",
            "lon",
            "lat",
            "x6933",
            "y6933",
            "grid_x",
            "grid_y",
            "grid_id",
            "ghsl_epoch"
        ]
    ].copy()

    table = pa.Table.from_pandas(
        out_df,
        preserve_index=False
    )

    if writer is None:

        writer = pq.ParquetWriter(
            OUT,
            table.schema,
            compression="zstd"
        )

    writer.write_table(table)

    total_out += len(out_df)

    print(
        f"Batch {batch_id:03d} | "
        f"input={len(batch):,} | "
        f"output={len(out_df):,}"
    )


if writer is not None:
    writer.close()


# =========================================================
# SUMMARY
# =========================================================

print("\n" + "=" * 80)
print("FINISHED")
print("=" * 80)

print(f"Input rows:        {total_in:,}")
print(f"Output rows:       {total_out:,}")
print(f"Invalid dates:     {invalid_date:,}")
print(f"Invalid geometry:  {invalid_geometry:,}")
print(f"Invalid coords:    {invalid_coord:,}")

print(f"\nSaved to:\n{OUT}")