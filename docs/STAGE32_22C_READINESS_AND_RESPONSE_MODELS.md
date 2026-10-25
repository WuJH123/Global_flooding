# Stage22c → Stage23 | safe Results 3.2 model run

**Scope:** read-only diagnostics on locally completed `standard_calendar_22_compact/22_standard_daily_with_climate.parquet`. No GEE and no retransmission of earlier CSV jobs. The original Stage20b/21f/21g/21h frozen plans are unchanged.

## Stage22b expected input state

- 2,680,571 unique grid-dates (`climate_id`), 607,710 calendar strata, 650,361 reported flood onsets through 2026-01-31.
- 113,551 continuing reported flood days.
- 16,143 rows were flagged by Stage22b with one or more NULL weather fields; this is **not** necessarily the number of rows missing the four Stage23 predictor fields.
- Stage22b's `eligible_days=2,567,020` only means *not marked as ongoing positive*, not a complete weather-matched model population.
- Never replace missing ERA5 values with zero, infer missing no-report days as verified no-flood, or treat each grid episode as an independent storm.

## 1. Pull reviewed code and run Stage22c (recommended mandatory gate)

```powershell
cd E:\Global_flooding
git status --short
git pull --ff-only origin main
$BASE = 'E:\Global_flooding'
$PY = "$BASE\.venv\Scripts\python.exe"
$R32 = "$BASE\pipelines\r32"
$PANEL = "$BASE\standard_calendar_22_compact\22_standard_daily_with_climate.parquet"

& $PY "$R32\22c_validate_compact_model_input.py" --base "$BASE" --panel "$PANEL"
Get-Content "$BASE\stage32_qc\22c_model_readiness.json"
Import-Csv "$BASE\stage32_qc\22c_retention_by_year.csv" | Format-Table -AutoSize
Import-Csv "$BASE\stage32_qc\22c_weather_missing_by_field.csv" |
  Sort-Object {[int]$_.invalid_or_missing_days} -Descending |
  Select-Object -First 15 | Format-Table -AutoSize
```

Stage22c validates both full-34-field weather missingness and the *specific* four Stage23 predictor fields, including physically valid SM1 [0,1]; detects duplicated grid dates, invalid flags, bad event key mismatches and incorrect onset count; computes weather-complete, informative strata and loss by year; records outputs in `stage32_qc`. `PASS_STRUCTURE` means structural/statistical-eligibility checks passed, not representative/causal validity.

## 2. A smoke test of Stage23

```powershell
& $PY "$R32\23_conditional_poisson_stage32.py" `
  --base "$BASE" `
  --panel-path "$PANEL" `
  --validation temporal `
  --max-strata 10000
```

This gives `stage32_results/23_model_comparison_smoke10000.csv`, an eligibility JSON, coefficient CSV and transform JSON. It uses a **subset of 10k strata**, therefore any coefficients are for smoke testing only. Temporal split trains <=2022 and tests on 2023–2025; 2026 is not an evaluation year.

## 3. Run full exploratory conditional Poisson models

```powershell
& $PY "$R32\23_conditional_poisson_stage32.py" `
  --base "$BASE" `
  --panel-path "$PANEL" `
  --validation all `
  --seed 2026 `
  --block-cells 25
```

Current predictor selection and models (from code):

| Model | Predictor list | Interpretation |
|---|---|---|
| D0_daily | P24 | day precipitation only |
| D1_antecedent | P24, API7 | contribution of antecedent rainfall |
| D2_plus_soil | P24, API7, SM1 | additional soil moisture contribution |
| D3_short_daily_API | P24, P3H, API7 | extra short-duration rain signal compared with D1 |
| D4_exploratory_curvature | P24, API7, SM1 and their squared terms | exploratory nonlinearity; NOT a fully flexible spline/interaction model |

`--validation all` runs (1) temporal: train <=2022, test 2023–2025, (2) spatial blocks of 25×20 km (about 500 km grid steps), (3) city-holdout on UCDB-mapped grids. Compare **held-out conditional log-loss**, keeping the same test split. Lower log-loss is better. `top1` is within-stratum date ranking, not physical flood detection accuracy.

Expected CSV outputs in `stage32_results`: `23_model_comparison.csv`, `23_coefficients_exploratory.csv`, `23_eligibility_qc.json`, `23_eligibility_by_year.csv`, `23_feature_transforms.json`.

## 4. Prespecified sensitivity (run separately)

```powershell
& $PY "$R32\23_conditional_poisson_stage32.py" `
  --base "$BASE" `
  --panel-path "$PANEL" `
  --validation all `
  --include-continuing
```

Here continuing reported flood days are included as *non-onset* reference days, not as additional events. Do not confuse this sensitivity with the main analysis.

## 5. Draft the exploratory figure

```powershell
& $PY "$R32\24_stage32_figures.py" --base "$BASE"
```

Outputs are `Figure2_stage32_EXPLORATORY.png` and `.svg`.

## Scientific limitations before journal-quality claims

- Conditional-Poisson models here report penalized coefficients and holdout diagnostics, **not** final standard errors, storm/city-clustered uncertainty, or externally validated causal estimates.
- D3 and D2 are not nested, so use D1→D3 to isolate P3H and D1→D2 for SM1.
- No-report days are unmatched news *referents*, not verified flood-free days; Google Groundsource has reporting, location, date and flood-type selection bias.
- Daily weather aggregation uses UTC dates. Report date may differ from physical flood onset; assess ±1d/±2d exposure lag and local-date sensitivity before mechanism claims.
- Shared storms can generate onsets in adjacent grids; check storm- or region-block clustering/bootstrapping and source overlap.
- Compare complete-case retained strata and events by city, year, climate zone, distance to coast; spatially clustered ERA5 gaps can bias generalization.
- Fit final city-heterogeneity interactions/hierarchical city effects only *after* main Stage23 model eligibility and out-of-sample behavior are acceptable.
