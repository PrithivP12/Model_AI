"""Fixed-cohort process diagnostics and training-only biological profile selection."""
from pathlib import Path
from dataclasses import replace
import json
import numpy as np
import pandas as pd
from ..io import write_json
from ..processes import default_crops,Processes
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import pair_predictions,scores
from ..state_intelligence.trajectories import run_trajectory
from ..state_intelligence.paired import bootstrap_changes
from .plant import BiologicalKernel

PROFILES={
    'legacy_scheduled':{},'legacy_long1900':{'maturity':1900},
    'legacy_temperature_plateau':{'temperature_plateau':True},
    'organs_default':{'organ_model':True},
    'organs_plateau':{'organ_model':True,'temperature_plateau':True},
    'organs_plateau_long1900':{'organ_model':True,'temperature_plateau':True,'maturity':1900},
    'organs_plateau_long1900_SLA28':{'organ_model':True,'temperature_plateau':True,'maturity':1900,'SLA':28},
}


def support_gate(unique,profile,fit_farms):
    required=set(unique.loc[unique.profile.eq('legacy_scheduled')&unique.crop.eq('corn')&unique.farm.isin(fit_farms),'key'])
    supported=set(unique.loc[unique.profile.eq(profile)&unique.farm.isin(fit_farms),'key'])
    return required-supported


def observed_state(row,crop):
    if crop=='soybeans':return {'grain':row.get('AGR34')}
    return {'grain':row.get('AGR33'),
        'shoot_dry':None if any(row.get(k)is None for k in ['AGR04','AGR32'])else row['AGR04']+row['AGR32'],
        'shoot_N':None if any(row.get(k)is None for k in ['AGR10','AGR26'])else row['AGR10']+row['AGR26'],
        'shoot_C':None if any(row.get(k)is None for k in ['AGR09','AGR24'])else row['AGR09']+row['AGR24']}


def kernel_for(layers,plants,record,cal,profile):
    crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,
        harvest_index=cal['harvest_index'],grain_n_fraction=cal['grain_n_fraction'],
        maturity_gdd=profile.get('maturity',1500),specific_leaf_area_m2_kg=profile.get('SLA',22))
    options={'root_beta_per_m':4,'observed_harvest_sowing_dates':[str(p.date)for p,s in zip(plants,record['timeline'])if s['harvest_date']],
             **{k:v for k,v in profile.items()if k in ['organ_model','temperature_plateau','maturity_leaf_drop_fraction']}}
    return BiologicalKernel(layers,crops,Processes(maize_n_dilution_a=.034),record['latitude'],options=options)


def run_biology(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    base=root/'reports/rotation-recovery/reproduced-final'
    records=json.loads((base/'input-audit/records.json').read_text());soils=json.loads((base/'input-audit/soil_observations.json').read_text())
    obs=json.loads((base/'validated-observations/observed_tissue_and_grain.json').read_text())['rows']
    old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv')
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    cal=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())['candidates'][1]['parameters']
    write_json(out/'profiles_frozen.json',PROFILES)
    predictions=fixed[['pair_id','farm_id','site_id','year','fold','observed_difference','reference_records','candidate_records']].copy()
    state_rows=[];soy_bounds=[];budget_rows=[];allpred={};reports={}
    for name,profile in PROFILES.items():
        pred={};runs={};max_error=0.
        for i,rid in enumerate(ids):
            rec=records[rid];scenario=[]
            for n,pulsed in [(40,True),(120,False)]:
                w,l,s,ps,es,_=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',n,pulsed)
                k=kernel_for(l,ps,rec,cal,profile)
                run=run_trajectory(w,l,s,ps,es,k,target_year=rec['year'])
                run['stage_snapshots']=k.stage_snapshots;run['growth_budgets']=k.growth_budgets;run['N_scenario']=n
                max_error=max(max_error,max(abs(v)for v in run['ledger_error'].values()));scenario.append(run)
                for planted,stage in k.stage_snapshots.items():
                    y=int(planted[:4]);source=obs.get(f"{rec['site_id']}:{rec['plot_id']}:{y}",{})
                    for variable,value in observed_state(source,stage['crop']).items():
                        if value is None or value<0:continue
                        state_rows.append({'profile':name,'farm':rec['farm_id'],'site':rec['site_id'],'plot':rec['plot_id'],'year':y,
                            'crop':stage['crop'],'variable':variable,'observed':value,'predicted':stage[variable],
                            'sample_stage':'R6'if stage['crop']=='corn'else'R8 grain, timing qualified','simulated_date':stage['date'],'record_context':rid})
                    if stage['crop']=='soybeans'and stage['leaf_N']is not None and source.get('AGR12')is not None:
                        soy_bounds.append({'profile':name,'site':rec['site_id'],'plot':rec['plot_id'],'year':y,'record_context':rid,
                            'measured_post_drop_N':source['AGR12'],'standing_shoot_N':stage['shoot_N'],
                            'standing_minus_all_remaining_leaf_N':max(0,stage['shoot_N']-stage['leaf_N']),
                            'leaf_drop_measured':False,'interval_is_sensitivity_not_probability':True})
                for planted,budget in k.growth_budgets.items():budget_rows.append({'profile':name,'record_id':rid,'farm':rec['farm_id'],'site':rec['site_id'],'plot':rec['plot_id'],
                    'crop':next(p.crop for p in ps if str(p.date)==planted),'crop_year':int(planted[:4]),'N_scenario':n,**budget})
            runs[rid]=scenario;pred[rid]={'prediction':float(np.mean([next(h['grain_dry_kg_ha']for h in run['harvests']if h['planted'].startswith(str(rec['year'])))for run in scenario]))}
            if i%70==0:print('biological profile',name,i,flush=True)
        write_json(out/(name+'_trajectories.json'),runs);write_json(out/(name+'_record_predictions.json'),pred)
        allpred[name]=pred;predictions[name]=pair_predictions(fixed,pred)
        reports[name]={**scores(predictions,name),'yield_MAE':float(np.mean([abs(pred[rid]['prediction']-float(actual.loc[rid,'observed_yield_dry_kg_ha']))for rid in ids])),
            'maximum_WCN_ledger_error':max_error}
    state=pd.DataFrame(state_rows)
    unique=state.groupby(['profile','farm','site','plot','year','crop','variable'],as_index=False).agg(observed=('observed','first'),predicted=('predicted','mean'))
    unique['key']=unique.apply(lambda r:f'{r.site}:{r["plot"]}:{r.year}:{r.variable}',axis=1)
    common=set.intersection(*[set(unique.loc[unique.profile.eq(name),'key'])for name in PROFILES])
    unique['common_stage_cohort']=unique.key.isin(common);unique['error']=unique.predicted-unique.observed
    unique.to_csv(out/'stage_observations.csv',index=False);state.to_csv(out/'stage_all_contexts.csv',index=False)
    pd.DataFrame(soy_bounds).to_csv(out/'soy_leaf_drop_support_bounds.csv',index=False)
    pd.DataFrame(budget_rows).to_csv(out/'growth_budgets.csv',index=False)
    folds=json.loads((root/'reports/reassessed/direct-rotation/report.json').read_text())['folds'];selections=[];selected={};predictions['training_selected']=np.nan
    scales={('corn','grain'):9000,('corn','shoot_dry'):8000,('corn','shoot_N'):60,('soybeans','grain'):3200}
    for fold in folds:
        test=fixed.farm_id.isin(fold['test_farms'])
        if not test.any():continue
        fit=unique[unique.farm.isin(fold['fit_farms'])&unique.common_stage_cohort];leader=[]
        for name in PROFILES:
            cells=[]
            for (farm,crop,variable),g in fit[fit.profile.eq(name)].groupby(['farm','crop','variable'],sort=True):
                if(crop,variable)in scales:cells.append({'farm':farm,'crop':crop,'variable':variable,'normalized_MAE':float(g.error.abs().mean()/scales[(crop,variable)])})
            if not cells:raise ValueError('No supported fit-stage observations')
            means=pd.DataFrame(cells).groupby('farm').normalized_MAE.mean()
            missing=support_gate(unique,name,fold['fit_farms'])
            leader.append({'name':name,'objective':float(means.mean()),'fit_cells':cells,'fit_R6_support_gate':not missing,
                           'fit_R6_keys_lost_vs_legacy':len(missing)})
        eligible=[r for r in leader if r['fit_R6_support_gate']]
        chosen=min(eligible,key=lambda r:(r['objective'],r['name']))['name']
        selections.append({'fold':fold['fold'],'fit_farms':fold['fit_farms'],'calibration_farm':fold['calibration_farm'],'test_farms':fold['test_farms'],
            'chosen':chosen,'leaderboard':leader,'near_optimal_within_0_02':[v['name']for v in eligible if v['objective']<=min(x['objective']for x in eligible)+.02]})
        predictions.loc[test,'training_selected']=predictions.loc[test,chosen]
        for row in fixed.loc[test].to_dict('records'):
            for col in ['reference_records','candidate_records']:
                for rid in row[col].split(';'):selected[rid]=allpred[chosen][rid]
    predictions['history_baseline']=fixed.family_history_baseline
    predictions.to_csv(out/'paired_predictions.csv',index=False);write_json(out/'selections_frozen.json',selections);write_json(out/'selected_record_predictions.json',selected)
    stage_summary=[]
    for (profile,crop,variable),g in unique[unique.common_stage_cohort].groupby(['profile','crop','variable'],sort=True):
        stage_summary.append({'profile':profile,'crop':crop,'variable':variable,'unique_plot_years':len(g),'MAE':float(g.error.abs().mean()),'bias':float(g.error.mean()),'observed_mean':float(g.observed.mean()),'predicted_mean':float(g.predicted.mean())})
    selected_state=[]
    for fold in selections:
        selected_state.append(unique[unique.profile.eq(fold['chosen'])&unique.farm.isin(fold['test_farms'])&unique.common_stage_cohort])
    held=pd.concat(selected_state);held.to_csv(out/'held_farm_selected_stage_errors.csv',index=False)
    held_summary=[{'crop':crop,'variable':variable,'MAE':float(g.error.abs().mean()),'unique_plot_years':len(g)}for(crop,variable),g in held.groupby(['crop','variable'],sort=True)]
    control=pd.read_csv(root/'reports/state-intelligence/reproduced-final/states/paired_predictions.csv')
    parity=float(np.max(abs(predictions.legacy_scheduled-control.scheduled_control)))
    if parity>1e-8:raise AssertionError('Legacy process hooks changed predictions')
    report={'status':'biological_profiles_fit_on_training_farms_and_evaluated','same_pairs':106,'models':reports,'training_selected':scores(predictions,'training_selected'),
        'training_selected_yield_MAE':float(np.mean([abs(v['prediction']-float(actual.loc[rid,'observed_yield_dry_kg_ha']))for rid,v in selected.items()])),
        'stage_summary_common_cohort':stage_summary,'selected_held_farm_stage_summary':held_summary,
        'stage_cohort':{'common_keys':len(common),'keys_by_profile':{name:unique[unique.profile.eq(name)].key.nunique()for name in PROFILES},'all_rotation_pairs_retained':True},
        'legacy_parity_max_difference':parity,'cluster_uncertainty_vs_history':bootstrap_changes(predictions,'training_selected','history_baseline'),
        'phenology_error':None,'phenology_reason':'No observed emergence/flowering/R6/R8 dates in the existing measured table. Maturity rating is not an observation date.',
        'canopy_error':None,'canopy_reason':'No matched observed LAI in the existing cohort; SLA/leaf allocation cannot be uniquely identified.',
        'water_error':None,'water_reason':'Source unit conflict and observation volumes unresolved; old water observations are not calibrated here.',
        'independent_final_accuracy':None,'recommendation':None}
    write_json(out/'report.json',report);return report
