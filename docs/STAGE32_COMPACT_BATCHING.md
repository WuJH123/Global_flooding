# Stage 3.2 compact cross-year Earth Engine weather exports

Purpose: Repack the 433,090 missing Stage20b grid–dates into fewer queued tasks. Existing ERA5FB_DAILY jobs are unrelated; the original 2,789,073 climate records are not recomputed.

## Safety
- Stage21f imports the original 21_standard_weather_submit.py weather algorithm without modifying formulas.
- Dry run is default. Submission requires a printed 12-character plan ID and numerical calibration PASS.
- Each compact task is uniquely named STD32C_<hash>_B###; results are std32c_<hash>_b###.csv.
- A frozen plan and local submission log prevent changing limits or blindly restarting hidden historical tasks.
- If STDClimate_* tasks already exist, reconcile their climate IDs before using the new compact plan.
- Increasing the number of dates per task enlarges GEE computation graphs. A smaller queue does not guarantee faster computation.

## Commands (PowerShell)
Set local paths, then preview the **exact** number of tasks:

    $PY='E:\Global_flooding\.venv\Scripts\python.exe'
    $BASE='E:\Global_flooding'
    $R32='E:\Global_flooding\pipelines\r32'
    & $PY "$R32\21f_compact_crossyear_submit.py" --base "$BASE" --max-requests 60000 --max-dates 540

Compare an aggressive plan (preview only; do not immediately submit):

    & $PY "$R32\21f_compact_crossyear_submit.py" --base "$BASE" --max-requests 80000 --max-dates 730

Choose one set of limits before submitting. Inspect:

    E:\Global_flooding\standard_calendar_20b\21_compact\compact_task_plan.csv

Import all yearly Stage20b request CSV assets using the existing 21a script. Perform the Stage21 known-date calibration against the original production climate lookup and confirm its JSON status PASS.

Verify assets (read-only for GEE task submission):

    & $PY "$R32\21f_compact_crossyear_submit.py" --base "$BASE" --max-requests 60000 --max-dates 540 --verify-assets

Submit only the first block. Replace PLAN with the **exact** preview ID:

    $PLAN='<12-character-preview-ID>'
    & $PY "$R32\21f_compact_crossyear_submit.py" --base "$BASE" --max-requests 60000 --max-dates 540 --submit --plan-id $PLAN --blocks 0 --max-tasks 1

Inspect the completed first task and another high-request block before submitting the rest. All later runs must retain the identical limits and plan ID.

Download compact exports into E:\Global_flooding\Standard_Climate_Compact_all and run strict Stage22b merge:

    & $PY "$R32\22b_merge_compact_weather.py" --base "$BASE" --csv-root 'E:\Global_flooding\Standard_Climate_Compact_all'

Stage22b writes E:\Global_flooding\standard_calendar_22_compact\22_standard_daily_with_climate.parquet. This is separate from the old Stage22 output.

Use this panel for the conditional-Poisson pilot:

    & $PY "$R32\23_conditional_poisson_stage32.py" --base "$BASE" --panel-path 'E:\Global_flooding\standard_calendar_22_compact\22_standard_daily_with_climate.parquet' --validation temporal --max-strata 10000

After the complete calendar passes QC, run with --validation all and without --max-strata.

## Current 11-task plan: calibration is REQUIRED before submission

If the local preview reports **11 tasks** with `--max-requests 80000 --max-dates 730`,
plan ID `911b8e7ff283` is valid only while the input and immutable plan hash remain identical.
The message `Numerical calibration PASS is mandatory; refusing production submission`
is an intentional preflight stop, **not** an Earth Engine runtime or quota failure.

Commands for the user's reorganized folder layout:

    $PY='E:\Global_flooding\.venv\Scripts\python.exe'
    $BASE='E:\Global_flooding'
    $R32='E:\Global_flooding\pipelines\r32'
    & $PY "$R32\21_calibrate_known.py" --base "$BASE" --date 2020-07-01
    & $PY "$R32\21_calibrate_known.py" --base "$BASE" --date 2020-07-01 --submit

This submits **one small calibration task** from the older
`projects/eco-sector-503721-b7/assets/climate_requests_2020` asset,
not from the new `standard_requests_YYYY` assets. Confirm the export
`std32_calibration_2020.csv` in Google Drive folder
`Groundsource_Standard_Climate_Calibration`, then download it locally.

    & $PY "$R32\21c_compare_calibration.py" --base "$BASE" --calibration-csv 'E:\Global_flooding\Standard_Climate_all\std32_calibration_2020.csv'

Read the generated `standard_calendar_20b/calibration_qc/21c_calibration_features.csv`
and `21c_calibration_status.json`. PASS requires numerically matching weather
features, a minimum of five finite pairs per feature, and an SHA256 fingerprint
matching the current canonical `21_standard_weather_submit.py` implementation.
Never fabricate a PASS JSON and do not use the unsafe override in older scripts.

After yearly Standard Calendar assets are imported, check their presence
for the chosen compact task:

    & $PY "$R32\21f_compact_crossyear_submit.py" --base "$BASE" --max-requests 80000 --max-dates 730 --blocks 0 --verify-assets

Only after calibration and asset checks, submit one block:

    $PLAN='911b8e7ff283'
    & $PY "$R32\21f_compact_crossyear_submit.py" --base "$BASE" --max-requests 80000 --max-dates 730 --submit --plan-id $PLAN --blocks 0 --max-tasks 1

Block 0 has **2,516 requests but 711 unique dates** and may be computationally
challenging despite its small row count. Check its status and output CSV before
submitting other blocks. Block 9 (78,320 requests, 395 dates) tests a different
memory profile; do not automatically queue all 11 tasks before confirming
big-block feasibility.

Calibrating and importing assets do not modify the 11-task plan. Both are
prerequisites and must be kept separate from the old `ERA5FB_DAILY_*` jobs.

## Task counts and resource risks
At least ceil(433090 / max_requests) tasks are necessary, and the number of unique request dates imposes another lower bound. The planner calculates the exact count after applying both caps and monthly-chunk constraints. Tasks covering 540–730 distinct dates are experimental; the original reducer builds separate IMERG and ERA5 computations for every date, so larger jobs can fail due to server memory/timeouts. Export jobs have finite life and EECU use; reduced task count is not a quota bypass.

Do not change the frozen plan after submissions. If a block fails, prepare a separately indexed supplementary plan for its uncovered IDs after verifying all previous outputs and task histories.