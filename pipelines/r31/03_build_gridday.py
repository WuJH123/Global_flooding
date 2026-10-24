from pathlib import Path
import duckdb


INFILE = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位\processed\groundsource_stage1_records_20km.parquet"
)

OUTFILE = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位\processed\groundsource_gridday_20km.parquet"
)


con = duckdb.connect()

src = INFILE.as_posix()
out = OUTFILE.as_posix()


con.execute(
    f"""
    COPY (

        WITH expanded AS (

            SELECT
                uuid,
                area_km2,
                lon,
                lat,
                grid_x,
                grid_y,
                grid_id,

                CAST(gs.event_date AS DATE)
                    AS event_date

            FROM read_parquet('{src}')

            CROSS JOIN LATERAL
            generate_series(
                CAST(start_date AS DATE),
                CAST(end_date AS DATE),
                INTERVAL 1 DAY
            ) AS gs(event_date)

        )

        SELECT
            grid_id,
            grid_x,
            grid_y,
            event_date,

            1 AS flood,

            COUNT(*) AS n_location_records,

            COUNT(DISTINCT uuid)
                AS n_unique_records,

            MEDIAN(area_km2)
                AS median_location_area_km2,

            MAX(area_km2)
                AS max_location_area_km2,

            AVG(lon)
                AS mean_report_lon,

            AVG(lat)
                AS mean_report_lat

        FROM expanded

        GROUP BY
            grid_id,
            grid_x,
            grid_y,
            event_date

        ORDER BY
            grid_id,
            event_date

    )

    TO '{out}'
    (
        FORMAT PARQUET,
        COMPRESSION ZSTD
    );
    """
)


print(
    con.execute(
        f"""
        SELECT
            COUNT(*) AS positive_grid_days,
            COUNT(DISTINCT grid_id)
                AS positive_grids,
            MIN(event_date) AS first_date,
            MAX(event_date) AS last_date
        FROM read_parquet('{out}')
        """
    ).fetchdf()
)

con.close()

print("\nSaved:")
print(OUTFILE)