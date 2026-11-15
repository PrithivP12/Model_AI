"""Training-bounded N sensitivity and prior-only production-quality diagnosis."""
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
from ..processes import default_crops,Processes
from ..io import write_json
from ..data.lirf import old_cases,soil_prior,labels
from ..recovery.process import training_field_capacity
from ..intelligence.observations import crop_observations
from ..reproductive_recovery.observations import audit_lirf
from ..reproductive_recovery.experiment import parameter_profiles
from ..transfer_recovery.cross_environment import lirf_trace,lirf_score
from ..rotation_diagnosis.audit import load
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import pair_predictions,scores
from ..state_intelligence.trajectories import run_trajectory
from ..state_intelligence.paired import bootstrap_changes
from .kernel import MaterialKernel


def training_constraints(root,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);audit=audit_lirf(root,[2008,2009])
    values=[r['grain_N_fraction']for r in audit['tissue']]
    report={'years':[2008,2009],'grain_N_fraction_min':min(values),'grain_N_fraction_max':max(values),
        'new_parameters_fitted':[],'maturity_N_ceiling':max(values),
        'stage_specific_remobilization_identified':False,'root_N_measured':False,'separate_leaf_stalk_cob_N_stocks_identified':False,
        'tissue_observations':audit['tissue'],
        'rule':'Test one upper-training-concentration boundary, conserving N. Do not fit NHI, C:N or rotation yield benefit.'}
    write_json(out/'constraints_frozen.json',report);return report


def diagnose_partition(root,constraints,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    q=pd.read_csv(root/'reports/rotation-memory-recovery/verified-final/source-sink-production-quality/production_quality.csv')
    memory=json.loads((root/'reports/reproductive-recovery/final/rotation/source_sink_memory.json').read_text())
    records=json.loads((root/'reports/rotation-recovery/reproduced-final/input-audit/records.json').read_text())
    grouped={}
    for m in memory:
        rec=records[m['record_id']]
        for h in m['harvests']:
            if h['crop']!='corn':continue
            key=(rec['site_id'],rec['plot_id'],int(h['planted'][:4]));grouped.setdefault(key,[]).append(h)
    rows=[]
    for row in q[q.crop.eq('corn')].to_dict('records'):
        hs=grouped[(row['site'],str(row['plot']),int(row['year']))]
        pool={k:{v:float(np.mean([h['organ_state']['pools'][k][v]for h in hs]))for v in ['dry','N']}for k in hs[0]['organ_state']['pools']}
        grain=pool['grain']['dry'];above_N=sum(p['N']for k,p in pool.items()if k!='root');target=row['observed_aboveground_N_harvest_index']
        required=None if pd.isna(target)or grain<=0 else target*above_N/grain
        max_grain_N=grain*constraints['grain_N_fraction_max']
        rows.append({**row,'modeled_aboveground_N':above_N,
            'required_grain_N_fraction_for_observed_NHI_at_fixed_grain':required,
            'exceeds_training_grain_concentration_max':None if required is None else required>constraints['grain_N_fraction_max'],
            'max_NHI_at_training_grain_ceiling':max_grain_N/above_N,
            'modeled_labile_N_routed_with_non_grain_return':pool['labile_N']['N'],
            'root_N':pool['root']['N'],'root_dry':pool['root']['dry'],
            'senesced_leaf_N':pool['senesced_leaf']['N'],'green_leaf_N':pool['green_leaf']['N'],
            'structural_shoot_N':pool['structural_shoot']['N'],
            'cob_separate_model_stock':None,'measured_NHI_is_same_model_sample':False})
    frame=pd.DataFrame(rows);frame.to_csv(out/'feasibility.csv',index=False)
    report={'status':'grain_N_and_non_grain_allocation_feasibility_audited','maize_plot_years':len(frame),
        'required_grain_N_fraction_median':float(frame.required_grain_N_fraction_for_observed_NHI_at_fixed_grain.median()),
        'fraction_exceeding_training_grain_concentration_max':float(frame.exceeds_training_grain_concentration_max.dropna().mean()),
        'modeled_grain_N_fraction_median':float(frame.grain_N_fraction_model.median()),
        'observed_grain_N_fraction_median':float(frame.observed_grain_N_fraction.median()),
        'mean_labile_N_in_non_grain_return':float(frame.modeled_labile_N_routed_with_non_grain_return.mean()),
        'mean_senesced_leaf_N':float(frame.senesced_leaf_N.mean()),'mean_structural_shoot_N':float(frame.structural_shoot_N.mean()),
        'supported_diagnosis':'NHI discrepancy is coupled to underestimated grain/biomass and retained shoot/labile N. A grain concentration multiplier alone is not identified. Model has no separate cob account, and root/fallen-leaf stocks are unmeasured.',
        'no_global_N_multiplier':True,'no_new_independent_validation':True}
    write_json(out/'report.json',report);return report


def lirf_evaluate(root,constraints,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False);_,q,_=parameter_profiles(root)
    fit=json.loads((root/'reports/reproductive-recovery/final/training/fit_frozen.json').read_text())
    prior=training_field_capacity(root,soil_prior(root,True));cases=old_cases(root,2010)
    traits,_=crop_observations(root,[2010]);lai=json.loads((root/'reports/evidence-readiness/reproduced-final/observations/observations.json').read_text())['LAI']
    models={};runs_by={}
    for name,ceiling in [('material_bridge',None),('maturity_N_ceiling',constraints['maturity_N_ceiling'])]:
        runs=[];ds=[];options={'root_beta_per_m':4,'shape_alpha':1,'organ_model':True,'sink_coefficient':fit['coefficient']}
        if ceiling:options['maturity_grain_N_ceiling']=ceiling
        with patch('agrosim.transfer_recovery.cross_environment.TransferKernel',MaterialKernel):
            for c in cases:
                r,d,_=lirf_trace(c,prior,q,options,initialize_canopy=False);runs.append(r);ds.extend(d)
        m=lirf_score(runs,{2010:labels(root,2010)},traits);lookup={(r['case_id'],r['date']):r for r in ds}
        m['dated_LAI_RMSE']=float(np.sqrt(np.mean([(lookup[(r['case_id'],r['date'])]['lai']-r['value'])**2 for r in lai if r['year']==2010])))
        old=json.loads((root/'reports/reproductive-recovery/final/LIRF/source_sink_predictions.json').read_text())
        m['grain_prediction_max_difference_vs_source_sink']=float(max(abs(a['predicted_yield_dry_kg_ha']-b['predicted_yield_dry_kg_ha'])for a,b in zip(runs,old)))
        models[name]=m;runs_by[name]=runs;write_json(out/(name+'_predictions.json'),runs)
    report={'models':models,'same_six_exposed_development_cases':True,'new_independent_accuracy':None,
        'maturity_N_changes_no_dry_mass':True,'2010_tissue_N_error':None,'reason':'No measured2010 tissue N; no invented concentration or stock targets.'}
    if models['material_bridge']['grain_prediction_max_difference_vs_source_sink']>1e-7:raise AssertionError('Material bridge changed legacy grain')
    write_json(out/'report.json',report);return report


def exact_prior_composition(state,harvest,observed,*,site,plot,mode,relative_sd,target_year,resources):
    y=int(harvest['planted'][:4]);row=observed.get(f'{site}:{plot}:{y}',{})
    empty={'water':0.,'carbon':0.,'nitrogen':0.}
    if y>=target_year or harvest['crop']!='corn':return state,[],empty
    keys=['AGR09','AGR24','AGR10','AGR26']
    if any(row.get(k)is None or row[k]<0 for k in keys):return state,[],empty
    C=row['AGR09']+row['AGR24'];N=row['AGR10']+row['AGR26']
    if C<=0 or N<=0:return state,[],empty
    newN=harvest['shoot_residue_c_added_kg_ha']*(N/C);delta=newN-harvest['shoot_residue_n_added_kg_ha']
    s=deepcopy(state)
    if s.surface_residue_n_kg_ha+delta< -1e-8:raise ArithmeticError('Prior-production constraint would overdraw residue N')
    s.surface_residue_n_kg_ha+=delta
    adjustment={'water':0.,'carbon':0.,'nitrogen':delta}
    audit={'applied':True,'kind':'prior_maize_production_CN_retrospective_diagnostic','crop_year':y,
        'target_year':target_year,'source_row':row['source_row'],'analysis_increment':adjustment,'physical_flux':False,
        'observed_production_CN':C/N,'assumed_return_fraction':1-harvest['residue_removal_fraction'],
        'actual_return_measured':False,'exact_measurement_date_and_availability':None,
        'scope':'Composition operator applied only to newly returned prior maize shoots; C stock retained, roots/old residues unchanged. Analysis N is explicitly outside physical sources.'}
    return s,[audit],adjustment


def rotation_evaluate(root,constraints,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    fixed,records,soils,old,actual=load(root);paired=fixed.copy();p,_,joint=parameter_profiles(root)
    fit=json.loads((root/'reports/reproductive-recovery/final/training/fit_frozen.json').read_text())
    observed=json.loads((root/'reports/rotation-recovery/reproduced-final/validated-observations/observed_tissue_and_grain.json').read_text())['rows']
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    variants={'material_bridge':{},'maturity_N_ceiling':{'ceiling':True},'half_shoot_return':{'removal':.5},
        'prior_production_CN':{'observed_CN':True},'prior_production_CN_half_return':{'observed_CN':True,'removal':.5}}
    write_json(out/'protocol_frozen.json',{'variants':variants,'training_constraints':constraints,'new_fits':[],
        'fixed_pairs':106,'prior_outcome_operator_only':True,'current_target_C_N_grain_never_assimilated':True,
        'observed_CN_is_production_not_actual_return':True,'new_final_test':False})
    metrics={};memory={}
    for name,spec in variants.items():
        pred={};mem=[];maximum=0.;adjustments=0
        for i,rid in enumerate(ids):
            rec=records[rid];ys=[]
            for n in [40,120]:
                w,l,s,ps,es,_=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',n,True)
                ps=[replace(pl,aboveground_residue_removal_fraction=spec.get('removal',0))for pl in ps]
                crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=p['harvest_index'],grain_n_fraction=p['grain_n_fraction'],
                    rue_g_dry_per_MJ_PAR=joint['RUE'],specific_leaf_area_m2_kg=joint['leaf_area_coefficient']/.4)
                options={'root_beta_per_m':4,'shape_alpha':1,'organ_model':True,'sink_coefficient':fit['coefficient'],
                    'observed_harvest_sowing_dates':[str(pl.date)for pl,season in zip(ps,rec['timeline'])if season['harvest_date']]}
                if spec.get('ceiling'):options['maturity_grain_N_ceiling']=constraints['maturity_N_ceiling']
                k=MaterialKernel(l,crops,Processes(maize_n_dilution_a=.034),rec['latitude'],options=options);k.capture_runs=False
                if spec.get('observed_CN'):
                    with patch('agrosim.state_intelligence.trajectories.constrain_production',exact_prior_composition):
                        r=run_trajectory(w,l,s,ps,es,k,observed=observed,site=rec['site_id'],plot=rec['plot_id'],target_year=rec['year'],mode='retrospective')
                else:r=run_trajectory(w,l,s,ps,es,k,target_year=rec['year'])
                h=next(h for h in r['harvests']if h['planted'].startswith(str(rec['year'])));ys.append(h['grain_dry_kg_ha'])
                maximum=max(maximum,max(abs(v)for v in r['ledger_error'].values()));adjustments+=len(r['observation_audit'])
                mem.append({'record_id':rid,'initial_N':n,'harvests':r['harvests'],'Jan1_state':r['pretarget_state'],
                    'annual':r['annual_states'],'fluxes':r['physical_fluxes'],'analysis_increments':r['state_adjustments'],'observation_audit':r['observation_audit'],
                    'ledger_error':r['ledger_error']})
            pred[rid]={'prediction':float(np.mean(ys))}
            if i%100==0:print('nutrient experiment',name,i,flush=True)
        paired[name]=pair_predictions(fixed,pred)
        metrics[name]={**scores(paired,name),'mean_effect':float(paired[name].mean()),
            'yield_MAE':float(np.mean([abs(pred[r]['prediction']-actual.loc[r,'observed_yield_dry_kg_ha'])for r in ids])),
            'max_adjusted_WCN_ledger_error':maximum,'retrospective_prior_CN_updates':adjustments,
            'new_free_prediction_accuracy':not spec.get('observed_CN',False)}
        write_json(out/(name+'_record_predictions.json'),pred);write_json(out/(name+'_memory.json'),mem);memory[name]=mem
    parity=float(max(abs(paired.material_bridge-fixed.source_sink)))
    if parity>1e-7:raise AssertionError('Reusable material interface changed preserved source-sink predictions')
    paired.to_csv(out/'paired_predictions.csv',index=False)
    report={'models':metrics,'material_bridge_parity':parity,'same_106_pairs':True,'same_folds':paired.fold.equals(fixed.fold),
        'uncertainty_vs_history':{name:bootstrap_changes(paired,name,'history_baseline')for name in variants},
        'no_independent_validation':True,'water_state_error':None,'water_reason':'No new compatible depth/time matched field water in cohort.',
        'promoted_model':None,'recommendation':None}
    write_json(out/'report.json',report);return report
