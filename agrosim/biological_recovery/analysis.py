"""Process attribution, ecological memory and a frozen small learned correction."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from ..io import write_json
from ..state_intelligence.features import prior_state_features,mean_or_missing
from ..state_intelligence.paired import RAW,STATES,fit_ridge,bootstrap_changes
from ..rotation_recovery.experiment import scores


def analyze(root,biology_dir,output):
    root=Path(root);bio=Path(biology_dir);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    fixed=pd.read_csv(bio/'paired_predictions.csv');selected=json.loads((bio/'selections_frozen.json').read_text())
    source=root/'reports/state-intelligence/reproduced-final'
    raw=pd.read_csv(source/'features/features.csv');manifest=json.loads((source/'features/feature_manifest.json').read_text())
    raw_columns=sorted(k for k,v in manifest.items()if v['group']in RAW)
    old=pd.read_csv(source/'paired/paired_predictions.csv')
    if not raw.pair_id.equals(fixed.pair_id)or not old.pair_id.equals(fixed.pair_id):raise ValueError('Comparison cohort changed')
    profiles={name:json.loads((bio/(name+'_trajectories.json')).read_text())for name in json.loads((bio/'profiles_frozen.json').read_text())}
    result=fixed.copy();result['new_state_hybrid_alpha1000']=np.nan
    result['previous_state_hybrid']=old.hybrid_paired;result['direct_paired']=old.direct_paired
    records=json.loads((root/'reports/rotation-recovery/reproduced-final/input-audit/records.json').read_text())
    memory=[];artifacts=[]
    for fold in selected:
        profile=fold['chosen'];runs=profiles[profile];values={}
        for rid,scenarios in runs.items():
            rec=records[rid];parts=[prior_state_features(r,rec['year'])for r in scenarios]
            values[rid]={k:mean_or_missing([v[k]for v in parts])for k in parts[0]}
        frame=raw.copy();state_columns=[]
        for key in next(iter(values.values())):
            for role in ['mean','difference']:
                col='new_'+role+'_'+key;state_columns.append(col);numbers=[]
                for pair in fixed.to_dict('records'):
                    sides={side:mean_or_missing([values[rid][key]for rid in pair[field].split(';')])for side,field in [('candidate','candidate_records'),('reference','reference_records')]}
                    numbers.append((sides['candidate']+sides['reference'])/2 if role=='mean'else sides['candidate']-sides['reference'])
                frame[col]=numbers
        fit=frame.farm_id.isin(fold['fit_farms']);test=frame.farm_id.isin(fold['test_farms'])
        model=fit_ridge(frame.loc[fit,raw_columns+state_columns],frame.loc[fit,'observed_difference'],frame.loc[fit,'farm_id'],1000.)
        pred=model.predict(frame.loc[test,raw_columns+state_columns]);result.loc[test,'new_state_hybrid_alpha1000']=pred
        artifacts.append({'fold':fold['fold'],'biological_profile':profile,'fit_farms':fold['fit_farms'],'test_farms':fold['test_farms'],
            'calibration_farm_excluded':fold['calibration_farm'],'alpha':1000.,'alpha_origin':'Frozen strongest shrinkage selected in all original folds in the preceding phase; not selected on new outer outcomes.',
            'predictors':raw_columns+state_columns,'physical_state_modified_by_learned_correction':False})
        held_ids=set(rid for pair in fixed.loc[test].to_dict('records')for key in ['reference_records','candidate_records']for rid in pair[key].split(';'))
        for rid in sorted(held_ids):
            for which,name in [('before','legacy_scheduled'),('after',profile)]:
                for r in profiles[name][rid]:
                    s=r['pretarget_state'];memory.append({'record_id':rid,'farm':records[rid]['farm_id'],'profile':name,'role':which,
                        'initial_N_scenario':r['N_scenario'],'water':sum(s['water_mm']),'mineral_N':sum(s['mineral_n_kg_ha']),
                        'residue_C':sum(s['residue_c_kg_ha'])+s['surface_residue_c_kg_ha'],'residue_N':sum(s['residue_n_kg_ha'])+s['surface_residue_n_kg_ha'],
                        'humus_C':sum(s['humus_c_kg_ha']),'fixed_N':r['physical_fluxes'].get('fixed_n_kg_ha',0.),
                        'leached_N':r['physical_fluxes'].get('leached_n_kg_ha',0.),
                        'target_grain':next(h['grain_dry_kg_ha']for h in r['harvests']if h['planted'].startswith(str(records[rid]['year'])))})
    result.to_csv(out/'paired_predictions.csv',index=False);pd.DataFrame(memory).to_csv(out/'held_farm_memory.csv',index=False);write_json(out/'fit_artifacts.json',artifacts)
    budget=pd.read_csv(bio/'growth_budgets.csv')
    # Repeated contexts and starting-N scenarios are not independent plant seasons.
    numeric=['available_PAR_MJ_m2','intercepted_PAR_MJ_m2','radiation_potential_dry_kg_ha','temperature_loss_dry_kg_ha','water_loss_dry_kg_ha','N_loss_dry_kg_ha','growth_dry_kg_ha']
    unique=budget.groupby(['profile','farm','site','plot','crop_year','crop'],as_index=False)[numeric].mean()
    unique.to_csv(out/'unique_crop_process_budgets.csv',index=False);processes=[]
    for (name,crop),g in unique.groupby(['profile','crop'],sort=True):
        total=g[numeric].sum();potential=total.radiation_potential_dry_kg_ha
        closure=potential-total.temperature_loss_dry_kg_ha-total.water_loss_dry_kg_ha-total.N_loss_dry_kg_ha-total.growth_dry_kg_ha
        processes.append({'profile':name,'crop':crop,'unique_plot_years':len(g),'intercepted_PAR_fraction':float(total.intercepted_PAR_MJ_m2/total.available_PAR_MJ_m2),
            'temperature_loss_fraction':float(total.temperature_loss_dry_kg_ha/potential),'water_loss_fraction':float(total.water_loss_dry_kg_ha/potential),
            'N_loss_fraction':float(total.N_loss_dry_kg_ha/potential),'realized_growth_fraction':float(total.growth_dry_kg_ha/potential),
            'sequential_growth_budget_closure_kg_ha':float(closure),
            'interpretation':'Ordered model growth-budget decomposition, not additive causal attribution or a no-stress counterfactual.'})
    models={k:scores(result,k)for k in ['history_baseline','legacy_scheduled','training_selected','direct_paired','previous_state_hybrid','new_state_hybrid_alpha1000']}
    physical=models['training_selected'];baseline=models['history_baseline']
    gate=lambda v:bool(v['mae_difference_kg_dry_ha']<=baseline['mae_difference_kg_dry_ha']and v['farm_macro_rmse_kg_dry_ha']<baseline['farm_macro_rmse_kg_dry_ha']and v['correct_signed_decisions_fraction_all_pairs']>=baseline['correct_signed_decisions_fraction_all_pairs']and v['control_fallback_regret_all_pairs']<=baseline['control_fallback_regret_all_pairs'])
    report={'status':'process_memory_and_small_correction_evaluated','models':models,'process_budgets':processes,
        'joint_gate_vs_history':{name:gate(v)for name,v in models.items()if name!='history_baseline'},
        'hybrid_change_vs_old_hybrid':bootstrap_changes(result,'new_state_hybrid_alpha1000','previous_state_hybrid'),
        'hybrid_change_vs_history':bootstrap_changes(result,'new_state_hybrid_alpha1000','history_baseline'),
        'mechanistic_change_vs_history':bootstrap_changes(result,'training_selected','history_baseline'),
        'nominal90_new_farm_interval':'Unbounded under the original single-calibration-farm design; no supported directional recommendation.',
        'uncertainty_decision_coverage':0.,'operational_preplant_accuracy':None,'independent_final_accuracy':None,'recommendation':None,
        'treatment_contrasts_causal':False,'forecast_boundary':'Archived actual forcing/management and present-day trained models; all numerical scores are exposed-development retrospective diagnostics.'}
    write_json(out/'report.json',report);return report
