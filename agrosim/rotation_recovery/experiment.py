"""Identical-pair environmental intervention experiment before mechanistic changes."""
from pathlib import Path
from dataclasses import replace,asdict
from collections import Counter
import json
import numpy as np
import pandas as pd
from ..io import write_json
from ..processes import default_crops,Processes
from ..intelligence.physical import PhysicalCandidate
from ..rotation_learning import metrics
from .inputs import prepare_record


def simulate(root,records,old,soils,weather_dir,ids,variant,parameters=None,return_diagnostics=False):
    parameters=parameters or {};cache={};results={};conservation=0.;input_evidence={}
    source=json.loads((Path(root)/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text());cal=next(r for r in source['candidates']if r['name']=='fixed_root4_measured_partition')['parameters']
    for i,rid in enumerate(ids):
        rec=records[rid];outputs=[]
        for initial_n,pulsed in [(40,True),(120,False)]:
            weather,layers,soil,plants,events,evidence=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],weather_dir,variant,initial_n,pulsed)
            key=json.dumps({'site':rec['site_id'],'latitude':rec['latitude'],'N':initial_n,'plants':[asdict(p)for p in plants],
                'events':[asdict(e)for e in events],'layers':[asdict(l)for l in layers],'pulsed':pulsed,'parameters':parameters},sort_keys=True,default=str)
            if key not in cache:
                crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=cal['harvest_index'],grain_n_fraction=cal['grain_n_fraction'],
                    rue_g_dry_per_MJ_PAR=crops['corn'].rue_g_dry_per_MJ_PAR*parameters.get('rue_multiplier',1.),
                    maturity_gdd=parameters.get('maize_maturity_gdd',crops['corn'].maturity_gdd))
                crops['soybeans']=replace(crops['soybeans'],rue_g_dry_per_MJ_PAR=crops['soybeans'].rue_g_dry_per_MJ_PAR*parameters.get('soy_rue_multiplier',1.),plant_n_fraction=parameters.get('soy_N_fraction',crops['soybeans'].plant_n_fraction))
                p=Processes(maize_n_dilution_a=.034*parameters.get('n_demand_multiplier',1.),residue_decay_per_day_20C=.008*parameters.get('residue_decay_multiplier',1.))
                options={'root_beta_per_m':4}
                if parameters.get('scheduled_harvest'):
                    from .mechanisms import ReconstructionKernel
                    options['observed_harvest_sowing_dates']=[str(plant.date)for plant,season in zip(plants,rec['timeline'])if season['harvest_date']]
                    kernel=ReconstructionKernel(layers,crops,p,rec['latitude'],options=options)
                else:kernel=PhysicalCandidate(layers,crops,p,rec['latitude'],options=options)
                run=kernel.run(weather,soil,plants,events,keep_daily=return_diagnostics)
                harvest=[h for h in run['harvests']if h['planted'].startswith(str(rec['year']))]
                if len(harvest)!=1:raise ValueError('Missing target harvest; preserve pair and diagnose inputs')
                h=harvest[0];whole=max(abs(v)for v in run['whole_run_balance_error'].values());conservation=max(conservation,whole)
                cache[key]={'yield':h['grain_dry_kg_ha'],'harvests':run['harvests'],'max_balance_error':whole,
                    'final_mineral_n':sum(run['final_state']['mineral_n_kg_ha']),
                    'total_min_n_released':run['totals']['mineralized_n_kg_ha'],'total_N_immobilized':run['totals']['immobilized_n_kg_ha'],
                    'total_fixed_N':run['totals']['fixed_n_kg_ha'],'total_leached_N':run['totals']['leached_n_kg_ha'],
                    'daily':run['daily']if return_diagnostics else []}
            outputs.append(cache[key]);input_evidence[rid]=evidence
        results[rid]={'prediction':float(np.mean([o['yield']for o in outputs])),'scenario_min':min(o['yield']for o in outputs),'scenario_max':max(o['yield']for o in outputs),
            'scenarios':outputs}
        if i%60==0:print(variant,'record',i,'cache',len(cache),flush=True)
    return results,{'unique_runs':len(cache),'maximum_conservation_error':conservation,'inputs':input_evidence}


def pair_predictions(fixed,record_predictions):
    values=[]
    for r in fixed.to_dict('records'):
        sides={side:float(np.mean([record_predictions[k]['prediction']for k in r[column].split(';')]))for side,column in [('reference','reference_records'),('candidate','candidate_records')]}
        values.append(sides['candidate']-sides['reference'])
    return np.asarray(values)


def scores(frame,column):
    result=metrics(frame,column);p=frame[column].to_numpy();y=frame.observed_difference.to_numpy()
    result['control_fallback_regret_all_pairs']=float(np.mean(np.where(p>1e-6,np.maximum(-y,0),np.maximum(y,0))))
    result['correct_signed_decisions_fraction_all_pairs']=float(np.mean((np.sign(p)==np.sign(y))&(abs(p)>1e-6)))
    result['abstentions']=int((abs(p)<=1e-6).sum());return result


def run_environment(root,output,weather_dir=None,audit_dir=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed environmental comparison')
    weather_dir=Path(weather_dir or root/'reports/rotation-recovery/weather-exact');audit_dir=Path(audit_dir or root/'reports/rotation-recovery/input-audit')
    records=json.loads((audit_dir/'records.json').read_text());soils=json.loads((audit_dir/'soil_observations.json').read_text())
    old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv');ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    protocol={'fixed_pairs':106,'fixed_ids':fixed.pair_id.tolist(),'farm_folds_unchanged':True,'no_model_coefficients_changed':True,
        'variants':['B_replay','C_weather','C_dates','C_N','C_soil'],'weather_directory':str(weather_dir),
        'input_rule':'Only traceable replacements; unresolved conditions remain explicitly labeled frozen nuisance scenarios, not newly invented field observations.',
        'not_preplant_forecast':True,'final_evaluation':False}
    write_json(out/'protocol_frozen.json',protocol);rows={};predictions=fixed.copy();diagnostics={}
    for variant in protocol['variants']:
        pred,meta=simulate(root,records,old,soils,weather_dir,ids,variant)
        write_json(out/(variant+'_record_predictions.json'),pred);write_json(out/(variant+'_inputs.json'),meta)
        predictions[variant]=pair_predictions(fixed,pred);rows[variant]=scores(predictions,variant)
        # Yield labels are raw measured field outcomes, not model-generated targets.
        original=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
        errors=[pred[k]['prediction']-float(original.loc[k,'observed_yield_dry_kg_ha'])for k in ids]
        rows[variant]['yield_MAE_kg_dry_ha']=float(np.mean(abs(np.array(errors))))
        lag=[]
        for rid in ids:
            rec=records[rid]
            for outputrun in pred[rid]['scenarios']:
                for h in outputrun['harvests']:
                    season=next(s for s in rec['timeline']if s['year']==int(h['planted'][:4]))
                    if season['harvest_date']:
                        from datetime import date
                        lag.append((date.fromisoformat(season['harvest_date'])-date.fromisoformat(h['harvested'])).days)
        diagnostics[variant]={'mean_days_modeled_harvest_precedes_recorded':float(np.mean(lag)),
            'fraction_before_recorded':float(np.mean(np.array(lag)>0)),'matched_scenario_harvests':len(lag)}
        print(variant,rows[variant]['directional_agreement'],rows[variant]['mae_difference_kg_dry_ha'],flush=True)
    predictions.to_csv(out/'paired_predictions.csv',index=False)
    replay=float(np.max(abs(predictions.B_replay-predictions.corrected_coupled)))
    report={'status':'fixed_cohort_environmental_interventions_evaluated','protocol':protocol,'models':{'A_history':scores(predictions,'family_history_baseline'),**rows},
        'B_replay_max_difference':replay,'same_pairs_and_folds':True,'harvest_timing_diagnostics':diagnostics,
        'water_error':None,'water_error_reason':'No matched soil-moisture observations in these eligible plot records; retention measurements are not moisture observations.',
        'initial_states_still_unknown':True,'recommendation':None,'independent_or_causal_rotation_accuracy':False}
    write_json(out/'report.json',report);return report
