"""Frozen joint-composite candidates on exposed development, never a final test."""
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
from ..io import write_json
from ..data.lirf import old_cases,labels,soil_prior
from ..recovery.process import training_field_capacity
from ..intelligence.observations import crop_observations
from ..transfer_recovery.cross_environment import lirf_trace,lirf_score
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import pair_predictions,scores
from ..state_intelligence.trajectories import run_trajectory
from ..state_intelligence.paired import bootstrap_changes
from ..processes import default_crops,Processes
from .identifiability import ShapeKernel


def run_evaluation(root,observations,fit_dir,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    fits=json.loads((Path(fit_dir)/'joint_fit_frozen.json').read_text())
    p=fits[0]['unidentified_parameters_fixed'];prior=training_field_capacity(root,soil_prior(root,True));dev=old_cases(root,2010);crop,_=crop_observations(root,[2010])
    ref=json.loads((root/'reports/transfer-recovery/reproduced-final/cross/report.json').read_text())
    reference={'name':'reference','shape_alpha':0.,'RUE':p['rue'],'leaf_area_coefficient':p['sla']*p['leaf_fraction']}
    models={};daily_lai=[];records={};pairs=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv')
    paired=pairs[['pair_id','farm_id','site_id','year','fold','observed_difference','reference_records','candidate_records']].copy();paired['history_baseline']=pairs.family_history_baseline
    base=root/'reports/rotation-recovery/reproduced-final';audit=json.loads((base/'input-audit/records.json').read_text());soils=json.loads((base/'input-audit/soil_observations.json').read_text());old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    ids=sorted(set(pairs[['reference_records','candidate_records']].stack().str.split(';').explode()));actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    for spec in [reference,*fits]:
        name=spec['name'];q={**p,'rue':spec['RUE'],'sla':spec['leaf_area_coefficient']/p['leaf_fraction']};runs=[]
        with patch('agrosim.transfer_recovery.cross_environment.TransferKernel',ShapeKernel):
            for case in dev:
                run,daily,_=lirf_trace(case,prior,q,{'root_beta_per_m':4,'shape_alpha':spec['shape_alpha']});runs.append(run)
                bydate={r['date']:r for r in daily}
                for row in observations['LAI']:
                    if row['case_id']!=case['id']:continue
                    if row['date']not in bydate:raise ValueError('Development LAI support mismatch')
                    predicted=bydate[row['date']]['lai'];daily_lai.append({'model':name,**row,'predicted':predicted,'error':predicted-row['value']})
        score=lirf_score(runs,{2010:labels(root,2010)},crop);errors=[r['error']for r in daily_lai if r['model']==name]
        score['dated_LAI_RMSE']=float(np.sqrt(np.mean(np.square(errors))));score['dated_LAI_MAE']=float(np.mean(abs(np.array(errors))));score['dated_LAI_records']=len(errors)
        write_json(out/(name+'_LIRF_predictions.json'),runs)
        predictions={};max_error=0.;states=[]
        for i,rid in enumerate(ids):
            rec=audit[rid];y=[]
            for n,pulsed in [(40,True),(120,False)]:
                weather,layers,state,plants,events,_=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',n,pulsed)
                crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=p['harvest_index'],grain_n_fraction=p['grain_n_fraction'],
                    rue_g_dry_per_MJ_PAR=spec['RUE']if name!='reference'else 3.2,
                    specific_leaf_area_m2_kg=(spec['leaf_area_coefficient']/.4)if name!='reference'else 22.)
                kernel=ShapeKernel(layers,crops,Processes(maize_n_dilution_a=.034),rec['latitude'],options={'root_beta_per_m':4,'shape_alpha':spec['shape_alpha'],
                    'observed_harvest_sowing_dates':[str(pl.date)for pl,s in zip(plants,rec['timeline'])if s['harvest_date']]})
                kernel.capture_runs=False
                result=run_trajectory(weather,layers,state,plants,events,kernel,target_year=rec['year']);y.append(next(h['grain_dry_kg_ha']for h in result['harvests']if h['planted'].startswith(str(rec['year']))))
                max_error=max(max_error,max(abs(v)for v in result['ledger_error'].values()));states.append({'model':name,'record_id':rid,'initial_N_scenario':n,
                    'Jan1_state':result['pretarget_state'],'annual':result['annual_states'],'fluxes':result['physical_fluxes']})
            predictions[rid]={'prediction':float(np.mean(y))}
            if i%100==0:print('joint evaluation',name,i,flush=True)
        paired[name]=pair_predictions(pairs,predictions);rotation={**scores(paired,name),'yield_MAE':float(np.mean([abs(predictions[r]['prediction']-float(actual.loc[r,'observed_yield_dry_kg_ha']))for r in ids])),
            'max_WCN_ledger_error':max_error}
        models[name]={'LIRF':score,'rotation':rotation};write_json(out/(name+'_rotation_records.json'),predictions);write_json(out/(name+'_memory_states.json'),states)
    pd.DataFrame(daily_lai).to_csv(out/'development_LAI_predictions.csv',index=False);paired.to_csv(out/'paired_predictions.csv',index=False)
    parity={'LIRF_yield':abs(models['reference']['LIRF']['yield_mae_kg_dry_ha']-ref['LIRF']['root_reference']['yield_mae_kg_dry_ha']),
            'rotation_contrast':abs(models['reference']['rotation']['mae_difference_kg_dry_ha']-ref['rotation']['root_reference']['mae_difference_kg_dry_ha'])}
    if max(parity.values())>1e-8:raise AssertionError('Frozen reference changed')
    gates={}
    for name in [s['name']for s in fits]:
        b=models['reference'];m=models[name]
        ratios={**{'LIRF_'+k:m['LIRF'][k]/b['LIRF'][k]for k in ['yield_mae_kg_dry_ha','aboveground_biomass_MAE','water_rmse_m3_m3','canopy_RMSE','dated_LAI_RMSE']},
                **{'rotation_'+k:m['rotation'][k]/b['rotation'][k]for k in ['yield_MAE','mae_difference_kg_dry_ha','control_fallback_regret_all_pairs']}}
        gates[name]={'ratios':ratios,'worst_ratio':max(ratios.values()),'joint_gate':max(ratios.values())<=1.05,
            'beats_history_all_pair_regret':m['rotation']['control_fallback_regret_all_pairs']<=scores(paired,'history_baseline')['control_fallback_regret_all_pairs']}
    report={'status':'dated_state_joint_composite_calibration_evaluated','models':models,'same_rotation_pairs':106,'reference_parity':parity,'acceptance_gates':gates,
        'uncertainty_vs_history':{s['name']:bootstrap_changes(paired,s['name'],'history_baseline')for s in fits},
        'new_independent_test':False,'operational_preplant_accuracy':None,'default_model_changed':False,'recommendation':None,
        'identifiability_limit':'Only one training year has dated LAI; temperature/phenology/initial-state uncertainty remains. SLA and leaf fraction are never fitted separately.'}
    write_json(out/'report.json',report);return report
