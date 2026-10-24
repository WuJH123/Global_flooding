from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import ee
import pandas as pd


# ============================================================
# 0. CONFIG
# ============================================================

PROJECT = "eco-sector-503721-b7"

ASSET_ROOT = (
    f"projects/{PROJECT}/assets"
)


MANIFEST_PATH = Path(
    r"E:\Global_flooding\climate_requests_chunked"
    r"\climate_request_chunk_manifest.csv"
)


LOG_PATH = Path(
    r"E:\Global_flooding\climate_requests_chunked"
    r"\climate_export_submission_log.csv"
)


# ------------------------------------------------------------
# 2020 has already been successfully calculated.
# Do not run it again.
# ------------------------------------------------------------

SKIP_YEARS = {
    2020
}


# ------------------------------------------------------------
# Set to None for ALL remaining tasks.
#
# For the very first Python test, I recommend:
#
# SUBMIT_LIMIT = 1
#
# After that task works:
#
# SUBMIT_LIMIT = None
#
# ------------------------------------------------------------

SUBMIT_LIMIT = None


# ------------------------------------------------------------
# Delay between task.start()
#
# This does NOT control EE compute concurrency.
# It only avoids hammering the API with hundreds
# of task-start requests at once.
# ------------------------------------------------------------

SECONDS_BETWEEN_SUBMISSIONS = 0.5


# ------------------------------------------------------------
# If a task with the same description already exists:
#
# READY/RUNNING/COMPLETED -> always skip
#
# FAILED ->
# False: skip
# True: resubmit
# ------------------------------------------------------------

RETRY_FAILED = True


# ============================================================
# 1. EARTH ENGINE INITIALIZATION
# ============================================================

print("=" * 80)
print("EARTH ENGINE INITIALIZATION")
print("=" * 80)


try:

    ee.Initialize(
        project=PROJECT
    )

except Exception as exc:

    raise RuntimeError(
        "\nEarth Engine initialization failed.\n"
        "\nRun first:\n"
        "earthengine authenticate\n"
        f"earthengine set_project {PROJECT}\n"
    ) from exc


print(
    "Earth Engine project:",
    PROJECT
)


# ============================================================
# 2. LOAD TASK MANIFEST
# ============================================================

if not MANIFEST_PATH.exists():

    raise FileNotFoundError(
        MANIFEST_PATH
    )


manifest = pd.read_csv(
    MANIFEST_PATH
)


required_manifest_cols = {

    "year",

    "month",

    "request_chunk",

    "requests",
}


missing_cols = (
    required_manifest_cols
    -
    set(manifest.columns)
)


if missing_cols:

    raise RuntimeError(

        "Manifest is missing columns:\n"
        f"{sorted(missing_cols)}"
    )


manifest["year"] = (
    manifest["year"]
    .astype(int)
)

manifest["month"] = (
    manifest["month"]
    .astype(int)
)

manifest["request_chunk"] = (
    manifest["request_chunk"]
    .astype(int)
)

manifest["requests"] = (
    manifest["requests"]
    .astype(int)
)


manifest = (

    manifest

    .sort_values(
        [
            "year",
            "month",
            "request_chunk",
        ]
    )

    .reset_index(
        drop=True
    )
)


print("\nManifest:")
print(
    f"{len(manifest):,} total tasks"
)


print(
    "Total requests:",
    f"{manifest['requests'].sum():,}"
)


# ============================================================
# 3. FILTER YEARS
# ============================================================

production_manifest = (

    manifest[
        ~manifest[
            "year"
        ].isin(
            SKIP_YEARS
        )
    ]

    .copy()

    .reset_index(
        drop=True
    )
)


print(
    "\nSkipped years:",
    sorted(
        SKIP_YEARS
    )
)


print(
    "Tasks remaining:",
    f"{len(production_manifest):,}"
)


print(
    "Requests remaining:",
    f"{production_manifest['requests'].sum():,}"
)


# ============================================================
# 4. VERIFY ALL INPUT ASSETS
# ============================================================

print("\n" + "=" * 80)
print("VERIFY INPUT ASSETS")
print("=" * 80)


years_to_run = sorted(
    production_manifest[
        "year"
    ].unique()
)


missing_assets = []


for year in years_to_run:

    asset_id = (
        f"{ASSET_ROOT}/"
        f"climate_requests_{year}"
    )


    try:

        ee.data.getAsset(
            asset_id
        )

        print(
            f"[OK] {year}"
        )

    except Exception:

        missing_assets.append(
            (
                year,
                asset_id
            )
        )

        print(
            f"[MISSING] {year}"
        )


if missing_assets:

    print(
        "\nThe following assets are not yet ready:"
    )

    for year, asset_id in missing_assets:

        print(
            year,
            asset_id
        )


    raise RuntimeError(

        "\nSome annual request assets are still "
        "missing or ingesting.\n"
        "Wait until they are completed before "
        "running climate extraction."
    )


print(
    "\nAll required assets are available."
)


# ============================================================
# 5. DATASETS
# ============================================================

IMERG = (

    ee.ImageCollection(
        "NASA/GPM_L3/IMERG_V07"
    )

    .select(
        "precipitation"
    )
)


ERA5 = ee.ImageCollection(
    "ECMWF/ERA5_LAND/HOURLY"
)


CLIMATE_SCALE = 11132


# ============================================================
# 6. ORIGINAL 20-km GRID
# ============================================================

GRID_SIZE_M = 20000


EASE6933_WKT = (
    'PROJCS["WGS 84 / NSIDC EASE-Grid 2.0 Global",'
    'GEOGCS["WGS 84",'
    'DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,'
    'AUTHORITY["EPSG","7030"]],'
    'AUTHORITY["EPSG","6326"]],'
    'PRIMEM["Greenwich",0,'
    'AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,'
    'AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4326"]],'
    'PROJECTION["Cylindrical_Equal_Area"],'
    'PARAMETER["standard_parallel_1",30],'
    'PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,'
    'AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],'
    'AXIS["Northing",NORTH],'
    'AUTHORITY["EPSG","6933"]]'
)


GRID_PROJ = ee.Projection(
    EASE6933_WKT
)


# ============================================================
# 7. REBUILD ORIGINAL 20-km GRID
# ============================================================

def rebuild_grid(feature):

    f = ee.Feature(
        feature
    )


    gx = ee.Number(
        f.get(
            "grid_x"
        )
    )


    gy = ee.Number(
        f.get(
            "grid_y"
        )
    )


    xmin = gx.multiply(
        GRID_SIZE_M
    )


    ymin = gy.multiply(
        GRID_SIZE_M
    )


    geometry = ee.Geometry.Rectangle(

        ee.List(
            [

                xmin,

                ymin,

                xmin.add(
                    GRID_SIZE_M
                ),

                ymin.add(
                    GRID_SIZE_M
                ),

            ]
        ),

        GRID_PROJ,

        False,

        True,
    )


    return ee.Feature(

        geometry,

        f.toDictionary()
    )


# ============================================================
# 8. IMERG 30-MINUTE ACCUMULATION
# ============================================================

def get_imerg_30min(
    date
):

    date = ee.Date(
        date
    )


    collection = IMERG.filterDate(

        date,

        date.advance(
            1,
            "day"
        )
    )


    def convert_image(img):

        img = ee.Image(
            img
        )


        return (

            img

            .max(0)

            .multiply(
                0.5
            )

            .rename(
                "p"
            )

            .copyProperties(

                img,

                [
                    "system:time_start"
                ]
            )
        )


    return collection.map(
        convert_image
    )


# ============================================================
# 9. IMERG ROLLING MAX
# ============================================================

def rolling_imerg_max(

    date,

    accum_30,

    window_steps,

    output_name,
):

    date = ee.Date(
        date
    )


    last_start = (

        ee.Number(
            48
        )

        .subtract(
            window_steps
        )
    )


    starts = ee.List.sequence(

        0,

        last_start,
    )


    def build_window(i):

        i = ee.Number(
            i
        )


        start = date.advance(

            i.multiply(
                30
            ),

            "minute",
        )


        end = start.advance(

            ee.Number(
                window_steps
            )
            .multiply(
                30
            ),

            "minute",
        )


        return (

            accum_30

            .filterDate(
                start,
                end
            )

            .sum()

            .rename(
                output_name
            )
        )


    images = starts.map(
        build_window
    )


    return (

        ee.ImageCollection
        .fromImages(
            images
        )

        .max()

        .rename(
            output_name
        )
    )


# ============================================================
# 10. IMERG METRICS
# ============================================================

def build_imerg_metrics(
    date
):

    date = ee.Date(
        date
    )


    p30 = get_imerg_30min(
        date
    )


    p30max = (

        p30
        .max()
        .rename(
            "imerg_p30_max_mm"
        )
    )


    p1h = rolling_imerg_max(

        date,

        p30,

        2,

        "imerg_p1h_max_mm",
    )


    p3h = rolling_imerg_max(

        date,

        p30,

        6,

        "imerg_p3h_max_mm",
    )


    p6h = rolling_imerg_max(

        date,

        p30,

        12,

        "imerg_p6h_max_mm",
    )


    p12h = rolling_imerg_max(

        date,

        p30,

        24,

        "imerg_p12h_max_mm",
    )


    p24 = (

        p30
        .sum()
        .rename(
            "imerg_p24_utc_mm"
        )
    )


    return ee.Image.cat(
        [

            p30max,

            p1h,

            p3h,

            p6h,

            p12h,

            p24,

        ]
    )


# ============================================================
# 11. ERA5 FLOW SUM
# ============================================================

def era5_flow_sum(

    start_date,

    end_date,

    band_name,

    output_name,
):

    start_date = ee.Date(
        start_date
    )


    end_date = ee.Date(
        end_date
    )


    collection = (

        ERA5

        .filterDate(

            start_date,

            end_date,
        )

        .select(
            band_name
        )
    )


    def process_image(img):

        return (

            ee.Image(
                img
            )

            .max(0)

            .multiply(
                1000
            )
        )


    return (

        collection

        .map(
            process_image
        )

        .sum()

        .rename(
            output_name
        )
    )


# ============================================================
# 12. API
# ============================================================

API_LAMBDA = 0.85


def build_api(

    date,

    n_days,

    output_name,
):

    date = ee.Date(
        date
    )


    day_numbers = ee.List.sequence(
        1,
        n_days
    )


    def weighted_day(k):

        k = ee.Number(
            k
        )


        start = date.advance(

            k.multiply(
                -1
            ),

            "day",
        )


        daily_p = era5_flow_sum(

            start,

            start.advance(
                1,
                "day"
            ),

            "total_precipitation_hourly",

            output_name,
        )


        weight = (

            ee.Number(
                API_LAMBDA
            )

            .pow(
                k.subtract(
                    1
                )
            )
        )


        return (

            daily_p

            .multiply(
                weight
            )

            .rename(
                output_name
            )
        )


    images = day_numbers.map(
        weighted_day
    )


    return (

        ee.ImageCollection
        .fromImages(
            images
        )

        .sum()

        .rename(
            output_name
        )
    )


# ============================================================
# 13. ERA5 METRICS
# ============================================================

def build_era5_metrics(
    date
):

    date = ee.Date(
        date
    )


    event_p24 = era5_flow_sum(

        date,

        date.advance(
            1,
            "day"
        ),

        "total_precipitation_hourly",

        "era5_event_p24_mm",
    )


    event_runoff = era5_flow_sum(

        date,

        date.advance(
            1,
            "day"
        ),

        "runoff_hourly",

        "era5_event_runoff24_mm",
    )


    event_surface_runoff = era5_flow_sum(

        date,

        date.advance(
            1,
            "day"
        ),

        "surface_runoff_hourly",

        "era5_event_surface_runoff24_mm",
    )


    ant_p3 = era5_flow_sum(

        date.advance(
            -3,
            "day"
        ),

        date,

        "total_precipitation_hourly",

        "era5_ant_p3d_mm",
    )


    ant_p7 = era5_flow_sum(

        date.advance(
            -7,
            "day"
        ),

        date,

        "total_precipitation_hourly",

        "era5_ant_p7d_mm",
    )


    ant_runoff3 = era5_flow_sum(

        date.advance(
            -3,
            "day"
        ),

        date,

        "runoff_hourly",

        "era5_ant_runoff3d_mm",
    )


    ant_runoff7 = era5_flow_sum(

        date.advance(
            -7,
            "day"
        ),

        date,

        "runoff_hourly",

        "era5_ant_runoff7d_mm",
    )


    previous_day = ERA5.filterDate(

        date.advance(
            -1,
            "day"
        ),

        date,
    )


    sm1 = (

        previous_day

        .select(
            "volumetric_soil_water_layer_1"
        )

        .mean()

        .rename(
            "era5_sm1_prevday"
        )
    )


    sm2 = (

        previous_day

        .select(
            "volumetric_soil_water_layer_2"
        )

        .mean()

        .rename(
            "era5_sm2_prevday"
        )
    )


    api3 = build_api(

        date,

        3,

        "era5_api3_mm",
    )


    api7 = build_api(

        date,

        7,

        "era5_api7_mm",
    )


    return ee.Image.cat(
        [

            event_p24,

            event_runoff,

            event_surface_runoff,

            ant_p3,

            ant_p7,

            ant_runoff3,

            ant_runoff7,

            api3,

            api7,

            sm1,

            sm2,

        ]
    )


# ============================================================
# 14. SPATIAL REDUCER
# ============================================================

SPATIAL_REDUCER = (

    ee.Reducer
    .mean()

    .combine(

        reducer2=
            ee.Reducer.max(),

        sharedInputs=
            True,
    )
)


# ============================================================
# 15. EXTRACT ONE DATE
# ============================================================

def extract_one_date(

    date,

    request_features,
):

    date = ee.Date(
        date
    )


    imerg = build_imerg_metrics(
        date
    )


    era = build_era5_metrics(
        date
    )


    imerg_proj = (

        imerg
        .select(0)
        .projection()
    )


    with_imerg = imerg.reduceRegions(

        collection=
            request_features,

        reducer=
            SPATIAL_REDUCER,

        scale=
            CLIMATE_SCALE,

        crs=
            imerg_proj,

        tileScale=
            4,
    )


    era_proj = (

        era
        .select(0)
        .projection()
    )


    with_era = era.reduceRegions(

        collection=
            with_imerg,

        reducer=
            SPATIAL_REDUCER,

        scale=
            CLIMATE_SCALE,

        crs=
            era_proj,

        tileScale=
            4,
    )


    return with_era


# ============================================================
# 16. PROCESS MONTH × CHUNK
# ============================================================

def process_subset(
    raw_subset
):

    dates = (

        ee.List(
            raw_subset.aggregate_array(
                "date_str"
            )
        )

        .distinct()

        .sort()
    )


    def process_date(ds):

        ds = ee.String(
            ds
        )


        date = ee.Date.parse(

            "YYYY-MM-dd",

            ds,
        )


        day_features = (

            raw_subset

            .filter(
                ee.Filter.eq(
                    "date_str",
                    ds
                )
            )

            .map(
                rebuild_grid
            )
        )


        return extract_one_date(

            date,

            day_features,
        )


    nested = dates.map(
        process_date
    )


    return (

        ee.FeatureCollection(
            nested
        )

        .flatten()
    )


# ============================================================
# 17. OUTPUT FIELDS
# ============================================================

OUTPUT_FIELDS = [

    "climate_id",

    "grid_id",

    "grid_x",

    "grid_y",

    "sample_date",

    "date_str",

    "year",

    "month",

    "day",

    "request_chunk",

    "n_matched_references",

    "used_as_case",

    "used_as_control_count",


    "imerg_p30_max_mm_mean",
    "imerg_p30_max_mm_max",

    "imerg_p1h_max_mm_mean",
    "imerg_p1h_max_mm_max",

    "imerg_p3h_max_mm_mean",
    "imerg_p3h_max_mm_max",

    "imerg_p6h_max_mm_mean",
    "imerg_p6h_max_mm_max",

    "imerg_p12h_max_mm_mean",
    "imerg_p12h_max_mm_max",

    "imerg_p24_utc_mm_mean",
    "imerg_p24_utc_mm_max",


    "era5_event_p24_mm_mean",
    "era5_event_p24_mm_max",

    "era5_event_runoff24_mm_mean",
    "era5_event_runoff24_mm_max",

    "era5_event_surface_runoff24_mm_mean",
    "era5_event_surface_runoff24_mm_max",

    "era5_ant_p3d_mm_mean",
    "era5_ant_p3d_mm_max",

    "era5_ant_p7d_mm_mean",
    "era5_ant_p7d_mm_max",

    "era5_ant_runoff3d_mm_mean",
    "era5_ant_runoff3d_mm_max",

    "era5_ant_runoff7d_mm_mean",
    "era5_ant_runoff7d_mm_max",

    "era5_api3_mm_mean",
    "era5_api3_mm_max",

    "era5_api7_mm_mean",
    "era5_api7_mm_max",

    "era5_sm1_prevday_mean",
    "era5_sm1_prevday_max",

    "era5_sm2_prevday_mean",
    "era5_sm2_prevday_max",
]


# ============================================================
# 18. REMOVE GEOMETRY
# ============================================================

def clean_output(fc):

    def clean_feature(f):

        f = ee.Feature(
            f
        )


        return ee.Feature(

            None,

            f.toDictionary(
                OUTPUT_FIELDS
            )
        )


    return fc.map(
        clean_feature
    )


# ============================================================
# 19. EXISTING EE TASKS
# ============================================================

print("\n" + "=" * 80)
print("CHECK EXISTING EXPORT TASKS")
print("=" * 80)


try:

    remote_tasks = (
        ee.data.getTaskList()
    )

except Exception:

    remote_tasks = []


existing_tasks = {}


for item in remote_tasks:

    description = item.get(
        "description"
    )


    state = item.get(
        "state"
    )


    if description:

        existing_tasks[
            description
        ] = state


print(
    f"Existing remote tasks scanned: "
    f"{len(remote_tasks):,}"
)


# ============================================================
# 20. LOCAL SUBMISSION LOG
# ============================================================

log_columns = [

    "description",

    "year",

    "month",

    "chunk",

    "requests",

    "task_id",

    "submitted_at",
]


if LOG_PATH.exists():

    local_log = pd.read_csv(
        LOG_PATH
    )

else:

    local_log = pd.DataFrame(
        columns=log_columns
    )


# ============================================================
# 21. BUILD + START ALL EXPORTS
# ============================================================

print("\n" + "=" * 80)
print("AUTOMATIC CLIMATE EXPORT SUBMISSION")
print("=" * 80)


submitted_count = 0

skipped_count = 0


for row in production_manifest.itertuples(
    index=False
):

    year = int(
        row.year
    )


    month = int(
        row.month
    )


    chunk = int(
        row.request_chunk
    )


    requests = int(
        row.requests
    )


    mm = f"{month:02d}"


    description = (

        f"Climate_"
        f"{year}_"
        f"M{mm}_"
        f"C{chunk}"
    )


    # --------------------------------------------------------
    # Already exists remotely?
    # --------------------------------------------------------

    current_state = (
        existing_tasks.get(
            description
        )
    )


    if current_state in {

        "READY",

        "RUNNING",

        "COMPLETED",

    }:

        print(

            f"[SKIP] "
            f"{description:<24} "
            f"{current_state}"
        )


        skipped_count += 1

        continue


    if (
        current_state == "FAILED"
        and not RETRY_FAILED
    ):

        print(

            f"[SKIP FAILED] "
            f"{description}"
        )


        skipped_count += 1

        continue


    # --------------------------------------------------------
    # Build annual collection
    # --------------------------------------------------------

    asset_id = (

        f"{ASSET_ROOT}/"
        f"climate_requests_{year}"
    )


    raw_requests = (
        ee.FeatureCollection(
            asset_id
        )
    )


    # --------------------------------------------------------
    # Exact manifest subset
    # --------------------------------------------------------

    subset = (

        raw_requests

        .filter(
            ee.Filter.eq(
                "month",
                month
            )
        )

        .filter(
            ee.Filter.eq(
                "request_chunk",
                chunk
            )
        )
    )


    # --------------------------------------------------------
    # Build computation graph
    # --------------------------------------------------------

    result = process_subset(
        subset
    )


    output = clean_output(
        result
    )


    # --------------------------------------------------------
    # Create Drive export
    # --------------------------------------------------------

    task = ee.batch.Export.table.toDrive(

        collection=
            output,


        description=
            description,


        folder=
            f"Groundsource_Climate_{year}",


        fileNamePrefix=(

            f"climate_"
            f"{year}_"
            f"m{mm}_"
            f"c{chunk}"

        ),


        fileFormat=
            "CSV",


        selectors=
            OUTPUT_FIELDS,
    )


    # ========================================================
    # THIS IS THE IMPORTANT AUTOMATION STEP
    # ========================================================

    task.start()


    task_id = (
        task.id
    )


    submitted_at = (
        datetime.now()
        .isoformat(
            timespec="seconds"
        )
    )


    print(

        f"[STARTED] "
        f"{description:<24} "
        f"requests={requests:>6,} "
        f"id={task_id}"
    )


    # --------------------------------------------------------
    # Update local log immediately
    # --------------------------------------------------------

    new_row = pd.DataFrame(
        [
            {

                "description":
                    description,

                "year":
                    year,

                "month":
                    month,

                "chunk":
                    chunk,

                "requests":
                    requests,

                "task_id":
                    task_id,

                "submitted_at":
                    submitted_at,
            }
        ]
    )


    local_log = pd.concat(

        [
            local_log,
            new_row
        ],

        ignore_index=True,
    )


    local_log.to_csv(

        LOG_PATH,

        index=False,

        encoding="utf-8-sig",
    )


    submitted_count += 1


    # --------------------------------------------------------
    # First-run safeguard
    # --------------------------------------------------------

    if (

        SUBMIT_LIMIT is not None

        and

        submitted_count
        >=
        SUBMIT_LIMIT

    ):

        print(

            "\nSUBMIT_LIMIT reached."
        )

        break


    time.sleep(
        SECONDS_BETWEEN_SUBMISSIONS
    )


# ============================================================
# 22. SUMMARY
# ============================================================

print("\n" + "=" * 80)
print("SUBMISSION FINISHED")
print("=" * 80)


print(
    "Submitted:",
    submitted_count
)


print(
    "Skipped:",
    skipped_count
)


print(
    "\nLocal task log:"
)

print(
    LOG_PATH
)


print(
    "\nEarth Engine tasks will now continue "
    "running on Google's servers."
)