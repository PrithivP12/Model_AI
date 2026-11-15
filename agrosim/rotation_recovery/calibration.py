"""Small mechanism sensitivity grid, selected exclusively in existing fit-farm groups."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from ..io import write_json
from .experiment import simulate,pair_predictions,scores


def run_mechanisms(root,output,weather_dir=None,audit_dir=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed mechanism selection')
    weather_dir=Path(weather_dir or root/'reports/rotation-recovery/weather-exact');audit_dir=Path(audit_dir or root/'reports/rotation-recovery/input-audit')
    records=json.loads((audit_dir/'records.json').read_text());soils=json.loads((audit_dir/'soil_observations.json').read_text());old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv');ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    folds=json.loads((root/'reports/reassessed/direct-rotation/report.json').read_text())['folds']
    raw=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id');observed={rid:float(raw.loc[rid,'observed_yield_dry_kg_ha'])for rid in ids}
    specs=[('C_observed_inputs',{}),('D_harvest_timing',{'scheduled_harvest':True})]
    for rue in [.85,1.,1.15]:
        for n in [.8,1.,1.2]:
            if rue==1. and n==1.:continue
            specs.append((f'D_rue{rue}_N{n}',{'scheduled_harvest':True,'rue_multiplier':rue,'n_demand_multiplier':n}))
    specs.extend([('carryover_slow',{'scheduled_harvest':True,'residue_decay_multiplier':.25}),('carryover_fast',{'scheduled_harvest':True,'residue_decay_multiplier':2.})])
    protocol={'same_106_pairs_and_original_folds':True,'cohort_selection_changed':False,'specifications':[{'name':name,'parameters':p}for name,p in specs],
        'fit':'Parameter selection using only original fit farms; calibration and test farm outcomes excluded from selection.',
        'objective':'farm-macro contrast RMSE/2000 + farm-macro target-yield MAE/10000 + .05*((RUE_multiplier-1)^2+(N_multiplier-1)^2).',
        'test_role':'Outer development farms are evaluation only; they are exposed data, not a new final test.',
        'regularization':'Narrow multiplier grid around existing physical parameters, no crop-history/yield bonus.',
        'identifiability':'Report spread of near-optimal fit parameters, initialization sensitivity and tissue-N comparisons; do not interpret the minimum as uniquely measured physiology.',
        'E_verified_carryover':'Not claimed unless matched residue-return and subsequent N observations establish that process, beyond measured standing tissue production.'}
    write_json(out/'protocol_frozen.json',protocol);candidates={};pairgrid=fixed.copy()
    for name,parameters in specs:
        pred,meta=simulate(root,records,old,soils,weather_dir,ids,'C_soil',parameters)
        write_json(out/(name+'_record_predictions.json'),pred);pairgrid[name]=pair_predictions(fixed,pred);candidates[name]={'parameters':parameters,'record_predictions':pred,'conservation':meta['maximum_conservation_error']}
        print('mechanism candidate complete',name,flush=True)
    # Parameter-choice records are frozen before computing the outer test summary.
    selection=[];selected_predictions=fixed.copy();selected_predictions['D_training_selected']=np.nan;record_selected={};all_inner=[]
    for fold in folds:
        train=pairgrid[pairgrid.farm_id.isin(fold['fit_farms'])];test=fixed[fixed.farm_id.isin(fold['test_farms'])]
        if test.empty:continue
        leaderboard=[]
        for name,parameters in specs:
            pred=candidates[name]['record_predictions'];train_ids=sorted({rid for r in train.to_dict('records')for column in ['reference_records','candidate_records']for rid in r[column].split(';')})
            farm_errors=[]
            for farm in sorted(train.farm_id.unique()):
                subset=train[train.farm_id==farm];farm_ids={rid for r in subset.to_dict('records')for column in ['reference_records','candidate_records']for rid in r[column].split(';')}
                rmse=float(np.sqrt(np.mean((subset[name]-subset.observed_difference)**2)))
                yieldmae=float(np.mean([abs(pred[rid]['prediction']-observed[rid])for rid in farm_ids]));farm_errors.append((rmse,yieldmae))
            rmse,yieldmae=np.mean(farm_errors,axis=0);penalty=.05*((parameters.get('rue_multiplier',1)-1)**2+(parameters.get('n_demand_multiplier',1)-1)**2)
            leaderboard.append({'name':name,'fit_farm_macro_RMSE':float(rmse),'fit_farm_macro_yield_MAE':float(yieldmae),'objective':float(rmse/2000+yieldmae/10000+penalty)})
        chosen=min(leaderboard,key=lambda x:(x['objective'],x['name']))['name'];selection.append({'fold':fold['fold'],'fit_farms':fold['fit_farms'],'calibration_farm':fold['calibration_farm'],'test_farms':fold['test_farms'],'selected':chosen,'leaderboard':leaderboard,
            'near_optimal_within_0_01':[x['name']for x in leaderboard if x['objective']<=min(v['objective']for v in leaderboard)+.01]})
        selected_predictions.loc[test.index,'D_training_selected']=pairgrid.loc[test.index,chosen]
        for r in test.to_dict('records'):
            for column in ['reference_records','candidate_records']:
                for rid in r[column].split(';'):record_selected[rid]=candidates[chosen]['record_predictions'][rid]['prediction']
    write_json(out/'selections_frozen.json',selection);pairgrid.to_csv(out/'all_candidate_predictions.csv',index=False);selected_predictions.to_csv(out/'paired_predictions.csv',index=False)
    result={'status':'fit_farm_physical_selection_evaluated_on_fixed_development_farms','protocol':protocol,'fold_selection':selection,
        'models':{name:scores(pairgrid,name)for name,_ in specs},'D_selected':scores(selected_predictions,'D_training_selected'),
        'D_selected_absolute_yield_MAE':float(np.mean([abs(value-observed[rid])for rid,value in record_selected.items()])),
        'candidate_conservation_errors':{name:obj['conservation']for name,obj in candidates.items()},
        'E_verified_residue_N_model':{'status':'not_established','metrics':None,'reason':'Standing tissue production is partly measured, but return fractions, roots, initial stocks and complete subsequent mineral-N observations are insufficient for verified carryover across the fixed cohort.'},
        'recommendation':None,'new_independent_final_test':False}
    write_json(out/'report.json',result);return result
