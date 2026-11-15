"""Explicit feature whitelist, temporal supports and missingness for paired trials."""
from pathlib import Path
from datetime import date
import json
import numpy as np
import pandas as pd
from ..io import write_json


def mean_or_missing(values):
    return float(np.mean(values)) if values and all(v is not None and np.isfinite(v)for v in values) else np.nan


def prior_state_features(run, target_year):
    """No current target harvest/yield/tissue can enter this vector."""
    state=run['pretarget_state'];values={}
    for i,v in enumerate(state['water_mm']):values['water_'+str(i)]=v
    for i,v in enumerate(state['mineral_n_kg_ha']):values['mineral_N_'+str(i)]=v
    values.update(residue_C=sum(state['residue_c_kg_ha'])+state['surface_residue_c_kg_ha'],
                  residue_N=sum(state['residue_n_kg_ha'])+state['surface_residue_n_kg_ha'],
                  humus_C=sum(state['humus_c_kg_ha']),humus_N=sum(state['humus_n_kg_ha']))
    previous=[h for h in run['harvests']if h['harvested']<f'{target_year}-01-01']
    h=max(previous,key=lambda h:h['harvested'])if previous else {}
    for key in ['biomass_dry_kg_ha','root_dry','plant_total_N','peak_LAI','mean_water_stress','mean_N_growth_factor','maturity_fraction']:
        values['previous_'+key]=h.get(key,np.nan)
    plant=state.get('standing_plant')
    values['standing_biomass']=plant['biomass_dry_kg_ha']if plant else 0.
    values['standing_N']=plant['n_kg_ha']if plant else 0.
    return values


def record_features(record, soil, weather):
    year=record['year'];history=[s for s in record['timeline']if s['year']<year]
    values={};groups={}
    def add(group,name,value):values[name]=value;groups[name]=group
    add('history','prior_soy',float(history[-1]['crop']=='soybeans')if history else np.nan)
    for lag in (1,2,3):
        add('history',f'soy_lag{lag}',float(history[-lag]['crop']=='soybeans')if len(history)>=lag else np.nan)
    add('history','years_of_known_history',len(history))
    add('history','soy_history_count',sum(s['crop']=='soybeans'for s in history))
    current=record['timeline'][-1]
    add('management','target_reported_N',current['fertilizer_total_if_recorded_complete'])
    add('management','previous_reported_N',history[-1]['fertilizer_total_if_recorded_complete']if history else None)
    add('management','planting_doy',date.fromisoformat(current['planting_date']).timetuple().tm_yday if current['planting_date']else None)
    # Dated property observations only, not future/undated soil values from old tables.
    for field in ('SOIL01','SOIL13','SOIL14','SOIL31','SOIL32'):
        eligible=[r for r in soil if r['sample_date'] and r['sample_date']<f'{year}-01-01' and r.get('depth_cm')==[0.,30.] and r['values'].get(field)is not None]
        latest=max(eligible,key=lambda r:r['sample_date'])if eligible else None
        add('soil_properties','dated_0_30_'+field,latest['values'][field]if latest else None)
    add('weather','latitude',record['latitude']);add('weather','longitude',record['longitude'])
    for y,label in [(year,'target'),(year-1,'prior')]:
        subset=weather[(weather.date>=f'{y}-04-01')&(weather.date<=f'{y}-09-30')]
        if len(subset)!=183:raise ValueError('Incomplete fixed-season weather support')
        add('weather',label+'_rain',float(subset.rain_mm.sum()))
        add('weather',label+'_solar',float(subset.solar_MJ_m2.sum()))
        add('weather',label+'_GDD8',float(np.maximum(0,(subset.tmin_C+subset.tmax_C)/2-8).sum()))
        add('weather',label+'_hot35_days',float((subset.tmax_C>35).sum()))
    return values,groups


def make_features(root, states_dir, output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False);states=Path(states_dir)
    base=root/'reports/rotation-recovery/reproduced-final'
    records=json.loads((base/'input-audit/records.json').read_text());soils=json.loads((base/'input-audit/soil_observations.json').read_text())
    fixed=pd.read_csv(states/'paired_predictions.csv')
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    sim=json.loads((states/'scheduled_control_trajectories.json').read_text())
    constrained=json.loads((states/'retro_sd50_trajectories.json').read_text())
    weather={site:pd.read_csv(root/'reports/rotation-recovery/weather-exact'/(site+'.csv'))for site in fixed.site_id.unique()}
    values={};groups={};state_groups={};metadata={}
    for rid in ids:
        rec=records[rid];v,g=record_features(rec,soils[rec['site_id']+':'+rec['plot_id']],weather[rec['site_id']])
        for prefix,raw in [('sim_',sim),('constrained_',constrained)]:
            parts=[prior_state_features(r,rec['year'])for r in raw[rid]]
            for key in parts[0]:
                value=mean_or_missing([p[key]for p in parts]);name=prefix+key
                if key.startswith('water_'):group='soil_water_state'
                elif key.startswith('mineral_N'):group='mineral_N_state'
                elif key.startswith(('residue_','humus_')):group='residue_state'
                else:group='physiology_state'
                v[name]=value;g[name]=group if prefix=='sim_' else 'retrospective_observed_state'
        values[rid]=v;groups.update(g)
    rows=[];feature_groups={}
    for pair in fixed.to_dict('records'):
        sides={side:{key:mean_or_missing([values[rid][key]for rid in pair[col].split(';')])for key in groups}
               for side,col in [('reference','reference_records'),('candidate','candidate_records')]}
        row={k:pair[k]for k in ['pair_id','farm_id','site_id','year','fold','observed_difference']}
        for key,group in groups.items():
            for role in ['mean','difference']:
                name=role+'_'+key;a=sides['candidate'][key];b=sides['reference'][key]
                row[name]=(a+b)/2 if role=='mean'else a-b;feature_groups[name]=group
        # An explicit weather-by-history hypothesis, not an arbitrary yield credit.
        row['previous_soy_x_rain']=row['difference_prior_soy']*row['mean_target_rain'];feature_groups['previous_soy_x_rain']='weather'
        row['previous_soy_x_target_N']=row['difference_prior_soy']*row['mean_target_reported_N'];feature_groups['previous_soy_x_target_N']='management'
        rows.append(row)
    frame=pd.DataFrame(rows);frame.to_csv(out/'features.csv',index=False)
    for name,group in feature_groups.items():
        metadata[name]={'group':group,'role':'retrospective_reconstruction_feature',
            'max_physical_time':'Jan1 target year for simulated state; Sep30 for weather; actual sowing/N-event date for management',
            'historic_availability':None,'operational_preplant_eligible':False,
            'outcome_used':False,'uncertainty':'Scenario for physical state; measurement/management completeness uncertain where recorded; missing retained'}
    write_json(out/'feature_manifest.json',metadata)
    write_json(out/'report.json',{'pairs':len(frame),'features':len(feature_groups),'groups':{g:sum(v==g for v in feature_groups.values())for g in sorted(set(feature_groups.values()))},
        'wholly_missing_features':[k for k in feature_groups if frame[k].isna().all()],
        'target_yield_or_tissue_as_input':False,'simulated_yields_used_as_training_labels':False,
        'historical_operational_preplant_score':None,'forecast_boundary':'None of the archived trial feature records establishes historical report availability. All numerical scores are labeled retrospective reconstruction.',
        'state_time':'Jan1 of target year, including a standing crop when harvest crosses a year boundary.',
        'soil_property_rule':'Only exact 0–30cm dated pre-Jan1 properties admitted. No incompatible-depth, future or undated mapping.'})
    return frame,feature_groups
