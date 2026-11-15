"""Real paired outcomes under explicitly uncertain existing climate scenarios.

No matched daily 2011–2014 weather/initial profiles are present for these farms.
This restricted benchmark scores *conditional scenario predictions* against real
yields. It cannot identify pure rotation causality or validate physical fluxes.
"""
from pathlib import Path
from datetime import date,timedelta
from dataclasses import replace
from collections import Counter
from time import perf_counter
import calendar,json
import numpy as np
import pandas as pd
from ..rotation_learning import paired_records,baseline,metrics
from ..processes import Layer,SoilState,WeatherDay,Planting,Management,Processes,default_crops
from ..soil import DepthSimulator
from ..io import write_json,sha256
from ..recovery.uncertainty import conformal_radius
from .physical import PhysicalCandidate


CROP_MAP={'corn':'corn','maize':'corn','soybean':'soybeans','soybeans':'soybeans'}


def trajectory(row,index):
    """Return eligible raw history/current season; never create pre-2011 crops."""
    history=json.loads(row['previous_crops_json']);year=int(row['year'])
    if not history:return None,'missing_crop_history'
    crops={int(h['year']):CROP_MAP.get(str(h['crop']).strip().lower())for h in history};crops[year]='corn'
    if sorted(crops)!=list(range(2011,year+1)):return None,'noncontiguous_history'
    if any(c is None for c in crops.values()):return None,'unsupported_or_unknown_crop'
    sow=row['planting_date_resolved']
    if pd.isna(sow):return None,'missing_current_planting'
    if not bool(row['fertilizer_scope_complete'])or bool(row['zero_treatment_conflict']):return None,'ambiguous_target_N'
    total=row['recorded_n_total_conditional']
    if not np.isfinite(total):return None,'missing_target_N'
    events=[]
    for event in json.loads(row['operation_events_json']):
        value=event.get('nitrogenelem');t=event.get('date')
        if value is None or not np.isfinite(value)or value<0 or not t:return None,'ambiguous_N_event'
        if date.fromisoformat(t).year==year:events.append((t,float(value)))
    if abs(sum(v for t,v in events)-float(total))>1e-5:return None,'N_total_event_mismatch'
    return {'site_id':row['site_id'],'farm_id':row['farm_id'],'year':year,'latitude':float(row['latitude']),
        'history':[(y,crops[y])for y in sorted(crops)],'target_sowing':str(sow)[:10],'target_N_events':events,
        'record_id':row['record_id'],'recorded_target_N':float(total)},None


def climate_weather(root,site,start,end,pulsed=True):
    p=Path(root)/'rotation-learning-v1/weather'/f'{site}.json';d=json.loads(p.read_text())['response'];parameters=d['properties']['parameter'];monthly={}
    for name in ['T2M','PRECTOTCORR','ALLSKY_SFC_SW_DWN']:
        units=d['parameters'][name]['units'];expected={'T2M':['C'],'PRECTOTCORR':['mm/day'],'ALLSKY_SFC_SW_DWN':['MJ/m^2/day','kW-hr/m^2/day']}
        if units not in expected[name]:raise ValueError('Unsupported monthly normal unit')
        factor=3.6 if units=='kW-hr/m^2/day'else 1.
        monthly[name]={m:float(np.mean([v for key,v in parameters[name].items()if key[-2:]==f'{m:02d}'and 2001<=int(key[:4])<=2010 and v!=-999]))*factor for m in range(1,13)}
    weather=[]
    for i in range((end-start).days+1):
        t=start+timedelta(days=i);temp=monthly['T2M'][t.month];rain=monthly['PRECTOTCORR'][t.month]
        if pulsed:rain=rain*calendar.monthrange(t.year,t.month)[1]/4 if t.day in [4,11,18,25]else 0.
        weather.append(WeatherDay(t,rain,temp-5,temp+5,monthly['ALLSKY_SFC_SW_DWN'][t.month]))
    return weather,sha256(p)


def run_rotation(root,output,refinement_path=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed paired rotation experiment')
    frame=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet');frame=frame[(frame.year<=2014)&~frame.farm_id.isin(['KELLOGG','ORR','LAN'])].copy()
    pairs=paired_records(frame);records=frame.set_index('record_id').to_dict('index');sourcefold=json.loads((root/'reports/reassessed/direct-rotation/report.json').read_text())['folds']
    refinement=json.loads(Path(refinement_path or root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text());selected=next(r for r in refinement['candidates']if r['name']=='fixed_root4_measured_partition')
    accepted=[];excluded=Counter();models={};trajectory_metadata={}
    for pair in pairs.to_dict('records'):
        if pair['family']!='CS':excluded['unsupported_cover_or_wheat_system']+=1;continue
        sides={};reason=None
        for side,column in [('reference','reference_records'),('candidate','candidate_records')]:
            sides[side]=[]
            for rid in pair[column].split(';'):
                row=records[rid];row['record_id']=rid;t,reason=trajectory(row,records)
                if reason:break
                sides[side].append(t);trajectory_metadata[rid]=t
            if reason:break
        if reason:excluded[reason]+=1;continue
        accepted.append({**pair,'trajectories':sides})
    if not accepted:raise ValueError('No paired maize/soybean records support the declared input scope')
    protocol={'same_outer_farm_memberships':True,'source_folds':'reports/reassessed/direct-rotation/report.json',
        'source_feature_sha256':sha256(root/'reports/reassessed/data/enhanced_site_seasons.parquet'),
        'selection_cohort':'Paired CS vs CC systems with contiguous 2011+ corn/soybean history, resolved current sowing and target N events. No pre-study crop history invented.',
        'models':['family_history_baseline','existing_mechanistic','corrected_physical_no_organic_N','corrected_coupled','corrected_coupled_no_residue_decay'],
        'physics_fit':'LIRF 2008–2009 root-flux and crop-trait fit; no CSCAP outcomes fitted by process candidates',
        'forcing':'Existing NASA POWER 2001–2010 monthly normals; daily temperature +/-5 C and rain timing are declared scenarios, not observed 2011–2014 farm weather',
        'two_initialization_weather_scenarios':[{'initial_N_kg_ha':40,'rainfall':'4 pulses/month'},{'initial_N_kg_ha':120,'rainfall':'uniform daily'}],
        'initial_soil':'Common assumed 1.2 m loam: WP .12, FC .28, saturation .45; 45 Mg C/ha, C:N12; initial water 75/80/150 mm and 1000kg C surface residue CN50. Not matched field profiles.',
        'management':'Target maize uses resolved dated N events and planting date. Prior maize N=140kg/ha and May15 sowing when unknown; prior soy N=0. Harvest deadline Oct15 assumed. No irrigation application assumed, not an observed zero.',
        'ablation':'corrected_physical_no_organic_N disables residue/humus turnover and fixation while retaining explicit mineral N/uptake. It is a mechanism control, not a plausible full agronomic model.',
        'release':'Conditional scenario benchmark on exposed development farms, not causal or final validation. Reject physical-system promotion if it fails the history baseline.',
        'outcomes_not_simulation_targets':True,'no_new_datasets':True}
    write_json(out/'protocol_frozen.json',protocol);write_json(out/'eligible_trajectories.json',trajectory_metadata)
    starttime=perf_counter();cache={};weathercache={};sources={};raw=[]
    for i,pair in enumerate(accepted):
        outputrow={k:v for k,v in pair.items()if k!='trajectories'}
        for model in protocol['models'][1:]:
            predictions=[]
            for initial_n,pulsed in [(40,True),(120,False)]:
                values={}
                for side,trajectories in pair['trajectories'].items():
                    estimates=[]
                    for t in trajectories:
                        key=json.dumps([model,initial_n,pulsed,{k:v for k,v in t.items()if k not in ['record_id','farm_id']}],sort_keys=True)
                        if key not in cache:
                            year=t['year'];weatherkey=(t['site_id'],year,pulsed)
                            if weatherkey not in weathercache:
                                weathercache[weatherkey],sources[t['site_id']]=climate_weather(root,t['site_id'],date(2011,1,1),date(year,12,31),pulsed)
                            weather=weathercache[weatherkey];layers=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
                            soil=SoilState([75,80,150],[initial_n*.4,initial_n*.3,initial_n*.3],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)
                            crops=default_crops();plantings=[];events=[]
                            for y,crop in t['history']:
                                sow=date.fromisoformat(t['target_sowing'])if y==year else date(y,5,15)
                                plantings.append(Planting(sow,crop,date(y,10,15)))
                                if y==year:events.extend(Management(date.fromisoformat(day),dose,0)for day,dose in t['target_N_events'])
                                else:events.append(Management(sow,140 if crop=='corn'else 0,0))
                            if model=='existing_mechanistic':engine=DepthSimulator(layers,crops,Processes(),t['latitude'])
                            else:
                                par=selected['parameters'];crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=par['harvest_index'],grain_n_fraction=par['grain_n_fraction'])
                                processes=Processes(maize_n_dilution_a=.034)
                                if model=='corrected_physical_no_organic_N':
                                    processes=replace(processes,residue_decay_per_day_20C=0,humus_decay_per_day_20C=0);crops['soybeans']=replace(crops['soybeans'],fixation_max_n_kg_ha_day=0)
                                if model=='corrected_coupled_no_residue_decay':processes=replace(processes,residue_decay_per_day_20C=0)
                                engine=PhysicalCandidate(layers,crops,processes,t['latitude'],options=selected['options'])
                            result=engine.run(weather,soil,plantings,events,keep_daily=False)
                            harvest=[h for h in result['harvests']if h['planted'].startswith(str(year))]
                            if len(harvest)!=1:raise ValueError('Expected one target maize harvest')
                            cache[key]={'yield':harvest[0]['grain_dry_kg_ha'],'max_balance_error':max(abs(v)for v in result['whole_run_balance_error'].values())}
                        estimates.append(cache[key]['yield'])
                    values[side]=float(np.mean(estimates))
                predictions.append(values['candidate']-values['reference'])
            outputrow[model]=float(np.mean(predictions));outputrow[model+'_scenario_min']=min(predictions);outputrow[model+'_scenario_max']=max(predictions)
        raw.append(outputrow)
        if i%20==0:print('rotation pair',i,'cached trajectories',len(cache),flush=True)
    scored=pd.DataFrame(raw);scored.to_csv(out/'physical_predictions_frozen.csv',index=False)
    allpairs=pd.DataFrame(accepted).drop(columns='trajectories');folds=[];blocks=[]
    for fold in sourcefold:
        train=allpairs[allpairs.farm_id.isin(fold['fit_farms'])];cal=allpairs[allpairs.farm_id==fold['calibration_farm']];test=scored[scored.farm_id.isin(fold['test_farms'])].copy()
        if test.empty:continue
        if train.empty:raise ValueError('No eligible training pairs in fixed farm fold')
        test['family_history_baseline']=baseline(train,test,'family');test['fold']=fold['fold']
        # Group max conformal cannot supply finite 90% radius from one calibration farm.
        test['nominal90_interval_unbounded']=True;blocks.append(test)
        folds.append({'fold':fold['fold'],'fit_farms':sorted(train.farm_id.unique()),'calibration_farms':sorted(cal.farm_id.unique()),'test_farms':sorted(test.farm_id.unique()),'pairs':len(test)})
    scored=pd.concat(blocks);scored.to_csv(out/'paired_predictions.csv',index=False)
    scores={name:metrics(scored,name)for name in protocol['models']};farm_scores={farm:{name:metrics(g,name)for name in protocol['models']}for farm,g in scored.groupby('farm_id')}
    intervals={name:{'scenario_range_coverage':float(((scored.observed_difference>=scored[name+'_scenario_min'])&(scored.observed_difference<=scored[name+'_scenario_max'])).mean()),
        'mean_scenario_range_width':float((scored[name+'_scenario_max']-scored[name+'_scenario_min']).mean()),'is_calibrated_interval':False}for name in protocol['models'][1:]}
    result={'status':'conditional_scenario_models_scored_against_real_paired_outcomes','protocol':protocol,'eligible_pairs':len(scored),'eligible_farms':int(scored.farm_id.nunique()),
        'excluded_pairs':dict(excluded),'folds':folds,'models':scores,'by_farm':farm_scores,'uncertainty':intervals,
        'nominal90_group_intervals':{'finite':False,'coverage':'vacuous if unbounded','decision_usefulness':0,'reason':'At most one independent calibration farm per original fold; geography exchangeability unverified'},
        'coupling_improves_contrast_magnitude_rmse':scores['corrected_coupled']['farm_macro_rmse_kg_dry_ha']<scores['corrected_physical_no_organic_N']['farm_macro_rmse_kg_dry_ha'],
        'coupled_beats_history_baseline':scores['corrected_coupled']['farm_macro_rmse_kg_dry_ha']<scores['family_history_baseline']['farm_macro_rmse_kg_dry_ha'],
        'maximum_conservation_error':max(v['max_balance_error']for v in cache.values()),'unique_trajectory_runs':len(cache),'elapsed_seconds':perf_counter()-starttime,
        'weather_source_sha256':sources,'recommendation':None,'global_or_causal_accuracy_established':False,
        'limits':['Real paired outcomes are used only for scoring and training the declared family baseline. Missing matched weather/soil/previous management prevents mechanistic attribution.',
        'Strict eligibility changes the denominator; these scores cannot be compared directly to the prior 399-pair headline.',
        'Coupled model combines transferred LIRF parameters and assumed multi-year states; empirical moisture improvement at LIRF is not proof of transfer.',
        'Scenario spread is initialization/forcing sensitivity, not a probability or calibrated confidence interval.']}
    write_json(out/'report.json',result)
    from .evaluation import rotation_decision_audit
    rotation_decision_audit(out)
    return result
