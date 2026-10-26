# Stage30c — local GHSL-BUILT-S, historical and E2030 scenario separation

Official source: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
Download: https://human-settlement.emergency.copernicus.eu/download.php?ds=bu

GHS-BUILT-S input cells store BUILT AREA in m2, not percentages and not
sewer capacity or imperviousness. The extractor converts source m2 to a
fraction of native equal-area 100m or 1km pixel, area-averages to 20km
EPSG:6933 grid, and multiplies by 400,000,000m2. It separately records
fraction of target covered by valid source.

E2030 is future extrapolated/modelled and cannot be used as 2020/2000
urban land conditions; E2025 is also projected. E2020 and past series
are reconstructed historical estimates, not necessarily direct year-specific
ground truth. Default Stage30 refuses both smoke and E2030 integration.

## Local Windows commands (NO GEE)

Run in PowerShell from E:\Global_flooding:

~~~powershell
git status --short
git switch main
git pull --ff-only origin main
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R33="$BASE\pipelines\r33"
$CENTERS="$BASE\mechanism_project\01_cities\m3\grid_centers.csv"
$OVERLAP="$BASE\mechanism_project\01_cities\m3\grid_city_crosswalk_all.csv"
$STATIC="$BASE\mechanism_project\05_static_merged\grid_static_context_m3.parquet"
$GHSL="$BASE\Groundsource_all"
$BUILT2030="E:\数据集\全球人类居住层GHS built-up surface (R2023)\GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100_V1_0\GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100_V1_0.tif"

& $PY -m pip install -U rasterio numpy pandas pyarrow duckdb scipy scikit-learn pytest
& $PY -m pytest -q tests/test_stage30c_built_surface.py

# Inspect metadata (no full raster read)
& $PY "$R33\30c_extract_built_surface.py" --raster "$BUILT2030" --epoch 2030 --centers "$CENTERS" --inspect

# 20 scattered-grid smoke; not for production use
& $PY "$R33\30c_extract_built_surface.py" --raster "$BUILT2030" --epoch 2030 --centers "$CENTERS" --city-overlap-csv "$OVERLAP" --max-grids 20 --tile-cells 2 --log-every 5

# Full M3 20km grid context; no GEE tasks submitted
& $PY "$R33\30c_extract_built_surface.py" --raster "$BUILT2030" --epoch 2030 --centers "$CENTERS" --city-overlap-csv "$OVERLAP" --tile-cells 4 --min-coverage 0.95 --log-every 100
if ($LASTEXITCODE -ne 0) { throw "30c failed" }
~~~

Results within mechanism_project/30c_built_raster:
30c_built_grid_2030.parquet; 30c_built_grid_2030_qc.csv;
30c_built_grid_2030_manifest.json;
30c_city_grid_context_2030.csv/parquet.
City value is 20km grid-context average weighted by UCDB overlap area.
It is NOT a direct polygon sum of city built area.

If large source window error: set --tile-cells 2. The script reads only
small source windows and does not load the global 100m raster into RAM.

## Baseline historical urban typology without E2020 BUILT-S

~~~powershell
& $PY "$R33\30_urban_context.py" --base "$BASE" --ghsl-root "$GHSL" --static-path "$STATIC" --city-overlap-csv "$OVERLAP"
if ($LASTEXITCODE -ne 0) { throw "30 failed" }
& $PY "$R33\30b_assemble_city_environment.py" --base "$BASE" --epoch 2020
if ($LASTEXITCODE -ne 0) { throw "30b failed" }
& $PY "$R33\32_mechanism_feature_selection.py" --city-table "$BASE\mechanism_project\30_urban_context\30b_city_features_2020.parquet" --epoch 2020 --out "$BASE\stage33_feature_selection"
if ($LASTEXITCODE -ne 0) { throw "32 failed" }
& $PY "$R33\33_city_typology.py" --ghsl-city "$BASE\mechanism_project\30_urban_context\30_city_ghsl_epochs.parquet" --static-grid "$STATIC" --selection-json "$BASE\stage33_feature_selection\32_feature_selection.json" --epoch 2020 --out "$BASE\stage33_city_typology"
if ($LASTEXITCODE -ne 0) { throw "33 failed" }
~~~

Built fraction remains NaN here unless matching E2020 raster has been
supplied; never substitute SMOD urban area fraction as built surface.

## Adding real historical raster epochs later

Prioritize JRC historical products E2000,E2005,E2010,E2015,E2020,
Mollweide, total RES+NRES, 1km (or existing 100m if already downloaded).
For 20km global comparison, 1km reduces source file size and I/O
while retaining global temporal consistency.

~~~powershell
$BUILT2020="<actual downloaded GHS_BUILT_S_E2020_GLOBE_R2023A_54009_1000_V1_0.tif path>"
& $PY "$R33\30c_extract_built_surface.py" --raster "$BUILT2020" --epoch 2020 --centers "$CENTERS" --tile-cells 4
& $PY "$R33\30_urban_context.py" --base "$BASE" --ghsl-root "$GHSL" --static-path "$STATIC" --city-overlap-csv "$OVERLAP" --built-grid-parquet "$BASE\mechanism_project\30c_built_raster\30c_built_grid_2020.parquet"
~~~

Repeat --built-grid-parquet for every new historical raster epoch.
The script enforces exact same year and full set of grid IDs; smoke
products are refused. E2025 only allowed with explicit projection flag.
E2030 is NEVER merged into the historical M3 GHSL table.

## Research design decision: static vs time-varying

- Terrain/slope/long-run climate zone: time-invariant first-pass
  environmental moderator is defensible, with human river works caveats.
- Built surface/population/impervious cover/urban development: highly
  time-varying; cannot claim historical urbanization effects from a 2030
  projected raster or a 2020 snapshot assigned to a 2000 flood.
- Climate-event IMERG/ERA5 rainfall: always day-specific; climatological
  CRU is only background.
- No historical change analysis until epoch-matched built surface is ready;
  no absolute event probability and no proven independent storms from
  a reported-onset case-crossover study.
