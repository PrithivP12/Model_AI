"""Paired diagnostics that preserve group dependence and all decision denominators."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from ..io import write_json


def rotation_decision_audit(output,filename='decision_audit.json'):
    out=Path(output)
    if(out/filename).exists():raise RuntimeError('Preserve completed decision audit')
    d=pd.read_csv(out/'paired_predictions.csv');report=json.loads((out/'report.json').read_text());names=list(report['models']);stats={};farms=sorted(d.farm_id.unique())
    for name in names:
        y=d.observed_difference.to_numpy();p=d[name].to_numpy();regret=np.where(p>1e-6,np.maximum(-y,0),np.maximum(y,0))
        stats[name]={**report['models'][name],
            'abstentions':int((abs(p)<=1e-6).sum()),'correct_signed_decisions_fraction_all_pairs':float(((np.sign(p)==np.sign(y))&(abs(p)>1e-6)).mean()),
            'all_pair_control_fallback_regret_kg_ha':float(regret.mean()),
            'policy':'For comparison only: choose diversified if predicted contrast>0, otherwise continuous maize; abstentions counted in fallback regret.'}
    a,b=stats['corrected_coupled'],stats['corrected_physical_no_organic_N'];base=stats['family_history_baseline']
    joint=lambda candidate,reference:(candidate['farm_macro_rmse_kg_dry_ha']<reference['farm_macro_rmse_kg_dry_ha']and
        candidate['all_pair_control_fallback_regret_kg_ha']<=reference['all_pair_control_fallback_regret_kg_ha']and
        candidate['correct_signed_decisions_fraction_all_pairs']>=reference['correct_signed_decisions_fraction_all_pairs']and
        (candidate['directional_agreement']or 0)>=(reference['directional_agreement']or 0))
    rng=np.random.default_rng(817);draws=rng.integers(0,len(farms),(4000,len(farms)));uncertainty={}
    for name in names[1:]:
        deltas=[]
        for farm in farms:
            g=d[d.farm_id==farm];y=g.observed_difference.to_numpy();p=g[name].to_numpy();q=g.family_history_baseline.to_numpy()
            rp=np.where(p>1e-6,np.maximum(-y,0),np.maximum(y,0));rq=np.where(q>1e-6,np.maximum(-y,0),np.maximum(y,0))
            deltas.append([np.mean(abs(p-y)-abs(q-y)),np.mean(rp-rq)])
        delta=np.asarray(deltas);bootstrap=delta[draws].mean(axis=1)
        uncertainty[name]={'farm_macro_MAE_change_vs_history':float(delta[:,0].mean()),'macro_MAE_change_95pct_percentile':np.quantile(bootstrap[:,0],[.025,.975]).tolist(),
            'farm_macro_fallback_regret_change_vs_history':float(delta[:,1].mean()),'macro_regret_change_95pct_percentile':np.quantile(bootstrap[:,1],[.025,.975]).tolist(),
            'scope':'Exploratory fixed-prediction farm-cluster bootstrap on seven exposed development farms; no new-geography confidence guarantee.'}
    result={'status':'all_metric_rotation_decision_audit','models':stats,'coupling_passes_joint_gate':joint(a,b),'coupled_passes_history_joint_gate':joint(a,base),
        'bootstrap':uncertainty,'final_promotion':False,'gate_version':2,'gate_definition':'Magnitude RMSE must improve; both conditional directional agreement and all-pair correct fraction must not decrease, and control-fallback regret must not increase. V1 only checked the all-pair directional denominator.',
        'interpretation':'A lower magnitude RMSE alone is insufficient if direction or decision regret worsens. Missing actual forcing/soil further limits causal inference.'}
    write_json(out/filename,result);return result


def process_change_audit(root,output,refinement_path=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed process paired audit')
    base=Path(refinement_path or root/'reports/scientific-intelligence/refinement')
    a=json.loads((base/'reference_predictions.json').read_text());b=json.loads((base/'regularized_root_measured_partition_predictions.json').read_text())
    from ..data.lirf import labels
    observed=labels(root,2010);rows=[]
    for r,s in zip(a,b):
        assert r['id']==s['id']and [(v['date'],v['layer_top_cm'],v['observed_theta'])for v in r['water_rows']]==[(v['date'],v['layer_top_cm'],v['observed_theta'])for v in s['water_rows']]
        y=observed[r['treatment']];rows.append({'case_id':r['id'],'n':len(r['water_squared_errors']),
            'reference_water_sse':sum(r['water_squared_errors']),'candidate_water_sse':sum(s['water_squared_errors']),
            'reference_yield_absolute_error':abs(r['predicted_yield_dry_kg_ha']-y),'candidate_yield_absolute_error':abs(s['predicted_yield_dry_kg_ha']-y),
            'reference_water_rmse':float(np.sqrt(np.mean(r['water_squared_errors']))),'candidate_water_rmse':float(np.sqrt(np.mean(s['water_squared_errors'])))})
    d=pd.DataFrame(rows);rng=np.random.default_rng(817);draws=rng.integers(0,len(d),(4000,len(d)));n=d.n.to_numpy()
    wr=np.sqrt(d.reference_water_sse.to_numpy()[draws].sum(axis=1)/n[draws].sum(axis=1));wc=np.sqrt(d.candidate_water_sse.to_numpy()[draws].sum(axis=1)/n[draws].sum(axis=1))
    yd=(d.candidate_yield_absolute_error-d.reference_yield_absolute_error).to_numpy()[draws].mean(axis=1)
    d.to_csv(out/'per_treatment.csv',index=False)
    report={'status':'identical_observation_support_verified','same_dates_layers_targets':True,'treatments':len(d),'independent_validation_site_years':1,
        'water_rmse_change_within_year_bootstrap_95pct':np.quantile(wc-wr,[.025,.975]).tolist(),
        'yield_MAE_change_within_year_bootstrap_95pct':np.quantile(yd,[.025,.975]).tolist(),
        'all_treatments_water_improved':bool((d.candidate_water_rmse<d.reference_water_rmse).all()),
        'interpretation':'These are descriptive treatment-block bootstrap ranges within one shared site-year, not confidence intervals for temporal/geographic transfer. Replicate raw uncertainty is not recovered from treatment means.',
        'new_final_test_accuracy':False}
    write_json(out/'report.json',report);return report
