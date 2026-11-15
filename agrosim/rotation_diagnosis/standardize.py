"""Standardized management counterfactuals with no false benchmark scoring."""
from pathlib import Path
from dataclasses import replace
from datetime import date
import json
import numpy as np
import pandas as pd
from ..processes import Management,Processes,default_crops
from ..evidence_readiness.identifiability import ShapeKernel
from ..reproductive_recovery.kernel import ReproductiveKernel
from ..reproductive_recovery.experiment import parameter_profiles
from ..rotation_recovery.inputs import prepare_record
from ..state_intelligence.trajectories import run_trajectory
from ..io import write_json
from .audit import load


def target_events(record,old):
    target=record['timeline'][-1]
    if target['fertilizer_total_if_recorded_complete']is not None:
        return [Management(date.fromisoformat(e['date']),e['n_kg_ha'],0)for e in target['fertilizer_events']if '2011-01-01'<=e['date']<=f"{record['year']}-12-31"]
    return [Management(date.fromisoformat(t),n,0)for t,n in old['target_N_events']]


def replace_current_N(events,candidate_events,reference_events):
    copied=list(events)
    for e in candidate_events:
        try:copied.remove(e)
        except ValueError:raise ValueError('Current-crop event ownership mismatch; never delete by calendar year')
    copied.extend(reference_events);return copied


def run_standardized(root,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    fixed,records,soils,old,_=load(root);p,_,joint=parameter_profiles(root)
    fit=json.loads((root/'reports/reproductive-recovery/final/training/fit_frozen.json').read_text())
    base=root/'reports/evidence-readiness/reproduced-final/evaluation'
    original={name:json.loads(((base/'reference_rotation_records.json')if name=='stable'else root/'reports/reproductive-recovery/final/rotation/source_sink_record_predictions.json').read_text())for name in ['stable','source_sink']}
    write_json(out/'protocol_frozen.json',{'controls':['equal_current_N','equal_current_N_and_calendar'],
        'models':['stable','source_sink'],'fit_parameters':[],
        'counterfactual_target_differs_from_observed_treatment_package':True,
        'forbidden_metrics':['MAE_against_unstandardized_observed_contrasts','claimed_accuracy_improvement','recommendation']})
    rows=[]
    for name in ['stable','source_sink']:
        for mode in ['equal_current_N','equal_current_N_and_calendar']:
            for pair in fixed.to_dict('records'):
                if ';'in pair['reference_records']or';'in pair['candidate_records']:raise ValueError('Only singleton arms have a unique management donor')
                a,b=records[pair['reference_records']],records[pair['candidate_records']]
                donor=target_events(a,old[pair['reference_records']]);own=target_events(b,old[pair['candidate_records']]);ys=[];max_error=0.
                for n in [40,120]:
                    w,l,s,ps,es,_=prepare_record(root,b,old[pair['candidate_records']],soils[b['site_id']+':'+b['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',n,True)
                    es=replace_current_N(es,own,donor)
                    if mode.endswith('and_calendar'):
                        _,_,_,refplants,_,_=prepare_record(root,a,old[pair['reference_records']],soils[a['site_id']+':'+a['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',n,True)
                        ps[-1]=replace(ps[-1],date=refplants[-1].date,harvest_deadline=refplants[-1].harvest_deadline)
                    crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=p['harvest_index'],grain_n_fraction=p['grain_n_fraction'],
                        rue_g_dry_per_MJ_PAR=joint['RUE']if name=='source_sink'else 3.2,
                        specific_leaf_area_m2_kg=joint['leaf_area_coefficient']/.4 if name=='source_sink'else 22.)
                    options={'root_beta_per_m':4,'shape_alpha':1. if name=='source_sink'else 0.,'organ_model':name=='source_sink',
                        'sink_coefficient':fit['coefficient'],'harvest_calendar_role':'planned',
                        'observed_harvest_sowing_dates':[str(pl.date)for pl,season in zip(ps,b['timeline'])if season['harvest_date']or(mode.endswith('and_calendar')and pl==ps[-1])]}
                    cls=ReproductiveKernel if name=='source_sink'else ShapeKernel
                    k=cls(l,crops,Processes(maize_n_dilution_a=.034),b['latitude'],options=options);k.capture_runs=False
                    run=run_trajectory(w,l,s,ps,es,k,target_year=b['year'])
                    ys.append(next(h['grain_dry_kg_ha']for h in run['harvests']if h['planted'].startswith(str(b['year']))))
                    max_error=max(max_error,max(abs(x)for x in run['ledger_error'].values()))
                ref=original[name][pair['reference_records']]['prediction'];oldcontrast=original[name][pair['candidate_records']]['prediction']-ref
                rows.append({'model':name,'mode':mode,'pair_id':pair['pair_id'],'farm':pair['farm_id'],
                    'standardized_candidate_yield':float(np.mean(ys)),'reference_yield':ref,'original_model_contrast':oldcontrast,
                    'standardized_model_contrast':float(np.mean(ys))-ref,'observed_original_package_contrast':pair['observed_difference'],
                    'reference_N_logging_complete':a['timeline'][-1]['fertilizer_total_if_recorded_complete']is not None,
                    'max_ledger_error':max_error,'counterfactual_observed_outcome':None})
            print('management standardization',name,mode,flush=True)
    table=pd.DataFrame(rows);table.to_csv(out/'controlled_predictions.csv',index=False)
    summary=[]
    for (name,mode),g in table.groupby(['model','mode']):
        summary.append({'model':name,'mode':mode,'pairs':len(g),'mean_original_model_contrast':float(g.original_model_contrast.mean()),
            'mean_standardized_model_contrast':float(g.standardized_model_contrast.mean()),
            'mean_change':float((g.standardized_model_contrast-g.original_model_contrast).mean()),
            'sign_changes':int(((g.standardized_model_contrast>1e-6)!=(g.original_model_contrast>1e-6)).sum()),
            'max_ledger_error':float(g.max_ledger_error.max()),'accuracy_against_observations':None})
    report={'status':'management_targets_separated','summary':summary,'no_field_counterfactual_labels_available':True,
        'interpretation':'Current N/calendar are standardized to the reference arm inside the model. Soil/history still follow their own arm; no genotype response is supported. Original treatment observations are not validation labels for these changed packages.',
        'new_independent_accuracy':None,'recommendation':None}
    write_json(out/'report.json',report);return report
