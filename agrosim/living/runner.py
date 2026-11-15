"""Finite, deterministic nonvisual demonstration and sensitivity design."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from itertools import combinations, product
from pathlib import Path
import csv
import gzip
import hashlib
import json
import math
import sys

from ..io import load_config, resolve, sha256, write_json
from ..processes import SoilState, WeatherDay
from ..ecosystem import EcosystemEngine, EcosystemState, decode_plant
from ..evaluator.audit import EvaluationKernel, reconcile
from ..evaluator.engine import normalize, digest, BOUNDARY
from ..simulation import prepare, schedule
from ..ecology_v2.soil_biology import Parameters, Decomposers, partition_humus
from ..crop_catalog.registry import CropRegistry
from .kernel import LivingKernel
from .registry import registry, validate_config, INITIAL_DOMAINS

STOCKS = ['soil_water_mm','soil_mineral_n_kg_ha','soil_organic_c_kg_ha','residue_c_kg_ha',
          'plant_biomass_dry_kg_ha','functional_decomposer_C_kg_ha','functional_decomposer_N_kg_ha']
FLOWS = ['grain_dry_kg_ha','growth_dry_kg_ha','transpiration_mm','plant_uptake_n_kg_ha','respired_c_kg_ha',
         'mineralized_n_kg_ha','immobilized_n_kg_ha','weed_water_consumed_mm','weed_uptake_N_kg_ha',
         'weed_assimilated_C_kg_ha','herbivore_consumed_C_kg_ha','predator_consumed_C_kg_ha',
         'detritivore_consumed_C_kg_ha','pathogen_necrotic_C_kg_ha','residue_consumed_C_kg_ha']


def make_engine(physical, soil, plants, config, stable=False):
    options = {'root_beta_per_m':4, 'organ_model':not stable, 'temperature_plateau':False,
               'observed_harvest_sowing_dates':[str(p.date) for p in plants], 'harvest_calendar_role':'planned'}
    if stable:
        return EcosystemEngine(EvaluationKernel(physical.layers,physical.crops,physical.processes,physical.latitude,options=options)), deepcopy(soil)
    p = Parameters(**config['microbes']['parameters'])
    soil, microbes = partition_humus(soil,config['microbes']['initial_humus_C_fraction'],p)
    # All new biomass is an explicit initial stock, NOT an import during simulation.
    # Reclassify existing humus to hold total initial C/N equal to stable V1.
    for g in validate_config(config):
        if g.role == 'pathogen': continue
        c,n = g.initial['C'],g.initial['N']
        if soil.humus_c_kg_ha[0] < c or soil.humus_n_kg_ha[0] < n:
            raise ValueError('Initial living-pool partition exceeds declared humus reserve')
        soil.humus_c_kg_ha[0] -= c; soil.humus_n_kg_ha[0] -= n
    options['soil_biology'] = asdict(p)
    kernel=LivingKernel(physical.layers,physical.crops,physical.processes,physical.latitude,
                        options=options,decomposers=microbes,living_config=config)
    return EcosystemEngine(kernel),soil


def capture(engine,state):
    k=engine.physical
    return {'ecosystem':asdict(state), 'decomposers':asdict(k.decomposers) if isinstance(k,LivingKernel) else None,
            'guild_state':deepcopy(k.guild_state) if isinstance(k,LivingKernel) else None,
            'diagnostics':{name:deepcopy(getattr(k,name)) for name in ['growth_stats','lifecycle','stage_snapshots','growth_budgets']}}


def restore(physical, original_soil, plants, config, checkpoint, stable=False):
    engine,_=make_engine(physical,original_soil,plants,config,stable)
    state=EcosystemState(**checkpoint['ecosystem']); k=engine.physical
    if state.engine_identity != engine.identity: raise ValueError('Checkpoint configuration or code identity mismatch')
    if not stable:
        k.decomposers=Decomposers(**checkpoint['decomposers']); k.decomposers.validate(k.layers,k.bio_parameters.microbial_cn)
        k.guild_state=deepcopy(checkpoint['guild_state']); k.validate_guild_state()
    for name,value in checkpoint['diagnostics'].items(): setattr(k,name,deepcopy(value))
    if capture(engine,state) != checkpoint: raise ArithmeticError('Checkpoint state did not restore exactly')
    return engine,state


def summary(state,engine,harvests):
    s=state.soil; f=state.cumulative_fluxes; k=engine.physical
    return {'total_harvest_dry_kg_ha':sum(h['grain_dry_kg_ha'] for h in harvests),
        'crop_growth_dry_kg_ha':f.get('growth_dry_kg_ha',0),
        'final_water_mm':sum(s['water_mm']), 'final_mineral_N_kg_ha':sum(s['mineral_n_kg_ha']),
        'final_humus_C_kg_ha':sum(s['humus_c_kg_ha']),
        'final_residue_C_kg_ha':sum(s['residue_c_kg_ha'])+s['surface_residue_c_kg_ha'],
        'crop_uptake_N_kg_ha':f.get('plant_uptake_n_kg_ha',0)-f.get('weed_uptake_N_kg_ha',0),
        **{name:f.get(name,0.) for name in FLOWS},
        **({f'{identity}_{name}':v for identity,stock in k.guild_state.items() for name,v in stock.items()} if isinstance(k,LivingKernel) else {}),
        'final_microbe_C_kg_ha':sum(k.decomposers.carbon_kg_ha) if isinstance(k,LivingKernel) else None}


def run_case(physical, soil, weather, plants, events, config, directory, *, stable=False):
    directory.mkdir(parents=True,exist_ok=False)
    engine,initial_soil=make_engine(physical,soil,plants,config,stable)
    state=engine.initialize(initial_soil,provenance='Declared hypothetical partition of supplied initial C/N; not a literal humus-to-animal process')
    initial=capture(engine,state); harvests=[]; daily=[]; years=[]; max_error=dict.fromkeys(BOUNDARY,0.)
    # Annual fresh-object checkpoint restoration; additionally roundtrip at every planting/harvest.
    stops={weather[-1].date} | {date(y,12,31) for y in range(weather[0].date.year,weather[-1].date.year+1)}
    stops |= {p.date-timedelta(days=1) for p in plants} | {p.harvest_deadline for p in plants}
    cursor=0; count=0; process_totals={}; transaction_count=0
    with (directory/'transfers.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as stream:
            for stop in sorted(d for d in stops if weather[0].date <= d <= weather[-1].date):
                start=cursor
                while cursor < len(weather) and weather[cursor].date <= stop: cursor+=1
                result=engine.run(weather[start:cursor],state,plants,events,keep_daily=True)
                state=result['checkpoint']; harvests.extend(result['harvests']); daily.extend(result['daily'])
                for resource,v in result['max_daily_balance_error'].items(): max_error[resource]=max(max_error[resource],v)
                if isinstance(engine.physical,LivingKernel):
                    for t in engine.physical.records:
                        stream.write((json.dumps(t,sort_keys=True,allow_nan=False)+'\n').encode()); transaction_count+=1
                        key=t['process']+':'+t['source']+'->'+t['destination']
                        target=process_totals.setdefault(key,{'C':0.,'N':0.})
                        target['C']+=t['material_kg_ha']['carbon']; target['N']+=t['material_kg_ha']['nitrogen']
                    engine.physical.records.clear()
                payload=capture(engine,state)
                path=directory/f'checkpoint-{count:02d}.json'; write_json(path,payload)
                engine,state=restore(physical,soil,plants,config,json.loads(path.read_text()),stable)
                if stop.month == 12 and stop.day == 31:
                    years.append({'year':stop.year,'state':capture(engine,state),'summary':summary(state,engine,harvests)})
                count+=1
    if state.plant is not None or len(harvests) != len(plants): raise ArithmeticError('Incomplete crop lifecycle')
    fields=['date','crop']+sorted({k for r in daily for k,v in r.items() if isinstance(v,(int,float))})
    with (directory/'daily.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); writer.writeheader(); writer.writerows(daily)
    final=engine.physical.inventories(SoilState(**state.soil),decode_plant(state.plant))
    balances={r:{'initial':state.initial_inventory[r],'final':final[r],
        'inputs':state.cumulative_fluxes[r+'_inputs'],'outputs':state.cumulative_fluxes[r+'_outputs'],
        'error':reconcile(final[r]-state.initial_inventory[r]-state.cumulative_fluxes[r+'_inputs']+state.cumulative_fluxes[r+'_outputs'],r)} for r in BOUNDARY}
    report={'status':'stable_V1' if stable else 'experimental_hypothetical_ecosystem',
        'summary':summary(state,engine,harvests),'initial_state':initial,'final_state':capture(engine,state),
        'harvests':harvests,'annual_states':years,'conservation':balances,'maximum_daily_error':max_error,
        'checkpoints_restored':count,'daily_rows':len(daily),'transfer_count':transaction_count,
        'interaction_totals':process_totals,'cumulative_fluxes':state.cumulative_fluxes,
        'independent_validation':False,'farmer_recommendation':None,
        'unsupported':{'pollinator_density':None,'pollination_yield_effect':None,'local_pest_count':None,
                       'microbial_taxon_abundance':None,'field_accuracy':None,'probabilistic_confidence_interval':None}}
    write_json(directory/'state.json',report)
    return report,daily


def effect(intervention, control, daily_a, daily_b):
    def delta(a,b):
        return {'absolute':a-b,'percent':100*(a-b)/b if abs(b)>1e-10 else None}
    a=intervention['summary']; b=control['summary']
    outcomes={k:delta(a[k],b[k]) for k in sorted(a.keys() & b.keys()) if isinstance(a[k],(int,float)) and isinstance(b[k],(int,float))}
    temporal={}
    names=STOCKS+FLOWS+sorted({k for row in daily_a+daily_b for k in row if k.startswith(('guild_stock_','pathogen_pressure_'))})
    if [r['date'] for r in daily_a] != [r['date'] for r in daily_b]: raise ValueError('Effects require identical dated cohorts')
    for name in names:
        values=[(ra['date'],ra.get(name,0.)-rb.get(name,0.)) for ra,rb in zip(daily_a,daily_b)]
        first=next((d for d,v in values if abs(v)>1e-8),None)
        peak=max(values,key=lambda x:abs(x[1]))
        annual=[]
        for year in sorted({d[:4] for d,_ in values}):
            rows=[v for d,v in values if d.startswith(year)]
            annual.append({'year':year,'endpoint_difference':rows[-1],
                           'cumulative_flux_difference':sum(rows) if name in FLOWS else None})
        temporal[name]={'first_effect_date':first,'peak_daily_difference':peak[1],'peak_date':peak[0],
                        'cumulative_flux_difference':sum(v for _,v in values) if name in FLOWS else None,'annual':annual}
    ai,bi=intervention['initial_state'],control['initial_state']
    return {'outcomes':outcomes,'temporal':temporal,'causal_scope':'within declared model and matched scenario, not field causal inference',
            'matched_weather':True,'matched_supplied_soil_configuration':True,
            'actual_initial_soil_pools_equal':ai['ecosystem']['soil']==bi['ecosystem']['soil'],
            'initial_guild_stocks_equal':ai['guild_state']==bi['guild_state'],
            'initial_microbe_stocks_equal':ai['decomposers']==bi['decomposers'],
            'total_initial_inventory_difference':{k:ai['ecosystem']['initial_inventory'][k]-bi['ecosystem']['initial_inventory'][k] for k in BOUNDARY}}


def perturb(config, coordinate, value):
    c=deepcopy(config); role,name=coordinate.split('.')
    if role=='microbes': c['microbes']['parameters'][name]=value
    else:
        for g in c['guilds']:
            if g['role'] != role: continue
            if name=='initial':
                g['initial']={'pressure':value} if role=='pathogen' else {'C':value,'N':value/g['parameters']['cn']}
            else:
                g['parameters'][name]=value
                if name=='cn': g['initial']['N']=g['initial']['C']/value
    validate_config(c)
    return c


def synthetic_weather(start_year,years):
    begin=date(start_year,1,1); end=date(start_year+years-1,12,31)
    for i in range((end-begin).days+1):
        d=begin+timedelta(days=i); seasonal=math.sin(2*math.pi*(d.timetuple().tm_yday-110)/365.25)
        t=12+13*seasonal
        yield WeatherDay(d,8. if i%4==0 else 0.,t-5,t+6,max(3.,14+8*seasonal))


def execute(config_path,output):
    config,base=load_config(config_path); guilds=validate_config(config)
    scenario,scenario_base=load_config(resolve(base,config['scenario_config']))
    if config['mode']=='stable':
        from ..evaluator.engine import write_evaluation
        return write_evaluation(resolve(base,config['scenario_config']),output)
    c,catalog=normalize(scenario); physical,soil,weather,provenance,area=prepare(c,scenario_base)
    directory=Path(output).resolve(); directory.mkdir(parents=True,exist_ok=False)
    write_json(directory/'configuration.json',config); write_json(directory/'scenario_configuration.json',c)
    write_json(directory/'parameter_registry.json',registry()); write_json(directory/'crop_catalog.json',catalog.catalog)
    write_json(directory/'guild_traits.json',{g.identity:g.traits() for g in guilds})
    sources=[]
    for item,source in zip(c['weather_scenarios'],provenance):
        p=resolve(scenario_base,item['path']); name='POWER-'+sha256(p)+'.json'
        (directory/name).write_bytes(p.read_bytes())
        sources.append({**source,'path':item['path'],'raw_asset':name,'physical_support':'daily NASA POWER grid-derived point sample, not plot measurement',
                        'role':'historical climate replay, not a forecast or local biological observation'})
    write_json(directory/'forcing_provenance.json',sources)
    for name,days in weather:
        write_json(directory/f'forcing-{name}.json',[{**asdict(d),'date':str(d.date)} for d in days])
    runs=[]; cache={}; daily_cache={}; pairs=[]; stage_a=[]; contexts=[]; factorial=[]; morris=[]
    def run(label, strategy, days, cfg, *, stable=False, physical_soil=None, events_override=None, custom_c=None):
        local_c=custom_c or c; s=physical_soil or soil
        plants,events,_=schedule(strategy['sequence'],local_c,physical.crops,area)
        if events_override is not None: events=events_override
        key=digest({'strategy':strategy,'weather':[asdict(d)|{'date':str(d.date)} for d in days],
                    'soil':asdict(s),'biology':cfg,'stable':stable,'plants':[asdict(p)|{'date':str(p.date),'harvest_deadline':str(p.harvest_deadline)} for p in plants],
                    'events':[asdict(e)|{'date':str(e.date)} for e in events]})
        if key in cache: return cache[key]
        identifier=f'run-{len(runs):03d}'
        report,daily=run_case(physical,s,days,plants,events,cfg,directory/identifier,stable=stable)
        report.update(id=identifier,label=label,strategy=strategy['id'],sequence=strategy['sequence'],forcing_hash=digest([asdict(d)|{'date':str(d.date)} for d in days]),
                      soil_initialization_hash=digest(asdict(s)),configuration=cfg)
        write_json(directory/identifier/'state.json',report)
        runs.append(report); cache[key]=identifier
        # Keep only scalar trajectories needed by effects; full raw trajectories are on disk.
        daily_cache[identifier]=[{k:v for k,v in row.items() if k in STOCKS+FLOWS+['date'] or k.startswith(('guild_stock_','pathogen_pressure_'))} for row in daily]
        return identifier
    def get(identifier): return runs[int(identifier.split('-')[1])]
    def compare(a,b,label):
        if get(a)['forcing_hash']!=get(b)['forcing_hash'] or get(a)['soil_initialization_hash']!=get(b)['soil_initialization_hash']:
            raise ValueError('Unmatched environmental comparison')
        value={'label':label,'intervention':a,'control':b,**effect(get(a),get(b),daily_cache[a],daily_cache[b])}
        pairs.append(value); return value
    full={}; stable_runs={}
    for wname,days in weather:
        for strategy in c['strategies']:
            full[wname,strategy['id']]=run('full:'+wname,strategy,days,config)
            stable_runs[wname,strategy['id']]=run('stable_V1:'+wname,strategy,days,config,stable=True)
            diff=compare(full[wname,strategy['id']],stable_runs[wname,strategy['id']],'full_vs_stable_V1')
            diff['initialization_caveat']='Total initial C/N/W identical; explicit humus partition and existing organ physiology differ. This aggregate comparison does not isolate biology.'
    wname,days=weather[0]; strategy=c['strategies'][0]; reference=full[wname,strategy['id']]
    for s in c['strategies']:
        for role in ['microbes','weed','herbivore','predator','detritivore','pathogen']:
            off=deepcopy(config); off['enabled'][role]=False
            identifier=run(role+'_off',s,days,off)
            compare(full[wname,s['id']],identifier,role+'_on_minus_off')
    # Organ-physiology and stock-partition control separates these from biology.
    no_biology=deepcopy(config); no_biology['enabled']={k:False for k in config['enabled']}
    no_biology_id=run('all_interactions_off_existing_organs',strategy,days,no_biology)
    compare(reference,no_biology_id,'full_vs_all_interactions_off')
    for a,b in combinations(c['strategies'],2):
        ra,rb=get(full[wname,a['id']]),get(full[wname,b['id']])
        matched=[]
        for ha,hb in zip(ra['harvests'],rb['harvests']):
            matched.append({'year':ha['planted'][:4],'crop_a':ha['crop'],'crop_b':hb['crop'],
                           'same_crop_harvest_difference_kg_ha':ha['grain_dry_kg_ha']-hb['grain_dry_kg_ha'] if ha['crop']==hb['crop'] else None})
        compare(ra['id'],rb['id'],'rotation_states:'+a['id']+'-'+b['id'])['crop_comparisons']=matched
    domain={r['id']:r for r in registry()['parameters']}
    for coordinate in config['design']['response_coordinates']:
        rows=[]
        for level in ['low','reference','high']:
            value=domain[coordinate][level]; cfg=perturb(config,coordinate,value)
            identifier=run('response:'+coordinate+':'+level,strategy,days,cfg)
            rows.append({'level':level,'value':value,'run':identifier,'summary':get(identifier)['summary']})
            compare(identifier,reference,'response:'+coordinate+':'+level)
        stage_a.append({'coordinate':coordinate,'tier':'D','points':rows,
                        'threshold_and_saturation_note':'Three-point finite response, not proof of a monotonic or saturated global response; mechanistic caps tested separately'})
    # Compact stratified environmental stress panel, not a Cartesian product.
    env=[('reference',1.,0.,1.),('dry',.4,0.,1.),('wet',2.,0.,1.),('cool',1.,-4.,1.),
         ('warm',1.,4.,1.),('N_poor',1.,0.,.15),('N_adequate',1.,0.,2.)]
    for label,rain,heat,nscale in env:
        d=[replace(d,rain_mm=d.rain_mm*rain,tmin_C=d.tmin_C+heat,tmax_C=d.tmax_C+heat) for d in days]
        s=deepcopy(soil); s.mineral_n_kg_ha=[v*nscale for v in s.mineral_n_kg_ha]
        cc=deepcopy(c)
        # Nutrient context intentionally changes both initial N and declared fertilizer.
        for m in cc['management_by_crop'].values(): m['fertilizer_n_kg_ha']*=nscale
        cc['constraints']['fertilizer_n_kg']*=max(1,nscale)
        f=run('context:'+label,strategy,d,config,physical_soil=s,custom_c=cc)
        ablations={}
        for role in ['microbes','weed','predator']:
            off=deepcopy(config); off['enabled'][role]=False
            o=run('context:'+label+':'+role+'_off',strategy,d,off,physical_soil=s,custom_c=cc)
            ablations[role]=compare(f,o,'context:'+label+':'+role)
        contexts.append({'id':label,'rain_multiplier':rain,'temperature_delta_C':heat,'mineral_and_fertilizer_N_multiplier':nscale,
                         'run':f,'matched_effects':ablations})
    for weed,herb,pred in product([False,True],repeat=3):
        cfg=deepcopy(config); cfg['enabled'].update(weed=weed,herbivore=herb,predator=pred)
        identifier=run(f'factorial:{weed}:{herb}:{pred}',strategy,days,cfg)
        factorial.append({'weed':weed,'herbivore':herb,'predator':pred,'run':identifier,'summary':get(identifier)['summary']})
    interactions={}
    def cell(w,h,p,target): return next(r['summary'][target] for r in factorial if (r['weed'],r['herbivore'],r['predator'])==(w,h,p))
    for target in ['total_harvest_dry_kg_ha','herbivore_consumed_C_kg_ha','crop_uptake_N_kg_ha','final_mineral_N_kg_ha']:
        interactions[target]={
            'predator_by_herbivore_with_weeds':(cell(True,True,True,target)-cell(True,True,False,target))-(cell(True,False,True,target)-cell(True,False,False,target)),
            'weed_by_herbivore_without_predator':(cell(True,True,False,target)-cell(True,False,False,target))-(cell(False,True,False,target)-cell(False,False,False,target))}
    # Two deterministic Morris elementary-effect paths over a constrained domain.
    coords=config['design']['screen_coordinates']
    for path in range(2):
        current=deepcopy(config)
        # Same coordinate order from opposite corners gives different background
        # conditions. Reversing the order here would retrace the identical edges.
        order=coords
        for coord in coords: current=perturb(current,coord,domain[coord]['low'] if path==0 else domain[coord]['high'])
        previous=run('morris:start:'+str(path),strategy,days,current)
        for coord in order:
            value=domain[coord]['high'] if path==0 else domain[coord]['low']
            current=perturb(current,coord,value); following=run('morris:'+str(path)+':'+coord,strategy,days,current)
            signed_step=1. if path==0 else -1.
            effects={k:(get(following)['summary'][k]-get(previous)['summary'][k])/signed_step for k in ['total_harvest_dry_kg_ha','herbivore_consumed_C_kg_ha','final_mineral_N_kg_ha','crop_uptake_N_kg_ha']}
            morris.append({'path':path,'coordinate':coord,'from':previous,'to':following,'normalized_step':signed_step,'elementary_effects':effects})
            previous=following
    rankings={}
    for target in morris[0]['elementary_effects']:
        entries=[]
        for coord in coords:
            values=[r['elementary_effects'][target] for r in morris if r['coordinate']==coord]
            entries.append({'coordinate':coord,'mean_abs_elementary_effect':sum(abs(v) for v in values)/len(values),
                            'signed_effects':values,'sign_reversal':min(values)<0<max(values)})
        rankings[target]=sorted(entries,key=lambda x:-x['mean_abs_elementary_effect'])
    # Longer synthetic numerical run, not an observed NASA history.
    years=config['design']['stability_years']; long_c=deepcopy(c)
    begin=date.fromisoformat(c['start']).year
    long_c['slots']=[dict(c['slots'][0],planting_date=f'{begin+i}-04-20',harvest_deadline=f'{begin+i}-10-15') for i in range(years)]
    long_c['constraints']={'max_consecutive':10}; long_c['seeds_available_dry_kg']={}
    long_strategy={'id':'synthetic_stability','sequence':(strategy['sequence']*math.ceil(years/3))[:years]}
    long_id=run('synthetic_10_year_stability',long_strategy,list(synthetic_weather(begin,years)),config,custom_c=long_c)
    # Biological control robustness, with no interpretation as a probability.
    robustness=[]
    for role,target in [('predator','herbivore_consumed_C_kg_ha'),('weed','crop_uptake_N_kg_ha'),('microbes','total_harvest_dry_kg_ha')]:
        values=[{'context':x['id'],'effect':x['matched_effects'][role]['outcomes'][target]['absolute']} for x in contexts]
        positive=any(v['effect']>1e-8 for v in values); negative=any(v['effect']< -1e-8 for v in values)
        robustness.append({'process':role,'outcome':target,'values':values,
                           'status':'assumption_sensitive_sign' if positive and negative else 'consistent_direction_in_tested_model_domain' if positive or negative else 'genuine_zero_in_tested_domain',
                           'field_causal_validation':False})
    from .screen import controlled_screen
    screen=controlled_screen(config)
    write_json(directory/'controlled_process_screen.json',screen)
    report={'status':'experimental_functional_demonstration','runs':[{'id':r['id'],'label':r['label'],'strategy':r['strategy'],'summary':r['summary'],
        'conservation':r['conservation'],'maximum_daily_error':r['maximum_daily_error'],'checkpoints_restored':r['checkpoints_restored'],
        'daily_rows':r['daily_rows'],'transfer_count':r['transfer_count']} for r in runs],
        'comparisons':pairs,'response_curves':stage_a,'environment_contexts':contexts,'factorial':factorial,'factorial_interactions':interactions,
        'morris_elementary_effects':morris,'sensitivity_rankings':rankings,'robustness':robustness,'long_run':long_id,
        'controlled_process_screen':'controlled_process_screen.json',
        'design':'Fixed bounded response curves, two deterministic extreme-to-extreme Morris paths and 2^3 mechanism factorial; not exhaustive variance decomposition or a probability sample',
        'initialization':'All C/N initial biological stocks conservatively reclassified from humus; this is a scenario partition, not literal transformation. Ablations retain identical stocks and mortality; feeding/growth switch only. Initial mineral N and water unchanged.',
        'accuracy_improvement_claimed':False,'independent_field_validation':False,'farmer_recommendation':None,
        'capabilities':{'previous_stable_physical_processes':'implemented_existing_unvalidated_scenario_model',
            'living_guild_resource_feedback':'implemented_experimental_Tier_D_numeric_rates',
            'pathogen_pressure':'implemented_experimental_dimensionless_proxy_not_abundance',
            'NASA_POWER':'historical_grid_forcing_with_verified_local_asset_provenance_not_field_truth',
            'independently_validated_ecological_predictions':None,'quantitative_crop_profiles':3,
            'catalog_crops':len(catalog.catalog['crops']) if 'crops' in catalog.catalog else 92,
            'other_89_quantitative_crop_profiles':None,'unverified_existing_organisms':'preserved_disabled'},
        'unavailable':{'pollination_limited_yield':None,'absolute_pathogen_density':None,'site_biological_population_prediction':None}}
    write_json(directory/'ecosystem_report.json',report)
    write_json(directory/'ecosystem_sensitivity.json',{k:report[k] for k in ['response_curves','environment_contexts','factorial','factorial_interactions','morris_elementary_effects','sensitivity_rankings','robustness','design']})
    write_json(directory/'code_provenance.json',{'python':sys.version,'implementation':{str(p.relative_to(Path(__file__).parents[2])):sha256(p) for p in sorted(Path(__file__).parents[1].rglob('*.py'))},
        'input_configuration_sha256':sha256(config_path),'scenario_configuration_sha256':sha256(resolve(base,config['scenario_config']))})
    write_findings(report,directory)
    artifacts={str(p.relative_to(directory)):sha256(p) for p in sorted(directory.rglob('*')) if p.is_file()}
    write_json(directory/'artifact_manifest.json',artifacts)
    return {'status':report['status'],'runs':len(runs),'daily_rows':sum(r['daily_rows'] for r in runs),
            'checkpoint_restores':sum(r['checkpoints_restored'] for r in runs),'maximum_balance_error':max(abs(v['error']) for r in runs for v in r['conservation'].values()),
            'report':str(directory/'ecosystem_report.json'),'field_validation':False,'farmer_recommendation':None}


def write_findings(report,directory):
    lines=['# AGROSIM ECOSYSTEM FUNCTIONAL COMPLETION REPORT','',
        'Implemented scenario behavior is not independent biological validation. All new numerical biological ranges are Tier D exploratory domains; response forms have literature context. No farmer recommendations, observed local populations or accuracy improvements are claimed.','',
        'Daily tissue removal changes the existing organ canopy and physiology. Predator assimilation is limited by prey C/N. Weed photosynthesis draws mineral N and water from the same soil column and intercepts light before the crop. Detritivore waste moves coarse surface material into layer0; no mineral N is created by fragmentation. Microbial C/N limitation, humus/residue return and explicit CO2 flux connect all years. Pathogen pressure is a dimensionless hypothetical host-dependent index; necrosis returns actual green-leaf material to residue. Pollination effects remain unavailable.','',
        '## Measured software execution','',f'{len(report["runs"])} unique coupled runs, {sum(r["daily_rows"] for r in report["runs"])} daily steps and {sum(r["checkpoints_restored"] for r in report["runs"])} fresh-object checkpoint restorations.',
        f'Maximum whole-run W/C/N ledger error: {max(abs(v["error"]) for r in report["runs"] for v in r["conservation"].values()):.12g}.','',
        '## Three-year reference outcomes','', '| Rotation | Dry harvest kg/ha (sum of three crops) | Final mineral N kg/ha | Final water mm | Final humus C kg/ha |','|---|---:|---:|---:|---:|']
    full=[r for r in report['runs'] if r['label'].startswith('full:')][:3]
    for r in full:
        s=r['summary']; lines.append(f'| {r["strategy"]} | {s["total_harvest_dry_kg_ha"]:.5f} | {s["final_mineral_N_kg_ha"]:.5f} | {s["final_water_mm"]:.5f} | {s["final_humus_C_kg_ha"]:.5f} |')
    lines+=['','The total dry-harvest column is a mass summary, not economic or causal rotation superiority. Unlike-crop comparisons are not reported as same-crop benefits. Management remains crop specific.','',
        '## Actual on-minus-off interaction magnitudes','', '| Mechanism | Harvest change kg dry/ha | Crop N uptake change kg/ha | Herbivore consumption change kg C/ha |','|---|---:|---:|---:|']
    for p in report['comparisons']:
        if p['label'].endswith('_on_minus_off') and p['intervention']==full[0]['id']:
            o=p['outcomes']; lines.append(f'| {p["label"]} | {o["total_harvest_dry_kg_ha"]["absolute"]:.6g} | {o["crop_uptake_N_kg_ha"]["absolute"]:.6g} | {o["herbivore_consumed_C_kg_ha"]["absolute"]:.6g} |')
    lines+=['','The machine report records first-effect dates, peak daily effects, annual stock differences, cumulative flux differences and percentages with a nonzero denominator. Negative and zero effects are retained.','',
        '## Assumption sensitivity','']
    for r in report['robustness']: lines.append(f'- {r["process"]} → {r["outcome"]}: {r["status"]}; range {min(x["effect"] for x in r["values"]):.6g} to {max(x["effect"] for x in r["values"]):.6g}.')
    lines+=['','## Limits and stable reference','',
        'These are numerical scenario effects, not observed treatment contrasts. The existing 106-pair benchmark and reserved outcomes were not opened or tuned. Previous water/yield/rotation baseline failures remain. Predator extinction or negligible late-season effects can follow from absent prey and no immigration; no recolonization is invented. Weed canopy order, pressure-to-necrosis, temperature cardinals and all guild rates require independent observations. Soil wetness is only a hypothetical disease suitability proxy, not measured leaf wetness. Aggregate assimilated biomass combines growth/recruitment; no unmeasured counts or age structure are inferred. Microbial compartments do not identify taxa.','',
        'The stable CLI delegates unchanged to V1. Experimental full-vs-V1 differs in organ accounting and initial pool partition as well as biological feedback; matched individual ablations isolate their named pathways. Long-run seasonal forcing is synthetic and supports only numerical stability. No universal global parameter, field confidence interval or biological validation is inferred.','',
        'See LIVING_ECOSYSTEM.md for equations, parameter units, exact CLI, controls and limitations. Final A–O readiness evidence is evaluated separately after reproduction and full test suites.','']
    (directory/'FINDINGS.md').write_text('\n'.join(lines))
