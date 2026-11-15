"""Fixed-cohort multi-metric comparison and farm-level uncertainty."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.stats import t
from ..io import write_json
from .experiment import scores


def assess(root,run_dir):
    root=Path(root);out=Path(run_dir)
    if(out/'assessment.json').exists():raise RuntimeError('Preserve completed rotation assessment')
    base=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv')
    env=pd.read_csv(out/'environment/paired_predictions.csv');mechanical=pd.read_csv(out/'mechanisms/paired_predictions.csv');prior=pd.read_csv(out/'prior-crop-calibration/paired_predictions.csv');condition=pd.read_csv(out/'observation-constrained/paired_predictions.csv')
    frames=[env,mechanical,prior,condition]
    for frame in frames:
        if frame.pair_id.tolist()!=base.pair_id.tolist()or frame.fold.tolist()!=base.fold.tolist():raise ValueError('Pairs/folds changed')
        if not np.allclose(frame.observed_difference,base.observed_difference,rtol=0,atol=1e-8):raise ValueError('Observed contrast changed')
    combined=base[['pair_id','farm_id','site_id','year','replicate','tillage','drainage','nitrogen_treatment','landscape','fold','observed_difference','reference_records','candidate_records']].copy()
    mapping={'A_history':base.family_history_baseline,'B_current':base.corrected_coupled,'C_historical_weather':env.C_weather,
        'C_historical_inputs':env.C_soil,'D_small_physical_calibration':mechanical.D_training_selected,
        'D_prior_crop_calibration':prior.D_crop_calibrated,'E_diagnostic_measured_production':condition.E_prior_tissue_constrained,
        'E_matched_unconstrained_control':condition.E_segmented_no_adjustments}
    metrics={}
    for name,p in mapping.items():combined[name]=p;metrics[name]=scores(combined,name)
    yieldenv=json.loads((out/'environment/report.json').read_text())['models']
    for name,source in [('B_current','B_replay'),('C_historical_weather','C_weather'),('C_historical_inputs','C_soil')]:metrics[name]['absolute_yield_MAE']=yieldenv[source]['yield_MAE_kg_dry_ha']
    metrics['D_small_physical_calibration']['absolute_yield_MAE']=json.loads((out/'mechanisms/report.json').read_text())['D_selected_absolute_yield_MAE']
    metrics['D_prior_crop_calibration']['absolute_yield_MAE']=json.loads((out/'prior-crop-calibration/report.json').read_text())['D_crop_calibrated_absolute_yield_MAE']
    actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    # An absolute-yield companion to the same paired family-history baseline.
    # Equal farm weighting of the two role means reproduces its contrast exactly.
    source_folds=json.loads((root/'reports/reassessed/direct-rotation/report.json').read_text())['folds']
    A_errors=[];A_contrast_check=[]
    def side_y(row,column):return float(np.mean([float(actual.loc[rid,'observed_yield_dry_kg_ha'])for rid in row[column].split(';')]))
    for fold in source_folds:
        fit=base[base.farm_id.isin(fold['fit_farms'])];test=base[base.farm_id.isin(fold['test_farms'])]
        if test.empty:continue
        role_means={}
        for role,column in [('reference','reference_records'),('candidate','candidate_records')]:
            role_means[role]=float(np.mean([np.mean([side_y(row,column)for row in group.to_dict('records')])for farm,group in fit.groupby('farm_id')]))
        for row in test.to_dict('records'):
            A_contrast_check.append(abs(role_means['candidate']-role_means['reference']-row['family_history_baseline']))
            for role,column in [('reference','reference_records'),('candidate','candidate_records')]:
                A_errors.extend(abs(role_means[role]-float(actual.loc[rid,'observed_yield_dry_kg_ha']))for rid in row[column].split(';'))
    metrics['A_history']['absolute_yield_MAE']=float(np.mean(A_errors))
    metrics['A_history']['absolute_companion_contrast_max_difference']=max(A_contrast_check)
    if max(A_contrast_check)>1e-8:raise ValueError('Absolute family baseline is not equivalent to frozen contrast baseline')
    records=json.loads((out/'observation-constrained/record_predictions.json').read_text())
    for name,source in [('E_diagnostic_measured_production','E_prior_tissue_constrained'),('E_matched_unconstrained_control','E_segmented_no_adjustments')]:
        metrics[name]['absolute_yield_MAE']=float(np.mean([abs(r['prediction']-float(actual.loc[rid,'observed_yield_dry_kg_ha']))for rid,r in records[source].items()]))
    farms=sorted(combined.farm_id.unique());rng=np.random.default_rng(817);draw=rng.integers(0,len(farms),(4000,len(farms)));uncertainty={}
    for candidate,reference in [(n,'A_history')for n in mapping if n!='A_history']+[('E_diagnostic_measured_production','E_matched_unconstrained_control')]:
        changes=[]
        for farm,g in combined.groupby('farm_id'):
            y=g.observed_difference.to_numpy();p=g[candidate].to_numpy();q=g[reference].to_numpy()
            regret=lambda x:np.where(x>1e-6,np.maximum(-y,0),np.maximum(y,0))
            changes.append([np.mean(abs(p-y)-abs(q-y)),np.mean(regret(p)-regret(q)),np.mean((np.sign(p)==np.sign(y))&(abs(p)>1e-6))-np.mean((np.sign(q)==np.sign(y))&(abs(q)>1e-6))])
        delta=np.asarray(changes);bootstrap=delta[draw].mean(axis=1)
        uncertainty[candidate+'_vs_'+reference]={'farm_macro_MAE_change':float(delta[:,0].mean()),'MAE_change_95pct_percentile':np.quantile(bootstrap[:,0],[.025,.975]).tolist(),
            'farm_macro_regret_change':float(delta[:,1].mean()),'regret_change_95pct_percentile':np.quantile(bootstrap[:,1],[.025,.975]).tolist(),
            'all_pair_signed_correctness_change_95pct_percentile':np.quantile(bootstrap[:,2],[.025,.975]).tolist(),
            'scope':'Seven exposed development farms; fixed-prediction cluster bootstrap, not an independent geography confidence guarantee.'}
    joint=lambda v,b:v['farm_macro_rmse_kg_dry_ha']<b['farm_macro_rmse_kg_dry_ha']and v['directional_agreement']>=b['directional_agreement']and v['control_fallback_regret_all_pairs']<=b['control_fallback_regret_all_pairs']and v['correct_signed_decisions_fraction_all_pairs']>=b['correct_signed_decisions_fraction_all_pairs']
    precision=[]
    keys=['site_id','year','tillage','drainage','nitrogen_treatment','landscape']
    for key,g in combined.groupby(keys,dropna=False):
        y=g.observed_difference.to_numpy();n=len(y);mean=float(y.mean());half=float(t.ppf(.975,n-1)*np.std(y,ddof=1)/np.sqrt(n))if n>1 else None
        precision.append({**dict(zip(keys,key)),'replicate_pairs':n,'mean_observed_contrast':mean,'descriptive_t_halfwidth':half,
            'interval_crosses_zero':None if half is None else abs(mean)<=half,'causal_identification':False})
    pd.DataFrame(precision).to_csv(out/'experimental_contrast_precision.csv',index=False)
    # Management differences are descriptive diagnostics, not fitted rotation effects.
    audit=json.loads((out/'input-audit/records.json').read_text());doses=[]
    for row in combined.to_dict('records'):
        sides={}
        for side,col in [('reference','reference_records'),('candidate','candidate_records')]:
            vals=[audit[rid]['timeline'][-1]['fertilizer_total_if_recorded_complete']for rid in row[col].split(';')]
            sides[side]=float(np.mean(vals))if all(v is not None for v in vals)else None
        doses.append({'pair_id':row['pair_id'],'candidate_minus_reference_reported_N':sides['candidate']-sides['reference']if all(v is not None for v in sides.values())else None})
    pd.DataFrame(doses).to_csv(out/'management_dose_differences.csv',index=False)
    pair_states=[];raw=json.loads((out/'environment/C_soil_record_predictions.json').read_text())
    for row in combined.to_dict('records'):
        values=[]
        for index in [0,1]:
            ref=np.mean([raw[rid]['scenarios'][index]['yield']for rid in row['reference_records'].split(';')]);can=np.mean([raw[rid]['scenarios'][index]['yield']for rid in row['candidate_records'].split(';')]);values.append(float(can-ref))
        pair_states.append({'pair_id':row['pair_id'],'N40_contrast':values[0],'N120_contrast':values[1],'direction_changes':np.sign(values[0])!=np.sign(values[1])})
    pd.DataFrame(pair_states).to_csv(out/'initial_N_scenario_sensitivity.csv',index=False)
    combined.to_csv(out/'all_models_same_pairs.csv',index=False)
    report={'status':'same_pair_rotation_gap_assessment_completed','pairs':len(combined),'farms':len(farms),'same_pairs_folds_targets':True,'models':metrics,
        'joint_gate_vs_history':{name:joint(m,metrics['A_history'])for name,m in metrics.items()if name!='A_history'},'uncertainty':uncertainty,
        'initial_N_direction_change_fraction':float(np.mean([r['direction_changes']for r in pair_states])),
        'initial_N_scope':'Only the declared 40/120 kg/ha starting scenarios, not full uncertainty in initial states.',
        'contrast_precision':{'groups':len(precision),'groups_with_replication':sum(p['descriptive_t_halfwidth']is not None for p in precision),
            'replicated_groups_interval_crosses_zero_fraction':float(np.mean([p['interval_crosses_zero']for p in precision if p['interval_crosses_zero']is not None])),
            'interpretation':'Descriptive within-experiment replicate uncertainty. Dose/management differences and repeated plots prevent pure causal or broad independent interpretation.'},
        'verified_E_model_available':False,'water_error_available':False,'preplant_prediction_scores_available':False,
        'nominal90_new_farm_intervals':'unbounded with at most one calibration farm in the fixed design; no probability of success issued',
        'final_test':'No genuinely untouched final farm is available among these previously exposed experiments. Excluded farms/2015/Australian outcomes were not used for new fitting.',
        'recommendation':None,'new_independent_rotation_accuracy_established':False}
    write_json(out/'assessment.json',report);return report
