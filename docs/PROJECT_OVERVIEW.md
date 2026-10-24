# Global urban-flood workflow — canonical repository

**Rule:** GitHub stores source code, configuration examples, tests and processing manifests; raw data and generated Parquet/CSV/GeoTIFF stay in `E:\Global_flooding` (or a separately versioned data store). Results 3.1 and 3.2 scripts live in `pipelines/r31` and `pipelines/r32`; Results 3.3 preparation remains in `mechanism_scripts`, with future models in `pipelines/r33`. Shared data checks live in `tools`.

## Research results
| Section | Research task | Canonical code | Main outputs |
| --- | --- | --- | --- |
| Results 3.1 | Reporting coverage, selection and representativeness | `pipelines/r31/01`–`05`, `31`–`31c` | `results_31/`, `results_31/reference_integrated/`, `results_31/reference_audit_31c/` |
| Results 3.2 | Shared hydroclimatic signatures | `pipelines/r32/06`–`24` | `climate_global/`, `standard_calendar_20b/`, `standard_calendar_22/`, `stage32_results/` |
| Results 3.3 | Explaining city-level heterogeneity | `mechanism_scripts/00_...` through `10_...` | `mechanism_project/05_static_merged/`, `MERIT_all/` |
| Results 3.4 | Transfer and robustness | planned | pending |
| Results 3.5 | Climate-change implications | planned — do not project absolute flood probability yet | pending |

## Initial local setup (PowerShell)
```powershell
cd E:\Global_flooding
git status --short
git remote -v
git fetch origin
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools\verify_project.py --base E:\Global_flooding
```
If this is not a Git working tree, see [LOCAL_SYNC.md](LOCAL_SYNC.md). **Never run `git reset --hard` or `git clean -fd` in the scientific data directory.**

## QC and provenance
Each analytical phase has a known expected key and record count in [DATA_REGISTRY.md](DATA_REGISTRY.md). Expected counts come from previous local runs, **not from files committed to GitHub**. Verify actual source files with `tools/verify_project.py`; it never modifies input data. Unknown/missing files are explicitly reported; the tool does not invent a pass.

After any GEE export: (1) count rows; (2) compare unique IDs against the task manifest; (3) validate physical values and missingness; (4) compare known-date climate features with the earlier production extraction before submitting large new batches. See [pipelines/r32/README.md](../pipelines/r32/README.md).

## Scientific guardrails
* Historical Groundsource **reports** are not a uniform or complete hazard observation network; no-report is not verified no-flood.
* 20 km grid-level episodes are not independent regional storms.
* The legacy one-case/four-controls models are exploratory. The standard monthly same-weekday risk set is the candidate main analysis.
* UCDB links cover only the spatially intersected city centres; missing city IDs remain in the grid analysis.
* MERIT Hydro `hnd` is **hydrologically adjusted elevation**, not conventional HAND.
* A global conditional model estimates relative reporting associations — not calibrated absolute future inundation probability.

## Change policy
Start each new feature on a short-lived branch. Use PRs, review tests and merge into `main`. Never silently overwrite already generated files. See [LOCAL_SYNC.md](LOCAL_SYNC.md).
