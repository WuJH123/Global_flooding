from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd


# ============================================================
# 0. CONFIG
# ============================================================

INPUT_PARQUET = Path(
    r"E:\Global_flooding\processed_local"
    r"\urban_catalogue_m3_pop100_share50.parquet"
)

OUT_DIR = Path(
    r"E:\Global_flooding\episode_control"
)

OUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ------------------------------------------------------------
# Episode definition
#
# If two positive days are consecutive:
#
#   difference = 1 day
#
# -> same episode
#
# If difference > 1 day
#
# -> new episode
#
# Later this can be sensitivity-tested using 2 days.
# ------------------------------------------------------------

EPISODE_MAX_GAP_DAYS = 1


# ------------------------------------------------------------
# Required number of controls per episode
# ------------------------------------------------------------

N_CONTROLS = 4


# ------------------------------------------------------------
# Strict control exclusion buffer
#
# A "strict" control must be more than ±3 days away from
# ANY positive flood day in the same grid.
#
# Later sensitivity:
#
#   0 days
#   3 days   <- main
#   7 days
#
# ------------------------------------------------------------

SAFE_BUFFER_DAYS = 3


# ------------------------------------------------------------
# Known size from previous processing.
#
# Used only as a sanity check.
# ------------------------------------------------------------

EXPECTED_GRID_DAYS = 1_378_124


# ============================================================
# 1. OUTPUT FILES
# ============================================================

WORK_DB = (
    OUT_DIR
    / "episode_control_work.duckdb"
)


EPISODES_OUT = (
    OUT_DIR
    / "flood_episodes_m3.parquet"
)


CONTROLS_OUT = (
    OUT_DIR
    / "matched_controls_m3.parquet"
)


MATCH_STATUS_OUT = (
    OUT_DIR
    / "episode_match_status.parquet"
)


MATCHED_EPISODES_OUT = (
    OUT_DIR
    / "matched_episodes_m3.parquet"
)


UNMATCHED_EPISODES_OUT = (
    OUT_DIR
    / "unmatched_episodes_m3.parquet"
)


MATCHED_DATA_OUT = (
    OUT_DIR
    / "matched_case_control_m3.parquet"
)


QC_OUT = (
    OUT_DIR
    / "episode_control_qc.csv"
)


EPISODE_YEARLY_OUT = (
    OUT_DIR
    / "episode_yearly_summary.csv"
)


CONTROL_QUALITY_OUT = (
    OUT_DIR
    / "control_quality_summary.csv"
)


# ============================================================
# 2. BASIC CHECK
# ============================================================

print("=" * 80)
print("INPUT CHECK")
print("=" * 80)

if not INPUT_PARQUET.exists():

    raise FileNotFoundError(
        f"\nInput file not found:\n"
        f"{INPUT_PARQUET}"
    )


print("Input:")
print(INPUT_PARQUET)

print("\nOutput directory:")
print(OUT_DIR)


# ============================================================
# 3. DUCKDB
# ============================================================

con = duckdb.connect(
    str(WORK_DB)
)


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


input_path = (
    INPUT_PARQUET
    .as_posix()
)


# ============================================================
# 4. CHECK SOURCE DATA
# ============================================================

print("\n" + "=" * 80)
print("STEP 1 — SOURCE AUDIT")
print("=" * 80)


source_stats = con.execute(
    f"""
    SELECT

        COUNT(*) AS grid_days,

        COUNT(DISTINCT grid_id)
            AS unique_grids,

        MIN(event_date)
            AS first_date,

        MAX(event_date)
            AS last_date,

        COUNT(*)
        -
        COUNT(
            DISTINCT
            grid_id || '|' ||
            CAST(event_date AS VARCHAR)
        )
            AS duplicate_grid_dates

    FROM read_parquet(
        '{input_path}'
    )
    """
).fetchdf()


print(
    source_stats.to_string(
        index=False
    )
)


n_grid_days = int(
    source_stats.loc[
        0,
        "grid_days"
    ]
)


if n_grid_days != EXPECTED_GRID_DAYS:

    print(
        "\nWARNING:"
        f"\nExpected {EXPECTED_GRID_DAYS:,} rows,"
        f" but found {n_grid_days:,}."
        "\nProcessing will continue."
    )


duplicates = int(
    source_stats.loc[
        0,
        "duplicate_grid_dates"
    ]
)


if duplicates != 0:

    raise RuntimeError(

        f"\nFound {duplicates:,} duplicate "
        "grid_id + event_date rows."

        "\nEpisode construction requires "
        "one row per grid-day."
    )


GLOBAL_START = str(
    source_stats.loc[
        0,
        "first_date"
    ]
)


GLOBAL_END = str(
    source_stats.loc[
        0,
        "last_date"
    ]
)


print(
    f"\nObservation window:"
    f"\n{GLOBAL_START}"
    f" -> "
    f"{GLOBAL_END}"
)


# ============================================================
# 5. BUILD POSITIVE-DAY EPISODE INDEX
# ============================================================

print("\n" + "=" * 80)
print("STEP 2 — BUILD FLOOD EPISODES")
print("=" * 80)


con.execute(
    f"""
    CREATE OR REPLACE TABLE positive_days AS

    WITH ordered AS (

        SELECT

            *,

            LAG(event_date)
            OVER (

                PARTITION BY grid_id

                ORDER BY event_date

            )
            AS previous_positive_date


        FROM read_parquet(
            '{input_path}'
        )

    ),


    flagged AS (

        SELECT

            *,

            CASE

                WHEN previous_positive_date
                     IS NULL
                THEN 1


                WHEN DATE_DIFF(
                        'day',
                        previous_positive_date,
                        event_date
                     )
                     >
                     {EPISODE_MAX_GAP_DAYS}
                THEN 1


                ELSE 0

            END
            AS new_episode


        FROM ordered

    )


    SELECT

        *,

        SUM(new_episode)
        OVER (

            PARTITION BY grid_id

            ORDER BY event_date

            ROWS BETWEEN
                UNBOUNDED PRECEDING
                AND CURRENT ROW

        )::BIGINT
        AS episode_seq


    FROM flagged
    """
)


# ============================================================
# 6. COLLAPSE GRID-DAYS INTO EPISODES
# ============================================================

con.execute(
    """
    CREATE OR REPLACE TABLE episodes_core AS

    SELECT

        grid_id,

        episode_seq,


        MIN(event_date)
            AS episode_start,


        MAX(event_date)
            AS episode_end,


        DATE_DIFF(
            'day',
            MIN(event_date),
            MAX(event_date)
        ) + 1
            AS episode_duration_days,


        COUNT(*)
            AS n_positive_days,


        SUM(
            COALESCE(
                n_location_records,
                0
            )
        )
            AS n_location_records_sum,


        SUM(
            COALESCE(
                n_unique_records,
                0
            )
        )
            AS n_unique_records_sum,


        MAX(
            COALESCE(
                n_location_records,
                0
            )
        )
            AS max_location_records_day,


        MEDIAN(
            median_location_area_km2
        )
            AS median_location_area_km2_episode,


        MAX(
            max_location_area_km2
        )
            AS max_location_area_km2_episode


    FROM positive_days


    GROUP BY

        grid_id,

        episode_seq
    """
)


# ------------------------------------------------------------
# Join episode-start-day attributes.
#
# We deliberately use episode_start as the CASE DATE.
#
# This is the most appropriate point for later extraction of:
#
#   rainfall onset
#   antecedent precipitation
#   antecedent soil moisture
#
# ------------------------------------------------------------

con.execute(
    """
    CREATE OR REPLACE TABLE episodes AS

    SELECT

        ec.grid_id
        || '__'
        || STRFTIME(
               ec.episode_start,
               '%Y%m%d'
           )
        || '__'
        || LPAD(
               CAST(
                   ec.episode_seq
                   AS VARCHAR
               ),
               4,
               '0'
           )
            AS episode_id,


        ec.grid_id,

        pd.grid_x,

        pd.grid_y,

        ec.episode_seq,


        ec.episode_start,

        ec.episode_end,

        ec.episode_duration_days,

        ec.n_positive_days,


        EXTRACT(
            YEAR FROM ec.episode_start
        )::INTEGER
            AS case_year,


        EXTRACT(
            MONTH FROM ec.episode_start
        )::INTEGER
            AS case_month,


        STRFTIME(
            ec.episode_start,
            '%w'
        )::INTEGER
            AS case_day_of_week,


        ec.n_location_records_sum,

        ec.n_unique_records_sum,

        ec.max_location_records_day,

        ec.median_location_area_km2_episode,

        ec.max_location_area_km2_episode,


        pd.mean_report_lon,

        pd.mean_report_lat,


        pd.ghsl_epoch,

        pd.grid_area_km2,

        pd.pop_total,

        pd.pop_density,

        pd.urban_pop,

        pd.urban_pop_share,

        pd.dense_pop,

        pd.dense_pop_share,

        pd.urban_m2,

        pd.urban_area_fraction,

        pd.dense_m2,

        pd.dense_area_fraction


    FROM episodes_core ec


    INNER JOIN positive_days pd

        ON
            ec.grid_id
            =
            pd.grid_id

        AND

            ec.episode_seq
            =
            pd.episode_seq

        AND

            ec.episode_start
            =
            pd.event_date
    """
)


episode_stats = con.execute(
    """
    SELECT

        COUNT(*) AS episodes,

        COUNT(
            DISTINCT grid_id
        ) AS grids,

        MIN(episode_start)
            AS first_episode,

        MAX(episode_start)
            AS last_episode,

        MIN(episode_duration_days)
            AS min_duration,

        MEDIAN(episode_duration_days)
            AS median_duration,

        QUANTILE_CONT(
            episode_duration_days,
            0.90
        ) AS p90_duration,

        QUANTILE_CONT(
            episode_duration_days,
            0.95
        ) AS p95_duration,

        QUANTILE_CONT(
            episode_duration_days,
            0.99
        ) AS p99_duration,

        MAX(episode_duration_days)
            AS max_duration

    FROM episodes
    """
).fetchdf()


print(
    episode_stats.to_string(
        index=False
    )
)


# ============================================================
# 7. BUILD POSITIVE DATE INDEX
# ============================================================

print("\n" + "=" * 80)
print("STEP 3 — BUILD CONTROL-DAY EXCLUSION INDEX")
print("=" * 80)


con.execute(
    """
    CREATE OR REPLACE TABLE positive_date_index AS

    SELECT DISTINCT

        grid_id,

        event_date

    FROM positive_days
    """
)


# ------------------------------------------------------------
# Strict exclusion dates
#
# Every positive flood day is expanded by:
#
#       ± SAFE_BUFFER_DAYS
#
# Example SAFE_BUFFER_DAYS = 3:
#
# flood = July 10
#
# forbidden:
# July 7 ... July 13
#
# This helps prevent controls being selected immediately
# before/after another documented flood.
# ------------------------------------------------------------

con.execute(
    f"""
    CREATE OR REPLACE TABLE forbidden_dates AS

    SELECT DISTINCT

        p.grid_id,


        CAST(

            p.event_date

            +

            r.i
            *
            INTERVAL 1 DAY

            AS DATE

        )
        AS forbidden_date


    FROM positive_date_index p


    CROSS JOIN

        RANGE(
            -{SAFE_BUFFER_DAYS},
            {SAFE_BUFFER_DAYS + 1}
        )
        AS r(i)
    """
)


forbidden_stats = con.execute(
    """
    SELECT

        COUNT(*) AS forbidden_grid_dates

    FROM forbidden_dates
    """
).fetchdf()


print(
    forbidden_stats.to_string(
        index=False
    )
)


# ============================================================
# 8. GENERATE CONTROL CANDIDATES
# ============================================================

print("\n" + "=" * 80)
print("STEP 4 — SELECT 4 MATCHED CONTROL DAYS")
print("=" * 80)


# ------------------------------------------------------------
# Matching rules
#
# HARD REQUIREMENTS:
#
#   same grid
#   same calendar year
#   same calendar month
#   NOT a positive flood day
#
#
# Preference hierarchy:
#
# Tier 1
#   >3 days from any flood
#   AND same weekday
#
# Tier 2
#   >3 days from any flood
#   different weekday
#
# Tier 3
#   near another flood
#   same weekday
#
# Tier 4
#   near another flood
#   different weekday
#
#
# Within the same tier:
#
#   choose dates closest to case date.
#
#
# Candidate dates are also constrained to the global
# Groundsource observation window.
# ------------------------------------------------------------


con.execute(
    f"""
    CREATE OR REPLACE TABLE selected_controls AS

    WITH candidate_dates AS (

        SELECT

            e.episode_id,

            e.grid_id,

            e.episode_start
                AS case_date,


            CAST(
                gs.control_timestamp
                AS DATE
            )
                AS control_date


        FROM episodes e


        CROSS JOIN LATERAL

        GENERATE_SERIES(

            CAST(

                GREATEST(

                    DATE_TRUNC(
                        'month',
                        e.episode_start
                    )::DATE,

                    DATE '{GLOBAL_START}'

                )

                AS TIMESTAMP

            ),


            CAST(

                LEAST(

                    (
                        DATE_TRUNC(
                            'month',
                            e.episode_start
                        )

                        +

                        INTERVAL 1 MONTH

                        -

                        INTERVAL 1 DAY

                    )::DATE,

                    DATE '{GLOBAL_END}'

                )

                AS TIMESTAMP

            ),


            INTERVAL 1 DAY

        )
        AS gs(control_timestamp)

    ),


    eligible AS (

        SELECT

            c.episode_id,

            c.grid_id,

            c.case_date,

            c.control_date,


            CASE

                WHEN fd.forbidden_date
                     IS NULL
                THEN TRUE

                ELSE FALSE

            END
                AS strict_ok,


            CASE

                WHEN STRFTIME(
                         c.control_date,
                         '%w'
                     )
                     =
                     STRFTIME(
                         c.case_date,
                         '%w'
                     )
                THEN TRUE

                ELSE FALSE

            END
                AS same_weekday,


            ABS(
                DATE_DIFF(
                    'day',
                    c.case_date,
                    c.control_date
                )
            )
                AS days_from_case


        FROM candidate_dates c


        LEFT JOIN positive_date_index p

            ON
                c.grid_id
                =
                p.grid_id

            AND

                c.control_date
                =
                p.event_date


        LEFT JOIN forbidden_dates fd

            ON
                c.grid_id
                =
                fd.grid_id

            AND

                c.control_date
                =
                fd.forbidden_date


        WHERE
            p.event_date
            IS NULL

    ),


    scored AS (

        SELECT

            *,


            CASE

                WHEN
                    strict_ok
                    AND
                    same_weekday
                THEN 1


                WHEN
                    strict_ok
                THEN 2


                WHEN
                    same_weekday
                THEN 3


                ELSE 4

            END
                AS priority_tier,


            CASE

                WHEN
                    strict_ok
                    AND
                    same_weekday
                THEN
                    'strict_same_weekday'


                WHEN
                    strict_ok
                THEN
                    'strict_other_weekday'


                WHEN
                    same_weekday
                THEN
                    'fallback_near_flood_same_weekday'


                ELSE
                    'fallback_near_flood_other_weekday'

            END
                AS control_quality


        FROM eligible

    ),


    ranked AS (

        SELECT

            *,


            ROW_NUMBER()
            OVER (

                PARTITION BY
                    episode_id

                ORDER BY

                    priority_tier,

                    days_from_case,

                    control_date

            )
                AS control_rank


        FROM scored

    )


    SELECT

        episode_id,

        grid_id,

        case_date,

        control_date,

        control_rank,

        strict_ok,

        same_weekday,

        days_from_case,

        priority_tier,

        control_quality


    FROM ranked


    WHERE
        control_rank
        <=
        {N_CONTROLS}
    """
)


# ============================================================
# 9. MATCH STATUS
# ============================================================

con.execute(
    f"""
    CREATE OR REPLACE TABLE match_status AS

    SELECT

        e.episode_id,

        e.grid_id,

        e.episode_start,


        COUNT(
            c.control_date
        )
            AS n_controls,


        SUM(

            CASE
                WHEN c.strict_ok
                THEN 1
                ELSE 0
            END

        )
            AS n_strict_controls,


        SUM(

            CASE
                WHEN c.same_weekday
                THEN 1
                ELSE 0
            END

        )
            AS n_same_weekday_controls,


        SUM(

            CASE
                WHEN
                    c.strict_ok = FALSE
                THEN 1
                ELSE 0
            END

        )
            AS n_fallback_controls,


        CASE

            WHEN
                COUNT(
                    c.control_date
                )
                =
                {N_CONTROLS}

            THEN TRUE

            ELSE FALSE

        END
            AS complete_match


    FROM episodes e


    LEFT JOIN selected_controls c

        ON
            e.episode_id
            =
            c.episode_id


    GROUP BY

        e.episode_id,

        e.grid_id,

        e.episode_start
    """
)


match_stats = con.execute(
    f"""
    SELECT

        COUNT(*) AS episodes,

        COUNT(*)
        FILTER (
            WHERE complete_match
        )
            AS matched_episodes,

        COUNT(*)
        FILTER (
            WHERE NOT complete_match
        )
            AS unmatched_episodes,

        AVG(n_controls)
            AS mean_controls,

        SUM(n_fallback_controls)
            AS fallback_controls,

        SUM(n_same_weekday_controls)
            AS same_weekday_controls,

        SUM(n_strict_controls)
            AS strict_controls

    FROM match_status
    """
).fetchdf()


print(
    match_stats.to_string(
        index=False
    )
)


# ============================================================
# 10. HARD QC CHECKS
# ============================================================

print("\n" + "=" * 80)
print("STEP 5 — MATCHING QUALITY CONTROL")
print("=" * 80)


# ------------------------------------------------------------
# Control cannot itself be a flood-positive date.
# ------------------------------------------------------------

controls_on_flood_days = con.execute(
    """
    SELECT
        COUNT(*)

    FROM selected_controls c

    INNER JOIN positive_date_index p

        ON
            c.grid_id
            =
            p.grid_id

        AND

            c.control_date
            =
            p.event_date
    """
).fetchone()[0]


# ------------------------------------------------------------
# Controls must have same year and month as case.
# ------------------------------------------------------------

wrong_month_year = con.execute(
    """
    SELECT
        COUNT(*)

    FROM selected_controls

    WHERE

        EXTRACT(
            YEAR FROM case_date
        )
        !=
        EXTRACT(
            YEAR FROM control_date
        )

        OR

        EXTRACT(
            MONTH FROM case_date
        )
        !=
        EXTRACT(
            MONTH FROM control_date
        )
    """
).fetchone()[0]


# ------------------------------------------------------------
# No duplicated control dates within one episode.
# ------------------------------------------------------------

duplicate_controls = con.execute(
    """
    SELECT
        COUNT(*)

    FROM (

        SELECT

            episode_id,

            control_date,

            COUNT(*) AS n


        FROM selected_controls


        GROUP BY

            episode_id,

            control_date


        HAVING
            COUNT(*) > 1

    )
    """
).fetchone()[0]


# ------------------------------------------------------------
# Complete episodes must have exactly 4 controls.
# ------------------------------------------------------------

bad_complete_sets = con.execute(
    f"""
    SELECT
        COUNT(*)

    FROM match_status

    WHERE

        complete_match

        AND

        n_controls
        !=
        {N_CONTROLS}
    """
).fetchone()[0]


print(
    "Controls occurring on positive flood days:",
    controls_on_flood_days
)

print(
    "Controls with wrong month/year:",
    wrong_month_year
)

print(
    "Duplicated controls within episode:",
    duplicate_controls
)

print(
    "Complete sets without exactly 4 controls:",
    bad_complete_sets
)


if any(
    [
        controls_on_flood_days,
        wrong_month_year,
        duplicate_controls,
        bad_complete_sets,
    ]
):

    raise RuntimeError(
        "\nControl matching QC failed."
    )


# ============================================================
# 11. BUILD MATCHED EPISODE TABLE
# ============================================================

con.execute(
    """
    CREATE OR REPLACE TABLE matched_episodes AS

    SELECT
        e.*

    FROM episodes e

    INNER JOIN match_status m

        ON
            e.episode_id
            =
            m.episode_id

    WHERE
        m.complete_match
    """
)


con.execute(
    """
    CREATE OR REPLACE TABLE unmatched_episodes AS

    SELECT

        e.*,

        m.n_controls,

        m.n_strict_controls,

        m.n_same_weekday_controls,

        m.n_fallback_controls


    FROM episodes e

    INNER JOIN match_status m

        ON
            e.episode_id
            =
            m.episode_id


    WHERE
        NOT m.complete_match
    """
)


# ============================================================
# 12. BUILD FINAL MATCHED CASE-CONTROL DATASET
# ============================================================

print("\n" + "=" * 80)
print("STEP 6 — BUILD FINAL MATCHED DATASET")
print("=" * 80)


con.execute(
    """
    CREATE OR REPLACE TABLE matched_case_control AS


    -- ======================================================
    -- CASE ROW
    -- ======================================================

    SELECT

        e.episode_id
            AS stratum_id,


        e.episode_id
        || '__CASE'
            AS sample_id,


        e.grid_id,

        e.grid_x,

        e.grid_y,


        e.episode_start
            AS sample_date,


        1::INTEGER
            AS is_case,


        'case'::VARCHAR
            AS role,


        0::INTEGER
            AS control_rank,


        NULL::BOOLEAN
            AS strict_control,


        NULL::BOOLEAN
            AS same_weekday,


        0::INTEGER
            AS days_from_case,


        0::INTEGER
            AS priority_tier,


        'case'::VARCHAR
            AS control_quality,


        e.episode_start,

        e.episode_end,

        e.episode_duration_days,

        e.n_positive_days,


        e.case_year,

        e.case_month,

        e.case_day_of_week,


        e.n_location_records_sum,

        e.n_unique_records_sum,

        e.max_location_records_day,

        e.median_location_area_km2_episode,

        e.max_location_area_km2_episode,


        e.mean_report_lon,

        e.mean_report_lat,


        e.ghsl_epoch,

        e.grid_area_km2,

        e.pop_total,

        e.pop_density,

        e.urban_pop,

        e.urban_pop_share,

        e.dense_pop,

        e.dense_pop_share,

        e.urban_m2,

        e.urban_area_fraction,

        e.dense_m2,

        e.dense_area_fraction


    FROM matched_episodes e


    UNION ALL


    -- ======================================================
    -- CONTROL ROWS
    -- ======================================================

    SELECT

        e.episode_id
            AS stratum_id,


        e.episode_id
        || '__CTRL'
        || CAST(
               c.control_rank
               AS VARCHAR
           )
            AS sample_id,


        e.grid_id,

        e.grid_x,

        e.grid_y,


        c.control_date
            AS sample_date,


        0::INTEGER
            AS is_case,


        'control'::VARCHAR
            AS role,


        c.control_rank,


        c.strict_ok
            AS strict_control,


        c.same_weekday,


        c.days_from_case,


        c.priority_tier,


        c.control_quality,


        e.episode_start,

        e.episode_end,

        e.episode_duration_days,

        e.n_positive_days,


        e.case_year,

        e.case_month,

        e.case_day_of_week,


        e.n_location_records_sum,

        e.n_unique_records_sum,

        e.max_location_records_day,

        e.median_location_area_km2_episode,

        e.max_location_area_km2_episode,


        e.mean_report_lon,

        e.mean_report_lat,


        e.ghsl_epoch,

        e.grid_area_km2,

        e.pop_total,

        e.pop_density,

        e.urban_pop,

        e.urban_pop_share,

        e.dense_pop,

        e.dense_pop_share,

        e.urban_m2,

        e.urban_area_fraction,

        e.dense_m2,

        e.dense_area_fraction


    FROM selected_controls c


    INNER JOIN matched_episodes e

        ON
            c.episode_id
            =
            e.episode_id
    """
)


# ============================================================
# 13. FINAL MATCHED-DATA QC
# ============================================================

final_stats = con.execute(
    """
    SELECT

        COUNT(*)
            AS rows,

        COUNT(
            DISTINCT stratum_id
        )
            AS matched_episodes,

        SUM(is_case)
            AS cases,

        COUNT(*)
        FILTER (
            WHERE is_case = 0
        )
            AS controls

    FROM matched_case_control
    """
).fetchdf()


print(
    final_stats.to_string(
        index=False
    )
)


bad_strata = con.execute(
    f"""
    SELECT
        COUNT(*)

    FROM (

        SELECT

            stratum_id,

            COUNT(*) AS n_rows,

            SUM(is_case)
                AS n_cases,

            COUNT(*)
            FILTER (
                WHERE is_case = 0
            )
                AS n_controls


        FROM matched_case_control


        GROUP BY
            stratum_id


        HAVING

            n_rows
            !=
            {N_CONTROLS + 1}

            OR

            n_cases
            !=
            1

            OR

            n_controls
            !=
            {N_CONTROLS}

    )
    """
).fetchone()[0]


print(
    "Invalid matched strata:",
    bad_strata
)


if bad_strata != 0:

    raise RuntimeError(
        "\nFinal matched-set QC failed."
    )


# ============================================================
# 14. EXPORT PARQUET FILES
# ============================================================

print("\n" + "=" * 80)
print("STEP 7 — EXPORT DATA")
print("=" * 80)


def export_parquet(
    table_name: str,
    output_path: Path,
    order_by: str | None = None,
):

    path = (
        output_path
        .as_posix()
    )


    if output_path.exists():

        output_path.unlink()


    order_sql = ""

    if order_by:

        order_sql = (
            f"ORDER BY {order_by}"
        )


    con.execute(
        f"""
        COPY (

            SELECT *

            FROM {table_name}

            {order_sql}

        )

        TO '{path}'

        (
            FORMAT PARQUET,

            COMPRESSION ZSTD
        )
        """
    )


    print(
        output_path
    )


export_parquet(
    "episodes",
    EPISODES_OUT,
    "grid_id, episode_start"
)


export_parquet(
    "selected_controls",
    CONTROLS_OUT,
    "episode_id, control_rank"
)


export_parquet(
    "match_status",
    MATCH_STATUS_OUT,
    "episode_start, grid_id"
)


export_parquet(
    "matched_episodes",
    MATCHED_EPISODES_OUT,
    "grid_id, episode_start"
)


export_parquet(
    "unmatched_episodes",
    UNMATCHED_EPISODES_OUT,
    "grid_id, episode_start"
)


export_parquet(
    "matched_case_control",
    MATCHED_DATA_OUT,
    "stratum_id, is_case DESC, control_rank"
)


# ============================================================
# 15. YEARLY EPISODE SUMMARY
# ============================================================

episode_yearly = con.execute(
    """
    SELECT

        case_year
            AS year,


        COUNT(*)
            AS episodes,


        COUNT(
            DISTINCT grid_id
        )
            AS grids,


        MEDIAN(
            episode_duration_days
        )
            AS median_duration_days,


        AVG(
            episode_duration_days
        )
            AS mean_duration_days,


        COUNT(*)
        FILTER (
            WHERE episode_duration_days = 1
        )
            AS one_day_episodes


    FROM episodes


    GROUP BY
        case_year


    ORDER BY
        case_year
    """
).fetchdf()


episode_yearly.to_csv(
    EPISODE_YEARLY_OUT,
    index=False,
    encoding="utf-8-sig"
)


# ============================================================
# 16. CONTROL QUALITY SUMMARY
# ============================================================

control_quality = con.execute(
    """
    SELECT

        control_quality,

        COUNT(*)
            AS controls,

        COUNT(
            DISTINCT episode_id
        )
            AS episodes_using_this_control_type,

        AVG(days_from_case)
            AS mean_days_from_case,

        MEDIAN(days_from_case)
            AS median_days_from_case


    FROM selected_controls


    GROUP BY
        control_quality


    ORDER BY
        controls DESC
    """
).fetchdf()


control_quality.to_csv(
    CONTROL_QUALITY_OUT,
    index=False,
    encoding="utf-8-sig"
)


# ============================================================
# 17. GLOBAL QC SUMMARY
# ============================================================

episode_count = int(
    episode_stats.loc[
        0,
        "episodes"
    ]
)


matched_count = int(
    match_stats.loc[
        0,
        "matched_episodes"
    ]
)


unmatched_count = int(
    match_stats.loc[
        0,
        "unmatched_episodes"
    ]
)


controls_count = int(
    final_stats.loc[
        0,
        "controls"
    ]
)


qc = pd.DataFrame(
    [
        {
            "metric":
                "input_positive_grid_days",

            "value":
                n_grid_days,
        },

        {
            "metric":
                "independent_flood_episodes",

            "value":
                episode_count,
        },

        {
            "metric":
                "matched_episodes_exactly_4_controls",

            "value":
                matched_count,
        },

        {
            "metric":
                "unmatched_episodes",

            "value":
                unmatched_count,
        },

        {
            "metric":
                "match_success_rate",

            "value":
                matched_count
                /
                episode_count
                if episode_count
                else 0,
        },

        {
            "metric":
                "selected_control_rows",

            "value":
                controls_count,
        },

        {
            "metric":
                "safe_buffer_days",

            "value":
                SAFE_BUFFER_DAYS,
        },

        {
            "metric":
                "controls_per_episode",

            "value":
                N_CONTROLS,
        },

        {
            "metric":
                "controls_on_flood_days",

            "value":
                controls_on_flood_days,
        },

        {
            "metric":
                "wrong_month_year_controls",

            "value":
                wrong_month_year,
        },

        {
            "metric":
                "duplicate_controls",

            "value":
                duplicate_controls,
        },

        {
            "metric":
                "invalid_matched_strata",

            "value":
                bad_strata,
        },
    ]
)


qc.to_csv(
    QC_OUT,
    index=False,
    encoding="utf-8-sig"
)


# ============================================================
# 18. PRINT SUMMARIES
# ============================================================

print("\n" + "=" * 80)
print("EPISODE YEARLY SUMMARY")
print("=" * 80)

print(
    episode_yearly.to_string(
        index=False
    )
)


print("\n" + "=" * 80)
print("CONTROL QUALITY SUMMARY")
print("=" * 80)

print(
    control_quality.to_string(
        index=False
    )
)


print("\n" + "=" * 80)
print("GLOBAL QC")
print("=" * 80)

print(
    qc.to_string(
        index=False
    )
)


# ============================================================
# 19. FINISH
# ============================================================

con.execute(
    "CHECKPOINT"
)

con.close()


print("\n" + "=" * 80)
print("FINISHED")
print("=" * 80)


print("\nCore outputs:")

for p in [

    EPISODES_OUT,

    CONTROLS_OUT,

    MATCH_STATUS_OUT,

    MATCHED_EPISODES_OUT,

    UNMATCHED_EPISODES_OUT,

    MATCHED_DATA_OUT,

    QC_OUT,

    EPISODE_YEARLY_OUT,

    CONTROL_QUALITY_OUT,

]:

    print(p)


print(
    "\nMain climate-extraction input:"
)

print(
    MATCHED_DATA_OUT
)