# GF10 frozen 10km production, Results 3.1–3.5

This pipeline is ALREADY located in pipelines/r35/62–71. The changes here are Stage65 weather-completeness enforcement and Stage72 absolute frozen-grid area/ID integrity check. No new GEE test tasks. Commands with --submit create PRODUCTION tasks and must not be run twice.

Global lattice: EPSG:6933; 10000 m; [-17360000,-7320000,17360000,7320000]; affine [10000,0,-17360000,0,-10000,7320000]. Every complete projected cell has exactly 100,000,000 m²; urban/valid/city intersection area is smaller and must be retained as a distinct denominator. Projection area-of-use excludes 86+ degrees; source and domain coverage must be reported.

## 0. Local

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R35="$BASE\pipelines\r35"
$P="eco-sector-503721-b7"
& $PY -m pytest -q tests/test_stage62_68_grid10.py tests/test_stage65b_weather_merge_grid10.py tests/test_stage72_grid10_integrity.py
~~~

## 1. Global E2020 SMOD 10 km, two PRODUCTION GEE tasks

~~~powershell
& $PY "$R35\62_gee_freeze_grid10.py" --project "$P" --out "$BASE\grid10\contracts"
& $PY "$R35\62_gee_freeze_grid10.py" --project "$P" --out "$BASE\grid10\contracts" --submit
~~~

Download completed GF10_SMOD2020_V1_S00 and S01 TIFFs to grid10/gee_tifs. Then:

~~~powershell
$TIFF="$BASE\grid10\gee_tifs"
$GRID="$BASE\grid10\universe\grid10_urban_2020.parquet"
& $PY "$R35\62b_freeze_grid10_local.py" --west "$TIFF\gf10_smod2020_v1_s00.tif" --east "$TIFF\gf10_smod2020_v1_s01.tif" --out "$BASE\grid10\universe"
if($LASTEXITCODE -ne 0){throw "STOP frozen grid"}
& $PY "$R35\72_grid10_integrity_contract.py" --grid "$GRID" --out "$BASE\grid10\qc"
if($LASTEXITCODE -ne 0){throw "STOP geodesic area contract"}
~~~

## 2. UCDB city + ORIGINAL Groundsource event geometries, all local

~~~powershell
$UCDB="E:\数据集\2024城市中心数据库GHS_UCDB_GLOBE_R2024A_V1_1\GHS_UCDB_GLOBE_R2024A.gpkg"
& $PY "$R35\62c_ucdb_city_crosswalk10.py" --grid "$GRID" --ucdb-gpkg "$UCDB" --out "$BASE\grid10\cities"
Get-ChildItem "E:\数据集\Groundsource_全球城市内涝点位\processed" -Recurse -File -Filter "*.parquet" | Select-Object FullName
~~~

Set variable EVENTS to VERIFIED original events parquet with uuid, geometry, start_date, end_date; no example path is guaranteed to exist.

~~~powershell
& $PY "$R35\63_event_geometry_grid10.py" --events "$EVENTS" --grid "$GRID" --out "$BASE\grid10\events"
& $PY "$R35\63b_standard_calendar10.py" --links-root "$BASE\grid10\events" --out "$BASE\grid10\calendar" --analysis-end 2025-12-31
& $PY "$R35\65a_make_event_weather_requests.py" --grid "$GRID" --calendar "$BASE\grid10\calendar\63b_calendar_grid10.parquet" --out "$BASE\grid10\weather_requests"
~~~

The original 20 km count is not a validation target for newly geolocated 10 km event onset. Administrative news polygons are NOT flood-inundation polygons.

## 3. Historical GHSL, 10 GEE PRODUCTION image exports

~~~powershell
& $PY "$R35\64_gee_context10_exports.py" --source ghsl --epochs 2000,2005,2010,2015,2020 --out "$BASE\grid10\gee_jobs" --submit
~~~

After BOTH stripe TIFFs of an epoch have COMPLETED and downloaded:

~~~powershell
& $PY "$R35\64b_grid10_static_dynamic_join.py" --source ghsl --label E2020 --grid "$GRID" --west "$TIFF\ghs10_v1_e2020_s00.tif" --east "$TIFF\ghs10_v1_e2020_s01.tif" --out "$BASE\grid10\context"
& $PY "$R35\72_grid10_integrity_contract.py" --grid "$GRID" --tables "$BASE\grid10\context\64b_ghsl_E2020_grid10.parquet" --out "$BASE\grid10\qc"
~~~

Population_count is number of people per source 100m pixel: SUM before 10km density. BUILT-S source is m² per 100m pixel: SUM, NOT original-pixel average as percent. 2025 GHSL is extrapolated, not historical proof.

## 4. Event/referent GEE production, upload request CSV assets first

Upload the actual yearly requests_YYYY.csv to GEE TABLE projects/eco-sector-503721-b7/assets/gf10_requests_YYYY with intact event_date ISO strings and integer grid_x/y.

~~~powershell
$YEAR=2020
$start=[datetime]::new($YEAR,1,1)
$finish=[datetime]::new($YEAR+1,1,1)
while($start -lt $finish){
  $days=[Math]::Min(30,($finish-$start).Days)
  & $PY "$R35\65_gee_event_weather10.py" --project "$P" --asset "projects/$P/assets/gf10_requests_$YEAR" --start $start.ToString("yyyy-MM-dd") --days $days --out "$BASE\grid10\gee_jobs" --submit
  if($LASTEXITCODE -ne 0){throw "STOP task submission failure"}
  $start=$start.AddDays($days)
}
~~~

Do not rerun blocks already submitted. Repeat only dates that occur in the verified request manifest for years 2000–2025. Stage65 enforces complete P24/P3h/API7; no null-to-zero.

~~~powershell
& $PY "$R35\65b_merge_weather10.py" --calendar "$BASE\grid10\calendar\63b_calendar_grid10.parquet" --requests "$BASE\grid10\weather_requests\65a_unique_requests_grid10.parquet" --csv-root "$BASE\grid10\weather_csv" --out "$BASE\grid10\climate_panel"
& $PY "$R35\66_shared_response10.py" --panel "$BASE\grid10\climate_panel\65b_standard_daily_grid10.parquet" --context "$BASE\grid10\context\64b_ghsl_E2020_grid10.parquet" --out "$BASE\grid10\results32"
& $PY "$R35\68_city_fingerprints10.py" --panel "$BASE\grid10\climate_panel\65b_standard_daily_grid10.parquet" --dominant-city "$BASE\grid10\cities\62c_dominant_city_grid10.parquet" --out "$BASE\grid10\results33"
~~~

## 5. Annual ERA5/IMERG and CMIP6

These are FULL production exports, two stripes for each period, not smoke tasks. Prioritize 2020–2022 production and inspect actual completion before submitting 52 years of expensive global reductions.

~~~powershell
& $PY "$R35\64_gee_context10_exports.py" --source era5 --years 2020,2021,2022 --out "$BASE\grid10\gee_jobs" --submit
& $PY "$R35\64_gee_context10_exports.py" --source imerg --years 2020,2021,2022 --out "$BASE\grid10\gee_jobs" --submit
~~~

For complete 3.4 historical early vs late periods, extend to all years 2000–2025, download and use Stage64b to create per-year Parquet; Stage67 demands at least 8 years in each reference period. Stage71 requires same GCM 1995–2014 historical and future SSP245/585 outputs for at least three independent GCMs. Outputs are hazardous weather change, NOT anthropogenic attribution or future absolute flood probabilities.

~~~powershell
$K="$BASE\koppen_geiger_tif\1991_2020\koppen_geiger_0p1.tif"
& $PY "$R35\70_observation_framework10.py" --grid "$GRID" --links-root "$BASE\grid10\events" --population-2020 "$BASE\grid10\context\64b_ghsl_E2020_grid10.parquet" --koppen "$K" --out "$BASE\grid10\results31"
& $PY "$R35\67_hazard_exposure10.py" --annual-root "$BASE\grid10\context" --population-2020 "$BASE\grid10\context\64b_ghsl_E2020_grid10.parquet" --out "$BASE\grid10\results34" --min-years 8
~~~

Remaining research gates: original geocode precision classification, independent storms, reporting-bias covariates, city transfer validation, time-varying urban cohorts, CMIP6 model availability and SSP population. No claim of completing them merely because scripts exist. All GF10 outputs go under E:\Global_flooding\grid10 without overwriting M3.
