# Stage 21h: ONLY B008_S01 and B008_S02 second-level retry

## Verified observed input
- Original Stage21f compact plan ID: `911b8e7ff283`.
- First Stage21g rescue plan ID: `8cfafdb0b7d8`; 10 tasks, 8 successful, 2 failed according to the user's GEE screenshot.
- Failure candidates: `STD32R_8cfafdb0_B008_S01` (27,746 grid-days/214 unique dates), `STD32R_8cfafdb0_B008_S02` (24,417/213).
- Total targeted 52,163 climate requests; do NOT recalculate the other 8 Stage21g children or 8 original successful Stage21f blocks.

The screenshot shows these two tasks failed but the expanded error message is not visible. Obtain their exact status/error by Task Manager or the original task IDs before drawing a causal conclusion. A smaller per-task request/date footprint is a prudent response to the previous timeout failures.

## 1. Sync and preview (ZERO GEE API calls)

```powershell
cd E:\Global_flooding
git status --short
git pull --ff-only origin main

$PY = 'E:\Global_flooding\.venv\Scripts\python.exe'
$BASE = 'E:\Global_flooding'
$R32 = "$BASE\pipelines\r32"

& $PY "$R32\21h_retry_failed_rescue_parts.py" `
  --base "$BASE" `
  --targets 8:1,8:2 `
  --max-requests 10000 `
  --max-dates 90
```

This plan is deterministic, partitions each selected failed Stage21g child **by source date**, and confirms every original `(year,month,request_chunk,date_str)` group occurs exactly once with its original request count. It does not change any old 21f/21g file or its frozen plan.

The plan and full provenance are written into:

- `E:\Global_flooding\standard_calendar_20b\21_retry\retry_plan.csv`
- `E:\Global_flooding\standard_calendar_20b\21_retry\retry_plan_meta.json`

Copy the printed **NEW** 12-character plan ID; the previous IDs `911b8e7ff283` and `8cfafdb0b7d8` cannot authorize this retry. The exact number of jobs comes from your local date distributions; expect at least six based on the request count but don't assume six.

## 2. Submit ONE child only

```powershell
$RETRY_PLAN = '<copy the latest 21h PLAN ID>'

& $PY "$R32\21h_retry_failed_rescue_parts.py" `
  --base "$BASE" `
  --targets 8:1,8:2 `
  --max-requests 10000 `
  --max-dates 90 `
  --plan-id $RETRY_PLAN `
  --parts 0 `
  --max-tasks 1 `
  --submit
```

Production safety gates: original Stage21 reference numerical calibration `PASS` matches SHA256; original Stage21g task IDs for B008_S01 and B008_S02 are both `FAILED` on the server; annual request assets exist and have correct field types/row counts; selected retry plan ID matches; existing local/remote submission logs are respected.

**Do not start all retry tasks before this first task completes and its CSV contains the expected rows.**

## 3. Submit the rest after first success

```powershell
& $PY "$R32\21h_retry_failed_rescue_parts.py" `
  --base "$BASE" `
  --targets 8:1,8:2 `
  --max-requests 10000 `
  --max-dates 90 `
  --plan-id $RETRY_PLAN `
  --max-tasks 0 `
  --submit
```

Task names have independent prefix `STD32T_<hash>_B008_S01_T##` or `STD32T_<hash>_B008_S02_T##`. Download exports from the Google Drive folder `Groundsource_Standard_Climate_Compact_Retry2`. Submission ledger: `standard_calendar_20b\21_retry\retry_submissions.jsonl`.

If a Stage21h task also times out, do NOT resubmit it blindly with the same limits; inspect its date/grid composition and create a specific smaller recovery plan.

## 4. Merge the three levels

Keep three directories separate:
- original eight successful `STD32C` exports: `E:\Global_flooding\Standard_Climate_Compact_all`;
- first-level eight successful `STD32R` exports: `E:\Global_flooding\Standard_Climate_Compact_Rescue_all`;
- second-level completed `STD32T` exports: `E:\Global_flooding\Standard_Climate_Compact_Retry2_all`.

Do **not** place `STD32R_8cfafdb0_B008_S01` or `S02` failed/partial CSVs into the first-level successful folder.

```powershell
& $PY "$R32\22b_merge_compact_weather.py" `
  --base "$BASE" `
  --csv-root "E:\Global_flooding\Standard_Climate_Compact_all" `
  --rescue-plan "E:\Global_flooding\standard_calendar_20b\21_rescue\rescue_plan.csv" `
  --rescue-csv-root "E:\Global_flooding\Standard_Climate_Compact_Rescue_all" `
  --retry-plan "E:\Global_flooding\standard_calendar_20b\21_retry\retry_plan.csv" `
  --retry-csv-root "E:\Global_flooding\Standard_Climate_Compact_Retry2_all"
```

The merger strictly checks actual CSV row counts, every unique `climate_id`, date/grid identity, and total **433,090** missing weather requests against Stage20b master. It uses only the valid leaf exports, so two failed first-level children are replaced without repeating other work. It writes the same target `standard_calendar_22_compact\22_standard_daily_with_climate.parquet` output.

The completed global daily panel should preserve exactly **2,680,571** unique grid-dates and **650,361** onset days. Some ERA5 variables can still be missing in offshore/coastal cells; do not fill with zeros and make model-eligibility QC mandatory.
