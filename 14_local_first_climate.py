#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Groundsource climate: no-GEE complete-case analysis, with optional
same-day nearby-grid ERA5 proxy (sensitivity only).

Windows:
 E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\14_local_first_climate.py
 E:\\Global_flooding\\.venv\\Scripts\\python.exe E:\\Global_flooding\\14_local_first_climate.py --neighbor --max-donor-km 35
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(r"E:\Global_flooding")
PRIMARY = ROOT / "climate_global" / "climate_features_primary.parquet"
MATCHED = ROOT / "episode_control" / "matched_case_control_m3.parquet"
OUT = ROOT / "climate_local_first"
EXPECTED_CLIMATE = 2_789_073
EXPECTED_MATCHED = 3_250_710
EXPECTED_STRATA = 650_142


def qp(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def parquet_export(con, statement: str, filename: str) -> Path:
    path = OUT / filename
    if path.exists():
        path.unlink()
    con.execute(f"COPY ({statement}) TO '{qp(path)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"[SAVED] {path}")
    return path


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neighbor", action="store_true", help="Build nearest available same-day ERA5 proxy (sensitivity only)")
    ap.add_argument("--max-donor-km", type=float, default=35.0)
    args = ap.parse_args()
    if args.max_donor_km <= 0 or args.max_donor_km > 100:
        raise ValueError("--max-donor-km must be between 0 and 100")
    for p in (PRIMARY, MATCHED):
        if not p.exists():
            raise FileNotFoundError(p)
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(OUT / "local_first_work.duckdb"))
    con.execute(f"PRAGMA threads={min(os.cpu_count() or 4, 8)}")
    src, mat = qp(PRIMARY), qp(MATCHED)

    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()]
    era = [c for c in cols if c.startswith("era5_")]
    imerg = [c for c in cols if c.startswith("imerg_")]
    needed = {"climate_id", "grid_id", "grid_x", "grid_y", "date_str"}
    if not needed.issubset(cols) or len(era) != 22 or len(imerg) != 12:
        raise RuntimeError(f"Climate schema unexpected: {len(era)} ERA, {len(imerg)} IMERG; missing {needed - set(cols)}")
    era_ok = " AND ".join(f"p.{c} IS NOT NULL" for c in era)
    era_ok_d = " AND ".join(f"d.{c} IS NOT NULL" for c in era)
    imerg_ok = " AND ".join(f"p.{c} IS NOT NULL" for c in imerg)

    stats = con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT climate_id),
               COUNT(*) FILTER (WHERE NOT ({era_ok})),
               COUNT(*) FILTER (WHERE NOT ({imerg_ok}))
        FROM read_parquet('{src}') p
    """).fetchone()
    print("[PRIMARY] rows, unique, ERA incomplete, IMERG incomplete:", stats)
    if stats[0] != EXPECTED_CLIMATE or stats[1] != EXPECTED_CLIMATE or stats[3] != 0:
        raise RuntimeError("Primary climate data structural QC failed; stop")

    select_features = ",\n            ".join(f"c.{c}" for c in (imerg + era))
    join_key = "m.grid_id || '__' || STRFTIME(CAST(m.sample_date AS DATE), '%Y%m%d')"

    def join_output(climate_path: Path, basename: str):
        cp = qp(climate_path)
        sql = f"""
          SELECT m.*, c.climate_id,
                 {select_features},
                 c.era5_source_method, c.era5_fallback_level,
                 c.era5_donor_grid_id, c.era5_donor_distance_km
          FROM read_parquet('{mat}') m
          LEFT JOIN read_parquet('{cp}') c ON {join_key}=c.climate_id
        """
        path = parquet_export(con, sql, basename + "_all_strata.parquet")
        cstat = con.execute(f"""SELECT COUNT(*), COUNT(DISTINCT stratum_id), SUM(is_case),
          COUNT(*) FILTER (WHERE climate_id IS NULL),
          COUNT(*) FILTER (WHERE era5_fallback_level=9)
          FROM read_parquet('{qp(path)}')""").fetchone()
        print("[JOIN] rows, strata, cases, unmatched climate IDs, ERA unresolved:", cstat)
        if cstat[0] != EXPECTED_MATCHED or cstat[1] != EXPECTED_STRATA or cstat[2] != EXPECTED_STRATA or cstat[3]:
            raise RuntimeError("Matched data integrity failed")

        all_fields_not_null = " AND ".join(f"{c} IS NOT NULL" for c in (imerg + era))
        strict_sql = f"""
          WITH good AS (
            SELECT stratum_id
            FROM read_parquet('{qp(path)}')
            GROUP BY stratum_id
            HAVING COUNT(*)=5 AND SUM(is_case)=1 AND
              COUNT(*) FILTER (WHERE NOT ({all_fields_not_null}))=0
          )
          SELECT a.* FROM read_parquet('{qp(path)}') a
          INNER JOIN good USING (stratum_id)
        """
        strict_file = parquet_export(con, strict_sql, basename + "_complete_strata.parquet")
        strict_stats = con.execute(f"""SELECT COUNT(*), COUNT(DISTINCT stratum_id), SUM(is_case)
         FROM read_parquet('{qp(strict_file)}')""").fetchone()
        print("[COMPLETE STRATA] rows, strata, cases:", strict_stats)
        return dict(source=basename, all_rows=cstat[0], all_strata=cstat[1],
                    missing_era_matched_rows=cstat[4], complete_rows=strict_stats[0],
                    complete_strata=strict_stats[1], retained_strata_fraction=strict_stats[1]/EXPECTED_STRATA)

    base_sql = f"""
       SELECT p.*, CASE WHEN ({era_ok}) THEN 0 ELSE 9 END AS era5_fallback_level,
          CASE WHEN ({era_ok}) THEN 'primary' ELSE 'unresolved' END AS era5_source_method,
          CAST(NULL AS VARCHAR) AS era5_donor_grid_id,
          CAST(NULL AS DOUBLE) AS era5_donor_distance_km
       FROM read_parquet('{src}') p
    """
    base_path = parquet_export(con, base_sql, "climate_primary_tagged.parquet")
    reports = [join_output(base_path, "primary")]

    if args.neighbor:
        try:
            import numpy as np
            from pyproj import Geod, Transformer
            from scipy.spatial import cKDTree
        except ImportError as e:
            raise RuntimeError("Install scipy pyproj numpy for --neighbor") from e

        target = con.execute(f"""SELECT DISTINCT grid_id, grid_x, grid_y
          FROM read_parquet('{src}') p WHERE NOT ({era_ok})""").fetchdf()
        donors = con.execute(f"""SELECT DISTINCT grid_id, grid_x, grid_y
          FROM read_parquet('{src}') p WHERE ({era_ok})""").fetchdf()
        if donors.empty:
            raise RuntimeError("No available donors")
        to_ll = Transformer.from_crs("EPSG:6933", "EPSG:4326", always_xy=True)
        geod = Geod(ellps="WGS84")

        def lonlat(df):
            xs = (df.grid_x.to_numpy(dtype=float) + 0.5) * 20000.0
            ys = (df.grid_y.to_numpy(dtype=float) + 0.5) * 20000.0
            return to_ll.transform(xs, ys)

        tlon, tlat = lonlat(target)
        dlon, dlat = lonlat(donors)
        def xyz(lon, lat):
            lo = np.deg2rad(lon)
            la = np.deg2rad(lat)
            return np.stack([np.cos(la)*np.cos(lo), np.cos(la)*np.sin(lo), np.sin(la)], axis=1)
        tree = cKDTree(xyz(dlon, dlat))
        # Chord length for a spherical search; exact ellipsoidal distance is checked below.
        radius = 2.0 * np.sin((args.max_donor_km + 1.0) / 6371.0 / 2.0)
        nearby = tree.query_ball_point(xyz(tlon, tlat), r=radius)
        pairs = []
        for ti, hits in enumerate(nearby):
            for j in hits:
                if target.grid_id.iloc[ti] == donors.grid_id.iloc[j]:
                    continue
                _, _, dist_m = geod.inv(float(tlon[ti]), float(tlat[ti]), float(dlon[j]), float(dlat[j]))
                km = abs(dist_m) / 1000.0
                if km <= args.max_donor_km:
                    pairs.append((str(target.grid_id.iloc[ti]), str(donors.grid_id.iloc[j]), float(km)))
        print(f"[DONORS] missing grids: {len(target)}, static neighbor pairs <= {args.max_donor_km} km: {len(pairs)}")
        pairs_df = pd.DataFrame(pairs, columns=["missing_grid_id", "donor_grid_id", "distance_km"])
        pairs_df.to_csv(OUT / "donor_spatial_pairs.csv", index=False, encoding="utf-8-sig")
        if pairs_df.empty:
            print("[DONORS] No nearby donors found; retaining complete-case analysis only")
        else:
            con.register("neighbor_pairs_input", pairs_df)
            con.execute("CREATE OR REPLACE TABLE neighbor_pairs AS SELECT * FROM neighbor_pairs_input")
            donor_fields = ",\n                ".join(f"d.{c} AS {c}" for c in era)
            con.execute(f"""
             CREATE OR REPLACE TABLE selected_donors AS
             SELECT t.climate_id, x.donor_grid_id, x.distance_km,
                    {donor_fields}
             FROM read_parquet('{src}') t
             INNER JOIN neighbor_pairs x ON x.missing_grid_id=t.grid_id
             INNER JOIN read_parquet('{src}') d
                 ON d.grid_id=x.donor_grid_id AND d.date_str=t.date_str
             WHERE NOT ({era_ok.replace('p.', 't.')}) AND ({era_ok_d})
             QUALIFY ROW_NUMBER() OVER(PARTITION BY t.climate_id
                  ORDER BY x.distance_km, x.donor_grid_id)=1
            """)
            chosen = con.execute("SELECT COUNT(*), COUNT(DISTINCT climate_id), MAX(distance_km) FROM selected_donors").fetchone()
            print("[DONORS] resolved same-date requests, unique, max distance:", chosen)
            con.execute(f"""COPY (SELECT climate_id, donor_grid_id, distance_km
                FROM selected_donors) TO '{qp(OUT / 'neighbor_donor_decisions.csv')}'
                (HEADER, DELIMITER ',')""")
            replace_sql = ",\n            ".join(f"COALESCE(p.{c}, d.{c}) AS {c}" for c in era)
            near_sql = f"""
             SELECT p.* REPLACE ({replace_sql}),
                CASE WHEN ({era_ok}) THEN 0
                     WHEN d.climate_id IS NOT NULL THEN 3 ELSE 9 END AS era5_fallback_level,
                CASE WHEN ({era_ok}) THEN 'primary'
                     WHEN d.climate_id IS NOT NULL THEN 'neighbor_same_date'
                     ELSE 'unresolved' END AS era5_source_method,
                d.donor_grid_id AS era5_donor_grid_id,
                d.distance_km AS era5_donor_distance_km
             FROM read_parquet('{src}') p
             LEFT JOIN selected_donors d USING (climate_id)
            """
            near_path = parquet_export(con, near_sql, "climate_neighbor_candidate.parquet")
            reports.append(join_output(near_path, "neighbor_candidate"))

    pd.DataFrame(reports).to_csv(OUT / "local_first_summary.csv", index=False, encoding="utf-8-sig")
    print("\n[SUMMARY]\n", pd.DataFrame(reports).to_string(index=False))
    print("CAUTION: neighbor_candidate is a spatial proxy and sensitivity analysis, NOT observed ERA5 for the target grid.")
    con.execute("CHECKPOINT")
    con.close()


if __name__ == "__main__":
    run()
