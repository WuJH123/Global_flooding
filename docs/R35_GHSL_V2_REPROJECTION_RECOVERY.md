# GHSL V2 GF10 recovery (2026-10-29)

## Verified error
GHS10_V1_E2000_S00 and S01 failed: internal reprojection 34493x25290 /
26574x25289 vs requested 1736x1464. Large internal map projections
come from native high-resolution GHSL. Simply raising maxPixels does
not correct source reduction and output pixel alignment.

GHSL V1 also used direct reduceResolution(sum) on PER-SOURCE-PIXEL
population_count and built_surface. EE sum weights by fraction of
OUTPUT cell covered, so this is not conservative sum of source counts.
Do not use completed GHS10_V1 as verified scientific statistics.

Stage64 GHSL V2 computes per-native-source-area density for each
POP/BUILT band separately, clips before reprojection, reduces weighted
density onto EPSG:6933 10 km grid with exact global affine, then
multiplies output pixel area. Splitting latitude rows reduces intermediate
memory pressure; it does NOT create different grid origins.

Docs:
https://developers.google.com/earth-engine/guides/resample
https://developers.google.com/earth-engine/apidocs/export-image-todrive
https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_POP

## 0. Sync and local checks

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R35="$BASE\pipelines\r35"
$P="eco-sector-503721-b7"
$TIFS="$BASE\grid10\gee_tifs"
$GRID="$BASE\grid10\universe\grid10_urban_2020.parquet"
& $PY -m pytest -q tests/test_stage64_ghsl_v2_recovery.py tests/test_stage62_68_grid10.py
~~~

## 1. New GHSL 2000 V2 real PRODUCTION (8 tasks, no TEST)

~~~powershell
& $PY "$R35\64_gee_context10_exports.py" --source ghsl --epochs 2000 --row-parts 4 --out "$BASE\grid10\gee_jobs" --submit
~~~

Task labels GHS10_V2_E2000_S00_R00of04 ... R03of04 and S01 parts.
4 latitude partitions x 2 stripes; the global fixed affine is unchanged.
Only run once. --resume allows a resumed run to skip already submitted
names rather than automatically resubmit failed or completed tasks.

Remaining historical GHSL when ready (32 more PRODUCTION tasks):
~~~powershell
& $PY "$R35\64_gee_context10_exports.py" --source ghsl --epochs 2005,2010,2015,2020 --row-parts 4 --resume --out "$BASE\grid10\gee_jobs" --submit
~~~

## 2. Download and strict local stitch after ALL eight tiles/epoch complete

Download finished TIFFs into E:\Global_flooding\grid10\gee_tifs.
No missing tile can be silently counted as zero. Stage64c validates
each tile shape, transform, source origin and EPSG:6933 and copies
pixels without any resampling.

~~~powershell
& $PY "$R35\64c_stitch_ghsl_v2_tiles.py" --year 2000 --row-parts 4 --input-dir "$TIFS" --out "$BASE\grid10\gee_tifs_v2_stitched"
if($LASTEXITCODE -ne 0){throw "STOP: incomplete or offset tiles"}
& $PY "$R35\64b_grid10_static_dynamic_join.py" --source ghsl --label E2000 --grid "$GRID" --west "$BASE\grid10\gee_tifs_v2_stitched\ghs10_v2_e2000_s00.tif" --east "$BASE\grid10\gee_tifs_v2_stitched\ghs10_v2_e2000_s01.tif" --out "$BASE\grid10\context_v2"
& $PY "$R35\72_grid10_integrity_contract.py" --grid "$GRID" --tables "$BASE\grid10\context_v2\64b_ghsl_E2000_grid10.parquet" --out "$BASE\grid10\qc"
~~~

Repeat for 2005/2010/2015/2020 with year and E-year label changed.
Never pass V1 TIFFs to Stage64c. Check built_total_m2 <= 1e8 m2,
NRES <= built total, population_count >=0, nonmissing coverage,
and independent 100m native pixel comparison before final science.

## 3. Other GEE production sources are not changed by GHSL-unit bug

~~~powershell
& $PY "$R35\64_gee_context10_exports.py" --source era5 --years 2020,2021,2022 --resume --out "$BASE\grid10\gee_jobs" --submit
& $PY "$R35\64_gee_context10_exports.py" --source imerg --years 2020,2021,2022 --resume --out "$BASE\grid10\gee_jobs" --submit
~~~

If other sources show similar output-too-large, use aligned row partitions
but they require a source-specific stitch script (64c is GHSL V2 only).
Annual ERA5/IMERG after completed production extend to years 2000-2025.
CMIP6 pair each GCM future scenario to its OWN historical reference.

## 4. Remaining science dependency chain

Stage62 frozen 10km -> 62b ellipsoidal/affine QC -> 62c UCDB city overlap
-> 63 original news geometry -> 63b onset/reference calendar -> 65a exact
requests -> 65 daily weather -> 65b coverage -> 66 shared response ->
68 city fingerprints -> 67 annual hazard and exposure -> 71 CMIP6 ensemble
-> 70 observational reporting coverage -> 72 final grid ID and area checks.
Read docs/R35_GRID10_PRODUCTION_RUNBOOK.md for other runnable commands.

No task was submitted to the private GEE account during this repository edit.
No code verification establishes the current GEE queue/compute quota. There
are no small TEST jobs; each V2 job produces final data for its region.
