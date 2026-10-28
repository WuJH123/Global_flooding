# WARNING: This historical runbook was superseded for Stage45/53 on 2026-10-28.
# ALWAYS use docs/R34_STAGE45_V3_GEE_BOUNDS_RECOVERY.md for new SMOD/annual tasks.
# Older V2 production command geometry is invalid, and old V2 filenames cannot be used.

# R34 GEE main-only runbook — 2026-10-28

此提交直接更新 main，无需新分支。使用 PowerShell，所有命令在 E:\Global_flooding 根目录执行。请勿重跑已有114份GHSL、Stage22 IMERG/ERA5事件参照气候、MERIT、BasinATLAS、CRU。

## 0. 本地代码同步与测试

~~~powershell
cd E:\Global_flooding
git status --short
git switch main
git pull --ff-only origin main

$BASE="E:\Global_flooding"
$PY="$BASE\.venv\Scripts\python.exe"
$R34="$BASE\pipelines\r34"
$PROJECT="eco-sector-503721-b7"
$M3="projects/$PROJECT/assets/groundsource_merit_m3_grids"
$ASSET="projects/$PROJECT/assets/urban_universe_smod2020_20km_v3"
& $PY -m pytest -q tests/test_stage45_50_scripts.py tests/test_stage46_47_gee_context.py
~~~

## 1. Stage45 — 构建真正E2020全城市总体

~~~powershell
& $PY "$R34\45_smod2020_export.py"
& $PY "$R34\45_smod2020_export.py" --probe
& $PY "$R34\45_smod2020_export.py" --submit
~~~

GEE Task 名 SMOD2020_20KM_V3；Drive GlobalFlood_E2020_Urban；将 smod2020_20km_v3.tif 下载到 E:\Global_flooding\R34_SMOD2020。

~~~powershell
$TIF="$BASE\R34_SMOD2020\smod2020_20km_v3.tif"
$UCSV="$BASE\results_31\urban_universe_smod2020_20km_v3.csv"
& $PY "$R34\45b_smod2020_to_asset.py" --tif "$TIF" --out "$UCSV" --urban-min .001 --valid-min .95 --chunk-size 5000
$QC=Get-Content "$BASE\results_31\urban_universe_smod2020_20km_v3_qc.json" -Raw | ConvertFrom-Json
$LASTCHUNK=[int]$QC.chunks-1
$CHUNKS="0-$LASTCHUNK"
Write-Host "N2020=" $QC.rows "Chunks=" $CHUNKS
~~~

在 GEE Code Editor Assets > NEW > Table upload 上传 CSV；longitude/latitude 为WGS84点，仅用于Table导入，GEE脚本使用 grid_x/grid_y 重建20km等面积矩形。上传到：

projects/eco-sector-503721-b7/assets/urban_universe_smod2020_20km_v3

验证Asset行数与QC JSON一致、无重复grid_id、chunk_id为数值列后，再执行后续任务。

## 2A. Stage46 — 完整E2020城市域GHSL五期

~~~powershell
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --verify-asset --probe-projection
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --chunks 0 --test --submit --max-tasks 1
~~~

TEST成功、数值比对通过后再生产：

~~~powershell
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --chunks 0 --submit --max-tasks 1
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --chunks "$CHUNKS"
& $PY "$R34\46_gee_ghsl_epoch_context.py" --asset "$ASSET" --chunks "$CHUNKS" --submit --max-tasks 1
~~~

重复提交前核查历史任务和Drive同名文件，确认不是旧Task遗漏。全部CSV下载至 R34_GHSL_Epochs_all 后：

~~~powershell
& $PY "$R34\48_validate_new_context_csvs.py" --kind ghsl --csv-root "$BASE\R34_GHSL_Epochs_all" --expected-ids "$UCSV" --expected-years 2000,2005,2010,2015,2020 --require-full
~~~

## 2B. 仅补M3历史BUILT-S和NRES（方案A已完成时不必重复）

~~~powershell
& $PY "$R34\46b_gee_built_only.py" --asset "$M3" --epochs 2000,2005,2010,2015 --chunks 0 --test --submit --max-tasks 1
& $PY "$R34\46b_gee_built_only.py" --asset "$M3" --epochs 2000,2005,2010,2015 --chunks 0 --submit --max-tasks 1
& $PY "$R34\46b_gee_built_only.py" --asset "$M3" --epochs 2000,2005,2010,2015 --chunks 1-5 --submit --max-tasks 1
& $PY "$R34\50b_qc_new_exports.py" --kind built --csv-root "$BASE\R34_BUILT_History_all" --expected-ids "$BASE\mechanism_project\01_cities\m3\grid_centers.csv" --years 2000,2005,2010,2015 --require-full
~~~

## 3. Stage49 — GAIA历史不透水分类，E2020城市域

~~~powershell
& $PY "$R34\49_gee_gaia_impervious_history.py" --asset "$ASSET" --verify-asset --probe-projection
& $PY "$R34\49_gee_gaia_impervious_history.py" --asset "$ASSET" --chunks 0 --test --submit --max-tasks 1
& $PY "$R34\49_gee_gaia_impervious_history.py" --asset "$ASSET" --chunks 0 --submit --max-tasks 1
& $PY "$R34\49_gee_gaia_impervious_history.py" --asset "$ASSET" --chunks "$CHUNKS" --submit --max-tasks 1
& $PY "$R34\48_validate_new_context_csvs.py" --kind gaia --csv-root "$BASE\R34_GAIA_all" --expected-ids "$UCSV" --expected-years 2000,2005,2010,2015,2018 --require-full
~~~

GAIA表示大于50%不透水的分类像元，不等于地下排水设施容量。

## 4. Stage53 — 全球年度气候GeoTIFF，先单年局部测试

ERA5-Land 2020：

~~~powershell
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2020 --probe
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2020 --test --submit --max-tasks 1
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2020 --submit --max-tasks 1
~~~

IMERG 2020（每年约17500半小时影像；如整体全球Task超时则停下，改按月或地理分区）：

~~~powershell
& $PY "$R34\53_gee_annual_raster_exports.py" --source imerg --years 2020 --probe
& $PY "$R34\53_gee_annual_raster_exports.py" --source imerg --years 2020 --test --submit --max-tasks 1
& $PY "$R34\53_gee_annual_raster_exports.py" --source imerg --years 2020 --submit --max-tasks 1
~~~

仅在2020全球Task成功并通过原始气候时间序列抽样数值比对后，继续：

~~~powershell
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2000-2025
& $PY "$R34\53_gee_annual_raster_exports.py" --source era5 --years 2000-2025 --submit --max-tasks 1
& $PY "$R34\53_gee_annual_raster_exports.py" --source imerg --years 2000-2025
& $PY "$R34\53_gee_annual_raster_exports.py" --source imerg --years 2000-2025 --submit --max-tasks 1
~~~

注意：max-tasks 1 每次调用最多一个新任务，不保证旧历史任务列表完整。TEST、投影检查不证明所有年全球Task能成功。

## 5. Stage53b — 正式年度GeoTIFF转城市Parquet（硬性26年检查）

将正式年度TIF放入单独目录：
E:\Global_flooding\R34_Annual_ERA5_TIF_all 和 E:\Global_flooding\R34_Annual_IMERG_TIF_all。TEST文件必须放到其他目录。

~~~powershell
& $PY "$R34\53b_raster_to_grid_parquet.py" --source era5 --tif-root "$BASE\R34_Annual_ERA5_TIF_all" --grid-csv "$UCSV" --out "$BASE\climate_annual_53\era5" --require-full
& $PY "$R34\53b_raster_to_grid_parquet.py" --source imerg --tif-root "$BASE\R34_Annual_IMERG_TIF_all" --grid-csv "$UCSV" --out "$BASE\climate_annual_53\imerg" --require-full
~~~

--require-full 要求2000..2025完整26年且全部格网中心在TIF内；气候缺失为NaN，不填0。

## 科学与产物边界

- 新增：E2020城市总体；建成历史/不透水分类；完整年度气候背景。
- 复用：Stage22事件参照日期的IMERG雨峰/ERA5前期湿润；M3静态MERIT/BasinATLAS/CRU、城市crosswalk。
- 年度最大降雨是先在原生像元时间维度取极值，再将年极值面积平均到20km；这不同于20km每日平均降雨的最大值。
- 本次未实现GFD或Sentinel-1的独立事件真值验证，也未实现CMIP6未来情景；二者不可称为已经完成的GEE生产任务。
- ERA5/IMERG的局部TEST和输出结构QC均不能替代气象数值源校准，尤其是海岸缺测与1999/2000年IMERG的日期覆盖。
