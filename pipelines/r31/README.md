# Results 3.1 — observational representativeness

Scripts in this directory: `01`–`05` prepare and link the Groundsource grid-days and GHSL urban catalogue; `31_results_observational_coverage.py` and `31a`–`31c` analyze reporting coverage and reference data. Run them from the repository root as `python pipelines/r31/<script>.py`. Do not re-run original 31a erroneous no-data resampling to create the 2020 universe.

Inputs: Groundsource processed grid-days, GHSL SMOD settlement domain, Köppen–Geiger 1991–2020. Local results: `results_31/`; keep correction from 31c (exclude 1,740 invalid extreme southern cells). Independently verify urban reporting coverage and zone composition; do not infer hazard from newsroom reporting coverage.

Remaining for journal: annual first-report accumulation, independent manual event-type/date validation, full-domain settlement population representativeness.
