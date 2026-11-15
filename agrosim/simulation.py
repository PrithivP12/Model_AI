"""Common-scenario counterfactuals with persistent state and explicit objectives."""
from dataclasses import replace
from datetime import date, timedelta
from itertools import product
from collections import Counter
from copy import deepcopy
import math
import numpy as np
from .processes import WeatherDay, Planting, Management, Processes, default_crops, SoilState
from .soil import HorizonLayer, DepthSimulator, compile_horizons
from .data.power import read_power
from .data.quality import eligible
from .io import number, resolve, sha256


def prepare(config, base):
    location=config['location'];lat=number(location['latitude'],'latitude',-90,90)
    lon=number(location['longitude'],'longitude',-180,180)
    decision=date.fromisoformat(config['decision_date'])
    area=number(config['area_ha'],'area_ha',.00001,1e7)
    soil=config['soil']
    if not soil.get('provenance'):raise ValueError('Soil provenance is required')
    if soil.get('available_at')and date.fromisoformat(soil['available_at'][:10])>decision:
        raise ValueError('Soil measurements unavailable at decision date')
    if 'horizons'in soil:layers,state=compile_horizons(soil['horizons'])
    else:
        layers=[HorizonLayer(**h)for h in soil['layers']];state=SoilState(**soil['initial_state'])
    crops=default_crops()
    for name,changes in config.get('crop_parameters',{}).items():
        if name not in crops:raise ValueError(f'Unsupported crop {name}; do not invent a calibrated profile')
        crops[name]=replace(crops[name],**changes)
    engine=DepthSimulator(layers,crops,Processes(**config.get('processes',{})),lat)
    if layers[0].root_penetrability<=0:
        raise ValueError('Top horizon must permit crop establishment')
    state.validate(layers)
    start=date.fromisoformat(config['start']);end=date.fromisoformat(config['end'])
    if not 0<=(end-start).days<=366*10:raise ValueError('Simulation must span at most ten years')
    task=config.get('prediction_horizon')
    if task not in ['preplanting','historical_reconstruction']:raise ValueError('Explicit prediction_horizon required')
    if task=='preplanting'and start<decision:raise ValueError('Preplanting simulation starts before decision')
    if not 1<=len(config['slots'])<=10:raise ValueError('One to ten explicitly dated crop slots required')
    scenarios=[];provenance=[]
    for item in config['weather_scenarios']:
        frame,obs,meta=read_power(resolve(base,item['path']),location['site_id'],lat,lon)
        if task=='preplanting'and not all(eligible(r,decision.isoformat())for r in obs.to_dict('records')):
            raise ValueError('Post-decision or unknown-availability weather cannot drive preplanting scenarios')
        data={r['date']:r for r in frame.to_dict('records')};source_year=int(item['source_start_year'])
        days=[];derived=0
        for i in range((end-start).days+1):
            t=start+timedelta(days=i);y=source_year+t.year-start.year
            key=f'{y:04d}-{t.month:02d}-{t.day:02d}';row=data.get(key)
            if row is None and t.month==2 and t.day==29:
                before=data.get(f'{y}-02-28');after=data.get(f'{y}-03-01')
                if before and after:
                    row={k:(before[k]+after[k])/2 for k in ['rain_mm','tmin_C','tmax_C','solar_MJ_m2']};derived+=1
            if row is None:raise ValueError(f'Missing historical scenario day {key}')
            stress=config.get('stress_test',{})
            rain_scale=number(stress.get('rain_multiplier',1),'rain multiplier',0,5)
            heat=number(stress.get('temperature_delta_C',0),'temperature perturbation',-10,15)
            days.append(WeatherDay(t,row['rain_mm']*rain_scale,row['tmin_C']+heat,row['tmax_C']+heat,row['solar_MJ_m2']))
        scenarios.append((item['id'],days))
        provenance.append({**meta,'scenario_id':item['id'],'source_start_year':source_year,
                           'mapping':'month-day to target year; Feb 29 missing in source is adjacent-day mean',
                           'derived_leap_days':derived,'interpretation':'historical climate scenario, not weather forecast'})
        provenance[-1]['stress_test_transform']=config.get('stress_test')
    if not scenarios or len(scenarios)>20 or len({s[0]for s in scenarios})!=len(scenarios):
        raise ValueError('One to twenty uniquely named climate scenarios required')
    return engine,state,scenarios,provenance,area


def schedule(sequence, config, crops, area):
    if len(sequence)!=len(config['slots']):raise ValueError('Sequence length must match dated crop slots')
    plantings=[];events=[];seed=Counter();fertilizer=0.;water=0.
    consecutive=0;previous=None
    for name,slot in zip(sequence,config['slots']):
        if name not in crops or name not in slot['allowed_crops']:raise ValueError('Unsupported or disallowed crop')
        consecutive=consecutive+1 if name==previous else 1;previous=name
        if consecutive>config.get('constraints',{}).get('max_consecutive',10):raise ValueError('Maximum consecutive crop constraint')
        management={**config['management_by_crop'][name],**slot.get('management_overrides',{}).get(name,{})}
        sow=date.fromisoformat(slot['planting_date']);harvest=date.fromisoformat(slot['harvest_deadline'])
        permitted=management.get('allowed_planting_months')
        if permitted and sow.month not in permitted:raise ValueError('Planting date violates configured regional calendar')
        n=number(management['fertilizer_n_kg_ha'],'fertilizer',0)
        irrigation=number(management.get('irrigation_mm',0),'irrigation',0)
        efficiency=number(config.get('irrigation_efficiency',1),'irrigation efficiency',.01,1)
        fertilizer+=n*area;water+=irrigation*area*10/efficiency
        seed[name]+=crops[name].seed_dry_kg_ha*area
        plantings.append(Planting(sow,name,harvest,management.get('residue_removal_fraction',0),management.get('harvest_mode','grain')))
        events.append(Management(sow,n,irrigation))
    limits=config.get('constraints',{})
    for key,value in [('fertilizer_n_kg',fertilizer),('irrigation_m3',water)]:
        if key in limits and value>number(limits[key],key,0)+1e-8:raise ValueError(f'Insufficient {key}')
    for name,required in seed.items():
        stock=config.get('seeds_available_dry_kg',{}).get(name)
        if stock is not None and required>number(stock,'seed stock',0)+1e-8:raise ValueError(f'Insufficient seeds: {name}')
    return plantings,events,{'seed_dry_kg':dict(seed),'fertilizer_n_kg':fertilizer,'irrigation_m3':water}


def economics(result, sequence, config, area, resources):
    assumptions=config.get('economics')
    if assumptions is None:return None
    if not assumptions.get('currency')or not assumptions.get('price_date'):
        raise ValueError('Currency and price-scenario date required')
    revenue=0.;cost=0.
    for harvest in result['harvests']:
        name=harvest['crop'];price=number(assumptions['price_per_kg_dry'][name],f'{name} price',0)
        revenue+=harvest['grain_dry_kg_ha']*area*price
    for name in sequence:cost+=number(assumptions['crop_cost_per_ha'][name],f'{name} cost',0)*area
    cost+=resources['fertilizer_n_kg']*number(assumptions['fertilizer_cost_per_kg_n'],'N cost',0)
    cost+=resources['irrigation_m3']*number(assumptions['irrigation_cost_per_m3'],'water cost',0)
    return {'revenue':revenue,'specified_costs':cost,'net_return':revenue-cost,
            'currency':assumptions['currency'],'status':'explicit_price_and_cost_scenario; not a market forecast'}


def evaluate(config, base, sequences, daily=False):
    engine,state,scenarios,provenance,area=prepare(config,base)
    if len(sequences)*len(scenarios)>2000:raise ValueError('Run budget exceeds 2000 simulations')
    runs=[];rejected=[]
    for sequence in sequences:
        try:plantings,events,resources=schedule(sequence,config,engine.crops,area)
        except ValueError as exc:
            rejected.append({'sequence':list(sequence),'reason':str(exc)});continue
        for scenario,weather in scenarios:
            result=engine.run(weather,state,plantings,events,keep_daily=True)
            harvest_dates={h['harvested']for h in result['harvests']}
            state_fields=['date','soil_water_mm','snow_mm','soil_mineral_n_kg_ha',
                          'soil_organic_c_kg_ha','residue_c_kg_ha']
            result['harvest_soil_states']=[{k:r[k]for k in state_fields}for r in result['daily']if r['date']in harvest_dates]
            result['calendar_year_end_states']=[{k:r[k]for k in state_fields}for r in result['daily']if r['date'].endswith('12-31')]
            if not daily:result['daily']=[]
            cost=economics(result,sequence,config,area,resources)
            runs.append({'sequence':list(sequence),'sequence_id':'>'.join(sequence),'scenario_id':scenario,
                         'resources':resources,'economics':cost,'simulation':result})
    if not runs:raise ValueError('No feasible complete rotation remains')
    summaries=[]
    for seq in sorted({r['sequence_id']for r in runs}):
        group=[r for r in runs if r['sequence_id']==seq]
        values=[r['economics']['net_return']for r in group]if group[0]['economics']else None
        summaries.append({'sequence_id':seq,'sequence':group[0]['sequence'],'scenario_count':len(group),
            'irrigation_m3':group[0]['resources']['irrigation_m3'],
            'mean_final_soil_organic_c_kg_ha':float(np.mean([sum(r['simulation']['final_state']['humus_c_kg_ha'])for r in group])),
            'mean_leached_n_kg_ha':float(np.mean([r['simulation']['totals']['leached_n_kg_ha']for r in group])),
            'mean_net_return':float(np.mean(values))if values else None,
            'minimum_scenario_net_return':float(min(values))if values else None})
    return {'software':'AgroSim Global 0.1 research','status':'uncalibrated_counterfactual_scenarios',
        'prediction_horizon':config['prediction_horizon'],'location':config['location'],'area_ha':area,
        'recommendation':None,'validated_globally':False,'scenario_quantiles_are_prediction_intervals':False,
        'regional_calibration':{'status':'unsupported_for_field_recommendations','calibrated_crops':[],
            'configured_crop_parameters_are_validated':False},
        'irrigation_accounting':'Soil balance uses field-applied net mm; supply constraint uses gross m3 after configured efficiency. Off-field losses are outside the soil ledger.',
        'soil_provenance':config['soil']['provenance'],'weather_provenance':provenance,
        'summaries':summaries,'rejected_sequences':rejected,'runs':runs,
        'unsupported_effects':{'disease':None,'pest':None,'predator':None,'pollination':None,'genotype':None},
        'limitations':['Crop parameters, horizon root penetrability and long-term carbon are not field calibrated.',
            'Future scenarios are historical replays with unknown future probability; no calibrated confidence percentage.',
            'Crop variety, pests, disease and pollination are not inferred from generic climate.',
            'Price and cost scenarios are supplied assumptions. Unlike-crop tonnes are never summed into an optimization objective.']}


def compare(config, base):
    output=evaluate(config,base,config['sequences'],daily=config.get('keep_daily',False))
    original=config['sequences'][0];baseline='>'.join(original);contrasts=[]
    for scenario in [p['scenario_id']for p in output['weather_provenance']]:
        a=next((r for r in output['runs']if r['sequence_id']==baseline and r['scenario_id']==scenario),None)
        if a is None:raise ValueError('Baseline sequence is infeasible')
        for b in output['runs']:
            if b['scenario_id']!=scenario or b['sequence_id']==baseline:continue
            aa=a['simulation'];bb=b['simulation'];grain=[]
            for x,y in zip(aa['harvests'],bb['harvests']):
                grain.append({'baseline_crop':x['crop'],'alternative_crop':y['crop'],'date':y['harvested'],
                    'dry_yield_difference_kg_ha':y['grain_dry_kg_ha']-x['grain_dry_kg_ha']if x['crop']==y['crop']else None})
            contrasts.append({'baseline':baseline,'alternative':b['sequence_id'],'scenario_id':scenario,
                'harvest_contrasts':grain,'final_water_difference_mm':bb['final_inventory']['water']-aa['final_inventory']['water'],
                'final_total_n_difference_kg_ha':bb['final_inventory']['nitrogen']-aa['final_inventory']['nitrogen'],
                'net_return_difference':b['economics']['net_return']-a['economics']['net_return']if a['economics']else None,
                'explanation':'Daily growth, uptake, fixation, decomposition, drainage and residue carryover differ under the common forcing. Biological effects are not computed.',
                'causal_effect_established':False})
    output['contrasts']=contrasts
    return output


def optimize(config, base):
    count=math.prod(len(s['allowed_crops'])for s in config['slots'])
    if count>config.get('max_candidates',256):raise ValueError('Exhaustive search exceeds configured candidate budget; no silent truncation')
    sequences=list(product(*(s['allowed_crops']for s in config['slots'])))
    output=evaluate(config,base,sequences)
    rows=output['summaries']
    if any(r['mean_net_return']is None for r in rows):raise ValueError('Comparable economics required for this optimizer')
    # Maximize worst historical-scenario net return; minimize irrigation and N leaching.
    for row in rows:
        own=(row['minimum_scenario_net_return'],-row['irrigation_m3'],-row['mean_leached_n_kg_ha'])
        row['pareto_optimal']=not any(all(a>=b for a,b in zip(
            (other['minimum_scenario_net_return'],-other['irrigation_m3'],-other['mean_leached_n_kg_ha']),own))
            and any(a>b for a,b in zip((other['minimum_scenario_net_return'],-other['irrigation_m3'],-other['mean_leached_n_kg_ha']),own))for other in rows)
    output['pareto_sequences']=[r['sequence_id']for r in rows if r['pareto_optimal']]
    output['optimization']={'algorithm':'bounded exhaustive search','candidate_count':count,
        'objectives':['maximize minimum scenario net return','minimize irrigation m3','minimize mean leached N kg/ha'],
        'proof_of_real_world_benefit':False}
    return output
