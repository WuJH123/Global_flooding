# -*- coding: utf-8 -*-
"""Exploratory city-level 1:4 conditional-softmax slopes (NOT final inference).

Enforce fixed stable city IDs and exactly 5 rows / 1 case per stratum.
Estimate transformation scales AFTER filtering complete groups. Strongly pooled
ridge slopes are descriptive and not a statistically fitted hierarchical model.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp

FEATURES=['logp24','logapi7','sm1']

def fit_conditional(x,y,prior=None,lam=0.):
    n,d=x.shape[0],x.shape[-1]
    initial=np.zeros(d) if prior is None else np.asarray(prior,dtype=float)
    def obj(b):
        e=np.einsum('ntk,k->nt',x,b,optimize=True)
        logp=e-logsumexp(e,axis=1,keepdims=True)
        probs=np.exp(logp)
        loss=-np.mean(logp[np.arange(n),y])
        grad=np.mean(np.einsum('nt,ntk->nk',probs,x)-x[np.arange(n),y],axis=0)
        delta=b-initial
        return float(loss+.5*lam*np.dot(delta,delta)),grad+lam*delta
    opt=minimize(obj,initial,jac=True,method='L-BFGS-B',options={'maxiter':300,'ftol':1e-10})
    return opt.x,float(opt.fun),bool(opt.success)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',default=str(Path(__file__).with_name('project_paths.json')))
    ap.add_argument('--top-cities',type=int,default=30)
    ap.add_argument('--min-strata',type=int,default=80)
    ap.add_argument('--ridge',type=float,default=.02)
    ap.add_argument('--out',default=None)
    a=ap.parse_args()
    if a.top_cities<1 or a.min_strata<1 or a.ridge<0:ap.error('Invalid settings')
    cfg=json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    root=Path(cfg['project_root'])/'mechanism_project'
    out=Path(a.out or root/'06_city_response_pilot');out.mkdir(parents=True,exist_ok=True)
    mapping=pd.read_csv(root/'01_cities'/'m3'/'grid_primary_city.csv',dtype={'grid_id':str,'city_id':str})
    if mapping.grid_id.duplicated().any():raise RuntimeError('Duplicate primary city grid IDs')
    if mapping.city_id.fillna('').str.startswith('ucdb_row_').any():
        raise RuntimeError('Temporary row-based city IDs. Rerun corrected 01_link_cities.py.')
    mapping=mapping.loc[mapping.city_id.notna(),['grid_id','city_id']]
    con=duckdb.connect()
    con.register('city_map',mapping)
    source=str(Path(cfg['primary_matched'])).replace("'","''")
    # Read only selected fields, not all 3.2 million-row features.
    con.execute(f'''CREATE TEMP VIEW observations AS
        SELECT m.stratum_id,m.grid_id,CAST(m.is_case AS INTEGER) AS is_case,
            m.imerg_p24_utc_mm_mean AS p24,
            m.era5_api7_mm_mean AS api7,
            m.era5_sm1_prevday_mean AS sm1,
            c.city_id
        FROM read_parquet('{source}') AS m
        INNER JOIN city_map AS c ON m.grid_id=c.grid_id''')
    counts=con.execute('''SELECT city_id,COUNT(DISTINCT stratum_id) AS available_strata
        FROM observations GROUP BY city_id ORDER BY available_strata DESC,city_id''').fetchdf()
    counts.to_csv(out/'city_sample_counts.csv',index=False)
    selected=counts.loc[counts.available_strata>=a.min_strata,'city_id'].astype(str).head(a.top_cities).tolist()
    if not selected:raise RuntimeError('No eligible UCDB cities')
    con.register('selected_cities',pd.DataFrame({'city_id':selected}))
    df=con.execute('''SELECT o.* FROM observations o
        INNER JOIN selected_cities s USING(city_id)
        ORDER BY o.city_id,o.stratum_id,o.is_case DESC''').fetchdf()
    con.close()
    df[['p24','api7','sm1']]=df[['p24','api7','sm1']].apply(pd.to_numeric,errors='coerce')
    df=df.dropna(subset=['p24','api7','sm1']).copy()
    if df[['p24','api7']].lt(0).any().any():raise RuntimeError('Negative precipitation/API values')
    validity=df.groupby(['city_id','stratum_id'],sort=False).is_case.agg(['size','sum'])
    valid_index=validity.index[(validity['size']==5)&(validity['sum']==1)]
    df=df.set_index(['city_id','stratum_id']).loc[valid_index].reset_index()
    df=df.sort_values(['city_id','stratum_id','is_case'],ascending=[True,True,False]).reset_index(drop=True)
    if len(df)==0 or len(df)%5:raise RuntimeError('No complete case-control strata')
    assert df.groupby(['city_id','stratum_id']).is_case.agg(['size','sum']).eq([5,1]).all().all()
    df['logp24']=np.log1p(df.p24)
    df['logapi7']=np.log1p(df.api7)
    means=df[FEATURES].mean().to_numpy(dtype=float)
    sd=df[FEATURES].std(ddof=0).replace(0,1).to_numpy(dtype=float)
    df[FEATURES]=(df[FEATURES]-means)/sd
    arr=df[FEATURES].to_numpy(dtype=float).reshape(-1,5,3)
    yy=df.is_case.to_numpy(dtype=int).reshape(-1,5).argmax(axis=1)
    pooled, loss, success=fit_conditional(arr,yy)
    if not success:raise RuntimeError('Pooled conditional optimization failed')
    print('[PILOT CITIES]',len(selected),'complete strata',len(arr),'[POOLED]',pooled,'loss',loss)
    rows=[]
    for city,sub in df.groupby('city_id',sort=False):
        x=sub[FEATURES].to_numpy(dtype=float).reshape(-1,5,3)
        y=sub.is_case.to_numpy(dtype=int).reshape(-1,5).argmax(axis=1)
        if len(x)<a.min_strata:continue
        betas,ls,ok=fit_conditional(x,y,prior=pooled,lam=a.ridge)
        rows.append(dict(city_id=city,complete_matched_strata=len(x),
            coef_p24=betas[0],coef_api7=betas[1],coef_sm1=betas[2],
            penalized_training_loss=ls,converged=ok))
    result=pd.DataFrame(rows)
    if result.empty:raise RuntimeError('No city retains min-strata after complete-case QC')
    result.to_csv(out/'city_shrunk_response_pilot.csv',index=False)
    meta={'features':FEATURES,'transform':'log1p(P24), log1p(API7), untransformed SM1',
          'scaling_mean':means.tolist(),'scaling_sd':sd.tolist(),'selected_city_pooled':pooled.tolist(),
          'ridge':a.ridge,'n_completed_city_models':len(result),
          'limitations':'Historical nonstandard 1:4 referents; no out-of-city evaluation or uncertainty; not causal, not final paper model'}
    (out/'scaling_and_pooled.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print('[DONE]',len(result),'city models in',out)

if __name__=='__main__':main()
