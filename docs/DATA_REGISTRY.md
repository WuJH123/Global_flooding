# Data registry (verified local-run snapshots, not tracked datasets)

The following counts derive from prior local QC logs. They are **expectations to re-audit**, not proof of current files. Data should remain in the user's local directories, outside Git.

| Artifact | Local relative path | Expected rows or counts | Identity / scientific limitation |
| --- | --- | --- | --- |
| Normalized Groundsource | external `processed/groundsource_stage1_records_20km.parquet` | 2,646,302 records | Source location polygon is not flooded area |
| Positive grid-days | external `processed/groundsource_gridday_20km.parquet` | 2,375,933; 94,738 grids | One grid/day may contain multiple reports |
| GHSL-positive M3 urban sample | `processed_local/urban_catalogue_m3_pop100_share50.parquet` | 1,378,124; 26,317 grids | Epoch-linked urban filter, not all world cities |
| M3 episodes | `episode_control/flood_episodes_m3.parquet` | 650,368 episodes | Grid-level consecutive reported days |
| Legacy matched cases/controls | `episode_control/matched_case_control_m3.parquet` | 3,250,710 rows; 650,142 strata | Exploratory 1:4 design, selection concern |
| Primary climate lookup | `climate_global/climate_features_primary.parquet` | 2,789,073 distinct climate IDs | IMERG complete; 16,975 ERA5 missing requests |
| Primary complete matched set | `climate_local_first/primary_complete_strata.parquet` | 3,231,350 rows; 646,270 strata | Conditional model input, not full calendar |
| Canonical standard calendar | `standard_calendar_20b/20_standard_daily_panel.parquet` | 2,680,571 dates; 650,361 onsets through 2026-01-31 | 433,090 new climate requests, 13,746 existing ERA incomplete |
| Static grid context M3 | `mechanism_project/05_static_merged/grid_static_context_m3.parquet` | 26,317 grids | UCDB mapped 14,372; MERIT 26,317; BasinATLAS 25,129; CRU 26,220 |
| Global E2025 settlement reference | `results_31/global_urban_universe_2025_CORRECTED.csv` | 78,330 raw urban candidate cells; 76,590 geospatially valid | Do not use earlier uncorrected E2020 resampling |
| New standard-calendar weather | `Standard_Climate_all/` + `standard_calendar_22/` | **Pending** | Numeric calibration and exact ID completeness required |

The 2000–2026 observational series is incomplete in February 2026. The 2025 settlement domain should **not** be treated as historical yearly city boundaries. Headline reporting footprint rates are descriptive coverage, not physical flood occurrence rates.

## Dependency graph
`Groundsource → grid-day → GHSL urban catalogue → episodes → legacy controls → primary climate lookup → 20b standard calendar → incremental GEE climate → 22 verified calendar → 23 candidate conditional Poisson`.

`UCDB + BasinATLAS + CRU + MERIT → static city/grid context → heterogeneity in Results 3.3`.

Use `python tools/verify_project.py --base E:\Global_flooding` for current local verification. Keep downloaded external inputs and raw GEE exports unchanged.
