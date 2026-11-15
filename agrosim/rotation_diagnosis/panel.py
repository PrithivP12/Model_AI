"""Bounded assumption diagnostics, with no fitting or silent default changes."""
from pathlib import Path
from dataclasses import asdict,replace
from datetime import date,timedelta
from copy import deepcopy
import hashlib,json,gzip
import numpy as np
import pandas as pd
from ..io import write_json
from ..processes import Processes,default_crops,Management
from ..evidence_readiness.identifiability import ShapeKernel
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import pair_predictions,scores
from ..state_intelligence.trajectories import run_trajectory
from .audit import load

FLUXES=['mineralized_n_kg_ha','immobilized_n_kg_ha','fixed_n_kg_ha','plant_uptake_n_kg_ha',
    'fertilizer_n_kg_ha','irrigation_mm','rain_mm','seed_n_kg_ha','nitrogen_inputs','nitrogen_outputs',
    'leached_n_kg_ha','denitrified_n_kg_ha','net_assimilated_c_kg_ha','respired_c_kg_ha',
    'evaporation_mm','transpiration_mm','drainage_mm','runoff_mm','harvested_c_kg_ha','harvested_n_kg_ha']
SPECS={
    'baseline':{},'initial_N40':{'initial_N':40},'initial_N120':{'initial_N':120},
    'initial_water_dry':{'available_water_fraction':.3},'initial_water_FC':{'available_water_fraction':1.},
    'SOC_half':{'humus_scale':.5},'SOC_1_5':{'humus_scale':1.5},
    'initial_residue_empty':{'initial_residue_C':0},'initial_residue_high':{'initial_residue_C':3000},
    'shoot_half_return':{'shoot_removal':.5},'uniform_root_uptake':{'root_beta':0},
    'humus_decay_off':{'humus_decay':0},'humification_low':{'humification_fraction':.1},
    'humification_high':{'humification_fraction':.4},
    'crop_N_demand_low':{'N_demand_scale':.8},'crop_N_demand_high':{'N_demand_scale':1.2},
    'prior_fallback_N_zero':{'prior_fallback_N_zero':True},
    'preserve_known_partial_prior_N':{'partial_prior_N':True},
}
REUSED={'residue_decay_off':'no_residue_decay','fixation_off':'no_fixation','shoot_export':'remove_shoot_residue',
    'previous_production_C_constraint':'retro_C_only','previous_production_N_constraint':'retro_N_only','previous_production_CN_constraint':'retro_sd50'}


class DiagnosticKernel(ShapeKernel):
    """State/flux telemetry, identical physical equations to the stable reference."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.segments=[];self.capture_runs=False

    def run(self,weather,*args,**kwargs):
        requested_daily=kwargs.get('keep_daily',True)
        kwargs['keep_daily']=True
        result=super().run(weather,*args,**kwargs)
        dates={date.fromisoformat(s)-timedelta(days=1)for s in self.options.get('diagnostic_planting_dates',[])}
        sampled=[{key:row[key]for key in row if key in ['date','crop','soil_water_mm','soil_mineral_n_kg_ha','soil_organic_c_kg_ha','residue_c_kg_ha','plant_biomass_dry_kg_ha','lai','water_stress_factor','n_growth_factor']or key.startswith(('water_layer_','mineral_n_layer_'))}
            for row in result['daily']if row['date'].endswith('-01')or date.fromisoformat(row['date'])in dates]
        self.segments.append({'start':str(weather[0].date),'end':str(weather[-1].date),
            'fluxes':{k:result['totals'].get(k,0.)for k in FLUXES},'soil_after':result['final_state'],
            'harvests':result['harvests'],'sampled_end_of_day_states':sampled})
        if not requested_daily:result['daily']=[]
        return result


def prepare_variant(root,record,old,soil,weather_dir,spec,initial_N):
    w,l,s,ps,es,evidence=prepare_record(root,record,old,soil,weather_dir,'C_soil',spec.get('initial_N',initial_N),True)
    if 'available_water_fraction'in spec:
        s.water_mm=[layer.mm(layer.wilting_point+spec['available_water_fraction']*(layer.field_capacity-layer.wilting_point))for layer in l]
    scale=spec.get('humus_scale',1.)
    s.humus_c_kg_ha=[c*scale for c in s.humus_c_kg_ha];s.humus_n_kg_ha=[n*scale for n in s.humus_n_kg_ha]
    if 'initial_residue_C'in spec:
        s.surface_residue_c_kg_ha=spec['initial_residue_C'];s.surface_residue_n_kg_ha=spec['initial_residue_C']/50.
    ps=[replace(p,aboveground_residue_removal_fraction=spec.get('shoot_removal',0))for p in ps]
    changes=[]
    for season,p in zip(record['timeline'],ps):
        if season['year']>=record['year']or season['fertilizer_total_if_recorded_complete']is not None:continue
        if not spec.get('prior_fallback_N_zero')and not(spec.get('partial_prior_N')and season['fertilizer_events']):continue
        # Remove only the explicit unresolved previous-crop fallback. Real dated
        # events belonging to other crop years must not be removed by calendar year.
        fallback=Management(p.date,140 if p.crop=='corn'else 0,0)
        es=list(es)
        try:es.remove(fallback)
        except ValueError:raise ValueError('Expected frozen prior fallback missing; do not delete arbitrary events')
        replacement=[]
        if spec.get('partial_prior_N'):
            replacement=[Management(date.fromisoformat(e['date']),e['n_kg_ha'],0)for e in season['fertilizer_events']if str(w[0].date)<=e['date']<=str(w[-1].date)]
        es.extend(replacement);changes.append({'season':season['season_key'],'removed_fallback':asdict(fallback),
            'replacement_logged_events':[asdict(e)for e in replacement],
            'unlogged_N_remains_unknown':True,'prestart_applications_not_added_twice':True})
    s.validate(l)
    return w,l,s,ps,es,{'original_input_evidence':evidence,'declared_spec':spec,'prior_N_changes':changes}


def run_panel(root,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    fixed,records,soils,old,actual=load(root);paired=fixed.copy()
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    cal=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())['candidates'][1]['parameters']
    crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=cal['harvest_index'],grain_n_fraction=cal['grain_n_fraction'])
    write_json(out/'protocol_frozen.json',{'variants':SPECS,'reused_completed_experiments':REUSED,'fit_parameters':[],
        'interpretation':'Shared treatment-arm sensitivity assumptions or knockouts, not measured alternative field conditions or calibrated probabilities.',
        'range_provenance':{'initial_N':'Previously preserved40/120 kg/ha nuisance endpoints; no new field distribution.',
            'water':'30–100% plant-available storage between each measured/scenario WP and FC; physical bracket, not measured probability.',
            'humus':'0.5/1.5×frozen45Mg C/ha, matching humus N scaled to conserve initial C:N; unmeasured profile stock sensitivity.',
            'initial_residue':'0/3000kg C/ha at the existing scenario C:N50, approximately0/6.7t dry/ha; bracket not an observed return.',
            'retention':'0/0.5/1 shoot fraction, physical bounds; roots still return.',
            'root':'Existing conservative uniform uptake alternative versus beta4, no invented rooting data.',
            'humification':'0.1/0.4 versus0.25, bounded process sensitivities with explicit respiration/humus transfers, not measured microbial parameters.',
            'N_demand':'±20% around the frozen0.034 maize critical-N coefficient; no outcome selection.',
            'N_logging':'Existing dated scope-qualified entries replace only unobserved prior fallback; log completeness is not established.'},
        'all_106_pairs_retained':True,'no_model_promotion_by_parameter_search':True})
    cache={};references={};models={};hits=0;record_rows=[]
    for name,spec in SPECS.items():
        predictions={};maximum=0.
        for rid in ids:
            rec=records[rid];group=[];links={};scenarios=[spec['initial_N']]if 'initial_N'in spec else [40,120]
            for n in scenarios:
                w,l,s,ps,es,evidence=prepare_variant(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact',spec,n)
                proc=Processes(maize_n_dilution_a=.034*spec.get('N_demand_scale',1.),
                    humus_decay_per_day_20C=.000025*spec.get('humus_decay',1.),humification_fraction=spec.get('humification_fraction',.25))
                options={'root_beta_per_m':spec.get('root_beta',4),'shape_alpha':0.,
                    'diagnostic_planting_dates':[str(p.date)for p in ps],
                    'observed_harvest_sowing_dates':[str(p.date)for p,season in zip(ps,rec['timeline'])if season['harvest_date']]}
                identity={'site':rec['site_id'],'end':str(w[-1].date),'layers':[asdict(x)for x in l],'initial':asdict(s),
                    'plants':[asdict(p)for p in ps],'events':[asdict(e)for e in es],'processes':asdict(proc),'options':options}
                key=hashlib.sha256(json.dumps(identity,sort_keys=True,default=str).encode()).hexdigest()
                if key not in cache:
                    k=DiagnosticKernel(l,crops,proc,rec['latitude'],options=options)
                    result=run_trajectory(w,l,s,ps,es,k,target_year=rec['year'])
                    h=next(h for h in result['harvests']if h['planted'].startswith(str(rec['year'])))
                    cache[key]={'prediction':h['grain_dry_kg_ha'],'Jan1_state':result['pretarget_state'],
                        'input_identity':identity,
                        'annual':result['annual_states'],'harvests':result['harvests'],'segments':k.segments,
                        'fluxes':{x:result['physical_fluxes'].get(x,0.)for x in FLUXES},'ledger_error':result['ledger_error'],
                        'input_evidence':evidence}
                else:hits+=1
                result=cache[key];group.append(result['prediction']);maximum=max(maximum,max(abs(v)for v in result['ledger_error'].values()))
                links[str(n)]=key
            predictions[rid]={'prediction':float(np.mean(group))};references[name+':'+rid]=dict(links)
            record_rows.append({'variant':name,'record_id':rid,'prediction':predictions[rid]['prediction'],
                'observed_yield':float(actual.loc[rid,'observed_yield_dry_kg_ha'])})
        paired[name]=pair_predictions(fixed,predictions)
        models[name]={**scores(paired,name),'mean_prediction':float(paired[name].mean()),
            'mean_observation':float(paired.observed_difference.mean()),
            'yield_MAE':float(np.mean([abs(predictions[r]['prediction']-float(actual.loc[r,'observed_yield_dry_kg_ha']))for r in ids])),
            'maximum_ledger_error':maximum,'is_assumption_sensitivity_not_selected_correction':name!='baseline'}
        write_json(out/(name+'_record_predictions.json'),predictions)
        print('assumption diagnostic',name,'unique runs',len(cache),'mean contrast',models[name]['mean_prediction'],flush=True)
    validate_run_references(cache,references,record_rows,records)
    # Preserve complete old knockouts/observation constraints without rerunning.
    previous=root/'reports/state-intelligence/reproduced-final/states';oldpairs=pd.read_csv(previous/'paired_predictions.csv')
    oldreport=json.loads((previous/'report.json').read_text())
    if not paired.pair_id.equals(oldpairs.pair_id)or max(abs(paired.baseline-oldpairs.scheduled_control))>1e-8:raise AssertionError('Reused ecological ablation cohort/reference changed')
    for name,oldname in REUSED.items():
        paired[name]=oldpairs[oldname];m=scores(paired,name)
        pred=json.loads((previous/(oldname+'_record_predictions.json')).read_text())
        models[name]={**m,'mean_prediction':float(paired[name].mean()),'mean_observation':float(paired.observed_difference.mean()),
            'maximum_ledger_error':oldreport['models'][oldname]['maximum_WCN_ledger_error'],
            'yield_MAE':float(np.mean([abs(pred[r]['prediction']-float(actual.loc[r,'observed_yield_dry_kg_ha']))for r in ids])),
            'reused_artifact':str((previous/(oldname+'_trajectories.json')).relative_to(root)),
            'role':'Retrospective previous-production constraint with explicit ledgered analysis increments; not free prediction or verified residue return.'if oldname.startswith('retro')else'Previously completed conservative knockout; no rerun.'}
    paired.to_csv(out/'paired_predictions.csv',index=False);pd.DataFrame(record_rows).to_csv(out/'record_predictions.csv',index=False)
    with gzip.open(out/'unique_runs.json.gz','wt')as stream:json.dump(cache,stream,default=str,allow_nan=False)
    write_json(out/'run_references.json',references)
    variant_names=list(SPECS)+list(REUSED);matrix=paired[variant_names].to_numpy()
    positive=(matrix>1e-6).all(axis=1);negative=(matrix< -1e-6).all(axis=1)
    sign_flip=(matrix.max(axis=1)>1e-6)&(matrix.min(axis=1)< -1e-6)
    sensitivity=paired[['pair_id','farm_id','observed_difference']].copy();sensitivity['all_diagnostics_min']=matrix.min(axis=1);sensitivity['all_diagnostics_max']=matrix.max(axis=1)
    sensitivity['sign_flip_including_knockouts_and_retro_diagnostics']=sign_flip
    # Physical assumption panel excludes deliberate process knockouts and
    # observed-production interventions, which are not plausible forecasts.
    physical=[x for x in SPECS if x not in ['humus_decay_off','prior_fallback_N_zero']]
    physical_matrix=paired[physical].to_numpy();sensitivity['assumption_min']=physical_matrix.min(axis=1);sensitivity['assumption_max']=physical_matrix.max(axis=1)
    sensitivity['assumption_sign_flip']=(physical_matrix.min(axis=1)< -1e-6)&(physical_matrix.max(axis=1)>1e-6)
    sensitivity.to_csv(out/'ranking_sensitivity.csv',index=False)
    report={'status':'bounded_fixed_cohort_assumptions_and_reused_mechanisms','models':models,'pairs':len(paired),'farms':7,
        'unique_physical_runs':len(cache),'identical_input_cache_hits':hits,'baseline_parity_max_difference':float(max(abs(paired.baseline-fixed.reference))),
        'all_diagnostic_sign_flips':int(sign_flip.sum()),'all_positive':int(positive.sum()),'all_negative':int(negative.sum()),
        'physical_assumption_variants':physical,'assumption_sign_flips':int(sensitivity.assumption_sign_flip.sum()),
        'scenario_spread_is_probability_interval':False,'new_independent_accuracy':None,'promoted_model':None}
    write_json(out/'report.json',report);return report


def validate_run_references(cache,references,record_rows,records):
    """A shared physical run may serve equal inputs, never unrelated records."""
    for row in record_rows:
        rid=row['record_id'];rec=records[rid];links=references[row['variant']+':'+rid]
        predictions=[]
        for key in links.values():
            run=cache[key];identity=run['input_identity']
            if identity['site']!=rec['site_id']or identity['end']!=f"{rec['year']}-12-31":raise AssertionError('Linked run has the wrong site/year')
            if [p['crop']for p in identity['plants']]!=[s['crop']for s in rec['timeline']]:raise AssertionError('Linked run has the wrong crop history')
            predictions.append(run['prediction'])
        if abs(np.mean(predictions)-row['prediction'])>1e-8:raise AssertionError('Linked run predictions disagree with the saved treatment yield')
