from __future__ import annotations

from pathlib import Path
import math

import duckdb
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

INPUT = Path(
    r"E:\Global_flooding\climate_requests"
    r"\unique_climate_requests.parquet"
)

OUT_DIR = Path(
    r"E:\Global_flooding\climate_requests_chunked"
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


OUTPUT_PARQUET = (
    OUT_DIR
    / "unique_climate_requests_chunked.parquet"
)

MANIFEST = (
    OUT_DIR
    / "climate_request_chunk_manifest.csv"
)


# Maximum requests in one GEE batch task
MAX_REQUESTS_PER_TASK = 30000


# ============================================================
# CHECK
# ============================================================

if not INPUT.exists():
    raise FileNotFoundError(INPUT)


print("=" * 80)
print("BUILD CLIMATE REQUEST CHUNKS")
print("=" * 80)

print("Input:")
print(INPUT)

print(
    "\nMaximum requests per GEE task:",
    MAX_REQUESTS_PER_TASK
)


# ============================================================
# DUCKDB
# ============================================================

con = duckdb.connect()

src = INPUT.as_posix()
dst = OUTPUT_PARQUET.as_posix()


# ============================================================
# ASSIGN A DETERMINISTIC MONTH CHUNK
# ============================================================
#
# Within every year × month:
#
#   request_chunk = 0, 1, 2, ...
#
# Maximum ~30,000 rows/chunk.
#
# Ordering is deterministic by climate_id.
# ============================================================

con.execute(
    f"""
    COPY (

        WITH numbered AS (

            SELECT

                *,

                ROW_NUMBER()
                OVER (
                    PARTITION BY
                        year,
                        month

                    ORDER BY
                        climate_id
                ) - 1
                AS rn

            FROM read_parquet(
                '{src}'
            )
        )

        SELECT

            * EXCLUDE(rn),

            FLOOR(
                rn
                /
                {MAX_REQUESTS_PER_TASK}
            )::INTEGER
            AS request_chunk

        FROM numbered

    )

    TO '{dst}'

    (
        FORMAT PARQUET,
        COMPRESSION ZSTD
    )
    """
)


# ============================================================
# QC
# ============================================================

qc = con.execute(
    f"""
    SELECT

        COUNT(*) AS rows,

        COUNT(DISTINCT climate_id)
            AS unique_climate_ids,

        MAX(request_chunk)
            AS global_max_chunk,

        MIN(sample_date)
            AS first_date,

        MAX(sample_date)
            AS last_date

    FROM read_parquet(
        '{dst}'
    )
    """
).fetchdf()


print("\nQC:")
print(qc.to_string(index=False))


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


if duplicates:
    raise RuntimeError(
        f"Duplicate climate IDs: {duplicates}"
    )


# ============================================================
# BUILD TASK MANIFEST
# ============================================================

manifest = con.execute(
    f"""
    SELECT

        year,

        month,

        request_chunk,

        COUNT(*) AS requests

    FROM read_parquet(
        '{dst}'
    )

    GROUP BY
        year,
        month,
        request_chunk

    ORDER BY
        year,
        month,
        request_chunk
    """
).fetchdf()


max_task = int(
    manifest["requests"].max()
)

if max_task > MAX_REQUESTS_PER_TASK:
    raise RuntimeError(
        f"Chunk too large: {max_task}"
    )


manifest.to_csv(
    MANIFEST,
    index=False,
    encoding="utf-8-sig"
)


print("\n" + "=" * 80)
print("TASK MANIFEST")
print("=" * 80)

print(
    f"Total GEE batch tasks: "
    f"{len(manifest):,}"
)

print(
    f"Maximum requests/task: "
    f"{manifest['requests'].max():,}"
)

print(
    f"Median requests/task: "
    f"{manifest['requests'].median():,.0f}"
)


print("\nLargest 20 tasks:")

print(
    manifest
    .sort_values(
        "requests",
        ascending=False
    )
    .head(20)
    .to_string(index=False)
)


# ============================================================
# EXPORT ONE CSV PER YEAR
# ============================================================

years = con.execute(
    f"""
    SELECT DISTINCT year

    FROM read_parquet(
        '{dst}'
    )

    ORDER BY year
    """
).fetchdf()["year"].tolist()


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
            request_chunk,
            climate_id
        """
    ).fetchdf()


    # Existing longitude/latitude columns from previous
    # preparation are retained.

    if pd.api.types.is_datetime64_any_dtype(
        df["sample_date"]
    ):
        df["sample_date"] = (
            df["sample_date"]
            .dt.strftime("%Y-%m-%d")
        )
    else:
        df["sample_date"] = (
            pd.to_datetime(
                df["sample_date"]
            )
            .dt.strftime("%Y-%m-%d")
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


    n_tasks = len(
        manifest[
            manifest["year"] == year
        ]
    )


    print(
        f"{year}: "
        f"{len(df):,} requests, "
        f"{n_tasks} GEE tasks"
    )


con.close()


print("\n" + "=" * 80)
print("FINISHED")
print("=" * 80)

print("\nChunked parquet:")
print(OUTPUT_PARQUET)

print("\nGEE CSV directory:")
print(CSV_DIR)

print("\nTask manifest:")
print(MANIFEST)