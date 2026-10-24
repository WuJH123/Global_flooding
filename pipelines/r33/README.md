# Results 3.3 — explaining cross-city heterogeneity

Canonical scripts remain `mechanism_scripts/00_audit_downloads.py` to `mechanism_scripts/10_validate_outputs.py` (preserved file names). Primary local outputs in `mechanism_project/01_cities`, `02_basins`, `05_static_merged`, `07_climate_background`, `MERIT_all`.

Verified preceding local logs: 26,317 M3 grids; 14,372 assigned to UCDB city centers; 25,129 BasinATLAS-level07 grid-center assignments; 26,220 valid CRU 1991–2020 climatology; 26,317 MERIT exports. Each count needs re-validation on the machine. UCDB ID must be stable `ID_UC_G0`, not `ucdb_row_*`. Retain all alternative grid-city overlap links; a largest-overlap city is a modeling simplification. BasinATLAS slope encoded x10 must be reported in degrees after conversion. The MERIT product `hnd` is adjusted elevation, not HAND.

The local 30-city model was exploratory under old 1:4 reference-day selection. It is not yet a causal or transferable mechanism result. Connect static covariates to candidate Results 3.2 estimates after time-stratified validation.
