# R34 Stage57–59: zero-GEE multiscale audit

Scientific restriction: these scripts do NOT reconstruct 5/10km flood labels. Scale selection remains undetermined without original event geometries and independent geolocation-quality evidence.

## 0. Sync main and dependencies

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R34="$BASE\pipelines\r34"
$R33="$BASE\pipelines\r33"
$BUILT="E:\数据集\全球人类居住层GHS built-up surface (R2023)\GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0\GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0.tif"
$PARITY="$BASE\mechanism_project\55_multiscale_environment\55_stage30c_built_parity.csv"
$FINE="$BASE\mechanism_project\54_fine5_environment\built_2020"
$COARSE="$BASE\mechanism_project\55_multiscale_environment"
$GPKG="E:\数据集\2024城市中心数据库GHS_UCDB_GLOBE_R2024A_V1_1\GHS_UCDB_GLOBE_R2024A.gpkg"
& $PY -m pip install pyogrio geopandas shapely pyproj
& $PY -m pytest -q tests/test_stage57_59_scale_audit.py
~~~

## 1. Native-source pixel check (Stage57)

Original 100m values are summed by source-pixel centre within each 20km target. It is an independent approximate diagnostic, NOT exact fractional polygon-pixel integration. All-touched provides a boundary sensitivity bracket.

~~~powershell
& $PY "$R34\57_native_source_audit.py" --parity "$PARITY" --built-tif "$BUILT" --out "$BASE\stage34_scale_audit\57_native" --max-rows 10
& $PY "$R34\57_native_source_audit.py" --parity "$PARITY" --built-tif "$BUILT" --out "$BASE\stage34_scale_audit\57_native" --random-n 200
~~~

Inspect 57_native_pixel_audit.csv and 57_native_qc.json. Any paper-grade claim of true exact area still needs full fractional source-pixel overlap.

## 2. Direct paired CI: built mean vs built mean plus heterogeneity

Stage34b ALREADY supports direct paired spatial-block heldout comparisons within the SAME fit, when both features are supplied. No separate confidence interval subtraction is valid.

~~~powershell
$CITY="$BASE\mechanism_project\56_built5_city_bridge\56_city_environment_with_built5.parquet"
$TYPES="$BASE\stage33_city_typology\33_city_environment_types.csv"
$OUT="$BASE\stage33_continuous_response\mean_plus_heterogeneity_paired"
& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --city-types "$TYPES" --city-environment "$CITY" --features ghsl_built_5km_mean_wmean,ghsl_built_5km_sd_wmean --validation spatial --bootstrap-reps 99 --max-strata 5000 --out "$BASE\stage33_continuous_response\mean_plus_heterogeneity_smoke"
& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --city-types "$TYPES" --city-environment "$CITY" --features ghsl_built_5km_mean_wmean,ghsl_built_5km_sd_wmean --validation all --bootstrap-reps 999 --out "$OUT"
Import-Csv "$OUT\34b_heldout_comparison.csv" | Where-Object { $_.baseline -eq "S_within20km_built_mean" -and $_.augmented -eq "E2_continuous_P24" } | Format-Table -AutoSize
~~~

The mean covariate here is the new Stage55/56 mean, NOT the old Stage30c value. Main comparison S_within20km_built_mean -> E2_continuous_P24 has a direct paired CI in the same run. Holdout result is still observational.

## 3. Stratified city sampling: climate and location quality

Create UTF-8 CSV at E:\Global_flooding\stage34_scale_audit\59_candidates.csv with fields:

~~~csv
city_id,climate_zone,location_quality
REAL_UCDB_ID_1,Af,precise
REAL_UCDB_ID_2,Cfa,approximate
REAL_UCDB_ID_3,BWh,coarse
~~~

These IDs are illustrative only. Populate actual UCDB stable IDs and Koppen from known sources. Location tier must derive from geolocation metadata or manual review, never from 20km positive labels. Valid tiers: precise, approximate, coarse, unreviewed. If unknown, use unreviewed, and no scale winner is allowed.

~~~powershell
$CAND="$BASE\stage34_scale_audit\59_candidates.csv"
$PILOT="$BASE\stage34_scale_audit\59_pilot"
& $PY "$R34\59_stratified_scale_decision.py" --candidate-cities "$CAND" --out "$PILOT" --n 60 --seed 2026
~~~

## 4. UCDB city polygon clipping of existing 5/10/20km environment grids

~~~powershell
& $PY "$R34\58_city_boundary_multiscale.py" --gpkg "$GPKG" --fine-root "$FINE" --coarse-root "$COARSE" --city-ids "$PILOT\59_balanced_city_pilot.csv" --out "$BASE\stage34_scale_audit\58_city_boundary" --max-cities 60
& $PY "$R34\59_stratified_scale_decision.py" --candidate-cities "$CAND" --boundary-parquet "$BASE\stage34_scale_audit\58_city_boundary\58_city_boundary_multiscale.parquet" --out "$PILOT" --n 60
~~~

This is exact city polygon x grid geometry intersection, but assumes constant built density within each cached 5km cell: NOT exact built area inside a city from original 100m data. If the city output suggests large scale effects, rerun subset with full source-pixel fractional polygon clipping.

## 5. Full scale response comparison is conditional, not yet finished

A genuine event-based scale comparison requires ORIGINAL Groundsource polygons, location quality, new event/reference calendar at each scale, IMERG/ERA5 support, same independent storm clusters, spatial/temporal splits, shifted grid origins and paired blocked uncertainty. Never clone existing 20km onset labels into 5km cells.

Stage59 accepts independent multi-scale heldout losses: CSV with matched_event_id,city_id,scale_km,climate_zone,location_quality,heldout_logloss. The existing outputs do NOT include this new file. Stage59 intentionally reports scale_winner null even if losses are supplied, pending paired storm-block intervals and MAUP checks.

Expected near-term conclusion is environmental-scale sensitivity only; do not claim 5km or 10km flood-response superiority without verified original event location and fresh fitted risk sets.
