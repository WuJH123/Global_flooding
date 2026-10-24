# ============================================================
# Groundsource yearly climate-request batch uploader
#
# Local CSV
#      ↓
# Google Cloud Storage
#      ↓
# Earth Engine table assets
#
# Existing 2020 asset is skipped.
# ============================================================

$ErrorActionPreference = "Stop"


# ============================================================
# CONFIG
# ============================================================

$PROJECT = "eco-sector-503721-b7"

$LOCAL_DIR =
    "E:\Global_flooding\climate_requests_chunked\gee_upload"

# ------------------------------------------------------------
# CHANGE THIS if your bucket has another name.
# Do NOT include a trailing slash here.
# ------------------------------------------------------------

$GCS_ROOT =
    "gs://groundsource-climate-503721-b7/climate_requests"

$ASSET_ROOT =
    "projects/$PROJECT/assets"


# ============================================================
# YEARS
# ============================================================

$YEARS = 2000..2026


Write-Host ""
Write-Host "============================================================"
Write-Host "Groundsource climate-request batch upload"
Write-Host "============================================================"

Write-Host "Project:    $PROJECT"
Write-Host "Local dir:  $LOCAL_DIR"
Write-Host "GCS root:   $GCS_ROOT"
Write-Host "Asset root: $ASSET_ROOT"
Write-Host ""


# ============================================================
# CHECK LOCAL DIRECTORY
# ============================================================

if (!(Test-Path $LOCAL_DIR)) {

    throw "Local directory not found: $LOCAL_DIR"
}


# ============================================================
# PROCESS YEAR BY YEAR
# ============================================================

foreach ($YEAR in $YEARS) {

    Write-Host ""
    Write-Host "------------------------------------------------------------"
    Write-Host "YEAR $YEAR"
    Write-Host "------------------------------------------------------------"


    $FILE_NAME =
        "climate_requests_$YEAR.csv"


    $LOCAL_FILE =
        Join-Path $LOCAL_DIR $FILE_NAME


    $GCS_FILE =
        "$GCS_ROOT/$FILE_NAME"


    $ASSET_ID =
        "$ASSET_ROOT/climate_requests_$YEAR"


    # --------------------------------------------------------
    # Validate local CSV
    # --------------------------------------------------------

    if (!(Test-Path $LOCAL_FILE)) {

        throw "Missing local file: $LOCAL_FILE"
    }


    # --------------------------------------------------------
    # 2020 already exists in Earth Engine.
    #
    # Do NOT upload or overwrite it.
    # --------------------------------------------------------

    if ($YEAR -eq 2020) {

        Write-Host "2020 already exists in Earth Engine."
        Write-Host "Skipping 2020."

        continue
    }


    # --------------------------------------------------------
    # Step 1 — upload CSV to Cloud Storage
    # --------------------------------------------------------

    Write-Host ""
    Write-Host "[1/2] Uploading to GCS:"
    Write-Host "$GCS_FILE"


    gcloud storage cp `
        "$LOCAL_FILE" `
        "$GCS_FILE"


    if ($LASTEXITCODE -ne 0) {

        throw "GCS upload failed for $YEAR"
    }


    # --------------------------------------------------------
    # Step 2 — start Earth Engine table ingestion
    # --------------------------------------------------------

    Write-Host ""
    Write-Host "[2/2] Starting Earth Engine ingestion:"
    Write-Host "$ASSET_ID"


    earthengine upload table `
        --asset_id="$ASSET_ID" `
        "$GCS_FILE"


    if ($LASTEXITCODE -ne 0) {

        throw "Earth Engine ingestion submission failed for $YEAR"
    }


    Write-Host ""
    Write-Host "Submitted: $YEAR"

}


Write-Host ""
Write-Host "============================================================"
Write-Host "ALL UPLOAD TASKS SUBMITTED"
Write-Host "============================================================"

Write-Host ""
Write-Host "Check with:"
Write-Host ""
Write-Host "earthengine task list -l"