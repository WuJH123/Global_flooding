# P0 revision plan | Global urban flood mechanism paper (2026-10-26)

## Scope and objective

**Scientific target:** assess whether remotely measured daily/3-hour rainfall and
antecedent moisture show reproducible *associations with news-reported flood
onsets*, and whether terrain, climate and settlement conditions modify them.
This is not a validated model of physical flood probability or an attribution
of anthropogenic climate change.

Stage22b/22c frozen reference:
- 2,680,571 unique daily grid observations in the full calendar
- 600,723 eligible strata and 641,587 onset grid-days used by Stage23/25
- Stage25 P24×API7 flips from negative to positive when P3H×API7 is introduced
- Stage26 MERIT relief and BasinATLAS slope are favorable modifier candidates,
  but their individual models were fit on different complete-case populations.

## P0 tasks implemented now

### Stage27 (Results 3.2): rainfall aggregation parity and identifiability

`pipelines/r32/27_rainfall_parity_identifiability.py`

1. Read all six required columns (P24 mean/max, P3H mean/max, API7 mean and
   previous-day SM1 mean) **from the same existing 22b Parquet**.
2. Exclude continuing reported flood days and every entire time stratum with
   any nonfinite/negative rainfall/API7 or SM1 outside [0,1]; retain only
   informative strata. Freeze this common cohort for all variants and splits.
3. Three consistent rainfall comparisons:
    - `mean_mean`: 20 km mean P24 and 20 km mean P3H
    - `max_max`: 20 km max P24 and 20 km max P3H
    - `mean_max`: legacy 20 km mean P24 versus max P3H
4. For each time, space and UCDB-city holdout, fit I1, I3 and I4 with identical
   risk sets, training-only transformations and the existing Stage25 optimizer.
   Test **I3→I4 directly** (isolates P3H×API7) and I1→I3.
5. Report spatial-block sandwich CI on TRAIN coefficients, paired heldout
   bootstrap CI on gains, training-only VIF/correlation and real-world rainfall
   joint-support diagnostics. Absent observed combinations are never filled with
   fabricated risk-surface data.

**P0 decision gate:** do not interpret P24×API7 physically until sign,
support and collinearity are reviewed across `mean_mean`, `max_max`
and legacy `mean_max` and nested I3→I4 comparisons.

### Stage28 (Results 3.3): joint terrain/environment on a SAME cohort

`pipelines/r33/28_joint_terrain_context.py`

Freeze a **single common complete-case cohort** of all four verified
environment modifiers:
- `merit_local_relief_m_approx` (20 km local relief)
- `basin_slope_degrees` (BasinATLAS level07 center-assigned slope)
- `cru_pre_annual_mm` (CRU 1991–2020 long-term mean annual rainfall)
- `basin_urb_pc_sse` (BasinATLAS basin urban cover, NOT urban imperviousness)

Default weather representation is `mean_mean` to avoid the P24-mean/P3H-max
spatial operator mismatch in the legacy code. **Use Stage27 results before
deciding this weather representation is scientifically reliable.**

Nested models:
- J0: P24,P3H,API7,SM1,P24×API7,P24×SM1
- J1: J0 + terrain relief × (P24,API7)
- J2: J0 + basin slope × (P24,API7)
- J3: J0 + BOTH relief and slope × (P24,API7)
- J4: J3 + CRU annual rain and Basin urban × (P24,API7)

Nested comparisons: J0→J1, J0→J2, J1→J3 (slope beyond relief),
J2→J3 (relief beyond slope), J3→J4.

Outputs include test-set gain and block-bootstrap CI, coefficients/training
sandwich CI, coverage and UNIQUE-GRID environment collinearity. These do not
establish hydrologic causality or independent physical storm events.

## Windows PowerShell: synchronize and verify

```powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main

$BASE='E:\Global_flooding'
$PY="$BASE\.venv\Scripts\python.exe"
$PANEL="$BASE\standard_calendar_22_compact\22_standard_daily_with_climate.parquet"
$STATIC="$BASE\mechanism_project\05_static_merged\grid_static_context_m3.parquet"
$R32="$BASE\pipelines\r32"
$R33="$BASE\pipelines\r33"
Test-Path $PANEL
Test-Path $STATIC
Get-Content "$BASE\stage32_qc\22c_model_readiness.json" -Raw
```

## Stage27 smoke (subset, not manuscript results)

```powershell
& $PY "$R32\27_rainfall_parity_identifiability.py" `
 --base "$BASE" --panel-path "$PANEL" `
 --validation temporal --max-strata 10000 `
 --block-cells 25 --bootstrap-reps 99
```

If the subset contains fewer than 15 heldout blocks, increase `--max-strata`
to 50000. Do not disable spatial-uncertainty checks to hide the error.

## Stage27 full

```powershell
& $PY "$R32\27_rainfall_parity_identifiability.py" `
 --base "$BASE" --panel-path "$PANEL" `
 --validation all --seed 2026 --block-cells 25 --bootstrap-reps 399
```

Output folder `stage32_identifiability`:
- `27_interaction_parity.csv`: signed interactions, TRAIN sandwich intervals,
  the three rainfall representations and all holdout losses
- `27_nested_gain_ci.csv`: **I1→I3** and **I3→I4** test gains with paired
  heldout spatial-block 95% percentile intervals
- `27_joint_support_qc.csv`: observed P3H/P24 and rainfall correlation
- `27_collinearity_and_transform.json`: train-only correlation/VIF,
  condition numbers, transformations and same-cohort coverage

Inspect:
```powershell
Import-Csv "$BASE\stage32_identifiability\27_interaction_parity.csv" |
 Where-Object {$_.term -eq 'P24_x_API7'} |
 Format-Table validation,rainfall_representation,model,beta,beta_ci_low,beta_ci_high -AutoSize

Import-Csv "$BASE\stage32_identifiability\27_nested_gain_ci.csv" |
 Format-Table validation,rainfall_representation,baseline,augmented,gain,gain_ci_low,gain_ci_high -AutoSize

Get-Content "$BASE\stage32_identifiability\27_collinearity_and_transform.json" -Raw
```

## Stage28 smoke and full

```powershell
& $PY "$R33\28_joint_terrain_context.py" `
 --base "$BASE" --panel-path "$PANEL" --static-path "$STATIC" `
 --rain-parity mean_mean --validation temporal `
 --max-strata 10000 --bootstrap-reps 99

& $PY "$R33\28_joint_terrain_context.py" `
 --base "$BASE" --panel-path "$PANEL" --static-path "$STATIC" `
 --rain-parity mean_mean --validation all --seed 2026 `
 --block-cells 25 --bootstrap-reps 399
```

Output folder `stage33_joint`:
- `28_joint_environment_coefficients.csv`
- `28_joint_environment_gains.csv`
- `28_common_cohort_manifest.json`

```powershell
Import-Csv "$BASE\stage33_joint\28_joint_environment_gains.csv" |
 Format-Table validation,baseline,augmented,gain,gain_ci_low,gain_ci_high -AutoSize

Import-Csv "$BASE\stage33_joint\28_joint_environment_coefficients.csv" |
 Where-Object {$_.term -match 'RELIEF|SLOPE'} |
 Format-Table validation,model,term,beta,beta_ci_low,beta_ci_high -AutoSize
```

## Next unimplemented P0 work (NOT claimed fixed by 27/28)

1. **Stage29 storm-dependence:** spatial-temporal positive grid-event grouping
   via 20 km adjacent cells (8-neighbors) and onset windows ±1–3 days.
   Compare alternative thresholds, then refit/re-evaluate with larger block
   widths and event-cluster-based uncertainty. Statistical cluster IDs derived
   by adjacency are **proxy storms**, not independently observed weather systems.
2. **Stage30 temporal misalignment:** Groundsource is a reported event-date,
   not reliably physical onset time; extraction of UTC ±1/2 day weather
   requires a gap audit before submitting any targeted GEE jobs. Do NOT shift
   cases and reuse exposures from their same-weekday referent dates.
3. **Stage31 validation:** stratified manual review by source, continent,
   Koppen group, city size and decade (sample size determined by precision
   requirements) plus precision/recall for independent positive events where
   complete coverage exists. DFO/Global Flood Archive are severe river-flood
   biased and their absence is NOT evidence a local reported flash flood is false.
4. **Stage32 urban-specific mechanism:** GHSL built fraction in a fixed epoch,
   topology-informed HAND/flow accumulation, reported fluvial vs pluvial,
   and urban center/periphery sensitivity; verify MERIT `elv` vs `hnd`
   metadata before interpreting the hydro-adjusted elevation variable.
5. **Climate-change chapter only after 29–32:** formal forcing attribution,
   model-based daily/subdaily precipitation scenarios with bias correction,
   out-of-domain warning and future urban population grid. Do not multiply
   current news-case conditional ratios by population and call it flood exposure.

## Mapping to paper Results

| Section | Gate and output |
|---|---|
| 3.1 Global observational domain | source/decade/climate-zone and publication-selection QC, manual validation |
| 3.2 Hydroclimatic interaction | Stage27 parity, conditional response direction, joint support and robust nested gains |
| 3.3 Environmental heterogeneity | Stage28 common cohort, independent relief/slope terms, GHSL urban check |
| 3.4 Robustness/generalization | Stages29–31 independent storms, ±1/2 date shift, external validation and reporting bias |
| 3.5 Climate-change implications | forcing/scenario/persistence and population exposure as a **new** independently validated analysis |

Prioritize 27 and 28 now; prevent all final mechanism conclusions until
event-time uncertainty and reporter-selection P0 items are also addressed.

Relevant Nature-family comparators:
- Nature Communications (2026) antecedent moisture and runoff efficiency:
  https://www.nature.com/articles/s41467-026-69286-3
- Nature Cities (2026) urban-rainfall co-evolution review:
  https://www.nature.com/articles/s44284-026-00530-z
- Communications Earth & Environment (2026) news/social observation inequity:
  https://www.nature.com/articles/s43247-026-04049-y
- Communications Earth & Environment (2025) urban flood population exposure:
  https://www.nature.com/articles/s43247-025-02585-7
- Nature Communications (2026) climate-change forcing and flood timing:
  https://www.nature.com/articles/s41467-026-73839-x
