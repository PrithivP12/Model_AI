"""Matched-observer N checks; no tissue production is called verified residue return."""
from pathlib import Path
from datetime import date
import json
import numpy as np
import pandas as pd
from ..io import write_json
from .experiment import simulate


def prior_density(rows,sample,allow_year_bounds=False):
    lo,hi=sample['depth_cm'];groups={}
    for r in rows:
        bd=r['values'].get('SOIL01');t=r['sample_date'];bounds=r['depth_cm']
        if bd is None or not .5<=bd<=2.65 or not bounds:continue
        if t:earliest=latest=t
        elif allow_year_bounds and isinstance(r.get('year'),(int,float)):
            year=int(r['year']);earliest=f'{year}-01-01';latest=f'{year}-12-31'
        else:continue
        if latest>sample['sample_date']:continue
        if (date.fromisoformat(sample['sample_date'])-date.fromisoformat(earliest)).days>730:continue
        t=latest  # Ordering bound only, never a claimed measurement date.
        key=tuple(bounds)
        if key not in groups or t>groups[key][0]:groups[key]=(t,[r])
        elif t==groups[key][0]:groups[key][1].append(r)
    edges=sorted({lo,hi}|{max(lo,a)for a,b in groups if min(hi,b)>max(lo,a)}|{min(hi,b)for a,b in groups if min(hi,b)>max(lo,a)})
    mass=0.;covered=0.;sources=[]
    for a,b in zip(edges,edges[1:]):
        candidates=[(x,y,rs)for (x,y),(t,rs)in groups.items()if x<=a and y>=b]
        if not candidates:continue
        x,y,rs=min(candidates,key=lambda item:item[1]-item[0]);bd=float(np.mean([r['values']['SOIL01']for r in rs]))
        mass+=bd*(b-a);covered+=b-a;sources.extend(r['source_row']for r in rs)
    return (mass/covered,sorted(set(sources)))if covered>=hi-lo-1e-8 else (None,[])


def run_carryover(root,output,weather_dir=None,audit_dir=None,density_policy="cosampled"):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed carryover diagnosis')
    audit_dir=Path(audit_dir or root/'reports/rotation-recovery/input-audit');weather_dir=Path(weather_dir or root/'reports/rotation-recovery/weather-exact')
    records=json.loads((audit_dir/'records.json').read_text());soils=json.loads((audit_dir/'soil_observations.json').read_text());old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    if density_policy not in ['cosampled','dated_prior_proxy','year_bounded_proxy']:raise ValueError('Unknown bulk-density support policy')
    samples=[]
    for rid,rec in records.items():
        for sample in soils[rec['site_id']+':'+rec['plot_id']]:
            v=sample['values'];t=sample['sample_date'];bounds=sample['depth_cm']
            if not t or t<'2011-01-01'or t>f"{rec['year']}-12-31"or not bounds:continue
            if any(v[k]is None for k in ['SOIL15','SOIL22']):continue
            if not 0<=v['SOIL15']<=500 or not 0<=v['SOIL22']<=500:continue
            bd=v['SOIL01'];bd_sources=[sample['source_row']]if bd is not None else []
            if bd is None and density_policy in ['dated_prior_proxy','year_bounded_proxy']:
                bd,bd_sources=prior_density(soils[rec['site_id']+':'+rec['plot_id']],sample,allow_year_bounds=density_policy=='year_bounded_proxy')
            if bd is None or not .5<=bd<=2.65:continue
            if bounds[1]>120:continue
            samples.append({'record_id':rid,'site_id':rec['site_id'],'farm_id':rec['farm_id'],'plot_id':rec['plot_id'],'date':t,'source_row':sample['source_row'],
                'top_cm':bounds[0],'bottom_cm':bounds[1],'bulk_density_g_cm3':bd,'density_policy':density_policy,'density_source_rows':json.dumps(bd_sources),'observed_NO3_plus_NH4_mg_N_kg':v['SOIL15']+v['SOIL22']})
    # A physical sample repeated in trajectories with later target years is one observation.
    unique={}
    for row in sorted(samples,key=lambda v:records[v['record_id']]['year']):unique.setdefault((row['site_id'],row['plot_id'],row['source_row']),row)
    samples=list(unique.values());ids=sorted({row['record_id']for row in samples});rows=[]
    write_json(out/'protocol_frozen.json',{'observations':'Same-row dated NO3-N + NH4-N; bulk density is co-sampled or a traceable same-plot earlier depth-overlap proxy under the declared policy. No missing NH4 filled with zero.','density_policy':density_policy,
        'evaluation_only':True,'profile_water_observations_available':False,'observer_model':'Coarse-layer N interpolated uniformly within each layer; concentration conversion uses the reported core bulk density. Fine-earth/coarse-fragment and point-support mismatch remain uncertain.',
        'no_calibration_on_these_N_targets':True,'no_verified_full_cohort_carryover_claim':True})
    if ids:
        for model,variant,params in [('B_scenario','B_replay',{}),('C_historical','C_soil',{}),('D_harvest','C_soil',{'scheduled_harvest':True})]:
            predictions,_=simulate(root,records,old,soils,weather_dir,ids,variant,params,return_diagnostics=True)
            for sample in samples:
                estimates=[]
                for result in predictions[sample['record_id']]['scenarios']:
                    daily=next((r for r in result['daily']if r['date']==sample['date']),None)
                    if daily is None:continue
                    n=0.
                    for i,(a,b)in enumerate([(0,30),(30,60),(60,120)]):
                        overlap=max(0,min(b,sample['bottom_cm'])-max(a,sample['top_cm']))
                        n+=daily[f'mineral_n_layer_{i}_kg_ha']*overlap/(b-a)
                    soil_mass=sample['bulk_density_g_cm3']*1000*(sample['bottom_cm']-sample['top_cm'])/100*10000
                    estimates.append(n*1e6/soil_mass)
                if estimates:rows.append({**sample,'model':model,'prediction_mg_N_kg':float(np.mean(estimates)),
                    'scenario_min_mg_N_kg':min(estimates),'scenario_max_mg_N_kg':max(estimates)})
    frame=pd.DataFrame(rows)
    if not frame.empty:frame.to_csv(out/'matched_N_predictions.csv',index=False)
    metrics={}
    for model,g in frame.groupby('model')if not frame.empty else []:
        err=g.prediction_mg_N_kg-g.observed_NO3_plus_NH4_mg_N_kg
        metrics[model]={'samples':len(g),'farms':int(g.farm_id.nunique()),'MAE_mg_N_kg':float(abs(err).mean()),'RMSE_mg_N_kg':float(np.sqrt(np.mean(err**2))),'bias_mg_N_kg':float(err.mean())}
    report={'status':'partial_observer_support_carryover_checks_completed','matched_samples':len(samples),'models':metrics,'density_policy':density_policy,'density_proxy_is_not_a_cosampled_observation':density_policy!='cosampled','year_bounded_support':'If exact bulk-density date is absent, only a reported year ending before the N sample is eligible; its Jan1–Dec31 interval remains explicit, not a fictitious point date.',
        'E_verified_carryover_model':{'available':False,'full_cohort_metrics':None,'reason':'Even complete observed nitrate+ammonium samples do not identify returned residue/root mass, fixation, mineralization, or a complete initial profile. Sparse coarse concentration checks are not verification of causal carryover.'},
        'water_error':None,'water_error_reason':'No matched field moisture time series in the audited Soil table.',
        'forecast_leakage':'These postplant samples are evaluation targets only, never preplant inputs or calibration labels in this phase.',
        'recommendation':None}
    write_json(out/'report.json',report);return report
