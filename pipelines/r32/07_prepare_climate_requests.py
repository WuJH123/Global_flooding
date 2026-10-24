from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
from pyproj import Transformer


# ============================================================
# 0. CONFIG
# ============================================================

INPUT = Path(
    r"E:\Global_flooding\episode_control"
    r"\matched_case_control_m3.parquet"
)

OUT_DIR = Path(
    r"E:\Global_flooding\climate_requests"
)

CSV_DIR = OUT_DIR / "gee_upload"

OUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

CSV_DIR.mkdir(
    parents=True,
    exist_ok=True
)


UNIQUE_PARQUET = (
    OUT_DIR
    / "unique_climate_requests.parquet"
)

MANIFEST_CSV = (
    OUT_DIR
    / "climate_request_manifest.csv"
)


# ============================================================
# 1. CHECK
# ============================================================

if not INPUT.exists():

    raise FileNotFoundError(
        INPUT
    )


print("=" * 80)
print("BUILD UNIQUE CLIMATE REQUESTS")
print("=" * 80)

print("Input:")
print(INPUT)


# ============================================================
# 2. DUCKDB
# ============================================================

con = duckdb.connect()

threads = max(
    1,
    min(
        8,
        os.cpu_count() or 1
    )
)

con.execute(
    f"PRAGMA threads={threads}"
)


src = INPUT.as_posix()
dst = UNIQUE_PARQUET.as_posix()


# ============================================================
# 3. DEDUPLICATE GRID × DATE
# ============================================================
#
# Multiple matched strata may reuse the same control date.
# Climate only needs to be extracted once for each:
#
#       grid_id × sample_date
#
# ============================================================

con.execute(
    f"""
    COPY (

        SELECT

            grid_id,

            grid_x,

            grid_y,

            CAST(sample_date AS DATE)
                AS sample_date,


            grid_id
            || '__'
            || STRFTIME(
                   sample_date,
                   '%Y%m%d'
               )
                AS climate_id,


            EXTRACT(
                YEAR FROM sample_date
            )::INTEGER
                AS year,


            EXTRACT(
                MONTH FROM sample_date
            )::INTEGER
                AS month,


            EXTRACT(
                DAY FROM sample_date
            )::INTEGER
                AS day,


            STRFTIME(
                sample_date,
                '%Y-%m-%d'
            )
                AS date_str,


            COUNT(*)
                AS n_matched_references,


            MAX(is_case)
                AS used_as_case,


            COUNT(*)
            FILTER (
                WHERE is_case = 0
            )
                AS used_as_control_count


        FROM read_parquet(
            '{src}'
        )


        GROUP BY

            grid_id,

            grid_x,

            grid_y,

            sample_date

    )

    TO '{dst}'

    (
        FORMAT PARQUET,
        COMPRESSION ZSTD
    )
    """
)


# ============================================================
# 4. AUDIT
# ============================================================

audit = con.execute(
    f"""
    SELECT

        COUNT(*)
            AS unique_climate_requests,

        COUNT(DISTINCT grid_id)
            AS unique_grids,

        MIN(sample_date)
            AS first_date,

        MAX(sample_date)
            AS last_date,

        SUM(n_matched_references)
            AS original_matched_rows,

        COUNT(*)
        FILTER (
            WHERE used_as_case = 1
        )
            AS requests_used_as_case

    FROM read_parquet(
        '{dst}'
    )
    """
).fetchdf()


print("\n" + "=" * 80)
print("AUDIT")
print("=" * 80)

print(
    audit.to_string(
        index=False
    )
)


duplicates = con.execute(
    f"""
    SELECT COUNT(*)

    FROM (

        SELECT

            climate_id,

            COUNT(*) AS n

        FROM read_parquet(
            '{dst}'
        )

        GROUP BY climate_id

        HAVING COUNT(*) > 1
    )
    """
).fetchone()[0]


if duplicates != 0:

    raise RuntimeError(
        f"Duplicate climate_id: {duplicates}"
    )


# ============================================================
# 5. EXPORT ONE CSV PER YEAR
# ============================================================
#
# Add grid-centre lon/lat only to make GEE CSV ingestion easy.
#
# The GEE analysis will IGNORE the uploaded point geometry
# and rebuild the original EPSG:6933 20-km polygon from
# grid_x / grid_y.
#
# ============================================================

transformer = Transformer.from_crs(
    "EPSG:6933",
    "EPSG:4326",
    always_xy=True
)


years = con.execute(
    f"""
    SELECT DISTINCT year

    FROM read_parquet(
        '{dst}'
    )

    ORDER BY year
    """
).fetchdf()["year"].tolist()


manifest_rows = []


for year in years:

    df = con.execute(
        f"""
        SELECT *

        FROM read_parquet(
            '{dst}'
        )

        WHERE year = {int(year)}

        ORDER BY
            month,
            day,
            grid_id
        """
    ).fetchdf()


    # --------------------------------------------------------
    # Exact centre of original EPSG:6933 20-km cell
    # --------------------------------------------------------

    x_center = (
        df["grid_x"].astype(float)
        + 0.5
    ) * 20000.0

    y_center = (
        df["grid_y"].astype(float)
        + 0.5
    ) * 20000.0


    lon, lat = transformer.transform(
        x_center.to_numpy(),
        y_center.to_numpy()
    )


    df["longitude"] = lon
    df["latitude"] = lat


    # Keep sample_date as plain YYYY-MM-DD text in upload CSV.
    df["sample_date"] = (
        pd.to_datetime(
            df["sample_date"]
        )
        .dt.strftime(
            "%Y-%m-%d"
        )
    )


    out = (
        CSV_DIR
        / f"climate_requests_{year}.csv"
    )


    df.to_csv(
        out,
        index=False,
        encoding="utf-8"
    )


    month_counts = (
        df.groupby(
            "month"
        )
        .size()
        .to_dict()
    )


    for month in range(1, 13):

        manifest_rows.append({

            "year":
                int(year),

            "month":
                month,

            "requests":
                int(
                    month_counts.get(
                        month,
                        0
                    )
                )
        })


    print(
        f"{year}: "
        f"{len(df):,} unique requests"
    )


# ============================================================
# 6. SAVE MANIFEST
# ============================================================

manifest = pd.DataFrame(
    manifest_rows
)


manifest.to_csv(
    MANIFEST_CSV,
    index=False,
    encoding="utf-8-sig"
)


print("\n" + "=" * 80)
print("FINISHED")
print("=" * 80)

print("\nUnique request parquet:")
print(UNIQUE_PARQUET)

print("\nGEE upload CSV folder:")
print(CSV_DIR)

print("\nManifest:")
print(MANIFEST_CSV)


print(
    "\nTotal unique climate requests:"
)

print(
    f"{audit.loc[0, 'unique_climate_requests']:,}"
)


con.close()