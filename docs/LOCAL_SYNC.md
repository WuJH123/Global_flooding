# Windows local sync: preserve data and local edits

**One-off inspection:**
```powershell
cd E:\Global_flooding
git rev-parse --is-inside-work-tree
git status --short
git remote -v
```
If `git rev-parse` fails, `E:\Global_flooding` is not a Git checkout: **do not initialize in place** over your data. Instead clone to a code-only folder:
```powershell
cd E:\
git clone https://github.com/WuJH123/Global_flooding.git Global_flooding_code
cd E:\Global_flooding_code
```
Run scripts with `--base E:\Global_flooding` where supported, or sync only reviewed source files into the existing code location. Python paths should not be redirected to a code-only folder by accident.

**If already a clean checkout:**
```powershell
cd E:\Global_flooding
git status --short
git fetch origin
git pull --ff-only origin main
.\.venv\Scripts\python.exe tools\verify_project.py --base E:\Global_flooding
```
If `git status --short` shows edits, back them up / commit to a local branch before pulling; avoid a blind overwrite. `git pull --ff-only` refuses complex merges and is safer than an automatic merge.

Do not add raw `*.parquet`, `*.nc`, `*.csv`, raster data, generated model outputs, secrets or Earth Engine credential files. GitHub's normal repository size limits motivate keeping large scientific datasets in external storage (see GitHub Docs: managing large files). Use Git LFS only for deliberately tracked binaries with storage budgeting.

**Future work:** a new branch and PR per stage; run project QC locally, and only merge after a reviewable diff and tests. Keep an input/algorithm/output provenance record for each finished result.
