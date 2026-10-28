# Stage45 V3 + Stage53 V3: verified E2020 full-urban denominator and future climate exports

2026-10-28. Direct main update. V2 SMOD task Z4NEI6BTTED7NYIRJ3MGO2HK FAILED.
Do not retry V2, do not mix V2 test files with the V3 universe, do not
rerun existing M3 event climate, GHSL POP/SMOD epochs, MERIT or CRU.

## Root cause and extent contract

The V2 global EPSG:6933 region +/-17,400,000 E and +/-7,400,000 N
extends beyond geographic projection validity (at y=7,400,000 a
longitude/latitude transform returns NaN latitude), causing
Failed to compute bounds of <Geometry>. The old probe tested only one
equatorial 20km cell, not the whole rectangle.

V3 SAFE_BOUNDS = (-17,360,000, -7,320,000, 17,360,000, 7,320,000)
with 20km transform [20000,0,-17360000,0,-20000,7320000].
Expected GeoTIFF shape = 1736 columns x 732 rows, 1,270,752 cells.
This covers about +/-179.922 longitude, +/-85.56 latitude; narrow
dateline/polar edge slivers are not exported. Check omitted inhabited
places before using the word COMPLETE GLOBAL UNIVERSE.
Source is GHSL SMOD V2-0 2020, 1km; urban codes 21,22,23,30.
2 bands: 20km broad urban fraction of full cell and source valid fraction.
These describe the sampling frame, not flood incidence or drainage capacity.
Do NOT scale 2025 urban counts as a 2020 denominator.

## 0. Sync, test, dry run

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main

$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R34="$BASE\pipelines\r34"
$PROJECT="eco-sector-503721-b7"
$ASSET="projects/$PROJECT/assets/urban_universe_smod2020_20km_v3"
& $PY -m pytest -q tests/test_stage45_50_scripts.py
& $PY "$R34\45_smod2020_export.py"
~~~

## 1. V3 preflight: NO batch task

~~~powershell
& $PY "$R34\45_smod2020_export.py" --probe --project "$PROJECT"
~~~

Must print [CRS PASS], [BOUNDS PASS], [SMOD PROBE PASS].
If the global geometry still fails the server bounds probe, STOP:
do not submit more V3 tasks. Use local official GHS_SMOD_E2020
1km GeoTIFF as a fallback or investigate regional tiled export.
Do not turn off the geometry probe.

## 2. One 10x10 target-pixel smoke export

~~~powershell
& $PY "$R34\45_smod2020_export.py" --test --submit --project "$PROJECT"
~~~

EE task SMOD2020_20KM_V3_TEST, output smod2020_20km_v3_test.tif,
Drive folder GlobalFlood_E2020_Urban. Download test TIFF into
a separate local TEST directory and inspect band names, CRS, origin,
scale, valid/urban fractions [0,1]. A test file is NOT the universe.

## 3. One production Task after smoke succeeds

~~~powershell
& $PY "$R34\45_smod2020_export.py" --submit --project "$PROJECT"
~~~

EE task SMOD2020_20KM_V3, output smod2020_20km_v3.tif.
The CLI refuses duplicate V3 task names visible in the recent API task
history. Inspect GEE Tasks and Drive exports before any resubmission.

Download the completed production GeoTIFF into
E:\Global_flooding\R34_SMOD2020\smod2020_20km_v3.tif

## 4. Make E2020 urban census CSV (local only)

~~~powershell
$TIF="$BASE\R34_SMOD2020\smod2020_20km_v3.tif"
$UCSV="$BASE\results_31\urban_universe_smod2020_20km_v3.csv"
& $PY "$R34\45b_smod2020_to_asset.py" --tif "$TIF" --out "$UCSV" --urban-min 0.001 --valid-min 0.95 --chunk-size 5000
$QC=Get-Content "$BASE\results_31\urban_universe_smod2020_20km_v3_qc.json" -Raw | ConvertFrom-Json
$CHUNKS="0-$([int]$QC.chunks-1)"
Write-Host "Urban cells" $QC.rows "chunks" $CHUNKS
~~~

Converter now rejects V2 extent/TEST files by default and derives grid_x,
grid_y from the V3 raster affine, NOT stale hard-coded V2 origin.

Upload CSV in EE Code Editor -> Assets -> NEW -> Table upload.
Geometry longitude,latitude WGS84 for EE CSV ingest only. Set EE asset:
projects/eco-sector-503721-b7/assets/urban_universe_smod2020_20km_v3
Confirm asset type TABLE, exact rows, unique IDs/numeric grid_x,y/chunk_id.

## 5. Which GEE followup tasks are scientifically necessary?

### No need to rerun
- Groundsource 2,646,302 records / M3 26,317 20km grids.
- M3 six-epoch GHSL POP/SMOD: 157,902 grid-epoch rows.
- Existing Stage22: 2,680,571 grid-date climate panel.
- Existing MERIT, CRU, BasinATLAS, 2020 BUILT-S.
- Existing Stage54–59 local 5/10/20km environmental cache/pilot.

### Gate A: Global E2020 observation REPRESENTATIVENESS
The V3 E2020 SMOD urban universe itself is P0. If all you need is
the E2020 global observation denominator by Köppen/urban shares,
calculate these in Python and STOP; do not automatically submit
Stage46 full historical GHSL or Stage49 full GAIA.

### Gate B: full-city historical urbanization, optional for climate adaptation
Use Stage46 on the *new* V3 universe asset ONLY if historical
population/urban/built data for unreported cities is essential and not
already available locally. Stage46 includes GHSL 2000–2020 and can
be heavy. Check first 30 urban grids, then chunk 0.
~~~powershell
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --chunks 0 --test
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --verify-asset --probe-projection
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --chunks 0 --test --submit --max-tasks 1
~~~
After cloud TEST completes and CSV is numerically audited, proceed
with production limited to useful epochs and chunks; never submit all
chunks merely because they are listed:
~~~powershell
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --epochs 2000,2010,2020 --chunks 0 --submit --max-tasks 1
~~~
Do not mix different epoch-sets in the same output directory without
a complete unique grid_id/epoch audit.

### Gate C: annual ERA5/IMERG climate trends in ALL E2020 urban cells
Stage53 now uses safe V3 projected bounds; output names
ERA5RASTER_V3_Y2020 and IMERGRASTER_V3_Y2020.
This is for paper 3.5 emerging hazard-weather redistribution, NOT
event-day causal flood probabilities. First test the lightweight
ERA5 2020 product, then decide if the global annual sequence is feasible.
~~~powershell
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2020 --probe
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2020 --test --submit --max-tasks 1
~~~
After validated test, ONLY then:
~~~powershell
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2020 --submit --max-tasks 1
~~~
IMERG annual requires ~17,500 half-hour observations/year; on
restricted GEE quota, prefer pilot-only until computational viability:
~~~powershell
& $PY "$R34\53_gee_annual_raster_exports.py" --source imerg --years 2020 --probe
& $PY "$R34\53_gee_annual_raster_exports.py" --source imerg --years 2020 --test --submit --max-tasks 1
~~~
If pilot passes, annual global extent may STILL time out. Do not
automatically submit 2000-2025. First verify 2020 full production.
Stage53b currently accepts V3 TIFFs only:
~~~powershell
& $PY "$R34\53b_raster_to_grid_parquet.py" --source era5 --tif-root "$BASE\R34_Annual_ERA5_TIF_all" --grid-csv "$UCSV" --out "$BASE\climate_annual_53\era5"
~~~
Never combine annual test and production TIFFs in one directory.

### Gate D: optional historical GAIA
Stage49 V3, only if city impervious evolution is needed for 3.3; 
GAIA class area is not sewer capacity and GAIA stops at 2018:
~~~powershell
& $PY "$R34\49_gee_gaia_impervious_history.py" --asset "$ASSET" --chunks 0 --test
& $PY "$R34\49_gee_gaia_impervious_history.py" --asset "$ASSET" --chunks 0 --test --submit --max-tasks 1
~~~
Use existing local Roads/GRIP4/HydroRIVERS/GFID datasets for road,
river distance and independent archive overlap; these require NO GEE.

## 6. Honest scientific limitations

- The corrected target is the E2020 *broad urban* archive denominator,
  not the changing set of global urban grids in each observation year.
- Projected edge slivers are clipped, so literal global-total coverage
  must be audited with all known grid centers.
- Stage53 annual extrema are computed at native climate-pixel support
  before area averaging; NOT identical to the maximum 20km mean storm.
- No 5km flood labels are created by regridding 20km reported events.
- A 5/10/20km flood-response-scale winner requires new original-polygon
  case/reference assignment, matched storms, geolocation uncertainty,
  and actual independent holdout evaluation.
