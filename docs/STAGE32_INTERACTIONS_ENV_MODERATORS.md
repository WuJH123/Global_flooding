# Stage25–26: explanatory interactions, spatial-block confidence intervals, urban-environment moderation

**Source of truth:** `standard_calendar_22_compact/22_standard_daily_with_climate.parquet`
(Stage22c `PASS_STRUCTURE`; 2,680,571 grid-days, 641,587 eligible reported onsets, 600,723 eligible strata).

This version is **inferential development / exploratory**, not a proven mechanism or a causal estimate.
Prior Stage23 held-out results: API7 improves log-loss vs P24 alone in all three validation
splits; SM1 incremental gain inconsistent; P3H added value smaller but directionally
stable. Hence interactions and modifier tests are `a priori` motivated extensions,
not proofs of humidity-dependent physical causality.

## 1. Pull code and inspect model inputs

```powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main
$PY='E:\Global_flooding\.venv\Scripts\python.exe'
$BASE='E:\Global_flooding'
$R32="$BASE\pipelines\r32"
$R33="$BASE\pipelines\r33"
$PANEL="$BASE\standard_calendar_22_compact\22_standard_daily_with_climate.parquet"
$STATIC="$BASE\mechanism_project\05_static_merged\grid_static_context_m3.parquet"

Test-Path $PANEL
Test-Path $STATIC
Get-Content "$BASE\stage32_qc\22c_model_readiness.json" -Raw
```

Do NOT run Stage25 or Stage26 before the prior Stage22c status is PASS_STRUCTURE.

## 2. Stage25 smoke test (fast selected strata, not final inference)

```powershell
& $PY "$R32\25_conditional_interactions_ci.py" `
  --base "$BASE" --panel-path "$PANEL" `
  --validation temporal --max-strata 10000 `
  --block-cells 25 --bootstrap-reps 99
```

Outputs (`stage32_interactions`):
- `25_interaction_cluster_ci_smoke10000.csv`: conditional relative association, coefficients, cluster-sandwich uncertainty on *training subset*.
- `25_heldout_paired_gain_ci_smoke10000.csv`: paired test-only conditional log-loss difference CIs, block bootstrap conditioning on the fitted models.
- `25_transform_manifest_smoke10000.json`: feature scales, model terms, eligible sample.

**Smoke subsets should not be quoted in a manuscript.** Small temporal smoke tests may lack sufficient test clusters and will explicitly fail; in that case use 50,000 strata, not an unclustered CI or silent fallback.

## 3. Full Stage25, cross-validation and CI

```powershell
& $PY "$R32\25_conditional_interactions_ci.py" `
  --base "$BASE" --panel-path "$PANEL" `
  --validation all --seed 2026 `
  --block-cells 25 --bootstrap-reps 399
```

This **does not** change existing 23 outputs.
- I0: P24 + API7 + SM1 + P3H
- I1: I0 + P24 × API7
- I2: I0 + P24 × SM1
- I3: I0 + both interactions
- I4: I3 + P3H × API7

All interactions use TRAIN-standardized exposure features.
Report spatial-block-cluster sandwich confidence intervals **for train coefficients** and
held-out paired block-bootstrap intervals for relative conditional log-loss gains.
A difference (I1-I0) >0 in each held-out validation is further evidence of stable predictive contribution;
do **not** infer that the interaction is causal or has a universal physical threshold.

Inspect:

```powershell
Import-Csv "$BASE\stage32_interactions\25_interaction_cluster_ci.csv" |
  Where-Object { $_.term -like '*_x_*' } |
  Format-Table validation,model,term,beta,beta_ci_low,beta_ci_high -AutoSize

Import-Csv "$BASE\stage32_interactions\25_heldout_paired_gain_ci.csv" |
  Format-Table validation,augmented,heldout_loss_gain,gain_CI_low,gain_CI_high -AutoSize
```

## 4. Stage26 smoke, direct static join

```powershell
& $PY "$R33\26_city_environment_modifiers.py" `
  --base "$BASE" --panel-path "$PANEL" --static-path "$STATIC" `
  --modifiers basin_urb_pc_sse,cru_pre_annual_mm `
  --validation city --max-strata 10000 `
  --bootstrap-reps 99
```

No time-invariant static main effect is identifiable after conditioning.
Instead the model interacts standardized P24 and API7 with each standardized environmental
modifier, testing whether their *dynamic slope* depends on static environment.
One modifier at a time is compared on exactly the SAME complete-case strata against M0 baseline.
The city-holdout split is based on primary UCDB city IDs; sample selection/coverage is reported.

## 5. Full Stage26 environmental modification

```powershell
& $PY "$R33\26_city_environment_modifiers.py" `
  --base "$BASE" --panel-path "$PANEL" --static-path "$STATIC" `
  --validation all --seed 2026 --block-cells 25 `
  --bootstrap-reps 399
```

Outputs (`stage33_modifiers`):
- `26_environment_effect_modification_ci.csv`: train spatial-block-clustered coefficient CIs
- `26_environment_heldout_gain_ci.csv`: paired held-out spatial bootstrap CIs
- `26_environment_coverage.csv`: exact eligible date/stratum/grid coverage per modifier
- `26_environment_manifest.json`: inputs, source definitions, modeling limitations

The built-in four candidate static variables are:
- `basin_urb_pc_sse`: BasinATLAS *basin-level* urban cover, source-coded and log-transformed. **Not** a 20-km city imperviousness fraction.
- `cru_pre_annual_mm`: 1991–2020 precipitation climate norm.
- `basin_slope_degrees`: basin level07 slope (source raw degree×10 converted by Stage02).
- `merit_local_relief_m_approx`: 20-km approximate relief, min/max terrain.

Source values and semantic units must be checked before claiming physical effect magnitudes.
Do **not** use the `merit_hydro_adjusted_elev_1km_mean` column here pending original MERIT elv/hnd semantics review.

A separately verified **one-record-per-grid fixed-epoch** GHSL feature can be supplied using
`--ghsl-parquet <path> --ghsl-variable built_fraction` together with
`--modifiers built_fraction`. Do not feed a time-varying GHSL panel without fixing the epoch
or resampling design. This switch never synthesizes missing built cover.

Inspect:

```powershell
Import-Csv "$BASE\stage33_modifiers\26_environment_coverage.csv" |
  Format-Table -AutoSize

Import-Csv "$BASE\stage33_modifiers\26_environment_effect_modification_ci.csv" |
  Where-Object {$_.term -match '_x_ENV'} |
  Format-Table validation,modifier,term,beta,beta_ci_low,beta_ci_high -AutoSize

Import-Csv "$BASE\stage33_modifiers\26_environment_heldout_gain_ci.csv" |
  Format-Table validation,modifier,gain,gain_ci_low,gain_ci_high -AutoSize
```

## Quality safeguards and publication limitations

- The outcome is the **news-reported flood onset day**; no-report dates are exposure referents,
  **not** independently confirmed no-flood dates.
- Current 20-km grid entries can be hit by the same regional storm on the same date.
  Spatial-block sandwich/bootstraps *do not prove* storm-level independence, especially for
  systems spanning multiple blocks. Add event-level and same-date sensitivity before journal claims.
- City validation uses UCDB-assigned grids only; unassigned grid samples are not represented.
- CIs refer to TRAIN fitted coefficients (robust *conditional score approximation*). Held-out
  block-bootstrap CIs condition on trained models and do **not** include model refitting uncertainty.
- Effect-modification by time-invariant CRU/MERIT/BasinATLAS is estimated through interactions
  only, which are identifiable; static main effects are not under stratum conditioning.
- Model differences are paired within each modifier's complete-case subset; DO NOT compare
  log-loss values across subsets as if they had the same target population.
- Earth Engine ERA5 soil moisture outside [0,1] remains excluded by Stage23;
  never substitute zeros or winsorize raw physical data silently.
- The original Stage23 temporal/spatial/city splits and TRAIN-only transforms are reused.
- For top-journal inference, separately evaluate date ±1/2 mismatches, local-time-zone weather,
  joint storms crossing blocks, spatial reporter selection, county/city level reporting intensity,
  calibration of uncertainty, and external event validation.

**Methodology:** Armstrong et al. (2014) DOI 10.1186/1471-2288-14-122;
Wu et al. (2024), time-stratified case-crossover environmental epidemiology tutorial
DOI 10.1093/ije/dyae020.
