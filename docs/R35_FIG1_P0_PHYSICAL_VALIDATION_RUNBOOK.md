# GF10 Figure 1 P0: archive provenance and measured-water validation

## Research contract and download priority

Groundsource reports are not flood footprints. DFO region/master, GFID/DFO and GFD/MODIS all share **DFO event-selection lineage**; agreement is not multiple independent flood confirmations. GFD brings an independent **satellite measurement modality** for those DFO-selected events. No record does not imply no flood, and no MODIS flood pixel does not imply no short-duration urban flooding.

Priority 0: download the official USGS GFID AggregatedFloodData.xlsx AND Potential_Duplicate_Events.csv from https://doi.org/10.5066/P135ARGI . The 136,642 record database consists of DFO, DesInventar, EM-DAT, HANZE records; preserve Source, Source_ID, event date precision and duplicate flags. This is the principal new archive download. GFID representative locations are NOT inundation footprints.

Priority 0: use Google Earth Engine collection GLOBAL_FLOOD_DB/MODIS_EVENTS/V1 for small actual water-pixel verification. Satellite event selection already belongs to DFO; bands include flooded, jrc_perm_water, clear_views. 2000–2018, 913 large events. No GFD raster bulk download is necessary for the pilot. GEE sampling exports small tables, not global multiband TIFFs. Source: https://developers.google.com/earth-engine/datasets/catalog/GLOBAL_FLOOD_DB_MODIS_EVENTS_V1

Priority 1: recent Sentinel-1 Copernicus GFM flood polygons and event observation quality at https://emergency.copernicus.eu/data/ ; city/event selected downloads, not 26 years globally. UNOSAT flood activations https://unosat.org/ and Copernicus Mapping activation metadata https://mapping.emergency.copernicus.eu/about/how-to-harvest-cems-mapping-data/ complement case-based validation. Missing satellite revisit/coverage does not mean no flood.

Already available locally: DFO masterlist SHP with .prj; FloodArchive_region SHP without .prj and thus DISABLED until CRS is independently verified; legacy 1989–2017 flood points with uncertain source lineage thus DISABLED by default. CaMa-Flood simulation, HydroATLAS, MERIT, WorldPop, HFP2024, WSF2019, CRU and Köppen are **covariates**, NOT independently verified urban pluvial events. GHSL E2020 V2 remains an important covariate and should be produced with the patched row-part GEE workflow, but it is not a physical flooding observation.

## Before running

Maintain E:\Global_flooding local .gitignore modifications, do not git reset/clean. Read full Figure1 original provenance and confirm the original primary geocoded parquet has event_id, grid_id, start_day, high, longitude and latitude fields. Keep all old results untouched; use fresh output folders for new configurations.

## PowerShell 1: code, tests, archive readiness

    Set-Location E:\Global_flooding
    git status --short
    git pull --ff-only origin main
    $BASE="E:\Global_flooding"
    $PY="$BASE\.venv\Scripts\python.exe"
    $R35="$BASE\pipelines\r35"
    $F1="$BASE\grid10\figure1"
    $CFG="$BASE\grid10\fig1_sources.local.json"
    $OUT="$BASE\grid10\figure1_reference_v3"
    & $PY -m pip install numpy pandas scipy geopandas pyogrio shapely pyproj matplotlib rasterio pyarrow openpyxl earthengine-api
    & $PY -m pytest -q tests/test_stage73_fig1_multisource.py tests/test_stage77_79_fig1_physical.py
    if($LASTEXITCODE -ne 0){throw "P0 synthetic test failure"}
    if(-not(Test-Path $CFG)){Copy-Item "$R35\fig1_sources.example.json" $CFG}
    & $PY "$R35\75_fig1_reference_preflight.py" --config "$CFG" --out "$OUT"
    if($LASTEXITCODE -ne 0){throw "P0 reference preflight blocked"}

Default config enables only the DFO masterlist archive with verified .prj. To use FloodArchive_region, first verify the original coordinate reference system from a credible original dataset metadata record. Set confirmed_crs and crs_evidence in local JSON to document that *independent* evidence; do not guess EPSG:4326. Do not count legacy flood point SHP as separate family until source lineage is established. When GFID is downloaded, verify actual workbook column names via preflight, then enable usgs_gfid and run into a new output path. A year-only record is excluded from day-scale candidate matching.

## PowerShell 2: archive candidates and urban sampling coverage

    $REPORTS="$F1\figure1_primary_geocoded_reports.parquet"
    $CITIES="$F1\figure1_city_audit.csv"
    $DOM="$BASE\grid10\cities\62c_dominant_city_grid10.parquet"
    foreach($p in @($REPORTS,$CITIES,$DOM)){if(-not(Test-Path $p)){throw "Missing input $p"}}
    & $PY "$R35\73_fig1_multisource_audit.py" --config "$CFG" --reports "$REPORTS" --city-audit "$CITIES" --dominant-city "$DOM" --out "$OUT" --day-window 7
    if($LASTEXITCODE -ne 0){throw "Archive matching failed"}
    Import-Csv "$OUT\73_source_summary.csv" | Format-Table

    $CITYOUT="$BASE\grid10\figure1_city_selection_v3"
    & $PY "$R35\80_fig1_city_selection.py" --city-audit "$CITIES" --out "$CITYOUT" --pilot-per-stratum 4
    if($LASTEXITCODE -ne 0){throw "UCDB denominator failed"}

Only when the real GHSL E2020 V2 grid joins have passed QC:
    $CONTEXT="$BASE\grid10\context_v2\64b_ghsl_E2020_grid10.parquet"
    $KOPPEN="$BASE\koppen_geiger_tif\1991_2020\koppen_geiger_0p1.tif"
    if((Test-Path $CONTEXT) -and (Test-Path $KOPPEN)){
      & $PY "$R35\80_fig1_city_selection.py" --city-audit "$CITIES" --dominant-city "$DOM" --ghsl2020 "$CONTEXT" --koppen "$KOPPEN" --out "$BASE\grid10\figure1_city_selection_context_v3"
      if($LASTEXITCODE -ne 0){throw "City context failed"}
    }

Stage80 describes all official UCDB city observation-eligibility rates, unknown continent assignments, proxy population coverage, climate strata and geographically stratified candidate cities for GFM/UNOSAT manual validation. Not a model of verified flood-event detection probability.

## PowerShell 3: GFD GEE physical pilot

GEE only necessary here for the physical satellite sampled-event extraction. Stage76 is a low-cost metadata request and Stage77 is local. Stage78 by default is a DRY RUN. Before submitting, check available Earth Engine quota and previously submitted tasks. No duplicated submissions.

    $PROJECT="eco-sector-503721-b7"
    $GP="$BASE\grid10\gfd_physical_v3"
    & $PY "$R35\76_gfd_dfo_satellite_manifest.py" --project "$PROJECT" --out "$GP"
    if($LASTEXITCODE -ne 0){throw "GFD catalogue unavailable"}
    & $PY "$R35\77_gfd_candidate_requests.py" --reports "$REPORTS" --gfd-manifest "$GP\76_gfd_dfo_selected_satellite_event_manifest.csv" --out "$GP" --max-km 100 --days 7 --event-limit 8 --limit 80 --per-event 10
    if($LASTEXITCODE -ne 0){throw "GFD candidate check failed"}
    $REQUEST="$GP\77_gfd_city_grid_requests.csv"
    & $PY "$R35\78_gfd_gee_physical_pilot.py" --project "$PROJECT" --requests "$REQUEST" --out "$GP" --max-events 8 --max-rows 160
    if($LASTEXITCODE -ne 0){throw "GFD task plan failed"}
    Get-Content "$GP\78_gfd_export_plan.json"

Only AFTER reviewing the above and ensuring no conflicting existing GEE task, submit ONCE:

    & $PY "$R35\78_gfd_gee_physical_pilot.py" --project "$PROJECT" --requests "$REQUEST" --out "$GP" --max-events 8 --max-rows 160 --submit
    if($LASTEXITCODE -ne 0){throw "Cloud submission blocked"}

Download all completed Drive CSVs to E:\Global_flooding\grid10\gfd_physical_v3\gee_csv and merge:

    & $PY "$R35\79_gfd_merge_physical.py" --requests "$REQUEST" --csv-root "$GP\gee_csv" --out "$GP\merged" --minimum-coverage 0.20
    if($LASTEXITCODE -ne 0){throw "Satellite observation incomplete or inconsistent"}
    Get-Content "$GP\merged\79_gfd_observation_qc.json"

Interpretation:
- UNOBSERVED = no adequate valid clear satellite observations; never a zero/flood-free classification.
- OBSERVED_NO_GFD_FLOOD_PIXEL = enough clear pixels, no satellite water detected by this MODIS product, not a disproof of urban pluvial flood.
- GFD_FLOOD_PIXELS_PRESENT = nonpermanent flood water pixels observed somewhere in 10km cell during DFO-selected event; not verified news report or specific drainage failure.

The GFD pilot is purposive (near DFO centroid and news reports) and cannot estimate global recall, precision or observation probability. Follow with geographically stratified GFM/UNOSAT city-event manual verification and include no-news urban cells with clear satellite coverage as an observation-process comparison; still do not label absence of news as absence of flood.

## Primary output contract

75_reference_readiness.json; 73_candidate_pairs.parquet, 73_source_summary.csv, 73_family_summary.csv, 73_city_support.csv; 80_city_observation_profile.csv, 80_continent_screened_fraction.csv, 80_stratified_validation_city_candidates.csv; 76_gfd_dfo_selected_satellite_event_manifest.csv; 77_gfd_city_grid_requests.csv; 78_gfd_export_plan.json; 79_gfd_physical_observation_grid10.parquet, 79_gfd_observation_qc.json.

Figure1 a–f remains the original verified graphic until the actual E-drive archive data, GFID workbook and downloaded GEE results have been assessed. New stages do not fabricate a replacement Figure1 PNG. Required publication gates include record-source duplication review, sensitivity to +/-1/3/7/14 days and 25/50km, exact geocode uncertainty, coverage variation by year and climate/population, independent physical flood type verification, and resolution of 387 UCDB city country metadata gaps. CI is synthetic, not the local data validation.
