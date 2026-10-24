from pathlib import Path
import duckdb

SRC = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位\groundsource_2026.parquet"
)

src = SRC.as_posix()

con = duckdb.connect()

# 关键：禁止 DuckDB 自动把 GeoParquet WKB 转换成 GEOMETRY
con.execute("SET enable_geoparquet_conversion = false")

print("=" * 80)
print("1. BASIC COUNTS")
print("=" * 80)

print(
    con.execute(
        f"""
        SELECT
            COUNT(*) AS n_rows,
            COUNT(DISTINCT uuid) AS n_unique_uuid,
            COUNT(*) - COUNT(DISTINCT uuid) AS duplicated_uuid
        FROM read_parquet('{src}')
        """
    ).fetchdf()
)


print("\n" + "=" * 80)
print("2. NULL VALUES")
print("=" * 80)

print(
    con.execute(
        f"""
        SELECT
            SUM(CASE WHEN uuid IS NULL THEN 1 ELSE 0 END) AS null_uuid,
            SUM(CASE WHEN area_km2 IS NULL THEN 1 ELSE 0 END) AS null_area,
            SUM(CASE WHEN geometry IS NULL THEN 1 ELSE 0 END) AS null_geometry,
            SUM(CASE WHEN start_date IS NULL THEN 1 ELSE 0 END) AS null_start,
            SUM(CASE WHEN end_date IS NULL THEN 1 ELSE 0 END) AS null_end
        FROM read_parquet('{src}')
        """
    ).fetchdf()
)


print("\n" + "=" * 80)
print("3. DATE CHECK")
print("=" * 80)

print(
    con.execute(
        f"""
        WITH d AS (
            SELECT
                TRY_CAST(start_date AS DATE) AS s,
                TRY_CAST(end_date AS DATE) AS e
            FROM read_parquet('{src}')
        )
        SELECT
            MIN(s) AS min_start,
            MAX(s) AS max_start,
            MIN(e) AS min_end,
            MAX(e) AS max_end,

            SUM(CASE WHEN s IS NULL THEN 1 ELSE 0 END)
                AS invalid_start,

            SUM(CASE WHEN e IS NULL THEN 1 ELSE 0 END)
                AS invalid_end,

            SUM(CASE WHEN e < s THEN 1 ELSE 0 END)
                AS end_before_start

        FROM d
        """
    ).fetchdf()
)


print("\n" + "=" * 80)
print("4. EVENT DURATION")
print("=" * 80)

print(
    con.execute(
        f"""
        WITH d AS (
            SELECT
                TRY_CAST(start_date AS DATE) AS s,
                TRY_CAST(end_date AS DATE) AS e
            FROM read_parquet('{src}')
        ),
        x AS (
            SELECT
                DATE_DIFF('day', s, e) + 1 AS duration
            FROM d
            WHERE s IS NOT NULL
              AND e IS NOT NULL
              AND e >= s
        )
        SELECT
            MIN(duration) AS min_days,
            MEDIAN(duration) AS median_days,
            QUANTILE_CONT(duration, 0.90) AS p90_days,
            QUANTILE_CONT(duration, 0.95) AS p95_days,
            QUANTILE_CONT(duration, 0.99) AS p99_days,
            MAX(duration) AS max_days
        FROM x
        """
    ).fetchdf()
)


print("\n" + "=" * 80)
print("5. AREA DISTRIBUTION")
print("=" * 80)

print(
    con.execute(
        f"""
        SELECT
            MIN(area_km2) AS min_area,
            MEDIAN(area_km2) AS median_area,
            QUANTILE_CONT(area_km2, 0.90) AS p90_area,
            QUANTILE_CONT(area_km2, 0.95) AS p95_area,
            QUANTILE_CONT(area_km2, 0.99) AS p99_area,
            MAX(area_km2) AS max_area,

            SUM(
                CASE WHEN area_km2 <= 0
                THEN 1 ELSE 0 END
            ) AS nonpositive_area

        FROM read_parquet('{src}')
        """
    ).fetchdf()
)


print("\n" + "=" * 80)
print("6. SAME GEOMETRY + SAME DATE INTERVAL")
print("=" * 80)

print(
    con.execute(
        f"""
        SELECT
            COUNT(*) AS duplicate_groups,
            SUM(n - 1) AS excess_rows
        FROM (
            SELECT
                geometry,
                start_date,
                end_date,
                COUNT(*) AS n
            FROM read_parquet('{src}')
            GROUP BY
                geometry,
                start_date,
                end_date
            HAVING COUNT(*) > 1
        )
        """
    ).fetchdf()
)

con.close()