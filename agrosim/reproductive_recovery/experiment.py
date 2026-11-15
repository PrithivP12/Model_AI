"""Frozen source/sink experiment, training fit and unchanged development cohorts."""
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from ..io import write_json,sha256
from ..processes import default_crops,Processes
from ..data.lirf import old_cases,soil_prior,labels
from ..recovery.process import training_field_capacity
from ..intelligence.observations import crop_observations
from ..transfer_recovery.cross_environment import lirf_trace,lirf_score
from ..evidence_readiness.identifiability import ShapeKernel
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import pair_predictions,scores
from ..state_intelligence.trajectories import run_trajectory
from ..state_intelligence.paired import bootstrap_changes
from ..biological_recovery.experiment import observed_state
from .kernel import ReproductiveKernel


def parameter_profiles(root):
    fits=json.loads((root/'reports/evidence-readiness/reproduced-final/identifiability/joint_fit_frozen.json').read_text())
    p=fits[0]['unidentified_parameters_fixed'];joint=next(f for f in fits if f['name']=='joint_plateau')
    q={**p,'rue':joint['RUE'],'sla':joint['leaf_area_coefficient']/p['leaf_fraction']}
    return p,q,joint


def trace_cases(cases,prior,p,kind,coefficient=None):
    options={'root_beta_per_m':4,'shape_alpha':0 if kind=='reference'else 1,
        'organ_model':kind in ['organ_HI','source_sink']}
    cls=ReproductiveKernel if kind=='source_sink'else ShapeKernel
    if coefficient is not None:options['sink_coefficient']=coefficient
    runs=[];daily=[]
    with patch('agrosim.transfer_recovery.cross_environment.TransferKernel',cls):
        for case in cases:
            r,d,meta=lirf_trace(case,prior,p,options,initialize_canopy=False)
            if r['initial_assimilation']['canopy_assimilated']:raise AssertionError('Organ state cannot be silently reconstructed')
            runs.append(r);daily.extend(d)
    return runs,daily


def fit_sink(root,audit,out):
    out.mkdir(parents=True,exist_ok=False);p,q,joint=parameter_profiles(root)
    prior=training_field_capacity(root,soil_prior(root,True))
    cases=[c for y in [2008,2009]for c in old_cases(root,y)]
    target={r['case_id']:r['sampled_grain_dry_kg_ha']for r in audit['treatments']if r['year']in [2008,2009]}
    evaluations=[]
    def objective(k):
        runs,_=trace_cases(cases,prior,q,'source_sink',float(k))
        residual=[(r['predicted_yield_dry_kg_ha']-target[r['id']])/target[r['id']]for r in runs]
        value=float(np.mean(np.square(residual)));evaluations.append({'sink_coefficient':float(k),'mean_squared_relative_sample_grain_error':value})
        return value
    opt=minimize_scalar(objective,bounds=(.1,8),method='bounded',options={'maxiter':12,'xatol':.02})
    coefficient=float(opt.x)
    # Two prespecified perturbations diagnose practical flatness, not a sweep.
    for k in [max(.1,.8*coefficient),min(8,1.2*coefficient)]:objective(k)
    report={'name':'source_sink','coefficient':coefficient,'optimizer_success':bool(opt.success),
        'optimizer_message':str(opt.message),'evaluations':evaluations,'fit_years':[2008,2009],
        'fit_target':'mean of within-plot sampled biomass × sampled HI products; zero-moisture grain',
        'fit_cases':sorted(target),'fit_plot_yield_used':False,'development_used':False,
        'source_parameters_frozen':q,'existing_joint_source_fit':joint['name'],
        'window':[.35,.6],'filling_end':1.,'window_is_observed_stage':False,
        'identifiability':'One aggregate dry sink/critical-window-assimilate coefficient only. Grain number and kernel size not separately fitted. No reproductive heat-abortion function is identified.',
        'parameters_fixed_not_measured':['thermal window','leaf/stem remobilization caps','N floors','organ allocation'],
        'default_changed':False}
    write_json(out/'fit_frozen.json',report);return report


def evaluate_lirf(root,audit,fit,out):
    out.mkdir(parents=True,exist_ok=False);p,q,_=parameter_profiles(root);prior=training_field_capacity(root,soil_prior(root,True))
    cases=old_cases(root,2010);crop_obs,_=crop_observations(root,[2010])
    lai=json.loads((root/'reports/evidence-readiness/reproduced-final/observations/observations.json').read_text())['LAI']
    support={r['case_id']:r for r in audit['treatments']if r['year']==2010};models={};comparison=[]
    for name in ['reference','joint_plateau','organ_HI','source_sink']:
        runs,daily=trace_cases(cases,prior,p if name=='reference'else q,name,fit['coefficient']if name=='source_sink'else None)
        models[name]=lirf_score(runs,{2010:labels(root,2010)},crop_obs)
        by={(r['case_id'],r['date']):r for r in daily}
        errors=[by[(r['case_id'],r['date'])]['lai']-r['value']for r in lai if r['year']==2010]
        models[name]['dated_LAI_RMSE']=float(np.sqrt(np.mean(np.square(errors))))
        models[name]['sample_grain_MAE']=float(np.mean([abs(r['predicted_yield_dry_kg_ha']-support[r['id']]['sampled_grain_dry_kg_ha'])for r in runs]))
        models[name]['sample_grain_bias']=float(np.mean([r['predicted_yield_dry_kg_ha']-support[r['id']]['sampled_grain_dry_kg_ha']for r in runs]))
        models[name]['tissue_N_error']=None;models[name]['maturity_timing_error']=None
        write_json(out/(name+'_predictions.json'),runs)
        for r in runs:
            h=r['harvest_diagnostics'][0];obs=support[r['id']]
            comparison.append({'model':name,'case_id':r['id'],'grain':h['grain_dry_kg_ha'],'aboveground':h['biomass_dry_kg_ha']-h['root_dry'],
                'sample_grain':obs['sampled_grain_dry_kg_ha'],'plot_grain':obs['plot_grain_dry_kg_ha'],
                'sample_biomass':obs['sampled_aboveground_dry_kg_ha'],'sample_HI':obs['sampled_HI'],
                'modeled_maturity_harvest':h['harvested'],'sample_date':obs['biomass_sample_date'],
                'realized_HI':h['grain_dry_kg_ha']/(h['biomass_dry_kg_ha']-h['root_dry']),
                'sink_state':h.get('organ_state',{}).get('reproductive_sink'),
                'mean_water_factor':h['mean_water_stress'],'mean_N_factor':h['mean_N_growth_factor']})
    old=json.loads((root/'reports/evidence-readiness/reproduced-final/evaluation/report.json').read_text())['models']
    parity={name:max(abs(models[name][k]-old[name]['LIRF'][k])for k in ['yield_mae_kg_dry_ha','water_rmse_m3_m3','dated_LAI_RMSE'])for name in ['reference','joint_plateau']}
    if max(parity.values())>1e-8:raise AssertionError('Legacy hook changed preserved scientific outputs')
    pd.DataFrame(comparison).to_csv(out/'support_separated_predictions.csv',index=False)
    report={'models':models,'reference_parity':parity,'same_six_development_cases':True,
        'full_observation_equivalence':False,'qualifications':['2010 biomass sample day absent; mature modeled standing mass is a qualified comparison.',
            'Sampled grain and plot grain differ in spatial/temporal support. Neither is substituted for the other.',
            'Model LAI green-equivalent versus source total leaf support during senescence remains unresolved.',
            'No2010 measured tissue N; no treatment-specific physiological-maturity date.'],
        'independent_validation':False,'default_changed':False}
    write_json(out/'report.json',report);return report


def evaluate_rotation(root,fit,out):
    out.mkdir(parents=True,exist_ok=False);p,_,joint=parameter_profiles(root)
    source=root/'reports/evidence-readiness/reproduced-final/evaluation'
    fixed=pd.read_csv(source/'paired_predictions.csv');paired=fixed.copy()
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    base=root/'reports/rotation-recovery/reproduced-final';audit=json.loads((base/'input-audit/records.json').read_text())
    soils=json.loads((base/'input-audit/soil_observations.json').read_text())
    old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    measured=json.loads((base/'validated-observations/observed_tissue_and_grain.json').read_text())['rows']
    reference=json.loads((source/'report.json').read_text())['models'];models={k:reference[k]['rotation']for k in ['reference','joint_plateau']}
    stage=[];record_rows=[]
    for name in ['organ_HI','source_sink','source_sink_target_only']:
        predictions={};memories=[];max_error=0.
        for i,rid in enumerate(ids):
            rec=audit[rid];outputs=[]
            for n,pulsed in [(40,True),(120,False)]:
                w,l,s,ps,es,evidence=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',n,pulsed)
                crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,
                    harvest_index=p['harvest_index'],grain_n_fraction=p['grain_n_fraction'],rue_g_dry_per_MJ_PAR=joint['RUE'],
                    specific_leaf_area_m2_kg=joint['leaf_area_coefficient']/.4)
                options={'root_beta_per_m':4,'shape_alpha':1,'organ_model':True,
                    'observed_harvest_sowing_dates':[str(pl.date)for pl,season in zip(ps,rec['timeline'])if season['harvest_date']]}
                if name.startswith('source_sink'):options['sink_coefficient']=fit['coefficient']
                if name=='source_sink_target_only':options['source_sink_sowing_dates']=[str(pl.date)for pl in ps if pl.date.year==rec['year']]
                cls=ReproductiveKernel if name.startswith('source_sink')else ShapeKernel
                k=cls(l,crops,Processes(maize_n_dilution_a=.034),rec['latitude'],options=options);k.capture_runs=False
                run=run_trajectory(w,l,s,ps,es,k,target_year=rec['year'])
                harvest=next(h for h in run['harvests']if h['planted'].startswith(str(rec['year'])))
                outputs.append(harvest['grain_dry_kg_ha']);max_error=max(max_error,max(abs(v)for v in run['ledger_error'].values()))
                memories.append({'record_id':rid,'initial_N_scenario':n,'Jan1_state':run['pretarget_state'],'annual':run['annual_states'],
                    'fluxes':run['physical_fluxes'],'harvests':run['harvests']})
                for planted,state in k.stage_snapshots.items():
                    row=measured.get(f"{rec['site_id']}:{rec['plot_id']}:{int(planted[:4])}",{})
                    for variable,value in observed_state(row,state['crop']).items():
                        if value is not None and value>=0:stage.append({'model':name,'farm':rec['farm_id'],'site':rec['site_id'],'plot':rec['plot_id'],
                            'year':int(planted[:4]),'crop':state['crop'],'variable':variable,'observed':value,'predicted':state[variable]})
            predictions[rid]={'prediction':float(np.mean(outputs))}
            record_rows.append({'model':name,'record_id':rid,'site':rec['site_id'],'farm':rec['farm_id'],'year':rec['year'],
                'observed_yield':float(actual.loc[rid,'observed_yield_dry_kg_ha']),'predicted_yield':float(np.mean(outputs))})
            if i%70==0:print('reproductive rotation',name,i,flush=True)
        paired[name]=pair_predictions(fixed,predictions)
        models[name]={**scores(paired,name),'yield_MAE':float(np.mean([abs(predictions[r]['prediction']-float(actual.loc[r,'observed_yield_dry_kg_ha']))for r in ids])),
            'max_WCN_ledger_error':max_error}
        write_json(out/(name+'_record_predictions.json'),predictions);write_json(out/(name+'_memory.json'),memories)
    paired['always_CS']=1.;paired['always_CC']=-1.;paired['zero_effect']=0.
    baseline={k:scores(paired,k)for k in ['history_baseline','zero_effect']}
    for k in ['always_CS','always_CC']:
        score=scores(paired,k)
        baseline[k]={key:score[key]for key in ['directional_agreement','decision_coverage','control_fallback_regret_all_pairs','correct_signed_decisions_fraction_all_pairs']}
        baseline[k]['effect_magnitude_prediction']=None
    paired.to_csv(out/'paired_predictions.csv',index=False);pd.DataFrame(record_rows).to_csv(out/'record_predictions.csv',index=False)
    stages=pd.DataFrame(stage).groupby(['model','farm','site','plot','year','crop','variable'],as_index=False).agg(observed=('observed','first'),predicted=('predicted','mean'))
    stages['error']=stages.predicted-stages.observed;stages.to_csv(out/'stage_errors.csv',index=False)
    # Both candidates have identical phenology; enforce identical stage support.
    keys=lambda name:set(map(tuple,stages.loc[stages.model.eq(name),['site','plot','year','crop','variable']].to_numpy()))
    if any(keys('organ_HI')!=keys(name)for name in ['source_sink','source_sink_target_only']):raise AssertionError('Stage support changed; do not score different cohorts')
    summary=[{'model':name,'crop':crop,'variable':variable,'MAE':float(g.error.abs().mean()),'bias':float(g.error.mean()),'plot_years':len(g)}for(name,crop,variable),g in stages.groupby(['model','crop','variable'])]
    report={'models':models,'baselines':baseline,'pairs':len(paired),'record_count':len(ids),'folds_unchanged':paired.fold.equals(fixed.fold),
        'stage_summary':summary,'uncertainty_vs_history':{name:bootstrap_changes(paired,name,'history_baseline')for name in ['organ_HI','source_sink','source_sink_target_only']},
        'carryover_ablation':{'mean_abs_rotation_contrast_change':float(np.mean(abs(paired.source_sink-paired.source_sink_target_only))),
            'max_abs_rotation_contrast_change':float(np.max(abs(paired.source_sink-paired.source_sink_target_only))),
            'meaning':'All-seasons versus target-only maize reproductive sink; identical organ accounting, forcing and management. Difference contains prior-maize allocation/carryover and its interaction with target growth, not a unique observed field mechanism.'},
        'missing_measurement_scores':{'LAI':None,'water':None,'maturity_date':None},
        'missing_score_reason':'No eligible dated LAI; water unit/volume conflict; exact observed maturity dates unknown.',
        'training_role':'New sink fitted only on LIRF2008–09 sampled grain. No rotation outcome enters fitting or parameter selection; seven farms remain exposed development, not a pristine final test.',
        'recorded_weather_is_retrospective':True,'new_independent_accuracy':None,'recommendation':None}
    write_json(out/'report.json',report);return report
