# Global flooding：全球城市洪涝观测与气候分析

> **Canonical project management (2026-10-24):** Please use [Project Overview](docs/PROJECT_OVERVIEW.md), [Data Registry](docs/DATA_REGISTRY.md), [Local Git Sync](docs/LOCAL_SYNC.md), and [Results 3.2 pipeline](pipelines/r32/README.md) before running new tasks. Historical scripts at repository root are intentionally preserved; new Results 3.2 tools live in `pipelines/r32/`. Data and result files remain local; the repository cannot verify your Windows disks.

本仓库保存 Groundsource 全球洪涝报告的处理脚本。研究流程将报告定位到 20 km 格网，结合 GHSL 定义城市格网与洪涝事件，再建立对照、提取气候变量，进行配对诊断、探索性建模和观测覆盖分析。`mechanism_scripts/` 是城市、流域、地形与气候背景变量的独立补充流程。

## 代码流程

| 阶段 | 脚本 | 主要输入 → 输出 |
| --- | --- | --- |
| 1. 数据检查和格网化 | `01`–`04` | Groundsource 原始点位 → 标准化记录、20 km 格网日、唯一格网 |
| 2. 城市范围 | `05` | 格网日 + GHSL/GEE 导出 → 城市格网目录，含 M1/M2/M3 定义 |
| 3. 事件与对照 | `06` | M3 城市格网 → 洪涝 episode、匹配对照及质控 |
| 4. 气候请求与导出 | `07`、`07b`、`08`、`10` | 事件/对照日期 → 分块请求、Earth Engine 任务和 Drive 导出 |
| 5. 气候合并与缺失处理 | `11`、`11a`、`11b`、`12b`、`14_check`、`15` | 下载的气候 CSV → 质控后的气候表；ERA5 回补为可选流程 |
| 6. 分析与稳健性 | `14_local_first`、`16`–`19` | 匹配样本与气候表 → 描述诊断、探索性模型、消融和参照日审计 |
| 7. 标准日历设计 | `20`、`20b`、`21`、`21a`、`22` | 全部 M3 episode → 同月同星期几风险集、缺失天气请求及合并面板 |
| 8. 观测覆盖 | `31`、`31a`–`31c` | 已报告格网 + 全球城市格网参照 → 覆盖率、参照数据和验证表 |
| 9. 机制背景 | `mechanism_scripts/00`–`10` | 城市、流域、MERIT、CRU 等 → 格网静态变量、城市试验分析和验证 |

文件名前缀是开发阶段编号，并非必须一次从头运行到底。`20b_rebuild_full_episode_calendar.py` 从**全部 M3 事件起始日**重建标准日历；`20_build_standard_calendar_panel.py` 的较早版本沿用原始配对样本。标准日历工作的具体顺序和科学限制见 [21_README.txt](21_README.txt)。`31` 系列是观测代表性分析，不应把未报告日期自动解释为无洪涝。

## 运行准备

1. 安装 Python 依赖：`python -m pip install -r requirements.txt`。涉及 Earth Engine 的脚本还需要可用的 Earth Engine 项目、认证、Cloud Storage 和 Drive 权限；上传/提交任务可能产生费用。
2. 准备本机输入数据。脚本中的默认路径大多指向 `E:\Global_flooding`、`E:\数据集`，机制流程另有 `D:` 路径。迁移机器时先调整脚本顶部常量或其已有命令行参数。
3. 机制流程复制 `mechanism_scripts/project_paths.example.json` 为 `mechanism_scripts/project_paths.json`，将占位值改为本机真实路径。配置中的原始数据、生成结果和 GEE 资产必须分别取得访问权限。
4. 先运行本地检查/准备步骤并查看输出的 QC 文件；执行 `08`、`10`、`12b`、`21a` 或 `21 --submit` 前，核对项目、存储桶、资产名、任务数与配额。`21` 默认仅规划或限制提交数量，详见脚本参数和 [21_README.txt](21_README.txt)。

主要外部数据包括 Groundsource、GHSL、ERA5-Land、IMERG、MERIT Hydro、HydroATLAS、GHS-UCDB、CRU 等；本仓库没有重新分发这些原始数据。处理脚本的具体字段、阈值与输出以源码及阶段 QC 为准。

## 版本管理范围

提交 Python/PowerShell 源码、运行说明、依赖清单和配置示例。原始下载、栅格/矢量、CSV/Parquet/DuckDB 结果、任务日志、虚拟环境和论文草稿留在本地，由 `.gitignore` 排除。需要复核数值时，先准备相应数据并重跑阶段脚本；仅拉取仓库不能直接复现全部结果。当前代码包含本机绝对路径，尚未做跨机器路径改造。
