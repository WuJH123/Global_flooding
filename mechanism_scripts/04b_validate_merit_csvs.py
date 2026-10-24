# -*- coding: utf-8 -*-
"""Audit MERIT production CSV exports BEFORE invoking 05_finalize_static_table.py.
Never silently merge test and full exports or both a failed parent and split retry.

Examples:
  python 04b_validate_merit_csvs.py --merit-dir E:\\Global_flooding\\MERIT_all
  python 04b_validate_merit_csvs.py --merit-dir E:\\Global_flooding\\MERIT_all --allow-partial
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

EXPECTED = ['grid_id','grid_x','grid_y','chunk_id',
            'merit_elv_1km_mean','merit_elv_1km_min','merit_elv_1km_max',
            'merit_local_relief_m_approx','merit_slope_deg_1km_mean',
            'merit_slope_deg_1km_max','merit_hydro_adjusted_elev_1km_mean',
            'merit_stream_upa100_fraction_1km_mean','merit_upa_km2_1km_max']
PRODUCTION = re.compile(r'^merit_m3_c\d+_s\d+of\d+\.csv$',re.I)


def validate(merit_dir: Path, upload_csv: Path, output: Path, allow_partial: bool):
    upload = pd.read_csv(upload_csv, dtype={'grid_id':str})
    if len(upload) != 26317 or upload.grid_id.nunique() != 26317:
        raise ValueError('Local master CSV is not exactly 26,317 unique M3 grids')
    outputs = sorted(p for p in merit_dir.rglob('*.csv') if PRODUCTION.fullmatch(p.name))
    if not outputs:
        raise FileNotFoundError('No production MERIT CSVs found. Test CSVs are intentionally excluded.')
    frames = []
    for file in outputs:
        part = pd.read_csv(file, dtype={'grid_id':str})
        missing = sorted(set(EXPECTED) - set(part.columns))
        if missing:
            raise ValueError(f'{file.name} missing output fields: {missing}')
        if part.grid_id.isna().any() or part.grid_id.duplicated().any():
            raise ValueError(f'{file.name} has null or duplicated grid_id')
        print('[FILE]',file.name,'rows',len(part),'invalid slope',int((part.merit_slope_deg_1km_mean.dropna()<0).sum()))
        frames.append(part[EXPECTED])
    result = pd.concat(frames, ignore_index=True)
    if result.grid_id.duplicated().any():
        dup = result.loc[result.grid_id.duplicated(keep=False),'grid_id'].head(10).tolist()
        raise RuntimeError('MERIT outputs contain duplicated grids, likely parent+split retry: '+str(dup))
    if not result.grid_id.isin(upload.grid_id).all():
        raise RuntimeError('MERIT output has grid IDs outside the M3 input')
    src = upload[['grid_id','chunk_id']].copy()
    merged = result[['grid_id','chunk_id']].merge(src,on='grid_id',how='left',suffixes=('_output','_input'),validate='one_to_one')
    if not merged.chunk_id_output.eq(merged.chunk_id_input).all():
        raise RuntimeError('Chunk IDs do not match uploaded master CSV')
    fraction = pd.to_numeric(result['merit_stream_upa100_fraction_1km_mean'], errors='coerce')
    badfrac = int(((fraction<-1e-5)|(fraction>1.00001)).sum())
    badslope = int(((pd.to_numeric(result.merit_slope_deg_1km_mean,errors='coerce') < 0)).sum())
    badupa = int(((pd.to_numeric(result.merit_upa_km2_1km_max,errors='coerce') < 0)).sum())
    high = pd.to_numeric(result.merit_elv_1km_max,errors='coerce')
    low = pd.to_numeric(result.merit_elv_1km_min,errors='coerce')
    relief = pd.to_numeric(result.merit_local_relief_m_approx,errors='coerce')
    badrelief = int(((relief < -0.001) | ((relief-(high-low)).abs() > 0.01)).sum())
    if badfrac or badslope or badupa or badrelief:
        raise ValueError(f'Invalid physical outputs fraction={badfrac} slope={badslope} upa={badupa} relief={badrelief}')
    missing_ids = sorted(set(upload.grid_id) - set(result.grid_id))
    if missing_ids and not allow_partial:
        raise RuntimeError(f'{len(missing_ids)} expected grids missing; outputs incomplete. '
                           'Use --allow-partial only for intermediate diagnosis.')
    qc = {
        'files': [p.name for p in outputs], 'n_production_files': len(outputs),
        'input_grids': len(upload), 'exported_grids': len(result),
        'missing_input_grids': len(missing_ids),
        'all_metric_null_grids': int(result[[x for x in EXPECTED if x.startswith('merit_')]].isna().all(axis=1).sum()),
        'correction': "MERIT 'hnd' is hydro-adjusted elevation, not HAND. No HAND value calculated.",
        'status':'PASS' if not missing_ids else 'PARTIAL'
    }
    output.mkdir(parents=True, exist_ok=True)
    (output/'04b_merit_qa.json').write_text(json.dumps(qc,indent=2,ensure_ascii=False),encoding='utf-8')
    if missing_ids:
        pd.DataFrame({'grid_id':missing_ids}).to_csv(output/'04b_missing_merit_grid_ids.csv',index=False)
    print('[QC]',json.dumps(qc,ensure_ascii=False,indent=2))
    return qc


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--merit-dir',type=Path,required=True)
    p.add_argument('--upload-csv',type=Path,default=Path(r'E:\Global_flooding\mechanism_project\01_cities\m3\merit_m3_grids_gee_upload.csv'))
    p.add_argument('--out',type=Path,default=Path(r'E:\Global_flooding\mechanism_project\04_merit_validation'))
    p.add_argument('--allow-partial',action='store_true')
    args=p.parse_args()
    validate(args.merit_dir,args.upload_csv,args.out,args.allow_partial)

if __name__ == '__main__':
    main()
