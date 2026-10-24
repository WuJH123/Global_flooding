# Upload Stage20 yearly standard_requests_YYYY.csv to GCS and ingest EE tables.
# Review PROJECT, BUCKET first. Uses the SAME EE Cloud project (no quota splitting).
$ErrorActionPreference = 'Stop'
$PROJECT = 'eco-sector-503721-b7'
$BUCKET = 'gs://groundsource-climate-503721-b7'
$CSV_DIR = 'E:\Global_flooding\standard_calendar_20b\gee_upload_standard'

if (-not (Test-Path $CSV_DIR)) { throw "Missing local directory: $CSV_DIR" }
gcloud config set project $PROJECT
if ($LASTEXITCODE -ne 0) { throw 'gcloud configuration failed' }

foreach ($YEAR in 2000..2026) {
    $name = "standard_requests_$YEAR.csv"
    $local = Join-Path $CSV_DIR $name
    $asset = "projects/$PROJECT/assets/standard_requests_$YEAR"
    $gs = "$BUCKET/standard_requests/$name"
    if (-not (Test-Path $local)) { throw "File missing: $local" }

    # Asset info checks only completed/visible assets.
    # IMPORTANT: Do not re-run while previous table ingest is still pending.
    & earthengine asset info $asset *> $null
    if ($LASTEXITCODE -eq 0) {
       Write-Host "[SKIP ASSET EXISTS] $asset"
       continue
    }
    Write-Host "[COPY] $local -> $gs"
    & gcloud storage cp $local $gs
    if ($LASTEXITCODE -ne 0) { throw "Failed GCS copy: $YEAR" }
    Write-Host "[INGEST] $asset"
    & earthengine upload table "--asset_id=$asset" $gs
    if ($LASTEXITCODE -ne 0) { throw "Failed EE ingestion submission: $YEAR" }
}
Write-Host '[DONE] Ingestion tasks submitted; WAIT for ingestion completion before Stage21 --submit.'
