GROUNDSOURCE — publication-oriented corrected standard-calendar pipeline

Important finding:
Stage20 completed correctly (2,663,528 grid dates; 646,270 onset cases;
429,793 missing climate IDs). BUT its case universe was restricted to the
previous 1:4 matched case subset with complete original climate. For a new
reference-day design, avoid inheriting that restriction: Stage20b rebuilds
same-weekday risk-set strata from ALL M3 episode onsets (excludes incomplete
2026-02 by default). The Stage20b counts WILL DIFFER from 429,793.

COPY PYTHON FILES to E:\Global_flooding\ and PS1 to the same folder.

Step 1 — Recommended: rebuild all episodes (NO GEE):
 E:\Global_flooding\.venv\Scripts\python.exe E:\Global_flooding\20b_rebuild_full_episode_calendar.py

Outputs under E:\Global_flooding\standard_calendar_20b\ including
20_standard_daily_panel.parquet, 20_missing_climate_requests_chunked.parquet,
20_missing_task_manifest.csv, gee_upload_standard/standard_requests_YYYY.csv.

Step 2 — Replan the new missing-weather exports; NO GEE:
 E:\Global_flooding\.venv\Scripts\python.exe E:\Global_flooding\21_standard_weather_submit.py
Produces 21_standard_climate_optimized_task_plan.csv in standard_calendar_20b.
A group contains up to 20,000 requests and 45 DISTINCT dates, from one year.
These are initial scheduling limits, not a guarantee of zero timeouts.
Once submissions begin, do NOT change those limits or overwrite input assets.

Step 3 — Upload NEW standard_requests yearly CSV (not original climate_requests):
 powershell -ExecutionPolicy Bypass -File E:\Global_flooding\21a_upload_standard_assets.ps1
Review GCS bucket before running; Cloud Storage charges may apply.
WAIT for EE table ingestion to complete. Do not rerun while ingestion pending.

Step 4 — Test ONE small task:
 E:\Global_flooding\.venv\Scripts\python.exe E:\Global_flooding\21_standard_weather_submit.py --submit --year 2001 --max-tasks 1
Inspect completed output and expected request count/fields.

Step 5 — Submit remainder (when quota/workload permits):
 E:\Global_flooding\.venv\Scripts\python.exe E:\Global_flooding\21_standard_weather_submit.py --submit --max-tasks 0
 - max-tasks=0 means submit all newly unsubmitted groups.
 - The default max-tasks=1 safeguards against accidental mass submission.
 - Exports go to Drive folder Groundsource_Standard_Climate_YYYY,
   named stdclimate_YYYY_gNNN.csv.
 - Task status idempotency uses existing EE task descriptions; not a complete
   history forever. Local JSONL submission log is kept.

Step 6 — Download ONLY completed production files to:
 E:\Global_flooding\Standard_Climate_all\ (year subdirectories optional)

Step 7 — STRICT merge:
 E:\Global_flooding\.venv\Scripts\python.exe E:\Global_flooding\22_merge_standard_calendar_weather.py
Result: E:\Global_flooding\standard_calendar_22\22_standard_daily_with_climate.parquet

Science/quality notes:
 - Stage20b is conditioned on the M3 urban-event cohort, not a global all-city panel.
 - Non-onset positive days are flagged and excluded from eligible risk days;
   no-report days are NOT verified flood-free.
 - Atmospheric extraction matches original IMERG V07/ERA5-Land HOURLY pipeline.
 - Existing ERA5-null climate dates are not part of new missing-ID extraction;
   those remain separately flagged and should receive missingness analysis.
 - Use conditional Poisson and storm/spatial clustering, not absolute flood
   probability from case-selected strata.
 - This pipeline has passed Python syntax validation but HAS NOT been tested
   on your Windows full-data files or actual Earth Engine jobs.
