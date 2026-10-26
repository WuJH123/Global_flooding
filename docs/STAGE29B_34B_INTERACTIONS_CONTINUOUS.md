# Stage29b & Stage34b: mechanistic ablation / continuous city environment modifiers

These scripts consume EXISTING climate panel and E2020 GHSL-MERIT-CRU context.
No Earth Engine calls, new climate exports, or changes to production files.

Scientific rationale:
- Stage29 used BOTH P24 x API7 and C3 residual x API7; joint gain does not
  identify which interaction adds transferable information.
- Stage29b estimates four strict nested fits B0(base weather), B1(+P24xAPI7),
  B2(+C3xAPI7), B3(+both). Compare B0-B1 and B2-B3 to isolate P24xAPI7;
  B0-B2 and B1-B3 isolate C3xAPI7. All predictions share risk sets.
- P24/API7 surface is relative CONDITIONAL reported-onset intensity compared
  with the TRAIN median weather reference with SM1 and C3 fixed at 0. It is
  NOT flood probability or physically observed flood susceptibility.
  Low training-support regions are masked in the figure.
- Stage34b compares global weather model, prior T1/T2 urban classes, and
  continuous P24-by-built/terrain/climate/urban moderators on an IDENTICAL
  city/day/holdout sample. Static city main effects are unidentified under
  grid/month matched conditioning. Train-city-only transforms and identical
  risk sets are enforced.
- All stage34b selected static moderators must be non-missing in a city.
  This complete-case restriction changes sample size versus original Stage34,
  so NEVER compare absolute losses from Stage34 to Stage34b directly.
- Stage29b uses full weather-complete M3 sample, whereas Stage34b uses
  observed/typed cities and defaults to events in 2020 or later. Do not
  compare their raw logloss numbers with one another.
- 95% coefficient uncertainty: train spatial sandwich. Heldout gain
  uncertainty: fixed-model paired spatial-block bootstrap. Storms spanning
  spatial blocks remain unresolved; do not call these storm-independent CIs.

## Copy-paste Windows PowerShell commands

In this section each command is a SINGLE LINE; no trailing backtick, so it
is safe to paste in PS without accidental continuation whitespace.

1) Sync, set paths and test:

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main
$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R32="$BASE\pipelines\r32"
$R33="$BASE\pipelines\r33"
$PANEL="$BASE\standard_calendar_22_compact\22_standard_daily_with_climate.parquet"
$STATIC="$BASE\mechanism_project\05_static_merged\grid_static_context_m3.parquet"
$TYPES="$BASE\stage33_city_typology\33_city_environment_types.csv"
$CITY="$BASE\mechanism_project\30_urban_context\30b_city_features_2020.parquet"
$SELECT="$BASE\stage33_feature_selection\32_feature_selection.json"
& $PY -m pip install -U numpy pandas scipy duckdb pyarrow scikit-learn matplotlib pytest
& $PY -m pytest -q tests/test_stage29b_34b.py
@($PANEL,$STATIC,$TYPES,$CITY,$SELECT) | ForEach-Object { if (-not (Test-Path $_)) { throw "MISSING $_" } else { Write-Host "[OK] $_" } }
~~~

2) Small smoke - outputs have _smoke12000 suffix and are NOT production:

~~~powershell
& $PY "$R32\29b_interaction_ablation_surface.py" --base "$BASE" --panel-path "$PANEL" --aggregation mean --validation city --max-strata 12000 --bootstrap-reps 99 --surface-points 15
if ($LASTEXITCODE -ne 0) { throw "29b SMOKE failed" }
& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --panel-path "$PANEL" --static-path "$STATIC" --city-types "$TYPES" --city-environment "$CITY" --selection-json "$SELECT" --type-epoch 2020 --aggregation mean --validation city --max-strata 12000 --bootstrap-reps 99
if ($LASTEXITCODE -ne 0) { throw "34b SMOKE failed" }
~~~

3) Full global Stage29b (both 20km IMERG average and max, all validation folds):

~~~powershell
& $PY "$R32\29b_interaction_ablation_surface.py" --base "$BASE" --panel-path "$PANEL" --aggregation both --validation all --seed 2026 --block-cells 25 --bootstrap-reps 399 --surface-points 25
if ($LASTEXITCODE -ne 0) { throw "29b failed" }
Import-Csv "$BASE\stage32_interaction_ablation\29b_ablation_gains.csv" | Format-Table validation,rain_aggregation,baseline,augmented,gain,gain_ci_low,gain_ci_high -AutoSize
~~~

Files at stage32_interaction_ablation/
- 29b_ablation_gains.csv, 29b_coefficients.csv
- 29b_conditional_surface.csv, 29b_conditional_curves.csv
- 29b_surface_temporal_mean.png, spatial/max/city correspondingly
- 29b_manifest.json.

4) Full Stage34b (shared complete-case sample vs categorical typology):

~~~powershell
& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --panel-path "$PANEL" --static-path "$STATIC" --city-types "$TYPES" --city-environment "$CITY" --selection-json "$SELECT" --type-epoch 2020 --aggregation mean --validation all --seed 2026 --block-cells 25 --bootstrap-reps 399
if ($LASTEXITCODE -ne 0) { throw "34b failed" }
Import-Csv "$BASE\stage33_continuous_response\34b_heldout_comparison.csv" | Format-Table validation,baseline,augmented,gain,gain_ci_low,gain_ci_high,test_onsets -AutoSize
Import-Csv "$BASE\stage33_continuous_response\34b_P24_environment_marginal_ratios.csv" | Format-Table validation,environment_feature,environment_level,conditional_P24_ratio,ratio_ci_low,ratio_ci_high -AutoSize
Get-Content "$BASE\stage33_continuous_response\34b_manifest.json" -Raw
~~~

Files at stage33_continuous_response/
- 34b_heldout_comparison.csv and 34b_coefficients.csv
- 34b_P24_environment_marginal_ratios.csv
- 34b_manifest.json with included cities/cases, train transforms.

Comparison to focus:
- E0_global to E1_type: classification adds anything?
- E0_global to S_built/S_relief/S_climate/S_urban: each environment
  moderator on an equal sample.
- E0_global to E2_continuous_P24: combined continuous moderators.
- E1_type to E2_continuous_P24: direct paired heldout categorical vs
  continuous predictive performance. This comparison is NON-NESTED;
  interpret heldout gain, not chi-square significance.

5) Exploratory antecedent-by-environment model, SEPARATE output directory:

~~~powershell
& $PY "$R33\34b_continuous_environment_response.py" --base "$BASE" --panel-path "$PANEL" --static-path "$STATIC" --city-types "$TYPES" --city-environment "$CITY" --selection-json "$SELECT" --type-epoch 2020 --aggregation mean --validation all --seed 2026 --block-cells 25 --bootstrap-reps 399 --extended-api7 --out "$BASE\stage33_continuous_response_api7"
~~~

If the conditional city interaction coefficients appear distinct but test
gain CIs still overlap zero, report candidate environmental effect
modification only; avoid claims of validated city-specific mechanism.

## Relevant published methodological context

- Rushworth et al., Biometrics, 2013:
  https://pubmed.ncbi.nlm.nih.gov/23409735/
- Webb et al., Nature Communications, 2026:
  https://www.nature.com/articles/s41467-026-69286-3
- Urban development pattern influence on extreme rainfall,
  Nature Communications, 2024:
  https://www.nature.com/articles/s41467-024-48533-5

Independent storm identification, article dating errors, external
inundation/flow validation and bias in media reporting remain P0
requirements before strong physical mechanism and climate change claims.
