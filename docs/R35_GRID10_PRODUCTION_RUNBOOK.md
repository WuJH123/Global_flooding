# GF10 v1: globally fixed 10km EASE-grid urban flood project

**Do not delete the earlier M3/20km work.** This is a NEW 10km primary
data-contract version, with no 20km target values used to create 10km data.
No mock flood polygons, no flood probabilities, no unverified negative days.
GEE scripts have NO TEST export flag: `--submit` starts production directly.
All dependent stages must respect the printed QC gate. This repository code
alone cannot submit to a private GEE account; use authenticated PowerShell.

## 0. Freeze spatial metadata

EPSG:6933, 10,000m, affine [10000,0,-17360000,0,-10000,7320000].
Each full planar cell exactly 1e8m2 = 100km2. Ellipsoidal WGS84
geodesic area sampled on densified transformed boundaries must agree
within 0.5%; source/city/land *effective* covered areas are separate.
Valid latitude domain approximately 86S–86N, conservative East/West
edge slivers excluded. This is a FIXED E2020 urban denominator, NOT a
global land/flood denominator; 2000–2020 urban evolution is assessed
within fixed reference territory plus a separately documented entrants
cohort, not inferred from the 2020 domain only.

```powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R35="$BASE\pipelines\r35"
$PROJECT="eco-sector-503721-b7"
& $PY -m pip install -U rasterio numpy pandas pyarrow duckdb scipy shapely pyproj geopandas pyogrio earthengine-api pytest
& $PY -m pytest -q tests/test_stage62_68_grid10.py
```

## 1. GEE production urban frame, only two full-size image Tasks

Two contiguous stripes preserve same global transform. The public GHSL
2020 1km SMOD supports codes 21,22,23,30. Do not use 2025 raster or
the older Stage45 20km TIFF as the 10km universe.

```powershell
& $PY "$R35\62_gee_freeze_grid10.py" --project "$PROJECT" --out "$BASE\grid10\contracts"
& $PY "$R35\62_gee_freeze_grid10.py" --project "$PROJECT" --out "$BASE\grid10\contracts" --submit
```

Wait for these TWO GEE jobs to complete (no cloud TEST jobs):
GF10_SMOD2020_V1_S00 and GF10_SMOD2020_V1_S01.
Download exact `gf10_smod2020_v1_s00.tif` and
`gf10_smod2020_v1_s01.tif` into E:\Global_flooding\grid10\gee_tifs.

```powershell
$TIFS="$BASE\grid10\gee_tifs"
$GRID="$BASE\grid10\universe\grid10_urban_2020.parquet"
& $PY "$R35\62b_freeze_grid10_local.py" --west "$TIFS\gf10_smod2020_v1_s00.tif" --east "$TIFS\gf10_smod2020_v1_s01.tif" --out "$BASE\grid10\universe" --urban-min 0.001 --valid-min 0.95
Get-Content "$BASE\grid10\universe\62b_grid10_qc.json"
```

Grid10 is not valid until this QC, true ellipsoid area samples and
stripe unique-key/affine tests pass. All other data ONLY join by grid_id.

## 2. City association (Results 3.1 and 3.3), locally

```powershell
$UCDB="E:\数据集\2024城市中心数据库GHS_UCDB_GLOBE_R2024A_V1_1\GHS_UCDB_GLOBE_R2024A.gpkg"
& $PY "$R35\62c_ucdb_city_crosswalk10.py" --grid "$GRID" --ucdb-gpkg "$UCDB" --out "$BASE\grid10\cities"
```

Retain ALL overlap links and dominant city for analyses that require a
single city, record city coverage separately. City ID from official UCDB,
not generated row number. City polygons are not flood inundation polygons.

## 3. R3.1 original event geometry and referents: local ONLY

Find actual original Groundsource parquet containing uuid, geometry,
start_date and end_date. NEVER use 20km matched_case_control to generate
10km onset labels. If geometry is a vague news place footprint, DO NOT
call it a flood footprint or verified location precision.

```powershell
$EVENTS="E:\数据集\Groundsource_全球城市内涝点位\processed\groundsource_original_events.parquet"
Test-Path $EVENTS
& $PY "$R35\63_event_geometry_grid10.py" --events "$EVENTS" --grid "$GRID" --out "$BASE\grid10\events" --batch-size 10000 --max-candidate-cells 64
& $PY "$R35\63b_standard_calendar10.py" --links-root "$BASE\grid10\events" --out "$BASE\grid10\calendar" --analysis-end 2025-12-31
```

**IMPORTANT**: $EVENTS is a required example filename; inspect actual
repository/local path and replace it. Do not fabricate this file or
claim it exists. Current strict cohort labels only geocoded PRIMARY
high-specificity geometries (<=4 cells with largest overlap >=50%).
It excludes ambiguous polygons and flags sample bias. All same-weekday
within same month/year dates are the referent pool; unreported != verified
non-flood. No news overcount as independent physical storms.

## 4. GEE GHSL static/dynamic built and exposure at locked 10km

Official images are native 100m per-pixel population COUNT and BUILT-S
AREA in m2. Task sums retain these units; do not average raw population
count then call it inhabitants. CSV/Parquet conversion only after BOTH
stripe TIFFs from same epoch are complete.

```powershell
& $PY "$R35\64_gee_context10_exports.py" --source ghsl --epochs 2000,2005,2010,2015,2020 --out "$BASE\grid10\gee_jobs"
& $PY "$R35\64_gee_context10_exports.py" --source ghsl --epochs 2000,2005,2010,2015,2020 --out "$BASE\grid10\gee_jobs" --submit
```

10 tasks (2 stripes x 5 historical GHSL epochs). Save GeoTIFFs in
E:\Global_flooding\grid10\gee_tifs. To import epoch 2020:

```powershell
& $PY "$R35\64b_grid10_static_dynamic_join.py" --source ghsl --label E2020 --grid "$GRID" --west "$TIFS\ghs10_v1_e2020_s00.tif" --east "$TIFS\ghs10_v1_e2020_s01.tif" --out "$BASE\grid10\context"
```

Repeat --label E2000/E2005/E2010/E2015 for historical urban and population
transition; do not use 2025 projections as 2020 observed values.
Model 2000–2025 conditions must use an appropriate preceding/historical
GHSL epoch, not backfill future construction into earlier events.

### Local rivers and roads (not observed storm-drain capacity)

Existing GRIP4 and HydroRIVERS *vector* paths must be discovered from
the local directory (not inferred):
```powershell
Get-ChildItem "$BASE\external_data\Roads\GRIP4" -Recurse -File -Filter *.shp | Select-Object -First 20 FullName
Get-ChildItem "$BASE\external_data\HydroRIVERS" -Recurse -File -Filter *.shp | Select-Object -First 20 FullName
$ROADS="REPLACE_WITH_REAL_GRIP4_LINE_SHP"
$RIVERS="REPLACE_WITH_REAL_HYDRORIVERS_LINE_SHP"
& $PY "$R35\69_local_network_context10.py" --grid "$GRID" --source "$ROADS" --kind roads --out "$BASE\grid10\context"
& $PY "$R35\69_local_network_context10.py" --grid "$GRID" --source "$RIVERS" --kind rivers --out "$BASE\grid10\context"
```
These paths are placeholders; run Get-ChildItem first. Road length/km2 is
NOT the capacity of underground stormwater drainage. HAND extraction,
local topography and basin attribute overlap still require source-specific
QC and should not be guessed from MERIT `hnd` elevation.

## 5. R3.2 event-day climate and model, 10km native sampled weather

Generate only the grid/day dates ACTUALLY required by the new fixed
calendar, not all urban x 26 years x 365 days.

```powershell
& $PY "$R35\65a_make_event_weather_requests.py" --grid "$GRID" --calendar "$BASE\grid10\calendar\63b_calendar_grid10.parquet" --out "$BASE\grid10\weather_requests"
```

Upload yearly `requests_YYYY.csv` files into EE **TABLE** Assets,
preserving numeric `grid_x,grid_y`, ISO string `event_date`,
`grid_id` and `climate_id`, using IDs:
`projects/eco-sector-503721-b7/assets/gf10_requests_YYYY`.
GEE Assets > New > Table Upload, or Google Cloud/EE CLI if configured.
No upload has been performed by the local code.

Each Stage65 production export covers 1..90 UTC calendar days. It
calculates 48 halfhour IMERG-depth steps per UTC day, moving 6-step
3-hour maximum, ERA5 previous 7 *complete* days, previous-day soil
moisture and same-day temperature. 30-day tasks are safest in a
noncommercial restricted project; 60/90-day is possible but more
timeout-prone. NO test Task is submitted.

```powershell
& $PY "$R35\65_gee_event_weather10.py" --asset "projects/$PROJECT/assets/gf10_requests_2020" --start 2020-01-01 --days 30 --out "$BASE\grid10\gee_jobs" --submit
```

Generate remaining month/year boundaries in PowerShell:
```powershell
$YEAR=2020
$start=[datetime]::new($YEAR,1,1)
$finish=[datetime]::new($YEAR+1,1,1)
while($start -lt $finish){
  $days=[Math]::Min(30,($finish-$start).Days)
  & $PY "$R35\65_gee_event_weather10.py" --project "$PROJECT" --asset "projects/$PROJECT/assets/gf10_requests_$YEAR" --start $start.ToString("yyyy-MM-dd") --days $days --out "$BASE\grid10\gee_jobs" --submit
  if($LASTEXITCODE -ne 0){throw "Stage65 submit failed, STOP"}
  $start=$start.AddDays($days)
}
```
This submits ALL remaining 2020 production blocks. For full temporal
coverage repeat for 2000–2025, checking queue quota and year assets.
Do NOT submit the same first block both manually and through the loop:
duplicate-name protection will reject it. Use one approach or start
the loop at the next unsubmitted day.

After downloading all completed `gf10event_v1_*.csv` files to
`E:\Global_flooding\grid10\weather_csv`, execute:
```powershell
& $PY "$R35\65b_merge_weather10.py" --calendar "$BASE\grid10\calendar\63b_calendar_grid10.parquet" --requests "$BASE\grid10\weather_requests\65a_unique_requests_grid10.parquet" --csv-root "$BASE\grid10\weather_csv" --out "$BASE\grid10\climate_panel"
Get-Content "$BASE\grid10\climate_panel\65b_weather_qc.json"
```

**STRICT MODEL GATE**: incomplete rain days or API7 must be handled by
whole-stratum exclusion, NOT missing-to-zero, not partial referent
deletion. After coverage QC:

```powershell
& $PY "$R35\66_shared_response10.py" --panel "$BASE\grid10\climate_panel\65b_standard_daily_grid10.parquet" --context "$BASE\grid10\context\64b_ghsl_E2020_grid10.parquet" --out "$BASE\grid10\results32"
& $PY "$R35\68_city_fingerprints10.py" --panel "$BASE\grid10\climate_panel\65b_standard_daily_grid10.parquet" --dominant-city "$BASE\grid10\cities\62c_dominant_city_grid10.parquet" --out "$BASE\grid10\results33"
```

Stage66 currently provides shared rainfall/antecedent/temp interactions
and a 2020 built-space moderator with *spatial-block* heldout pair CIs
(PROXY blocks, not proven independent storms). Stage68 yields descriptive
ridge-fitted city fingerprints, NOT validated transferable hierarchical
partial pooling. A full city-transfer benchmark, storm linkage and
meteorological era attribution model are still followups.

## 6. R3.4 annual historical climate and future model

Historical ERA5-Land annual weather is derived from DAILY_AGGR as
daily UTC rain m*1000, daily temperature K->C, year total/quantile/max
and valid days; IMERG annual uses precipitation mm/hr*0.5 for
native halfhour total, native P30; P30 is NOT 3h or 24h extreme.
No spurious 10km downscaling of climate source information.

First historical production jobs; task counts 2 per requested year.
```powershell
& $PY "$R35\64_gee_context10_exports.py" --source era5 --years 2000,2001,2002,2003,2004,2005,2006,2007,2008,2009,2010,2011,2012,2013,2014,2015,2016,2017,2018,2019,2020,2021,2022,2023,2024,2025 --out "$BASE\grid10\gee_jobs" --submit
& $PY "$R35\64_gee_context10_exports.py" --source imerg --years 2000,2001,2002,2003,2004,2005,2006,2007,2008,2009,2010,2011,2012,2013,2014,2015,2016,2017,2018,2019,2020,2021,2022,2023,2024,2025 --out "$BASE\grid10\gee_jobs" --submit
```

WARNING: submitting 104 global historical image tasks at once may hit
EE queue/compute quota; submit year batches e.g. --years 2020,2021,2022
then expand. Source data availability for 2000 incomplete dates must
be audited; don't impute 0. In particular GEE IMERG annual full-globe
imagery may TIME OUT. Successful task submission != finished dataset.

Convert completed ERA5 year 2020 example:
```powershell
& $PY "$R35\64b_grid10_static_dynamic_join.py" --source era5 --label Y2020 --grid "$GRID" --west "$TIFS\era10_v1_y2020_s00.tif" --east "$TIFS\era10_v1_y2020_s01.tif" --out "$BASE\grid10\context"
```
Repeat all years. Then:
```powershell
& $PY "$R35\67_hazard_exposure10.py" --annual-root "$BASE\grid10\context" --population-2020 "$BASE\grid10\context\64b_ghsl_E2020_grid10.parquet" --out "$BASE\grid10\results34" --min-years 8
```
Requires at least eight complete years per baseline (2000-2010)
and late (2016-2025). 2020 FIXED population is an exposed-person
proxy, not projected SSP population or future physical flood risk.

Future NEX-GDDP-CMIP6 (NASA/GDDP-CMIP6): models and SSPs are
model/source specific. Example only, model existence/licensing and
the same-model historical baseline must be confirmed before any
multi-model inference:
```powershell
& $PY "$R35\64_gee_context10_exports.py" --source cmip6 --model ACCESS-CM2 --scenario historical --start-year 1995 --end-year 2014 --out "$BASE\grid10\gee_jobs" --submit
& $PY "$R35\64_gee_context10_exports.py" --source cmip6 --model ACCESS-CM2 --scenario ssp245 --start-year 2041 --end-year 2060 --out "$BASE\grid10\gee_jobs" --submit
& $PY "$R35\64_gee_context10_exports.py" --source cmip6 --model ACCESS-CM2 --scenario ssp585 --start-year 2041 --end-year 2060 --out "$BASE\grid10\gee_jobs" --submit
```
These are DAILY P99 and annualized mean rain/temperature only. Do NOT
project future 3h flood probability from daily CMIP6. 3.4 future
multimodel uncertainty and 3.5 attributable human exposure need
separate bias-adjusted model ensemble and SSP population.

## 7. Results map and hard gates

3.1: 62,62b,62c,63,63b (+ reporting bias covariates)
3.2: 65a,65,65b,66 (+ onset time/local rain lag and storm groups)
3.3: 64 ghsl, 69, 68 (+ validated city transfer, historical urbanization)
3.4: 64 era5/imerg/cmip6,64b,67 (+ multimodel baseline/SSP ensemble)
3.5: missingness/geocoding/storm robustness, 67 fixed-2020 exposure
       (+ population scenarios, counterfactual attribution limits)

No existing 20km result is overwritten. Every V10 output is stored under
E:\Global_flooding\grid10. Before publication publish provenance,
SHA256 of raw tiles and exported CSV/TIFFs, spatial support, era,
area denominators, per-stratum missingness, and sensitivity to alternative
urban definitions (SMOD21+ and SMOD23/30) and grid offsets.
