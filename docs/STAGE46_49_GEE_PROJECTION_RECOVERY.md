# R34 EE 6933 CRS repair: full execution runbook (2026-10-27)

## Diagnosis

GHSL_EPOCH_V2_Y0005101520_C00_S00of01_TEST failed after 5 seconds:
Projection: The CRS of a map projection could not be parsed.

Root cause: bare EPSG:6933 is accepted syntactically by the client but can fail
in the Earth Engine server at pixel access. Google community bug report:
https://github.com/google/earthengine-community/issues/971

Fix: shared pipelines/r34/ee_ease_projection.py contains the same complete
EASE-Grid 2.0 WKT1 used in successful mechanism_scripts/04_submit_merit_gee.py.
Stages 46, 47, 49 all reconstruct polygons and reduceImages in THIS WKT.
Stage submission now performs a tiny 20km square geometry and pixel area
server-side preflight. A CRS failure aborts before launching batch tasks.

New output prefixes: GHSL_EPOCH_V3, ERA5YR_V2, GAIA_IMP_V3.
The failed GHSL V2 TEST is obsolete and must NOT be retried or merged.
Stage48 audits recognize V3/V2/V3 files only.

## Setup and preflight (no batch tasks)

    cd E:\Global_flooding
    git status --short
    git switch main
    git pull --ff-only origin main

    $BASE="E:\Global_flooding"
    $PY="$BASE\.venv\Scripts\python.exe"
    $R34="$BASE\pipelines\r34"
    $GRID="$BASE\mechanism_project\01_cities\m3\grid_centers.csv"

    & $PY -m pytest -q tests/test_stage46_47_gee_context.py

    & $PY "$R34\46_gee_ghsl_epoch_context.py" --verify-asset --probe-projection
    & $PY "$R34\47_gee_era5_annual_context.py" --verify-asset --probe-projection
    & $PY "$R34\49_gee_gaia_impervious_history.py" --verify-asset --probe-projection

Expected: [CRS PASS] 20km square m2 ~400000000 raster m2 ~400000000.
If probe fails do not submit any EE task.

## Smoke tests, ONE task each (batch compute)

    & $PY "$R34\46_gee_ghsl_epoch_context.py" --chunks 0 --test --submit --max-tasks 1
    & $PY "$R34\49_gee_gaia_impervious_history.py" --chunks 0 --test --submit --max-tasks 1
    & $PY "$R34\47_gee_era5_annual_context.py" --years 2000-2001 --years-per-task 2 --chunks 0 --test --submit --max-tasks 1

Only submit the second and third smoke after first completed GHSL cloud task and
its CSV passes local sanity. All smoke tests read M3 EE asset containing 26317
grid IDs, not complete global all-city domain.

Drive folders:
- GlobalFlood_R34_GHSL_Epochs
- GlobalFlood_R34_GAIA_Impervious
- GlobalFlood_R34_ERA5_Annual

Inspect task cloud state and download each test CSV.
WARNING: GEE batch success does not prove per-pixel population or area
correctness; compare E2020 GHSL to native local built source on the SAME cells.

## Production, after smoke + numerical parity

First each series' original chunk 0:

    & $PY "$R34\46_gee_ghsl_epoch_context.py" --chunks 0 --submit --max-tasks 1
    & $PY "$R34\49_gee_gaia_impervious_history.py" --chunks 0 --submit --max-tasks 1
    & $PY "$R34\47_gee_era5_annual_context.py" --years 2000-2001 --years-per-task 2 --chunks 0 --submit --max-tasks 1

If each original first chunk completes and passes spatial and unit QC:

    & $PY "$R34\46_gee_ghsl_epoch_context.py" --chunks 1-5 --submit --max-tasks 0
    & $PY "$R34\49_gee_gaia_impervious_history.py" --chunks 1-5 --submit --max-tasks 0

Full ERA5 annual climate series 2000..2025, 2 years per task, 6 chunks:
13 x 6 = 78 annual ERA5 tasks. One task was covered by above first chunk.

    & $PY "$R34\47_gee_era5_annual_context.py" --years 2000-2025 --years-per-task 2 --chunks 0-5
    & $PY "$R34\47_gee_era5_annual_context.py" --years 2000-2025 --years-per-task 2 --chunks 0-5 --submit --max-tasks 1

The same --max-tasks 1 command may be invoked repeatedly after checking
cloud queue, or use --max-tasks 0 ONLY once confirmed safe. On GEE restricted
quota, submit a controlled small backlog. Large single tasks may timeout.
Use --subdivisions 2 or 4 for individual expensive chunks, but DO NOT
mix parent and child results without proven unique grid-year ID coverage.

## Download all production CSVs and check locally

Put complete downloaded production results under:

    E:\Global_flooding\R34_GHSL_Epochs_all\
    E:\Global_flooding\R34_GAIA_all\
    E:\Global_flooding\R34_ERA5_Annual_all\

Set:

    $GHSL="$BASE\R34_GHSL_Epochs_all"
    $GAIA="$BASE\R34_GAIA_all"
    $ERA5="$BASE\R34_ERA5_Annual_all"

    & $PY "$R34\48_validate_new_context_csvs.py" --kind ghsl --csv-root "$GHSL" --expected-ids "$GRID" --expected-years 2000,2005,2010,2015,2020 --require-full

    & $PY "$R34\48_validate_new_context_csvs.py" --kind gaia --csv-root "$GAIA" --expected-ids "$GRID" --expected-years 2000,2005,2010,2015,2018 --require-full

    & $PY "$R34\48_validate_new_context_csvs.py" --kind era5 --csv-root "$ERA5" --expected-ids "$GRID" --expected-years 2000,2001,2002,2003,2004,2005,2006,2007,2008,2009,2010,2011,2012,2013,2014,2015,2016,2017,2018,2019,2020,2021,2022,2023,2024,2025 --require-full

To check downloaded test CSV only, use --allow-test and OMIT --require-full.
Keep TEST and production in separate dirs. Stage48 only reads corresponding
V3 or V2 prefixes, so old test output will not be considered for merge.
Expected cardinalities are 26317 grid IDs PER requested epoch/year, no duplicates.
Annual ERA5 may have coastal missing values; do not replace missing with zero.

## Scientific limitations

- GHSL BUILT-S total built surface m2 is not exact impervious-area fraction.
- GAIA classified >50% impervious 30m pixels through 2018; cannot infer 2020
  historical imperviousness without independent data.
- ERA5 is an annual reanalysis climate DESCRIPTOR (not IMERG event-day data).
- These M3-only tasks cannot identify shifting extreme rainfall conditions
  in never-reported cities. A separate FULL 2020 urban universe EE table asset
  is required for global hazard trend analysis.
- GEE noncommercial restricted quota cannot be bypassed by changing scripts.
- Future CMIP6, GFD satellite independent verification, and all-city weather
  extraction require separate pipeline implementations, and are NOT
  launched by these three Stage46/47/49 scripts.
