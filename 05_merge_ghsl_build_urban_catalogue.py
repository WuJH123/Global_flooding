from __future__ import annotations

import os
import re
from pathlib import Path

import duckdb
import pandas as pd


# ============================================================
# 0. CONFIG
# ============================================================

# GEE 下载后的根目录
GHSL_ROOT = Path(
    r"E:\Global_flooding\Groundsource_all"
)

# 前面生成的 Groundsource positive grid-day 数据
GRIDDAY_PARQUET = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位"
    r"\processed\groundsource_gridday_20km.parquet"
)

# 本地处理结果输出目录
OUT_DIR = Path(
    r"E:\Global_flooding\processed_local"
)

OUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# GHSL epochs
YEARS = [
    2000,
    2005,
    2010,
    2015,
    2020,
    2025
]

EXPECTED_CHUNKS = set(range(19))

EXPECTED_GRIDS = 94738


# ============================================================
# OUTPUT FILES
# ============================================================

GHSL_ALL = (
    OUT_DIR
    / "ghsl_all_epochs.parquet"
)

JOINED = (
    OUT_DIR
    / "groundsource_gridday_with_ghsl.parquet"
)

QC_GHSL = (
    OUT_DIR
    / "ghsl_qc_by_epoch.csv"
)

SENSITIVITY = (
    OUT_DIR
    / "urban_definition_sensitivity.csv"
)

YEARLY = (
    OUT_DIR
    / "urban_catalogue_yearly_summary.csv"
)

EPOCH_SUMMARY = (
    OUT_DIR
    / "urban_catalogue_epoch_summary.csv"
)

M1_OUT = (
    OUT_DIR
    / "urban_catalogue_m1_pop100.parquet"
)

M2_OUT = (
    OUT_DIR
    / "urban_catalogue_m2_share50.parquet"
)

M3_OUT = (
    OUT_DIR
    / "urban_catalogue_m3_pop100_share50.parquet"
)


# ============================================================
# EXPECTED GEE CSV COLUMNS
# ============================================================

COLS = [

    "grid_id",

    "grid_x",
    "grid_y",

    "chunk_id",

    "epoch",

    "grid_area_km2",

    "pop_total",
    "pop_density",

    "urban_pop",
    "urban_pop_share",

    "dense_pop",
    "dense_pop_share",

    "urban_m2",
    "urban_area_fraction",

    "dense_m2",
    "dense_area_fraction",
]


NUM_COLS = [
    c
    for c in COLS
    if c != "grid_id"
]


CHUNK_RE = re.compile(
    r"_chunk_(\d+)\.csv$",
    re.IGNORECASE
)


# ============================================================
# 1. HELPER FUNCTIONS
# ============================================================

def get_chunk_id(
    path: Path
) -> int:

    m = CHUNK_RE.search(
        path.name
    )

    if not m:

        raise ValueError(
            f"Cannot parse chunk id from:\n{path}"
        )

    return int(
        m.group(1)
    )


def find_year_files(
    year: int
) -> list[Path]:

    """
    Example expected path:

    E:\\Global_flooding\\Groundsource_all\\
    Groundsource_GHSL_2000\\
    groundsource_ghsl_2000_chunk_0.csv

    rglob() allows one or more additional directory levels.
    """

    pattern = (
        f"groundsource_ghsl_"
        f"{year}_chunk_*.csv"
    )

    files = list(
        GHSL_ROOT.rglob(
            pattern
        )
    )

    files = [
        p
        for p in files
        if CHUNK_RE.search(
            p.name
        )
    ]

    files.sort(
        key=get_chunk_id
    )

    return files


def read_chunk(
    path: Path,
    year: int
) -> pd.DataFrame:

    df = pd.read_csv(
        path
    )

    # In case chunk_id was not exported,
    # reconstruct it from the filename.
    if "chunk_id" not in df.columns:

        df["chunk_id"] = (
            get_chunk_id(path)
        )


    missing = [
        c
        for c in COLS
        if c not in df.columns
    ]


    if missing:

        raise RuntimeError(

            f"\nMissing columns in:\n"
            f"{path}\n\n"

            f"Missing:\n"
            f"{missing}\n\n"

            f"Existing:\n"
            f"{list(df.columns)}"
        )


    df = df[
        COLS
    ].copy()


    # -----------------------------------------
    # Key type
    # -----------------------------------------

    df["grid_id"] = (
        df["grid_id"]
        .astype("string")
    )


    # -----------------------------------------
    # Numeric conversion
    # -----------------------------------------

    for col in NUM_COLS:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )


    # -----------------------------------------
    # Epoch
    # -----------------------------------------

    df["epoch"] = (
        df["epoch"]
        .fillna(year)
        .astype("int16")
    )


    # -----------------------------------------
    # Chunk id
    # -----------------------------------------

    file_chunk = (
        get_chunk_id(path)
    )


    df["chunk_id"] = (
        df["chunk_id"]
        .fillna(file_chunk)
        .astype("int16")
    )


    if (
        df["chunk_id"]
        != file_chunk
    ).any():

        raise RuntimeError(

            f"chunk_id mismatch:\n"
            f"{path}"
        )


    return df


# ============================================================
# 2. INPUT CHECK
# ============================================================

print(
    "=" * 80
)

print(
    "INPUT CHECK"
)

print(
    "=" * 80
)


if not GHSL_ROOT.exists():

    raise FileNotFoundError(
        GHSL_ROOT
    )


if not GRIDDAY_PARQUET.exists():

    raise FileNotFoundError(

        "\nGroundsource grid-day parquet "
        "was not found.\n"

        "Edit GRIDDAY_PARQUET at the top "
        "of this script.\n\n"

        f"Current path:\n"
        f"{GRIDDAY_PARQUET}"
    )


print(
    "GHSL root:"
)

print(
    GHSL_ROOT
)


print(
    "\nGroundsource grid-day:"
)

print(
    GRIDDAY_PARQUET
)


# ============================================================
# 3. MERGE 19 CHUNKS × 6 EPOCHS
# ============================================================

print(
    "\n"
    + "=" * 80
)

print(
    "STEP 1 — MERGE AND AUDIT GHSL CSV FILES"
)

print(
    "=" * 80
)


all_epochs = []

qc_rows = []


for year in YEARS:

    files = (
        find_year_files(
            year
        )
    )


    found_chunks = {
        get_chunk_id(p)
        for p in files
    }


    missing_chunks = (
        EXPECTED_CHUNKS
        - found_chunks
    )


    print(
        f"\n[{year}]"
    )

    print(
        f"CSV files found: "
        f"{len(files)}"
    )

    print(
        "Chunks found:",
        sorted(found_chunks)
    )


    # -----------------------------------------
    # Check all 19 chunks exist
    # -----------------------------------------

    if len(files) != 19:

        raise RuntimeError(

            f"{year}: expected 19 CSV files, "
            f"found {len(files)}."
        )


    if missing_chunks:

        raise RuntimeError(

            f"{year}: missing chunks:\n"
            f"{sorted(missing_chunks)}"
        )


    parts = []


    # -----------------------------------------
    # Read every chunk
    # -----------------------------------------

    for p in files:

        chunk = (
            get_chunk_id(p)
        )

        d = read_chunk(
            p,
            year
        )


        parts.append(
            d
        )


        print(

            f"  chunk "
            f"{chunk:02d}: "

            f"{len(d):,} rows"
        )


    # -----------------------------------------
    # Merge current epoch
    # -----------------------------------------

    df_year = pd.concat(

        parts,

        ignore_index=True,

        copy=False
    )


    # ========================================================
    # QC
    # ========================================================

    n_rows = len(
        df_year
    )


    n_unique = (
        df_year[
            "grid_id"
        ].nunique()
    )


    duplicate_keys = (
        df_year
        .duplicated(
            [
                "grid_id",
                "epoch"
            ],
            keep=False
        )
        .sum()
    )


    null_grid_id = (
        df_year[
            "grid_id"
        ]
        .isna()
        .sum()
    )


    null_pop_density = (
        df_year[
            "pop_density"
        ]
        .isna()
        .sum()
    )


    negative_pop_density = (
        (
            df_year[
                "pop_density"
            ]
            < 0
        )
        .sum()
    )


    bad_urban_share = (

        (
            df_year[
                "urban_pop_share"
            ]
            < -1e-9
        )

        |

        (
            df_year[
                "urban_pop_share"
            ]
            > 1.01
        )

    ).sum()


    bad_dense_share = (

        (
            df_year[
                "dense_pop_share"
            ]
            < -1e-9
        )

        |

        (
            df_year[
                "dense_pop_share"
            ]
            > 1.01
        )

    ).sum()


    bad_urban_area = (

        (
            df_year[
                "urban_area_fraction"
            ]
            < -1e-9
        )

        |

        (
            df_year[
                "urban_area_fraction"
            ]
            > 1.01
        )

    ).sum()


    bad_dense_area = (

        (
            df_year[
                "dense_area_fraction"
            ]
            < -1e-9
        )

        |

        (
            df_year[
                "dense_area_fraction"
            ]
            > 1.01
        )

    ).sum()


    print(
        f"Rows:           "
        f"{n_rows:,}"
    )

    print(
        f"Unique grids:   "
        f"{n_unique:,}"
    )

    print(
        f"Duplicate keys: "
        f"{duplicate_keys:,}"
    )


    # -----------------------------------------
    # Hard QC requirements
    # -----------------------------------------

    if n_rows != EXPECTED_GRIDS:

        raise RuntimeError(

            f"{year}: expected "
            f"{EXPECTED_GRIDS:,} rows, "

            f"found "
            f"{n_rows:,}"
        )


    if n_unique != EXPECTED_GRIDS:

        raise RuntimeError(

            f"{year}: expected "
            f"{EXPECTED_GRIDS:,} unique grids, "

            f"found "
            f"{n_unique:,}"
        )


    if duplicate_keys != 0:

        raise RuntimeError(

            f"{year}: duplicate "
            f"grid_id + epoch keys: "

            f"{duplicate_keys:,}"
        )


    epoch_values = sorted(

        df_year[
            "epoch"
        ]
        .dropna()
        .unique()
        .tolist()
    )


    if epoch_values != [year]:

        raise RuntimeError(

            f"{year}: epoch mismatch.\n"

            f"Found:\n"
            f"{epoch_values}"
        )


    if any([
        null_grid_id,
        null_pop_density,
        negative_pop_density,
        bad_urban_share,
        bad_dense_share,
        bad_urban_area,
        bad_dense_area,
    ]):

        raise RuntimeError(

            f"{year}: GHSL QC failed.\n"

            f"null_grid_id="
            f"{null_grid_id}\n"

            f"null_pop_density="
            f"{null_pop_density}\n"

            f"negative_pop_density="
            f"{negative_pop_density}\n"

            f"bad_urban_share="
            f"{bad_urban_share}\n"

            f"bad_dense_share="
            f"{bad_dense_share}\n"

            f"bad_urban_area="
            f"{bad_urban_area}\n"

            f"bad_dense_area="
            f"{bad_dense_area}"
        )


    # -----------------------------------------
    # Save epoch QC statistics
    # -----------------------------------------

    qc_rows.append({

        "epoch":
            year,

        "rows":
            n_rows,

        "unique_grids":
            n_unique,

        "duplicate_keys":
            int(
                duplicate_keys
            ),

        "null_grid_id":
            int(
                null_grid_id
            ),

        "null_pop_density":
            int(
                null_pop_density
            ),

        "negative_pop_density":
            int(
                negative_pop_density
            ),

        "bad_urban_pop_share":
            int(
                bad_urban_share
            ),

        "bad_dense_pop_share":
            int(
                bad_dense_share
            ),

        "bad_urban_area_fraction":
            int(
                bad_urban_area
            ),

        "bad_dense_area_fraction":
            int(
                bad_dense_area
            ),

        "pop_density_min":
            df_year[
                "pop_density"
            ].min(),

        "pop_density_median":
            df_year[
                "pop_density"
            ].median(),

        "pop_density_mean":
            df_year[
                "pop_density"
            ].mean(),

        "pop_density_max":
            df_year[
                "pop_density"
            ].max(),

        "urban_pop_share_mean":
            df_year[
                "urban_pop_share"
            ].mean(),

        "urban_area_fraction_mean":
            df_year[
                "urban_area_fraction"
            ].mean(),
    })


    all_epochs.append(
        df_year
    )


# ============================================================
# 4. MERGE SIX GHSL EPOCHS
# ============================================================

ghsl = pd.concat(

    all_epochs,

    ignore_index=True,

    copy=False
)


expected_total = (
    EXPECTED_GRIDS
    * len(YEARS)
)


if len(ghsl) != expected_total:

    raise RuntimeError(

        f"Expected "
        f"{expected_total:,} GHSL grid-epoch rows, "

        f"found "
        f"{len(ghsl):,}"
    )


if ghsl.duplicated(
    [
        "grid_id",
        "epoch"
    ]
).any():

    raise RuntimeError(

        "Duplicate grid_id + epoch "
        "exists in merged GHSL table."
    )


print(
    "\n"
    + "-" * 80
)

print(
    "ALL GHSL EPOCHS"
)

print(
    "-" * 80
)


print(
    f"Rows:           "
    f"{len(ghsl):,}"
)

print(
    f"Unique grids:   "
    f"{ghsl['grid_id'].nunique():,}"
)

print(
    f"Epochs:         "
    f"{sorted(ghsl['epoch'].unique())}"
)


# ============================================================
# 5. SAVE GHSL MASTER TABLE
# ============================================================

ghsl.to_parquet(

    GHSL_ALL,

    index=False,

    engine="pyarrow",

    compression="zstd"
)


pd.DataFrame(
    qc_rows
).to_csv(

    QC_GHSL,

    index=False,

    encoding="utf-8-sig"
)


print(
    "\nSaved:"
)

print(
    GHSL_ALL
)

print(
    QC_GHSL
)


# Release pandas memory
del ghsl
del all_epochs


# ============================================================
# 6. JOIN GHSL TO GROUNDSOURCE GRID-DAYS
# ============================================================

print(
    "\n"
    + "=" * 80
)

print(
    "STEP 2 — JOIN GHSL TO GROUNDSOURCE GRID-DAYS"
)

print(
    "=" * 80
)


# Remove old outputs when rerunning script
for p in [

    JOINED,

    M1_OUT,
    M2_OUT,
    M3_OUT,

]:

    if p.exists():

        p.unlink()


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


gridday_path = (
    GRIDDAY_PARQUET
    .as_posix()
)

ghsl_path = (
    GHSL_ALL
    .as_posix()
)

joined_path = (
    JOINED
    .as_posix()
)


# ============================================================
# GHSL EPOCH MATCHING
# ============================================================
#
# 2000-2004 -> 2000
# 2005-2009 -> 2005
# 2010-2014 -> 2010
# 2015-2019 -> 2015
# 2020-2024 -> 2020
# 2025-2026 -> 2025
#
# ============================================================

join_sql = f"""

WITH gd AS (

    SELECT

        *,

        CASE

            WHEN EXTRACT(
                YEAR FROM event_date
            )
            BETWEEN 2000 AND 2004
            THEN 2000


            WHEN EXTRACT(
                YEAR FROM event_date
            )
            BETWEEN 2005 AND 2009
            THEN 2005


            WHEN EXTRACT(
                YEAR FROM event_date
            )
            BETWEEN 2010 AND 2014
            THEN 2010


            WHEN EXTRACT(
                YEAR FROM event_date
            )
            BETWEEN 2015 AND 2019
            THEN 2015


            WHEN EXTRACT(
                YEAR FROM event_date
            )
            BETWEEN 2020 AND 2024
            THEN 2020


            WHEN EXTRACT(
                YEAR FROM event_date
            )
            BETWEEN 2025 AND 2026
            THEN 2025


            ELSE NULL

        END::SMALLINT
            AS ghsl_epoch


    FROM read_parquet(
        '{gridday_path}'
    )

),


g AS (

    SELECT

        grid_id,

        epoch::SMALLINT
            AS ghsl_epoch,

        grid_area_km2,

        pop_total,

        pop_density,

        urban_pop,

        urban_pop_share,

        dense_pop,

        dense_pop_share,

        urban_m2,

        urban_area_fraction,

        dense_m2,

        dense_area_fraction


    FROM read_parquet(
        '{ghsl_path}'
    )

)


SELECT

    gd.*,


    g.grid_area_km2,

    g.pop_total,

    g.pop_density,

    g.urban_pop,

    g.urban_pop_share,

    g.dense_pop,

    g.dense_pop_share,

    g.urban_m2,

    g.urban_area_fraction,

    g.dense_m2,

    g.dense_area_fraction,


    -- ======================================================
    -- M1
    -- Population density definition
    -- ======================================================

    (
        g.pop_density >= 100
    )
    AS urban_m1_pop100,


    -- ======================================================
    -- M2
    -- Urban population share
    -- ======================================================

    (
        g.urban_pop_share >= 0.25
    )
    AS urban_m2_share25,


    (
        g.urban_pop_share >= 0.50
    )
    AS urban_m2_share50,


    (
        g.urban_pop_share >= 0.75
    )
    AS urban_m2_share75,


    -- ======================================================
    -- M3
    -- Conservative intersection
    -- ======================================================

    (
        g.pop_density >= 100

        AND

        g.urban_pop_share >= 0.25
    )
    AS urban_m3_pop100_share25,


    (
        g.pop_density >= 100

        AND

        g.urban_pop_share >= 0.50
    )
    AS urban_m3_pop100_share50,


    (
        g.pop_density >= 100

        AND

        g.urban_pop_share >= 0.75
    )
    AS urban_m3_pop100_share75,


    -- ======================================================
    -- Spatial precision sensitivity
    -- ======================================================

    (
        gd.median_location_area_km2
        <= 25
    )
    AS location_median_area_le25,


    (
        gd.median_location_area_km2
        <= 100
    )
    AS location_median_area_le100,


    (
        gd.median_location_area_km2
        <= 500
    )
    AS location_median_area_le500


FROM gd


LEFT JOIN g

    ON
        gd.grid_id
        =
        g.grid_id

    AND

        gd.ghsl_epoch
        =
        g.ghsl_epoch
"""


con.execute(

    f"""

    COPY (

        {join_sql}

    )

    TO '{joined_path}'

    (
        FORMAT PARQUET,

        COMPRESSION ZSTD
    )

    """
)


print(
    "Saved joined dataset:"
)

print(
    JOINED
)


# ============================================================
# 7. JOIN AUDIT
# ============================================================

print(
    "\n"
    + "=" * 80
)

print(
    "STEP 3 — JOIN AUDIT"
)

print(
    "=" * 80
)


audit = con.execute(

    f"""

    SELECT

        COUNT(*)
            AS grid_days,

        COUNT(
            DISTINCT grid_id
        )
            AS unique_grids,


        COUNT(*)
        FILTER (
            WHERE pop_density
            IS NOT NULL
        )
            AS matched_grid_days,


        COUNT(*)
        FILTER (
            WHERE pop_density
            IS NULL
        )
            AS unmatched_grid_days,


        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE pop_density
            IS NULL
        )
            AS unmatched_grids,


        MIN(event_date)
            AS first_date,

        MAX(event_date)
            AS last_date


    FROM read_parquet(
        '{joined_path}'
    )

    """

).fetchdf()


print(
    audit.to_string(
        index=False
    )
)


unmatched = int(
    audit.loc[
        0,
        "unmatched_grid_days"
    ]
)


if unmatched != 0:

    raise RuntimeError(

        f"\nGHSL join incomplete.\n"

        f"{unmatched:,} grid-days "
        f"have no matching GHSL data.\n"

        "Stop here before ERA5/IMERG processing."
    )


# ============================================================
# 8. URBAN DEFINITION SENSITIVITY
# ============================================================

print(
    "\n"
    + "=" * 80
)

print(
    "STEP 4 — URBAN DEFINITION SENSITIVITY"
)

print(
    "=" * 80
)


sensitivity = con.execute(

    f"""

    SELECT

        'M1: pop_density >= 100'
            AS definition,

        COUNT(*)
        FILTER (
            WHERE urban_m1_pop100
        )
            AS positive_grid_days,

        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m1_pop100
        )
            AS unique_grids


    FROM read_parquet(
        '{joined_path}'
    )


    UNION ALL


    SELECT

        'M2: urban_pop_share >= 0.25',

        COUNT(*)
        FILTER (
            WHERE urban_m2_share25
        ),

        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m2_share25
        )


    FROM read_parquet(
        '{joined_path}'
    )


    UNION ALL


    SELECT

        'M2: urban_pop_share >= 0.50',

        COUNT(*)
        FILTER (
            WHERE urban_m2_share50
        ),

        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m2_share50
        )


    FROM read_parquet(
        '{joined_path}'
    )


    UNION ALL


    SELECT

        'M2: urban_pop_share >= 0.75',

        COUNT(*)
        FILTER (
            WHERE urban_m2_share75
        ),

        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m2_share75
        )


    FROM read_parquet(
        '{joined_path}'
    )


    UNION ALL


    SELECT

        'M3: pop>=100 AND share>=0.25',

        COUNT(*)
        FILTER (
            WHERE urban_m3_pop100_share25
        ),

        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m3_pop100_share25
        )


    FROM read_parquet(
        '{joined_path}'
    )


    UNION ALL


    SELECT

        'M3: pop>=100 AND share>=0.50',

        COUNT(*)
        FILTER (
            WHERE urban_m3_pop100_share50
        ),

        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m3_pop100_share50
        )


    FROM read_parquet(
        '{joined_path}'
    )


    UNION ALL


    SELECT

        'M3: pop>=100 AND share>=0.75',

        COUNT(*)
        FILTER (
            WHERE urban_m3_pop100_share75
        ),

        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m3_pop100_share75
        )


    FROM read_parquet(
        '{joined_path}'
    )

    """

).fetchdf()


total_grid_days = int(

    audit.loc[
        0,
        "grid_days"
    ]
)


sensitivity[
    "share_of_all_grid_days"
] = (

    sensitivity[
        "positive_grid_days"
    ]

    / total_grid_days
)


sensitivity.to_csv(

    SENSITIVITY,

    index=False,

    encoding="utf-8-sig"
)


print(
    sensitivity.to_string(
        index=False
    )
)


print(
    "\nSaved:"
)

print(
    SENSITIVITY
)


# ============================================================
# 9. YEARLY SUMMARY
# ============================================================

yearly = con.execute(

    f"""

    SELECT

        EXTRACT(
            YEAR FROM event_date
        )::INTEGER
            AS year,


        COUNT(*)
            AS all_grid_days,


        COUNT(
            DISTINCT grid_id
        )
            AS all_grids,


        COUNT(*)
        FILTER (
            WHERE urban_m1_pop100
        )
            AS m1_grid_days,


        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m1_pop100
        )
            AS m1_grids,


        COUNT(*)
        FILTER (
            WHERE urban_m2_share50
        )
            AS m2_50_grid_days,


        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m2_share50
        )
            AS m2_50_grids,


        COUNT(*)
        FILTER (
            WHERE urban_m3_pop100_share50
        )
            AS m3_50_grid_days,


        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m3_pop100_share50
        )
            AS m3_50_grids,


        MEDIAN(
            pop_density
        )
            AS median_pop_density,


        MEDIAN(
            urban_pop_share
        )
            AS median_urban_pop_share


    FROM read_parquet(
        '{joined_path}'
    )


    GROUP BY year

    ORDER BY year

    """

).fetchdf()


yearly.to_csv(

    YEARLY,

    index=False,

    encoding="utf-8-sig"
)


# ============================================================
# 10. EPOCH SUMMARY
# ============================================================

epoch_summary = con.execute(

    f"""

    SELECT

        ghsl_epoch,


        COUNT(*)
            AS all_grid_days,


        COUNT(
            DISTINCT grid_id
        )
            AS all_grids,


        COUNT(*)
        FILTER (
            WHERE urban_m3_pop100_share50
        )
            AS m3_50_grid_days,


        COUNT(
            DISTINCT grid_id
        )
        FILTER (
            WHERE urban_m3_pop100_share50
        )
            AS m3_50_grids,


        MIN(
            pop_density
        )
            AS pop_density_min,


        MEDIAN(
            pop_density
        )
            AS pop_density_median,


        AVG(
            pop_density
        )
            AS pop_density_mean,


        MAX(
            pop_density
        )
            AS pop_density_max,


        AVG(
            urban_pop_share
        )
            AS mean_urban_pop_share,


        AVG(
            urban_area_fraction
        )
            AS mean_urban_area_fraction


    FROM read_parquet(
        '{joined_path}'
    )


    GROUP BY ghsl_epoch

    ORDER BY ghsl_epoch

    """

).fetchdf()


epoch_summary.to_csv(

    EPOCH_SUMMARY,

    index=False,

    encoding="utf-8-sig"
)


print(
    "\n"
    + "=" * 80
)

print(
    "YEARLY SUMMARY"
)

print(
    "=" * 80
)

print(
    yearly.to_string(
        index=False
    )
)


print(
    "\n"
    + "=" * 80
)

print(
    "EPOCH SUMMARY"
)

print(
    "=" * 80
)

print(
    epoch_summary.to_string(
        index=False
    )
)


# ============================================================
# 11. EXPORT ANALYSIS-READY CATALOGUES
# ============================================================

print(
    "\n"
    + "=" * 80
)

print(
    "STEP 5 — WRITE URBAN CATALOGUES"
)

print(
    "=" * 80
)


exports = [

    (
        M1_OUT,
        "urban_m1_pop100"
    ),

    (
        M2_OUT,
        "urban_m2_share50"
    ),

    (
        M3_OUT,
        "urban_m3_pop100_share50"
    ),
]


for output_file, mask in exports:

    output_path = (
        output_file
        .as_posix()
    )


    con.execute(

        f"""

        COPY (

            SELECT *

            FROM read_parquet(
                '{joined_path}'
            )

            WHERE {mask}

        )

        TO '{output_path}'

        (
            FORMAT PARQUET,

            COMPRESSION ZSTD
        )

        """
    )


    info = con.execute(

        f"""

        SELECT

            COUNT(*)
                AS rows,

            COUNT(
                DISTINCT grid_id
            )
                AS unique_grids,

            MIN(event_date)
                AS first_date,

            MAX(event_date)
                AS last_date


        FROM read_parquet(
            '{output_path}'
        )

        """

    ).fetchdf()


    print(
        f"\n{mask}"
    )

    print(
        info.to_string(
            index=False
        )
    )

    print(
        output_file
    )


# ============================================================
# 12. FINISH
# ============================================================

con.close()


print(
    "\n"
    + "=" * 80
)

print(
    "FINISHED"
)

print(
    "=" * 80
)


print(
    "\nCore outputs:"
)


for p in [

    GHSL_ALL,

    QC_GHSL,

    JOINED,

    SENSITIVITY,

    YEARLY,

    EPOCH_SUMMARY,

    M1_OUT,

    M2_OUT,

    M3_OUT,

]:

    print(
        p
    )


print(

    "\nIMPORTANT:\n"

    "M3 = pop_density >= 100 AND "
    "urban_pop_share >= 0.50 "
    "is currently only a candidate main definition.\n"

    "Do not fix it as the final paper definition "
    "until urban_definition_sensitivity.csv "
    "has been reviewed."
)