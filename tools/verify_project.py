"""Read-only local audit of published workflow artifacts. No data recomputation."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DATASETS = [
    ("m3_catalogue", "processed_local/urban_catalogue_m3_pop100_share50.parquet", 1378124, ("grid_id", "event_date")),
    ("episodes", "episode_control/flood_episodes_m3.parquet", 650368, ("grid_id", "episode_start")),
    ("matched_case_control", "episode_control/matched_case_control_m3.parquet", 3250710, ("grid_id",)),
    ("climate_primary", "climate_global/climate_features_primary.parquet", 2789073, ("climate_id",)),
    ("primary_complete", "climate_local_first/primary_complete_strata.parquet", 3231350, ("grid_id",)),
    ("standard_calendar20b", "standard_calendar_20b/20_standard_daily_panel.parquet", 2680571, ("grid_id", "climate_id", "calendar_stratum_id", "episode_onset")),
    ("static_m3", "mechanism_project/05_static_merged/grid_static_context_m3.parquet", 26317, ("grid_id",)),
]

def specs():
    return [{"name": name, "path": rel, "expected_rows": expected, "key_fields": fields}
            for name, rel, expected, fields in DATASETS]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=Path(r"E:\Global_flooding"))
    ap.add_argument("--processed", type=Path,
                    default=Path(r"E:\数据集\Groundsource_全球城市内涝点位\processed"))
    ap.add_argument("--strict", action="store_true", help="Missing files are failures")
    ap.add_argument("--manifest-only", action="store_true", help="Print expectations without importing DuckDB")
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()
    if args.manifest_only:
        print(json.dumps(specs(), indent=2, ensure_ascii=False))
        return
    import duckdb
    con = duckdb.connect()
    results = []
    errors = []
    all_specs = specs() + [
        {"name": "source_gridday", "path": str(args.processed / "groundsource_gridday_20km.parquet"),
         "expected_rows": 2375933, "key_fields": ("grid_id", "event_date")},
    ]
    for spec in all_specs:
        path = Path(spec["path"])
        if not path.is_absolute():
            path = args.base / path
        rec = {"dataset": spec["name"], "path": str(path), "expected_rows": spec["expected_rows"]}
        if not path.is_file():
            rec["status"] = "MISSING"
            if args.strict:
                errors.append(f"{rec['dataset']}: missing file")
            results.append(rec)
            continue
        try:
            desc = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
            columns = {r[0] for r in desc}
            missing = set(spec["key_fields"]) - columns
            if missing:
                raise ValueError("missing required columns: " + ", ".join(sorted(missing)))
            num = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
            rec["actual_rows"] = num
            rec["status"] = "PASS" if num == spec["expected_rows"] else "COUNT_DIFF"
            if rec["status"] == "COUNT_DIFF":
                rec["note"] = "Expected count is a historical run snapshot; inspect provenance before accepting new count."
            unique_field = {
                "m3_catalogue": None,
                "episodes": None,
                "matched_case_control": None,
                "climate_primary": "climate_id",
                "primary_complete": None,
                "standard_calendar20b": "climate_id",
                "static_m3": "grid_id",
                "source_gridday": None,
            }.get(spec["name"])
            if unique_field:
                unique = con.execute(f'SELECT COUNT(DISTINCT "{unique_field}") FROM read_parquet(?)',
                                     [str(path)]).fetchone()[0]
                rec["unique_key_count"] = unique
                if unique != num:
                    rec["status"] = "ERROR"
                    errors.append(f"{rec['dataset']}: duplicate {unique_field} values")
            if spec["name"] == "standard_calendar20b":
                row = con.execute(
                    "SELECT SUM(episode_onset), SUM(CASE WHEN episode_onset=1 AND reported_flood_day=0 THEN 1 ELSE 0 END) "
                    "FROM read_parquet(?)", [str(path)]).fetchone()
                rec["episode_onsets"] = int(row[0])
                rec["onset_not_reported"] = int(row[1])
                if rec["onset_not_reported"]:
                    errors.append("standard_calendar20b: onset lacks a reported flood")
                    rec["status"] = "ERROR"
            if spec["name"] == "static_m3":
                cols = columns
                for col in ("city_id", "basin_slope_degrees"):
                    if col in cols:
                        rec[col + "_available"] = True
        except Exception as exc:
            rec["status"] = "ERROR"
            rec["error"] = str(exc)
            errors.append(f"{rec['dataset']}: {exc}")
        results.append(rec)
        print(f"[{rec['status']}] {rec['dataset']} {rec.get('actual_rows', '-')}", flush=True)
    con.close()
    output = args.output or args.base / "audits" / "project_qc" / "latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "base": str(args.base), "datasets": results, "errors": errors}
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[SAVED]", output)
    if errors:
        raise SystemExit("QC FAILED: " + "; ".join(errors))
    print("[QC] Completed. MISSING and COUNT_DIFF are not proof of current scientific validity.")

if __name__ == "__main__":
    main()
