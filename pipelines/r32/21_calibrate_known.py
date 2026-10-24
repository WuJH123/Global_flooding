"""Submit a small extraction of known historical requests for numerical parity checking."""
from __future__ import annotations
import argparse
import importlib.util
from pathlib import Path

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", default="eco-sector-503721-b7")
    p.add_argument("--date", default="2020-07-01")
    p.add_argument("--rows", type=int, default=20)
    p.add_argument("--submit", action="store_true")
    p.add_argument("--base", type=Path, default=Path(__file__).resolve().parents[2])
    a = p.parse_args()
    if a.rows < 10 or a.rows > 100:
        raise ValueError("Calibration rows must be 10..100")
    original = Path(__file__).with_name("21_standard_weather_submit.py")
    if not original.is_file():
        raise FileNotFoundError(original)
    print(f"[CALIBRATION] {a.rows} known requests on {a.date} via {original}")
    if not a.submit:
        print("Preview only. Run again with --submit to create a small GEE export.")
        return
    import ee
    spec = importlib.util.spec_from_file_location("legacy_stage21", original)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    ee.Initialize(project=a.project)
    source = ee.FeatureCollection(f"projects/{a.project}/assets/climate_requests_2020")
    sample = source.filter(ee.Filter.eq("date_str", a.date)).limit(a.rows)
    count = int(sample.size().getInfo())
    if count < 10:
        raise RuntimeError(f"Insufficient known records for date {a.date}: {count}")
    process, _ = legacy.make_ee_functions()
    out = process(sample)
    task = ee.batch.Export.table.toDrive(
        collection=out, description="STD32_CALIBRATION_2020",
        folder="Groundsource_Standard_Climate_Calibration",
        fileNamePrefix="std32_calibration_2020",
        selectors=legacy.OUTPUT_COLS, fileFormat="CSV")
    task.start()
    print(f"[SUBMITTED] calibration rows={count} task_id={task.id}")
    print("Download CSV and run pipelines/r32/21c_compare_calibration.py.")

if __name__ == "__main__":
    main()
