# GF10 Figure 1 v2 | multiple reference archives

Stage73 is **spatiotemporal candidate corroboration**, not verified inundation. It reads GF10 Figure1 high-specificity reports and links each news UUID to external flood archive geometries with a selectable date window. Source family is preserved; DFO FloodArchive and DFO masterlist belong to one DFO family, and USGS GFID DFO records belong to that SAME family. No-match does not mean no-flood. Polygon archive regions and GFID representative coordinates are NOT precise pluvial flood footprints. This stage never edits upstream data or submits GEE tasks.

## Existing local archives from project history

- E:\数据集\全球洪水点位信息Global Active Archive of Large Flood Events, 1985-Present\FloodArchive_region.shp
- E:\数据集\大型洪水事件全球活跃档案（DFO）\wlf_nhr_fl_dfomasterlist_20190418\wlf_nhr_fl_dfomasterlist_20190418.shp
- D:\博士资料\GIS材料\全球数据\全球重大洪水灾害典型案例数据集（2018.01-2018.12）\Summary\1989-2017 flood distribution point.shp

The third legacy data lineage is **not verified**: do not treat it as independent evidence. Check .dbf/.shx/.prj and real date columns before running. Already held CaMa-Flood, HydroATLAS, MERIT, CRU, WorldPop, HFP, WSF, UCDB and Köppen are hydrological/selection-bias covariates, NOT additional confirmed urban pluvial events. Do not mix historical 20km 31b/31c counts into GF10 10km denominators.

## Additional publicly available sources

- USGS GFID https://doi.org/10.5066/P135ARGI : 136,642 aggregated DFO/DesInventar/EM-DAT/HANZE records, not 136,642 independent confirmations; optional AggregatedFloodData.xlsx.
- Global Flood Database (MODIS 913 events) https://developers.google.com/earth-engine/datasets/catalog/GLOBAL_FLOOD_DB_MODIS_EVENTS_V1 : satellite modality but DFO event selection.
- Copernicus GFM https://emergency.copernicus.eu/data/ : recent Sentinel-1 flood extents; coverage and observation gaps matter.
- UNOSAT https://unosat.org/ and USFIMR https://sdml.ua.edu/usfimr/ : stratified case validation, not exhaustive global truth.
- EM-DAT https://doc.emdat.be/ and DesInventar https://db.desinventar.org/ : already represented by GFID; never double-count.
- IBTrACS: independent catalogued tropical cyclone *meteorology*, not flood extent.

## Windows PowerShell commands

From E:\Global_flooding after safely pulling main:

1. Install: .\.venv\Scripts\python.exe -m pip install geopandas pyogrio shapely pyproj pandas numpy pyarrow matplotlib openpyxl
2. Test: .\.venv\Scripts\python.exe -m pytest -q tests/test_stage73_fig1_multisource.py
3. Copy pipelines\r35\fig1_sources.example.json to grid10\fig1_sources.local.json and check all paths in the local JSON.
4. Inspect and audit with the following PowerShell block.

~~~powershell
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R35="$BASE\pipelines\r35"
$F1="$BASE\grid10\figure1"
$CFG="$BASE\grid10\fig1_sources.local.json"
$REPORTS="$F1\figure1_primary_geocoded_reports.parquet"
$CITIES="$F1\figure1_city_audit.csv"
$DOM="$BASE\grid10\cities\62c_dominant_city_grid10.parquet"
$OUT="$BASE\grid10\figure1_multisource_v2"
if(-not(Test-Path $CFG)){Copy-Item "$R35\fig1_sources.example.json" $CFG}
foreach($p in @($REPORTS,$CITIES,$DOM)){if(-not(Test-Path $p)){throw "Missing input $p"}}
& $PY "$R35\73_fig1_multisource_audit.py" --config $CFG --reports $REPORTS --city-audit $CITIES --dominant-city $DOM --out $OUT --inspect-only
if($LASTEXITCODE -ne 0){throw "Source schema audit failed"}
& $PY "$R35\73_fig1_multisource_audit.py" --config $CFG --reports $REPORTS --city-audit $CITIES --dominant-city $DOM --out $OUT --start 2000-01-01 --end 2025-12-31 --day-window 7 --batch 35000
if($LASTEXITCODE -ne 0){throw "Stage73 failed; do not publish numbers"}
Get-Content "$OUT\73_multisource_qc.json"
Import-Csv "$OUT\73_source_summary.csv" | Format-Table
& $PY "$R35\74_fig1_compile_evidence.py" --figure1 "$F1\Figure1_GF10_global_observation.png" --stage73 $OUT --out "$BASE\grid10\figure1_v2"
~~~

Inspect source date field names and CRS first. If auto date recognition fails, edit start_field/end_field in the local JSON, do not invent fields. The original Figure1 primary_geocoded_reports.parquet comes from the previous Figure1 pipeline and must retain event_id/grid_id/start_day/high/longitude/latitude. City is attached via Stage62c. For USGS GFID, download from DOI to E:\Global_flooding\grid10\reference_external\GFID\AggregatedFloodData.xlsx, enable in the LOCAL config, verify sheet, Source, Lat/Lon, dates, and family_map names, then run into a fresh output directory.

## QC products and limits

- 73_candidate_pairs.parquet: news UUID, source, source event. Candidate co-location ONLY.
- 73_source_summary.csv: per archive counts and temporal coverage envelope, NOT source recall.
- 73_family_summary.csv: events grouped by upstream source family.
- 73_city_support.csv: candidate overlaps in each UCDB city, NOT city observation accuracy.
- 73_multisource_qc.json: source checksum, valid date count, lineage and limitations.
- Figure1_multisource_audit.png/pdf: new supplementary evidence.
- Figure1_v2_two_page_observation_and_cross_reference.pdf: previous six-panel Figure 1 plus supplementary evidence page. This does NOT invent or alter original Figure 1 counts.

P0 before publication: source schemas, date errors, geolocation uncertainty, ±1/3/7/14 day and 25/50km radius sensitivity, disjoint source lineage, satellite/in-situ validation, 387 missing UCDB country assignments and reporting-bias covariates. Data-free GitHub CI does not verify local E-drive inputs.
