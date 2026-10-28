# R34 Scale strategy: minimum GEE for the main paper (2026-10-28)

## Scientific decision

Keep existing M3 20 km case–referent climate inference as validated baseline.
Use 5 km as a **local environment sufficient-statistic cache**, not as a new
5 km rainfall product or evidence of 5 km flood location. First establish
whether hidden urban-form heterogeneity changes mechanism interpretation;
only then consider remaking event–referent assignment from original flood
polygons for a well-geolocated 10 km subsample.

Nested 5 km cells: grid_x5=4*grid_x20+dx, grid_y5=4*grid_y20+dy
for dx,dy in {0,1,2,3}. Negative coordinates use mathematical FLOOR.
10 km index=floor(5km_index/2); 20 km index=floor(5km_index/4).
Each 20 km parent has 16 children, each 10 km parent has 4.

GHSL built surface is source-cell built AREA in m2; the Stage54 code
converts to density per m2, reprojects by area-weighted average to 5 km,
then multiplies by 25 million m2. It produces additive numerators
(built_total_m2, built_valid_area_m2, cell_area_m2) and derived fractions.
Stage55 sums numerators BEFORE reconstructing ratios at 10/20 km.

### A. Sync the only main branch

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main

$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R34="$BASE\pipelines\r34"
$CENTERS="$BASE\mechanism_project\01_cities\m3\grid_centers.csv"
$BUILT2020="E:\数据集\全球人类居住层GHS built-up surface (R2023)\GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0\GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0.tif"
Test-Path $BUILT2020
& $PY -m pytest -q tests/test_stage54_55_multiscale_environment.py
~~~

### B. Pilot using 20 original 20km M3 cells, no GEE

~~~powershell
& $PY "$R34\54_local_fine5_built_cache.py" --parents "$CENTERS" --built-tif "$BUILT2020" --out-root "$BASE\multiscale_smoke\fine5" --limit-parents 20 --batch-parents 10
& $PY "$R34\55_aggregate_fine5_environment.py" --fine-root "$BASE\multiscale_smoke\fine5" --out-root "$BASE\multiscale_smoke\coarse" --scales 10,20
~~~

Expected: 20 parent cells, 320 fine 5km rows, 80 nested 10km rows,
20 nested 20km rows. Compare area and physical bounds.

### C. Full already-observed M3 cohort, no GEE

~~~powershell
& $PY "$R34\54_local_fine5_built_cache.py" --parents "$CENTERS" --built-tif "$BUILT2020" --out-root "$BASE\mechanism_project\54_fine5_environment\built_2020" --batch-parents 100
& $PY "$R34\55_aggregate_fine5_environment.py" --fine-root "$BASE\mechanism_project\54_fine5_environment\built_2020" --out-root "$BASE\mechanism_project\55_multiscale_environment" --scales 10,20 --reference-20km "$BASE\mechanism_project\30c_built_raster\30c_built_grid_2020.parquet"
~~~

Expected: 421072 fine 5km rows, 105268 10km rows, 26317 20km rows.
Read the saved 55_scale_qc.json and 55_stage30c_built_parity.csv.
Compare Stage30c 2020 fraction on the SAME 20km grid IDs.
If difference is large, investigate source input, nodata, coastal denominator
and area reprojection. A local geometry/physical-bounds QC PASS is not a
guarantee of numerical parity against the old Stage30c algorithm.

Optional strict parity gate (p95 abs difference <=0.03):

~~~powershell
& $PY "$R34\55_aggregate_fine5_environment.py" --fine-root "$BASE\mechanism_project\54_fine5_environment\built_2020" --out-root "$BASE\mechanism_project\55_multiscale_environment" --scales 10,20 --reference-20km "$BASE\mechanism_project\30c_built_raster\30c_built_grid_2020.parquet" --strict-parity --max-p95-diff 0.03
~~~

### D. No new GEE batch tasks until the scale gate

Do not rerun previously completed Stage22 event IMERG/ERA5, six-epoch M3
POP/SMOD, MERIT, or Stage30c BUILT-S. Stage54/55 use local E2020
100m GHSL built raster. Historic GAIA, historical BUILT-S epochs on
non-reported urban domain, and complete-city annual climate remain new
datasets, but are not P0 for the immediate main M3 mechanistic analysis.

If the corrected full E2020 urban denominator has NOT been generated,
Stage45 remains an independent one-task followup after the local pilot:

~~~powershell
& $PY "$R34\45_smod2020_export.py" --probe
& $PY "$R34\45_smod2020_export.py" --submit
~~~

Check GEE history before resubmitting. This export is at 20km and
cannot be upsampled into valid 5km SMOD data. Source-level 1km SMOD is
needed to calculate 5km urban/land composition later.

## What CAN be aggregated

Built total area, population counts, classified GAIA area, SMOD class
area and HAND <2m/<5m area counts are additive. Recompute built shares
with total built area / total valid area or cell area, as declared.
A proper area-weighted covariance requires sum of X, Y and XY at the
same source support.

What CANNOT be aggregated exactly: medians and upper percentiles without
native-value histograms; the distance to nearest stream; minimum river
distance; independent flood-event counts when one storm spans multiple
cells; temporal climate maxima and event-level interactions from spatial
marginal averages. In general max(time,mean(space,P)) is not equal to
mean(space,max(time,P)). IMERG/ERA5 have coarser effective spatial support
than a 5km environment cache. Do NOT fabricate 5km climate detail.

## What to analyze next for the manuscript

1. Verify full 5km cache, source coverage and 20km numerical parity.
2. Use variation in 5km built fraction within the SAME 20km parent
   (min, max, SD, high-built share) as a heterogeneity modifier in the
   existing Stage22/23/34 M3 response models, not as 16 independent
   event labels.
3. Select ~30–60 quality-controlled cities for a 10km event-polygon
   sensitivity test; align cases/referents, storm clusters and time folds.
4. Retain 20km results until spatial scale results improve held-out
   validation and effect stability with shifted grid origins.
5. Publish geography/provenance, input source checksums, missing-area
   fractions, and scale sensitivity before drawing physical mechanisms.

## Status boundary

This update fully implements only the local BUILT-S 2020 5km cache and
its 10km/20km additive aggregation, not all high-resolution environment
datasets (GAIA/HAND/POP still need extraction), not a complete global
2020 all-urban universe, and not 5km rainfall or location observations.


## E. Bridge into the existing 34b interaction-response model (NO GEE)

After full Stage55 QC, retain original 20km onset risk sets and create a
NEW city-environment table. Stage56 weights the 20km parent-level **within
5km built heterogeneity** using the UCDB city-grid overlap, explicitly
noting that true within-city fine-cell intersections are not calculated.

~~~powershell
$R33="$BASE\pipelines\r33"
$CITY="$BASE\mechanism_project\30_urban_context\30b_city_features_2020.parquet"
$LINKS="$BASE\mechanism_project\01_cities\m3\grid_city_crosswalk_all.csv"
$BRIDGE="$BASE\mechanism_project\56_built5_city_bridge"
$CITYTYPES="$BASE\stage33_city_typology\33_city_environment_types.csv"

& $PY "$R34\56_bridge_fine5_city_response.py" --grid-20km "$BASE\mechanism_project\55_multiscale_environment\environment_grid_20km_2020.parquet" --city-crosswalk "$LINKS" --city-table "$CITY" --out-dir "$BRIDGE"

& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --city-types "$CITYTYPES" --city-environment "$BRIDGE\56_city_environment_with_built5.parquet" --features ghsl_built_5km_sd_wmean --validation spatial --bootstrap-reps 99 --max-strata 5000 --out "$BASE\stage33_continuous_response\multiscale_smoke"

# Once smoke passes and the Stage55 numeric parity review is acceptable:
& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --city-types "$CITYTYPES" --city-environment "$BRIDGE\56_city_environment_with_built5.parquet" --features ghsl_built_5km_sd_wmean --validation all --bootstrap-reps 399 --out "$BASE\stage33_continuous_response\multiscale_full"
~~~

For a fair ablation, fit baseline and modifier models on the exact same
complete-case strata, identical train/test folds and city membership.
The 2020 environment does not enter the pre-2020 model by default.
This tests whether internal built variability modifies *observed reported
event associations*; it does not identify true drainage network capacity,
street-scale flooding, or causal effects.
