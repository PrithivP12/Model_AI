"""Freeze a small published-formulation comparison before exposed-year audit."""
from pathlib import Path
import json
import numpy as np
from ..io import write_json,sha256
from ..data.lirf import old_cases,labels,soil_prior
from .process import trace_case,metrics,training_field_capacity


def run_recovery(root,output,audit_exposed=False):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve the completed recovery experiment')
    source=root/'reports/completion-pass/quality-corrected/depth-refinement/selected.json'
    p=json.loads(source.read_text())['parameters'];prior=soil_prior(root,True);field=training_field_capacity(root,prior)
    variants=[('reference',p,prior,{}),('fao56_dual',p,prior,{'evaporation':'fao56_dual'}),
      ('field_capacity',p,field,{}),('root105',{**p,'root_depth_m':1.05},prior,{}),
      ('field_capacity_root105',{**p,'root_depth_m':1.05},field,{}),
      ('field_capacity_dual',p,field,{'evaporation':'fao56_dual'}),
      ('field_capacity_root105_dual',{**p,'root_depth_m':1.05},field,{'evaporation':'fao56_dual'})]
    protocol={'fit_years':[2008,2009],'selection_year':2010,'new_parameters_fitted':False,
        'candidate_parameters_source_sha256':sha256(source),'field_capacity':'12 field-measured treatment profiles from 2008–09 only',
        'selection':'Lowest 2010 layer-wise water RMSE, subject to physical conservation; do not change sampling time',
        'timing':'End of modeled day; -1/+1 day are diagnostics only',
        'audit_exposed_years':[2012,2013]if audit_exposed else [],'fresh_independent_validation':False,
        'candidates':[name for name,_,_,_ in variants],'constraint':'Any raw observational-volume mismatch remains a limitation, not a fitted scaling factor'}
    write_json(out/'protocol_frozen.json',protocol)
    train=[c for y in [2008,2009]for c in old_cases(root,y)];validation=old_cases(root,2010)
    outcomes={y:labels(root,y)for y in [2008,2009,2010]};leaderboard=[]
    for name,parameters,profile,options in variants:
        fit=metrics([trace_case(c,profile,parameters,options)for c in train],outcomes)
        runs=[trace_case(c,profile,parameters,options)for c in validation]
        val=metrics(runs,outcomes)
        leaderboard.append({'name':name,'fit':fit,'selection':val})
        write_json(out/(name+'_selection_predictions.json'),runs)
        print(name,val['water_rmse_m3_m3'],val['yield_mae_kg_dry_ha'],flush=True)
    chosen=min(leaderboard,key=lambda r:r['selection']['water_rmse_m3_m3'])['name']
    write_json(out/'selection_frozen.json',{'selected':chosen,'leaderboard':leaderboard,'test_outcomes_used_for_selection':False})
    selected=next(v for v in variants if v[0]==chosen)
    report={'status':'fixed_formulation_recovery_evaluated','protocol':protocol,'leaderboard':leaderboard,'selected':chosen,
        'selection_beats_persistence':next(r for r in leaderboard if r['name']==chosen)['selection']['water_rmse_m3_m3']<leaderboard[0]['selection']['initial_profile_persistence_rmse_m3_m3'],
        'production_equations_changed':False,'recommendation':None,
        'limits':['One development validation year; no reliable region-wide confidence estimate.',
            'Pressure-plate and field-capacity transfer uncertainty remains.',
            'Row-probe observations are not exact field-average soil volumes.']}
    if audit_exposed:
        # Only after the candidate freeze, load already exposed audit inputs/outcomes.
        from ..data.lirf import new_cases
        cases,_=new_cases(root);_,parameters,profile,options=selected
        candidate=[trace_case(c,profile,parameters,options)for c in cases]
        original=[trace_case(c,prior,p,{})for c in cases]
        write_json(out/'exposed_audit_predictions_frozen.json',{'selected':candidate,'reference':original})
        audit_outcomes={y:labels(root,y)for y in [2012,2013]}
        report['exposed_audit']={'selected':metrics(candidate,audit_outcomes),'reference':metrics(original,audit_outcomes),
            'never_used_for_selection':True,'new_independent_accuracy_claim':False}
        report['exposed_audit']['by_year']={str(y):{'selected':metrics([r for r in candidate if r['year']==y],audit_outcomes),
            'reference':metrics([r for r in original if r['year']==y],audit_outcomes)}for y in [2012,2013]}
    write_json(out/'report.json',report);return report
