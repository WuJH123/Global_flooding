# R34 Urban flood mechanism: source contracts, GEE tasks and Windows workflow

These scripts extend prior Results3.1/3.3 while keeping Stage29b and Stage34b immutable.
All Earth Engine submission is opt-in; dry-run needs no GEE connection.
No new scripts claim to measure pipe hydraulic capacity, true inundation probability or climate-change attribution.

## Project facts

M3: 1,378,124 flood-positive grid days -> 650,368 grid Episodes -> 26,317 distinct 20km cells.
Prior 1:4 matched observations: 3,250,710. Primary climate lookup: 2,789,073 unique requests.
Standardized calendar: 2,680,571 grid-date rows. Stage22c 600,723 eligible strata and 641,587 eligible onset records.
Stage29b showed reproducible independent C3 × API7 predictive information. Stage34b found continuous terrain/urban context provides heldout gain over simple T1/T2 classification. Neither establishes physical causation.

This R34 export uses the EXISTING M3 EE TABLE by default. IT DOES NOT REPRESENT the entire global urban sampling universe. For global/all-city climate history, upload a SEPARATE 2020 full-city urban grid asset with numeric grid_x/grid_y, grid_id and chunk_id; specify --asset explicitly. Never backfill E2025/2030 into 2000-2020.

## Each indicator's role

1. Rainfall: P24 and short-peak residual C3, event forcing.
2. Antecedent: API7, ERA5 SM1 and independently sourced hydrologic states, conditional wetness memory.
3. Landform: MERIT relief and real HAND, upstream basin area, river distance, runoff-convergence background.
4. Settlement: GHSL built surface m²; SMOD urban land area; residential POP count. Different physical definitions!
5. Imperviousness: GAIA >50%-impervious classified area. GHSL BUILT is NOT this.
6. Roads: measured centreline road length km per (urban OR land) km², junction density, road structure. NOT pipe capacity or drainage design flow. Road density also creates impervious surface and runoff pathways, so its effect sign may be opposite that of real drainage capability.
7. Drainage capacity: where available, pipe flow m³/s, connected catchment, pumps m³/s, detention m³, design return period, inlet density and outfall water level. Must calibrate road proxies on cities with observed sewer network.
8. Observation selection: population and digital-news coverage; not a substitute for daily physical flood exposure.
9. Future: urban annual climate indicators and CMIP6, never absolute flood probabilities from a conditional case-crossover model.

## New scripts

pipelines/r34/46_gee_ghsl_epoch_context.py
* GHSL observed historical epochs 2000/2005/2010/2015/2020.
* Built-total m², non-residential built m², valid built pixel area m², population count/valid pixel area, SMOD urban/dense/valid land area m².
* SMOD urban = classes 21,22,23,30; dense=23,30. SMOD urban area != built surface area.
* 100m built/pop cell totals are spatially SUMMED, first to 1km, then to 20km. Do NOT average cell totals. Native raster pilot comparison is required before production.
* Default 6 original M3 chunks -> 6 EE tasks; oversized tasks can be subdivided, but never mix parent and child outputs blindly.

pipelines/r34/47_gee_era5_annual_context.py
* ERA5-Land annual mean temp °C, accumulated rain mm, RX1day mm, >20mm or >1mm rain days, annual mean SM1, valid-day counts, negative GRIB accumulation day QC.
* Daily negative GRIB packing artifacts are clipped to zero ONLY for derived rain indexes, and counted explicitly.
* This is a 11km reanalysis annual descriptor, NOT original event-day IMERG nor reported flood frequency.
* Default 2018-2025 split by 2 years and 6 M3 chunks -> 24 EE tasks.
* A trial over 1 chunk and small years must succeed before bulk production.

pipelines/r34/48_validate_new_context_csvs.py
* Local check: duplicate grid-year/epoch IDs, missing fields, built area/NRES, SMOD area ordering, ERA5 physical/unit ranges and coverage IDs.
* It does not establish hydraulic or geographical validity, and does not silently impute missing coastal ERA5 values.

## Official source and download entries

GHSL BUILT: https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_BUILT_S
GHSL POP: https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_POP
GHSL SMOD: https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_SMOD_V2-0
ERA5-Land: https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_DAILY_AGGR
Real HAND COG / STAC: https://registry.opendata.aws/glo-30-hand/
HydroRIVERS shapefiles: https://www.hydrosheds.org/products/hydrorivers
Roads OSM per-country PBF: https://download.geofabrik.de/
GRIP4 regional roads CC0: https://www.globio.info/download-grip-dataset
GloFAS historic (EWDS account): https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical
USGS GFID event tables: https://catalog.data.gov/dataset/the-global-flood-impacts-database-gfid-a-globally-aggregated-and-geocoded-flood-disaster-i
GFD GEE validation: https://developers.google.com/earth-engine/datasets/catalog/GLOBAL_FLOOD_DB_MODIS_EVENTS_V1
NEX-GDDP-CMIP6 future: https://developers.google.com/earth-engine/datasets/catalog/NASA_GDDP-CMIP6

Roads should be processed LOCALLY in EPSG:6933: clip LINESTRING to urban polygon/grid polygon; avoid duplicate OSM ways; calculate total centreline length km divided by urban polygon area km²; report separate road classes, intersect densities and missing coverage. OSM 2026 current snapshots cannot be used as historical 2000 roads. Do not label that metric sewer drainage capacity.
GRIP4 is cross-region harmonized but not a consistent 2020 road snapshot.
Independent drainage calibration needs selected cities' known stormwater network, inlet, outfall and pump design capacity.

## Local Windows commands — setup

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R34="$BASE\pipelines\r34"
$GRID="$BASE\mechanism_project\01_cities\m3\grid_centers.csv"

& $PY -m pytest -q tests/test_stage46_47_gee_context.py
~~~

## 46 Historical GHSL — no task until --submit

~~~powershell
& $PY "$R34\46_gee_ghsl_epoch_context.py" --epochs 2000,2005,2010,2015,2020 --chunks 0-5
& $PY "$R34\46_gee_ghsl_epoch_context.py" --verify-asset
& $PY "$R34\46_gee_ghsl_epoch_context.py" --chunks 0 --test --submit --max-tasks 1
~~~

Download the test CSV from Drive folder GlobalFlood_R34_GHSL_Epochs.
Check 2020 built against earlier locally extracted 2020 BUILT raster on the SAME grid IDs.
After numerical parity, submit one whole production chunk:

~~~powershell
& $PY "$R34\46_gee_ghsl_epoch_context.py" --chunks 0 --submit --max-tasks 1
~~~

If successful and full chunk numerically audited, submit remaining original chunks:

~~~powershell
& $PY "$R34\46_gee_ghsl_epoch_context.py" --chunks 1-5 --submit --max-tasks 0
~~~

## 47 Annual ERA5 climate — no task until --submit

~~~powershell
& $PY "$R34\47_gee_era5_annual_context.py" --years 2018-2025 --years-per-task 2 --chunks 0-5
& $PY "$R34\47_gee_era5_annual_context.py" --verify-asset
& $PY "$R34\47_gee_era5_annual_context.py" --years 2018-2019 --years-per-task 2 --chunks 0 --test --submit --max-tasks 1
~~~

Download pilot CSV from Drive folder GlobalFlood_R34_ERA5_Annual.
If validated, run the SAME grouping and the SAME 2018-2019 task name with test=false:

~~~powershell
& $PY "$R34\47_gee_era5_annual_context.py" --years 2018-2019 --years-per-task 2 --chunks 0 --submit --max-tasks 1
~~~

Then continue other chunks for this block and other years with no parent/child mixing:

~~~powershell
& $PY "$R34\47_gee_era5_annual_context.py" --years 2018-2025 --years-per-task 2 --chunks 0-5 --submit --max-tasks 1
~~~

A dry-run/single task is safer when the EE project is in restricted quota mode.
When ready for larger sequential scheduling, set --max-tasks 0 after confirming
computational limits and task history. Fewer batch tasks do not guarantee lower compute.

## 48 Local CSV QA

~~~powershell
$GHSLC="$BASE\R34_GHSL_Epochs_all"
$ERA5C="$BASE\R34_ERA5_Annual_all"
& $PY "$R34\48_validate_new_context_csvs.py" --kind ghsl --csv-root "$GHSLC" --expected-ids "$GRID" --require-full
& $PY "$R34\48_validate_new_context_csvs.py" --kind era5 --csv-root "$ERA5C" --expected-ids "$GRID" --require-full
~~~

Use --require-full only when every task/year is downloaded. During pilot omit it.
Validate year/epoch x grid cardinality and compare reported units with native source.
Do not merge automatically into established manuscript inputs until QC passes.

## Next scientific gates NOT implemented by these scripts

* GFID/GFD/manual flood-event validation and event date uncertainty.
* Independently defined meteorological storm footprints, storm-level inference.
* Hierarchical partially pooled city response fingerprints with city-independent holdout.
* Road/pipe calibration and physical drainage model.
* All-city (not only M3 reported city) annual IMERG extreme-rain climate history.
* CMIP6 climate model historical validation, bias correction and multi-model future scenarios.
These need their own frozen data and QA before causal or risk-change claims.
