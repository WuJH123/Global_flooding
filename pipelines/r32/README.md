# Results 3.2 — standardized risk-set climate associations

**Source of truth:** the root `20b_rebuild_full_episode_calendar.py`, `21_standard_weather_submit.py` and `22_merge_standard_calendar_weather.py` remain canonical historical scripts; helpers in this folder add calibration, QC, and conditional-Poisson exploratory modeling. No raw results are stored in Git.

1. `python tools/verify_project.py --base E:\Global_flooding` — audit existing results without recomputing.
2. If required, `python 20b_rebuild_full_episode_calendar.py --analysis-end 2026-01-31` (DO NOT regenerate once a task plan has frozen).
3. `python 21_standard_weather_submit.py` — **plan only**, no GEE tasks.
4. `python pipelines/r32/21a_upload_year_assets.py` — preview; `--upload` explicitly enables external upload.
5. `python pipelines/r32/21_calibrate_known.py --date 2020-07-01` — a small GEE calibration export. Compare downloaded values with production using `python pipelines/r32/21c_compare_calibration.py --calibration-csv <path>`.
6. **Only after PASS** submit in small batches: `python 21_standard_weather_submit.py --submit --year 2020 --max-tasks 1`. The root submitter checks the calibration gate.
7. `python 22_merge_standard_calendar_weather.py --csv-root E:\Global_flooding\Standard_Climate_all`; it must verify every exported task and every request ID.
8. `python pipelines/r32/23_conditional_poisson_stage32.py --validation temporal --max-strata 10000` for a smoke test; then rerun without `--max-strata` for the exploratory conditional Poisson analysis.
9. `python pipelines/r32/24_stage32_figures.py` creates an **exploratory** Figure 2 draft.

Strict rule: the new GEE extraction must be numerically comparable with the original `climate_features_primary.parquet`. Use original sample-date and API conventions; if calibration fails, do not submit production tasks.

Cases denote reported onset, not verified physical flooding. A control day without a report is not verified flood-free. Weather missing within a time stratum invalidates that stratum for the strict candidate model. Ongoing positive non-onset days are excluded from risk sets by default and sensitivity-tested separately. No absolute flood-risk probabilities are estimated.

The Results 3.2 stage produces candidate associations; further storm-level clustering, source reporting-date uncertainty, flood-type independent validation, and city transfer tests remain necessary before publication.
