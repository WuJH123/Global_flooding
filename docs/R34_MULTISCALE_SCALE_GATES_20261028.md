# R34 Stage57–60: main-only multiscale sensitivity and source verification

Scientific scope: **NO GEE Task**. Preserve old M3 20km event/reference
data, original 100m E2020 GHSL BUILT-S, corrected 5km additive cache and
UCDB city polygons. The experiment asks (a) which aggregation computes
BUILT-S correctly, (b) whether same-source mean built intensity and 5km
heterogeneity have independent heldout gains, and (c) how geocoded flood
polygons resolve on 5/10/20km grids across cities, Köppen zones, and
geocoding footprint-size groups. No script converts a 20km reported
positive into 16 independent 5km flood observations.

## 0. Direct sync main; no branch

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main

$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R33="$BASE\pipelines\r33"
$R34="$BASE\pipelines\r34"
$RAWBUILT="E:\数据集\全球人类居住层GHS built-up surface (R2023)\GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0\GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0.tif"
$UCDB="E:\数据集\2024城市中心数据库GHS_UCDB_GLOBE_R2024A_V1_1\GHS_UCDB_GLOBE_R2024A.gpkg"
$PARITY="$BASE\mechanism_project\55_multiscale_environment\55_stage30c_built_parity.csv"
$COARSE="$BASE\mechanism_project\55_multiscale_environment"
$LINKS="$BASE\mechanism_project\01_cities\m3\grid_city_crosswalk_all.csv"
$CITY="$BASE\mechanism_project\30_urban_context\30b_city_features_2020.parquet"
$BRIDGE="$BASE\mechanism_project\56_built5_city_bridge"
$TYPES="$BASE\stage33_city_typology\33_city_environment_types.csv"

& $PY -m pip install -U shapely pyproj geopandas pyogrio pyarrow rasterio pytest
& $PY -m pytest -q tests/test_stage57_59_scale_audits.py tests/test_stage60_scale_gate.py tests/test_stage56_city_bridge.py
~~~

## 1. Stage57: independent source-pixel audit (MUST finish first)

The reference is native 100m BUILT-S pixel-value area quadrature inside
each 20km target polygon; it does not call Stage30c/54 average reprojection.
4-points-per-source-pixel reduces boundary aliasing but is still NOT exact
partial-pixel vector integration. Audit all 325 >0.03 disagreements and
built-intensity-matched controls; do not cherry-pick three cells.

~~~powershell
& $PY "$R34\57_independent_built_area_audit.py" --parity "$PARITY" --raster "$RAWBUILT" --out "$BASE\audits\r34_source57_smoke" --controls 20 --limit 10 --subpoints 4

& $PY "$R34\57_independent_built_area_audit.py" --parity "$PARITY" --raster "$RAWBUILT" --out "$BASE\audits\r34_source57_full" --threshold 0.03 --controls 325 --subpoints 4
~~~

Read 57_source_audit_qc.json and 57_source_independent_per_grid.csv;
compare both mean/median errors and the fraction of outliers where
new-old source error is smaller. Investigate edge anomalies and any >5%
native-source coverage gaps. Inference is an *audit*, not proof of a new
hydrologic mechanism. Never overwrite original Stage30c/54 results.

## 2. Stage56: rebuild same-source city mean + SD

The existing Stage34b mean moderator was derived from **old** Stage30c.
Do not compare it directly with new Stage55 SD as the only independent
increment test. Stage56 now exports ghsl_built_5km_mean_wmean and
ghsl_built_5km_sd_wmean from the **same Stage55 20km table**.

~~~powershell
& $PY "$R34\56_bridge_fine5_city_response.py" --grid-20km "$COARSE\environment_grid_20km_2020.parquet" --city-crosswalk "$LINKS" --city-table "$CITY" --out-dir "$BRIDGE"
~~~

Both remain 20km-grid contextual measures weighted by UCDB city overlap.
Stage58 below tests the bias relative to actual UCDB city boundary clipping.

## 3. Stage34b: same run, same cases, direct paired mean -> mean+SD

IMPORTANT: run both moderators in **ONE** invocation, not three separate
runs with different complete-case selections/bootstraps. The modified
Stage34b emits direct pairs S_within20km_built_mean -> E2_continuous_P24
and S_within20km_built_heterogeneity -> E2_continuous_P24. Paired
spatial-block heldout CI and sign are directly computed. Verify the
number of test strata is identical in those rows.

~~~powershell
& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --city-types "$TYPES" --city-environment "$BRIDGE\56_city_environment_with_built5.parquet" --features ghsl_built_5km_mean_wmean,ghsl_built_5km_sd_wmean --validation spatial --bootstrap-reps 99 --max-strata 5000 --out "$BASE\stage33_continuous_response\r34_same_source_smoke"

& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --city-types "$TYPES" --city-environment "$BRIDGE\56_city_environment_with_built5.parquet" --features ghsl_built_5km_mean_wmean,ghsl_built_5km_sd_wmean --validation all --bootstrap-reps 999 --out "$BASE\stage33_continuous_response\r34_same_source_paired_full"
~~~

Review 34b_heldout_comparison.csv rows where baseline is
S_within20km_built_mean, augmented E2_continuous_P24. Positive gain with
both bounds above zero for unseen cities/spatial blocks is promising but
still observational, not causal or independent-storm verified.

## 4. Stage59: original event *geocoding* footprint across three scales

The pre-existing 20km event climate parquet does NOT contain the original
Groundsource polygon geometry. Find the true raw Groundsource GeoParquet:

~~~powershell
Get-ChildItem "E:\数据集\Groundsource_全球城市内涝点位" -Recurse -File -Include *.parquet | Select-Object FullName,Length
~~~

Set the actual source path after checking it contains uuid, geometry and
its documented source CRS (do not guess from arbitrary episode files):

~~~powershell
$EVENTS="REPLACE_WITH_VERIFIED_GROUNDSOURCE_RAW_PARQUET_PATH"
$ZONES="$BASE\results_31\reference_integrated\31b_global_urban_reporting_with_climate.csv"
$PILOT="$BASE\audits\r34_scale59"
Test-Path $EVENTS
Test-Path $ZONES

& $PY "$R34\59_multiscale_location_pilot.py" --events "$EVENTS" --ucdb "$UCDB" --crosswalk "$LINKS" --zone-grids "$ZONES" --event-crs EPSG:4326 --geom-field geometry --id-col uuid --target-cities 60 --candidate-per-zone 130 --min-events 10 --max-events-per-city 50 --out "$PILOT"
~~~

The zone table uses published 1991–2020 Köppen categories joined via a
previously prepared **E2025** urban reporting domain. This is a pilot
stratification source, NOT the frozen E2020 full urban denominator.
Check missing climate zones; do not extrapolate unsampled zones.

Stage59 outputs:
- 59_pilot_cities.csv: audited sampling frame, Köppen zone, dominant
  geocoding polygon area class, n_events;
- 59_geocoding_scale_sensitivity.parquet: ORIGINAL source event polygons
  evaluated at 5/10/20km and 0/half-grid origin shifts;
- 59_scale_stratified_summary.csv and 59_pilot_balance.csv;
- 59_scale_qc.json: explicit refusal to infer a 5/10km response winner.

Geocoding polygon area is NOT an independently hand-checked coordinate
accuracy score, nor actual inundation footprint. Do not claim "exact"
geolocation from the proxy or assign event positive labels to touched cells.

## 5. Stage58: within-city boundary from original 100m BUILT-S

~~~powershell
& $PY "$R34\58_city_boundary_fine5_audit.py" --pilot-cities "$PILOT\59_pilot_cities.csv" --ucdb "$UCDB" --built-tif "$RAWBUILT" --city-proxy "$BRIDGE\56_built5_city_modifiers.csv" --out "$BASE\audits\r34_city58_smoke" --limit 5

& $PY "$R34\58_city_boundary_fine5_audit.py" --pilot-cities "$PILOT\59_pilot_cities.csv" --ucdb "$UCDB" --built-tif "$RAWBUILT" --city-proxy "$BRIDGE\56_built5_city_modifiers.csv" --out "$BASE\audits\r34_city58_full"
~~~

Some enormous UCDB cities may be SKIP_MAX_PIXELS under a 3m pixel cap;
do not silently drop. Review geographic/climate/precision bias among
excluded cities. The 100m centre-in-city integration is approximate
along UCDB polygon edges; it does NOT measure actual drainage capacity.

## 6. Stage60: evidence-scoped scale decision

~~~powershell
& $PY "$R34\60_scale_decision_gate.py" --location "$PILOT\59_geocoding_scale_sensitivity.parquet" --pilot-cities "$PILOT\59_pilot_cities.csv" --boundary "$BASE\audits\r34_city58_full\58_city_boundary_comparison.csv" --source-qc "$BASE\audits\r34_source57_full\57_source_audit_qc.json" --out "$BASE\audits\r34_scale60"
~~~

Its verdict is intentionally two-tier:
- 5km = usable environmental detail cache if source audit passes;
- 20km = current validated event/reference climate-response baseline;
- 10km = candidate for later same-event geolocation and source-weather
  pilot, **not** a proven superior flood-response scale.

## Required before formally choosing between 5/10/20km response models

1. original source polygons and a documented manual localisation-quality
   stratification, not just area-based precision proxy;
2. meteorological exposures computed from ORIGINAL IMERG/ERA5 time series
   at each target support (not interpolated 20km maxima), where feasible;
3. event-onset and same-month same-weekday referents rebuilt at each
   scale while preserving event/storm IDs and single-episode dependence;
4. *same* high-quality events, cities, time windows and heldout storm/
   spatial/city folds for every resolution;
5. paired out-of-sample loss CI, origin shifts and effect-size intervals;
6. reporting-bias and UCDB city-boundary reliability audit.

A flood paper should NOT pick 5km based only on more cell counts, better
map aesthetics, or higher apparent model gain after duplicating 20km labels.
No additional GEE tasks are started by Stages57–60.

Source methods: Roberts et al. (2017), Ecography 40:913–929, doi:
10.1111/ecog.02881; GDAL weighted average reprojection documentation.
