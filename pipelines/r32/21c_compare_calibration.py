"""Numerically compare calibrated GEE weather against original production lookup."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import numpy as np
import pandas as pd

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=Path(r"E:\Global_flooding"))
    p.add_argument("--calibration-csv", type=Path, required=True)
    p.add_argument("--abs-tol", type=float, default=0.01)
    p.add_argument("--rel-tol", type=float, default=0.005)
    a = p.parse_args()
    import duckdb
    script = Path(__file__).with_name("21_standard_weather_submit.py")
    spec = importlib.util.spec_from_file_location("legacy21", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    weather = mod.WEATHER_COLS
    new = pd.read_csv(a.calibration_csv, encoding="utf-8-sig")
    new.columns = [str(c).lstrip("\ufeff").strip() for c in new.columns]
    missing = {"climate_id", *weather} - set(new.columns)
    if missing:
        raise ValueError(f"Calibration columns missing: {sorted(missing)}")
    if len(new) < 10 or new.climate_id.duplicated().any():
        raise RuntimeError("Calibration needs >=10 unique IDs")
    source = a.base / "climate_global" / "climate_features_primary.parquet"
    if not source.is_file():
        raise FileNotFoundError(source)
    conn = duckdb.connect()
    conn.register("cal_keys", new[["climate_id"]])
    old = conn.execute("SELECT p.* FROM read_parquet(?) p JOIN cal_keys USING(climate_id)",
                       [str(source)]).fetchdf()
    conn.close()
    merged = new.merge(old, on="climate_id", validate="one_to_one", suffixes=("_new", "_old"))
    if len(merged) != len(new):
        raise RuntimeError("Some calibration IDs are absent from production lookup")
    stats = []
    for key in weather:
        x = pd.to_numeric(merged[key + "_new"], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(merged[key + "_old"], errors="coerce").to_numpy(dtype=float)
        both = np.isfinite(x) & np.isfinite(y)
        mismatched_missing = int((np.isfinite(x) != np.isfinite(y)).sum())
        mismatched = int((~np.isclose(x[both], y[both], atol=a.abs_tol, rtol=a.rel_tol)).sum())
        stats.append({"feature": key, "compared": int(both.sum()),
                      "missingness_mismatches": mismatched_missing,
                      "numeric_mismatches": mismatched,
                      "max_absolute_difference": float(np.max(abs(x[both] - y[both]))) if both.any() else None})
    passed = all(row["numeric_mismatches"] == row["missingness_mismatches"] == 0 for row in stats)
    output = a.base / "standard_calendar_20b" / "calibration_qc"
    output.mkdir(exist_ok=True, parents=True)
    pd.DataFrame(stats).to_csv(output / "21c_calibration_features.csv", index=False)
    status = {"status": "PASS" if passed else "FAIL", "sample_size": len(merged),
              "comparison": "new vs original production, native units",
              "abs_tolerance": a.abs_tol, "relative_tolerance": a.rel_tol}
    (output / "21c_calibration_status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8")
    print("[CALIBRATION]", status)
    if not passed:
        raise SystemExit("Calibration FAILED; do not submit production GEE tasks.")

if __name__ == "__main__":
    main()
