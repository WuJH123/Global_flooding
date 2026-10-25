# Results 3.2 — safe recovery of timed-out compact exports

Original 11-task compact plan: `911b8e7ff283` (80,000 requests / 730 dates).
Failed parents: B007 (68,749 requests, 730 dates), B008 (75,077, 639), B009 (78,320, 395).
These three correspond to **222,146** requests. Do not restart the other eight successful tasks.
An Earth Engine batch FAILED/Computation timed out is NOT resolved by unconditional retry of the original oversized batch.

### Preflight and comparison (no Earth Engine calls)

```powershell
cd E:\Global_flooding
git status --short
git pull --ff-only origin main
$PY='E:\Global_flooding\.venv\Scripts\python.exe'
$BASE='E:\Global_flooding'
$R32="$BASE\pipelines\r32"
& $PY "$R32\21g_rescue_failed_compact.py" --base "$BASE" --parents 7,8,9 --max-requests 30000 --max-dates 240
# Alternative, fewer larger tasks: PREVIEW ONLY before any rescue submission
& $PY "$R32\21g_rescue_failed_compact.py" --base "$BASE" --parents 7,8,9 --max-requests 40000 --max-dates 300
```

Choose ONE plan based on exact number of tasks printed. Suggested start **30k/240** for reliability.
Copy the 12-character rescue plan ID from **the most recent preview**. Once any child task is
submitted, the rescue plan becomes frozen and cap changes are disallowed. Do not edit the original 21_compact plan.

### Submit one rescue child as a smoke test

```powershell
$RESCUE_PLAN='<copy printed rescue plan ID>'
& $PY "$R32\21g_rescue_failed_compact.py" --base "$BASE" --parents 7,8,9 --max-requests 30000 --max-dates 240 --plan-id $RESCUE_PLAN --parts 0 --max-tasks 1 --submit
```

On submission, Stage21g checks the numerical parity PASS and current algorithm SHA256,
the original parents' *actual* FAILED status via task IDs in `21_compact/submitted_tasks.jsonl`,
and the required 2000–2026 EE assets. The production code is imported unchanged from
`21_standard_weather_submit.py`; it does not alter weather calculations or units.

After verifying the first child's complete CSV and actual number of rows, run all other parts
with the identical cap values and plan ID:

```powershell
& $PY "$R32\21g_rescue_failed_compact.py" --base "$BASE" --parents 7,8,9 --max-requests 30000 --max-dates 240 --plan-id $RESCUE_PLAN --max-tasks 0 --submit
```

`--max-tasks 0` is ONLY for after the trial child succeeds. The local submission ledger
`standard_calendar_20b/21_rescue/rescue_submissions.jsonl` prevents repeat submission.
If a split rescue child fails, do not blindly retry it: make a smaller supplemental plan for that child.

### Download/merge all NEW weather results

Download the 8 successful originals (B000–B006, B010) into:

`E:\Global_flooding\Standard_Climate_Compact_all`

Download all completed rescue jobs from the Drive folder
`Groundsource_Standard_Climate_Compact_Rescue` into:

`E:\Global_flooding\Standard_Climate_Compact_Rescue_all`

Original completed exports use `std32c_911b8e7f_bNNN.csv`; new rescue outputs use
`std32r_<tag>_b007_sNN.csv` and correspondingly B008/B009.
Do **not** include original failed B007/B008/B009 CSVs, even if partial files appear.

```powershell
& $PY "$R32\22b_merge_compact_weather.py" --base "$BASE" `
  --csv-root 'E:\Global_flooding\Standard_Climate_Compact_all' `
  --rescue-plan 'E:\Global_flooding\standard_calendar_20b\21_rescue\rescue_plan.csv' `
  --rescue-csv-root 'E:\Global_flooding\Standard_Climate_Compact_Rescue_all'
```

The 22b merge rejects missing/duplicate/unexpected CSV files and checks both the entire new ID
set and grid/date consistency against the canonical Stage20b parquet. The successful 8 parent
tasks and split rescue outputs must collectively contain exactly 433,090 new unique climate IDs.
Output stays in `standard_calendar_22_compact`, without overwriting Stage20b input.

IMPORTANT: an ID-complete export does not prove all ERA5 weather values are non-null. Preserve
and audit coastal ERA5 missingness, eligibility of reported continuation days and model risk sets.

### Why not retry the parents unchanged?

All three died with `Computation timed out` despite complete annual request assets and a
previous 20-row numerical parity PASS. This is consistent with excessive work/graph complexity
for a single job: the parent had 68k–78k source grid–date requests and 395–730 distinct dates.
Errors alone do not reveal the exact internal bottleneck, and EECU-seconds are not elapsed seconds.
Partitioning the existing request groups into smaller independent exports reduces per-task load
without reprocessing the 210,944 requests associated with already completed original jobs.