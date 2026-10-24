from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd


# ============================================================
# 0. CONFIG
# ============================================================

CLIMATE_ROOT = Path(
    r"E:\Global_flooding\Climate_all"
)

MANIFEST_PATH = Path(
    r"E:\Global_flooding\climate_requests_chunked"
    r"\climate_request_chunk_manifest.csv"
)

REQUEST_MASTER = Path(
    r"E:\Global_flooding\climate_requests_chunked"
    r"\unique_climate_requests_chunked.parquet"
)

OUT_DIR = Path(
    r"E:\Global_flooding\climate_global"
)

OUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


EXPECTED_TOTAL_REQUESTS = 2_789_073


PRIMARY_PARQUET = (
    OUT_DIR
    / "climate_features_primary.parquet"
)

TASK_QC_CSV = (
    OUT_DIR
    / "climate_task_qc.csv"
)

GLOBAL_QC_CSV = (
    OUT_DIR
    / "climate_global_qc.csv"
)

ERA5_MISSING_BY_YEAR = (
    OUT_DIR
    / "era5_missing_by_year.csv"
)

ERA5_MISSING_BY_GRID = (
    OUT_DIR
    / "era5_missing_by_grid.csv"
)

FALLBACK_PARQUET = (
    OUT_DIR
    / "era5_fallback_requests.parquet"
)

FALLBACK_CSV = (
    OUT_DIR
    / "era5_fallback_requests.csv"
)

FALLBACK_MANIFEST = (
    OUT_DIR
    / "era5_fallback_manifest.csv"
)


# ============================================================
# 1. EXPECTED COLUMNS
# ============================================================

ID_COLS = [
    "climate_id",
    "grid_id",
    "grid_x",
    "grid_y",
    "sample_date",
    "date_str",
    "year",
    "month",
    "day",
    "request_chunk",
    "n_matched_references",
    "used_as_case",
    "used_as_control_count",
]


IMERG_COLS = [

    "imerg_p30_max_mm_mean",
    "imerg_p30_max_mm_max",

    "imerg_p1h_max_mm_mean",
    "imerg_p1h_max_mm_max",

    "imerg_p3h_max_mm_mean",
    "imerg_p3h_max_mm_max",

    "imerg_p6h_max_mm_mean",
    "imerg_p6h_max_mm_max",

    "imerg_p12h_max_mm_mean",
    "imerg_p12h_max_mm_max",

    "imerg_p24_utc_mm_mean",
    "imerg_p24_utc_mm_max",
]


ERA5_COLS = [

    "era5_event_p24_mm_mean",
    "era5_event_p24_mm_max",

    "era5_event_runoff24_mm_mean",
    "era5_event_runoff24_mm_max",

    "era5_event_surface_runoff24_mm_mean",
    "era5_event_surface_runoff24_mm_max",

    "era5_ant_p3d_mm_mean",
    "era5_ant_p3d_mm_max",

    "era5_ant_p7d_mm_mean",
    "era5_ant_p7d_mm_max",

    "era5_ant_runoff3d_mm_mean",
    "era5_ant_runoff3d_mm_max",

    "era5_ant_runoff7d_mm_mean",
    "era5_ant_runoff7d_mm_max",

    "era5_api3_mm_mean",
    "era5_api3_mm_max",

    "era5_api7_mm_mean",
    "era5_api7_mm_max",

    "era5_sm1_prevday_mean",
    "era5_sm1_prevday_max",

    "era5_sm2_prevday_mean",
    "era5_sm2_prevday_max",
]


EXPECTED_COLS = (
    ID_COLS
    + IMERG_COLS
    + ERA5_COLS
)


# ============================================================
# 2. HELPERS
# ============================================================

FILE_RE = re.compile(
    r"^climate_(\d{4})_m(\d{2})_c(\d+)\.csv$",
    re.IGNORECASE
)


def sql_quote_path(path: Path) -> str:

    return (
        path
        .as_posix()
        .replace(
            "'",
            "''"
        )
    )


def any_null_sql(
    cols: list[str],
    alias: str = ""
) -> str:

    prefix = (
        f"{alias}."
        if alias
        else ""
    )

    return " OR ".join(
        f"{prefix}{c} IS NULL"
        for c in cols
    )


def all_null_sql(
    cols: list[str],
    alias: str = ""
) -> str:

    prefix = (
        f"{alias}."
        if alias
        else ""
    )

    return " AND ".join(
        f"{prefix}{c} IS NULL"
        for c in cols
    )


# ============================================================
# 3. INPUT CHECK
# ============================================================

print("=" * 80)
print("GLOBAL CLIMATE QC")
print("=" * 80)


for p in [
    CLIMATE_ROOT,
    MANIFEST_PATH,
    REQUEST_MASTER,
]:

    if not p.exists():

        raise FileNotFoundError(
            p
        )


manifest = pd.read_csv(
    MANIFEST_PATH
)


for c in [
    "year",
    "month",
    "request_chunk",
    "requests",
]:

    manifest[c] = (
        manifest[c]
        .astype(int)
    )


print(
    f"Manifest tasks: "
    f"{len(manifest):,}"
)

print(
    f"Manifest requests: "
    f"{manifest['requests'].sum():,}"
)


if (
    int(
        manifest[
            "requests"
        ].sum()
    )
    !=
    EXPECTED_TOTAL_REQUESTS
):

    raise RuntimeError(
        "Manifest total is not 2,789,073."
    )


# ============================================================
# 4. DISCOVER PRODUCTION CSV FILES
# ============================================================

print("\n" + "=" * 80)
print("DISCOVER PRODUCTION FILES")
print("=" * 80)


file_map: dict[
    tuple[int, int, int],
    list[Path]
] = {}


for path in CLIMATE_ROOT.rglob(
    "*.csv"
):

    m = FILE_RE.match(
        path.name
    )

    if not m:
        continue


    key = (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
    )


    file_map.setdefault(
        key,
        []
    ).append(
        path
    )


expected_keys = {

    (
        int(r.year),
        int(r.month),
        int(r.request_chunk),
    )

    for r in manifest.itertuples(
        index=False
    )
}


missing_keys = (
    expected_keys
    -
    set(file_map)
)


extra_keys = (
    set(file_map)
    -
    expected_keys
)


duplicate_keys = {

    k: v

    for k, v in file_map.items()

    if len(v) != 1
}


if missing_keys:

    print(
        "\nMissing production files:"
    )

    for x in sorted(
        missing_keys
    ):

        print(x)

    raise RuntimeError(
        "Some expected climate CSV files are missing."
    )


if duplicate_keys:

    print(
        "\nMultiple CSVs found for the same task:"
    )

    for key, paths in duplicate_keys.items():

        print(
            key
        )

        for p in paths:
            print(
                "   ",
                p
            )

    raise RuntimeError(
        "Duplicate production task files exist."
    )


if extra_keys:

    print(
        "\nWARNING: extra production-like files:"
    )

    for x in sorted(
        extra_keys
    ):

        print(x)


print(
    "Expected files found:",
    len(expected_keys)
)


# ============================================================
# 5. DUCKDB
# ============================================================

con = duckdb.connect()


con.execute(
    "PRAGMA threads=8"
)


# ============================================================
# 6. TASK-BY-TASK ROW COUNT QC
# ============================================================

print("\n" + "=" * 80)
print("TASK ROW COUNT QC")
print("=" * 80)


task_rows = []


for r in manifest.itertuples(
    index=False
):

    key = (
        int(r.year),
        int(r.month),
        int(r.request_chunk),
    )


    path = (
        file_map[key][0]
    )


    qpath = sql_quote_path(
        path
    )


    count = con.execute(
        f"""
        SELECT COUNT(*)

        FROM read_csv_auto(
            '{qpath}',
            header = true
        )
        """
    ).fetchone()[0]


    task_rows.append({

        "year":
            key[0],

        "month":
            key[1],

        "request_chunk":
            key[2],

        "expected_rows":
            int(r.requests),

        "actual_rows":
            int(count),

        "difference":
            int(count)
            -
            int(r.requests),

        "file":
            str(path),
    })


task_qc = pd.DataFrame(
    task_rows
)


task_qc.to_csv(
    TASK_QC_CSV,
    index=False,
    encoding="utf-8-sig"
)


bad_tasks = (
    task_qc[
        task_qc[
            "difference"
        ]
        != 0
    ]
)


print(
    "Tasks checked:",
    len(task_qc)
)

print(
    "Tasks with wrong row counts:",
    len(bad_tasks)
)


if len(bad_tasks):

    print(
        bad_tasks.to_string(
            index=False
        )
    )

    raise RuntimeError(
        "Task row-count QC failed."
    )


# ============================================================
# 7. BUILD EXACT FILE LIST
# ============================================================

all_paths = [

    file_map[
        (
            int(r.year),
            int(r.month),
            int(r.request_chunk),
        )
    ][0]

    for r in manifest.itertuples(
        index=False
    )
]


file_list_sql = (

    "["

    + ",".join(

        "'"
        +
        sql_quote_path(p)
        +
        "'"

        for p in all_paths
    )

    + "]"
)


# ============================================================
# 8. MERGE ALL PRODUCTION CSVs
# ============================================================

print("\n" + "=" * 80)
print("MERGE ALL CLIMATE FILES")
print("=" * 80)


if PRIMARY_PARQUET.exists():

    PRIMARY_PARQUET.unlink()


primary_path = sql_quote_path(
    PRIMARY_PARQUET
)


con.execute(
    f"""
    COPY (

        SELECT *

        FROM read_csv_auto(

            {file_list_sql},

            header = true,

            union_by_name = true,

            filename = true

        )

    )

    TO '{primary_path}'

    (
        FORMAT PARQUET,

        COMPRESSION ZSTD
    )
    """
)


print(
    PRIMARY_PARQUET
)


# ============================================================
# 9. COLUMN CHECK
# ============================================================

columns = {

    x[0]

    for x in con.execute(
        f"""
        DESCRIBE
        SELECT *

        FROM read_parquet(
            '{primary_path}'
        )
        """
    ).fetchall()
}


missing_columns = (

    set(EXPECTED_COLS)
    -
    columns
)


if missing_columns:

    raise RuntimeError(
        "Missing columns:\n"
        + "\n".join(
            sorted(
                missing_columns
            )
        )
    )


# ============================================================
# 10. GLOBAL COVERAGE
# ============================================================

primary_stats = con.execute(
    f"""
    SELECT

        COUNT(*)
            AS rows,

        COUNT(
            DISTINCT climate_id
        )
            AS unique_climate_ids,

        COUNT(*)
        -
        COUNT(
            DISTINCT climate_id
        )
            AS duplicate_climate_ids,

        MIN(date_str)
            AS first_date,

        MAX(date_str)
            AS last_date

    FROM read_parquet(
        '{primary_path}'
    )
    """
).fetchdf()


print(
    "\nPrimary:"
)

print(
    primary_stats.to_string(
        index=False
    )
)


rows = int(
    primary_stats.loc[
        0,
        "rows"
    ]
)


unique_ids = int(
    primary_stats.loc[
        0,
        "unique_climate_ids"
    ]
)


duplicates = int(
    primary_stats.loc[
        0,
        "duplicate_climate_ids"
    ]
)


if rows != EXPECTED_TOTAL_REQUESTS:

    raise RuntimeError(
        f"Expected {EXPECTED_TOTAL_REQUESTS:,}; "
        f"got {rows:,}"
    )


if unique_ids != EXPECTED_TOTAL_REQUESTS:

    raise RuntimeError(
        "Unique climate_id count is wrong."
    )


if duplicates != 0:

    raise RuntimeError(
        "Duplicate climate_id exists."
    )


# ============================================================
# 11. VERIFY AGAINST REQUEST MASTER
# ============================================================

request_path = sql_quote_path(
    REQUEST_MASTER
)


coverage = con.execute(
    f"""
    WITH r AS (

        SELECT
            climate_id

        FROM read_parquet(
            '{request_path}'
        )

    ),

    c AS (

        SELECT
            climate_id

        FROM read_parquet(
            '{primary_path}'
        )

    )

    SELECT

        (
            SELECT COUNT(*)
            FROM r
        )
            AS expected_requests,

        (
            SELECT COUNT(*)
            FROM c
        )
            AS climate_rows,

        (
            SELECT COUNT(*)

            FROM r

            LEFT JOIN c
            USING(climate_id)

            WHERE c.climate_id IS NULL
        )
            AS missing_from_climate,

        (
            SELECT COUNT(*)

            FROM c

            LEFT JOIN r
            USING(climate_id)

            WHERE r.climate_id IS NULL
        )
            AS extra_in_climate

    """
).fetchdf()


print(
    "\nCoverage against request master:"
)

print(
    coverage.to_string(
        index=False
    )
)


if (
    int(
        coverage.loc[
            0,
            "missing_from_climate"
        ]
    )
    != 0
):

    raise RuntimeError(
        "Some request climate IDs are missing."
    )


if (
    int(
        coverage.loc[
            0,
            "extra_in_climate"
        ]
    )
    != 0
):

    raise RuntimeError(
        "Unexpected climate IDs exist."
    )


# ============================================================
# 12. PHYSICAL QC
# ============================================================

imerg_any_null = (
    any_null_sql(
        IMERG_COLS
    )
)


era_any_null = (
    any_null_sql(
        ERA5_COLS
    )
)


era_all_null = (
    all_null_sql(
        ERA5_COLS
    )
)


imerg_negative = " OR ".join(

    f"{c} < -1e-9"

    for c in IMERG_COLS
)


era_flow_cols = [

    c

    for c in ERA5_COLS

    if (
        "sm1_" not in c
        and
        "sm2_" not in c
    )
]


era_flow_negative = " OR ".join(

    f"{c} < -1e-9"

    for c in era_flow_cols
)


spatial_pairs = []


for col in (
    IMERG_COLS
    +
    ERA5_COLS
):

    if col.endswith(
        "_mean"
    ):

        max_col = (
            col[:-5]
            + "_max"
        )

        if (
            max_col
            in
            IMERG_COLS
            +
            ERA5_COLS
        ):

            spatial_pairs.append(
                (
                    col,
                    max_col
                )
            )


max_lt_mean_sql = " OR ".join(

    f"({mx} IS NOT NULL "
    f"AND {mn} IS NOT NULL "
    f"AND {mx} + 1e-10 < {mn})"

    for mn, mx
    in spatial_pairs
)


monotonic_mean = """
(
    imerg_p30_max_mm_mean
    >
    imerg_p1h_max_mm_mean + 1e-9
)
OR
(
    imerg_p1h_max_mm_mean
    >
    imerg_p3h_max_mm_mean + 1e-9
)
OR
(
    imerg_p3h_max_mm_mean
    >
    imerg_p6h_max_mm_mean + 1e-9
)
OR
(
    imerg_p6h_max_mm_mean
    >
    imerg_p12h_max_mm_mean + 1e-9
)
OR
(
    imerg_p12h_max_mm_mean
    >
    imerg_p24_utc_mm_mean + 1e-9
)
"""


monotonic_max = """
(
    imerg_p30_max_mm_max
    >
    imerg_p1h_max_mm_max + 1e-9
)
OR
(
    imerg_p1h_max_mm_max
    >
    imerg_p3h_max_mm_max + 1e-9
)
OR
(
    imerg_p3h_max_mm_max
    >
    imerg_p6h_max_mm_max + 1e-9
)
OR
(
    imerg_p6h_max_mm_max
    >
    imerg_p12h_max_mm_max + 1e-9
)
OR
(
    imerg_p12h_max_mm_max
    >
    imerg_p24_utc_mm_max + 1e-9
)
"""


sm_bad_sql = """
(
    era5_sm1_prevday_mean < -1e-6
    OR
    era5_sm1_prevday_mean > 1.0
    OR
    era5_sm1_prevday_max < -1e-6
    OR
    era5_sm1_prevday_max > 1.0
    OR
    era5_sm2_prevday_mean < -1e-6
    OR
    era5_sm2_prevday_mean > 1.0
    OR
    era5_sm2_prevday_max < -1e-6
    OR
    era5_sm2_prevday_max > 1.0
)
"""


physical = con.execute(
    f"""
    SELECT

        COUNT(*)
        FILTER (
            WHERE {imerg_any_null}
        )
            AS imerg_any_missing,

        COUNT(*)
        FILTER (
            WHERE {imerg_negative}
        )
            AS imerg_negative,

        COUNT(*)
        FILTER (
            WHERE {monotonic_mean}
        )
            AS imerg_monotonic_mean_violation,

        COUNT(*)
        FILTER (
            WHERE {monotonic_max}
        )
            AS imerg_monotonic_max_violation,

        COUNT(*)
        FILTER (
            WHERE {max_lt_mean_sql}
        )
            AS spatial_max_lt_mean_violation,

        COUNT(*)
        FILTER (
            WHERE {era_any_null}
        )
            AS era5_any_missing,

        COUNT(*)
        FILTER (
            WHERE {era_all_null}
        )
            AS era5_all_missing,

        COUNT(*)
        FILTER (
            WHERE
                ({era_any_null})
                AND
                NOT ({era_all_null})
        )
            AS era5_partial_missing,

        COUNT(*)
        FILTER (
            WHERE {era_flow_negative}
        )
            AS era5_negative_flow,

        COUNT(*)
        FILTER (
            WHERE {sm_bad_sql}
        )
            AS soil_moisture_out_of_range

    FROM read_parquet(
        '{primary_path}'
    )
    """
).fetchdf()


print(
    "\nPhysical QC:"
)

print(
    physical.to_string(
        index=False
    )
)


# ============================================================
# 13. HARD FAIL ON IMERG / STRUCTURAL ERRORS
# ============================================================

hard_fail_cols = [

    "imerg_any_missing",

    "imerg_negative",

    "imerg_monotonic_mean_violation",

    "imerg_monotonic_max_violation",

    "spatial_max_lt_mean_violation",

    "era5_negative_flow",

    "soil_moisture_out_of_range",
]


for c in hard_fail_cols:

    value = int(
        physical.loc[
            0,
            c
        ]
    )

    if value != 0:

        raise RuntimeError(
            f"Global physical QC failed: "
            f"{c}={value:,}"
        )


# ============================================================
# 14. SAVE GLOBAL QC SUMMARY
# ============================================================

qc_rows = [

    {
        "metric":
            "expected_climate_requests",

        "value":
            EXPECTED_TOTAL_REQUESTS
    },

    {
        "metric":
            "actual_climate_rows",

        "value":
            rows
    },

    {
        "metric":
            "unique_climate_ids",

        "value":
            unique_ids
    },

    {
        "metric":
            "duplicate_climate_ids",

        "value":
            duplicates
    },
]


for c in physical.columns:

    qc_rows.append({

        "metric":
            c,

        "value":
            int(
                physical.loc[
                    0,
                    c
                ]
            )
    })


pd.DataFrame(
    qc_rows
).to_csv(

    GLOBAL_QC_CSV,

    index=False,

    encoding="utf-8-sig"
)


# ============================================================
# 15. ERA5 MISSING SUMMARIES
# ============================================================

missing_year = con.execute(
    f"""
    SELECT

        year,

        COUNT(*)
            AS climate_requests,

        COUNT(*)
        FILTER (
            WHERE {era_any_null}
        )
            AS era5_missing,

        COUNT(*)
        FILTER (
            WHERE {era_any_null}
        )
        /
        COUNT(*)::DOUBLE
            AS missing_fraction

    FROM read_parquet(
        '{primary_path}'
    )

    GROUP BY year

    ORDER BY year
    """
).fetchdf()


missing_year.to_csv(

    ERA5_MISSING_BY_YEAR,

    index=False,

    encoding="utf-8-sig"
)


missing_grid = con.execute(
    f"""
    SELECT

        grid_id,

        MIN(grid_x)
            AS grid_x,

        MIN(grid_y)
            AS grid_y,

        COUNT(*)
            AS climate_requests,

        COUNT(*)
        FILTER (
            WHERE {era_any_null}
        )
            AS era5_missing,

        COUNT(*)
        FILTER (
            WHERE {era_any_null}
        )
        /
        COUNT(*)::DOUBLE
            AS missing_fraction

    FROM read_parquet(
        '{primary_path}'
    )

    GROUP BY grid_id

    HAVING
        COUNT(*)
        FILTER (
            WHERE {era_any_null}
        )
        > 0

    ORDER BY
        era5_missing DESC
    """
).fetchdf()


missing_grid.to_csv(

    ERA5_MISSING_BY_GRID,

    index=False,

    encoding="utf-8-sig"
)


# ============================================================
# 16. CREATE ERA5 FALLBACK REQUESTS
# ============================================================

fallback_parquet_path = sql_quote_path(
    FALLBACK_PARQUET
)

fallback_csv_path = sql_quote_path(
    FALLBACK_CSV
)


fallback_select = f"""
SELECT

    r.climate_id,

    r.grid_id,

    r.grid_x,

    r.grid_y,

    r.sample_date,

    r.date_str,

    r.year,

    r.month,

    r.day,

    r.request_chunk,

    r.n_matched_references,

    r.used_as_case,

    r.used_as_control_count

FROM read_parquet(
    '{request_path}'
) r

INNER JOIN read_parquet(
    '{primary_path}'
) c

USING(climate_id)

WHERE
    {any_null_sql(
        ERA5_COLS,
        alias="c"
    )}
"""


if FALLBACK_PARQUET.exists():
    FALLBACK_PARQUET.unlink()

if FALLBACK_CSV.exists():
    FALLBACK_CSV.unlink()


con.execute(
    f"""
    COPY (

        {fallback_select}

    )

    TO '{fallback_parquet_path}'

    (
        FORMAT PARQUET,

        COMPRESSION ZSTD
    )
    """
)


con.execute(
    f"""
    COPY (

        {fallback_select}

    )

    TO '{fallback_csv_path}'

    (
        HEADER,

        DELIMITER ','
    )
    """
)


fallback_stats = con.execute(
    f"""
    SELECT

        COUNT(*)
            AS requests,

        COUNT(
            DISTINCT climate_id
        )
            AS unique_ids,

        COUNT(
            DISTINCT grid_id
        )
            AS unique_grids,

        MIN(date_str)
            AS first_date,

        MAX(date_str)
            AS last_date

    FROM read_parquet(
        '{fallback_parquet_path}'
    )
    """
).fetchdf()


print(
    "\nERA5 fallback requests:"
)

print(
    fallback_stats.to_string(
        index=False
    )
)


# ============================================================
# 17. FALLBACK MANIFEST = YEAR × MONTH
# ============================================================

fallback_manifest = con.execute(
    f"""
    SELECT

        year,

        month,

        COUNT(*)
            AS requests

    FROM read_parquet(
        '{fallback_parquet_path}'
    )

    GROUP BY
        year,
        month

    ORDER BY
        year,
        month
    """
).fetchdf()


fallback_manifest.to_csv(

    FALLBACK_MANIFEST,

    index=False,

    encoding="utf-8-sig"
)


print("\n" + "=" * 80)
print("GLOBAL QC FINISHED")
print("=" * 80)


print("\nPrimary climate lookup:")
print(PRIMARY_PARQUET)

print("\nGlobal QC:")
print(GLOBAL_QC_CSV)

print("\nERA5 fallback requests:")
print(FALLBACK_CSV)

print("\nERA5 fallback manifest:")
print(FALLBACK_MANIFEST)


con.close()