"""Additive entry point; legacy-only selections delegate to the frozen runtime."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json

from ..crops import runtime as legacy
from ..crops.profiles import profiles as original_profiles
from ..processes import Crop,SoilState,PlantState
from ..ecosystem import EcosystemEngine,EcosystemState,decode_plant
from ..ecology_v2.soil_biology import Parameters,Decomposers,partition_humus
from ..biological_recovery.plant import validate_organs
from .profiles import resolve
from .kernel import SpecialKernel,IntegratedSpecialKernel


def build(physical,soil,identifiers,*,subtypes=None,living_config=None,crop_options=None,paddy=None,host_assumptions=None,initial=None):
    subtypes=subtypes or {};rows=[resolve(c,subtype=subtypes.get(c))for c in identifiers]
    ps={r['crop_parameters']['name']:r for r in rows};crops={k:Crop(**p['crop_parameters'])for k,p in ps.items()}
    options={'organ_model':True,'temperature_plateau':False,'root_beta_per_m':4,
             'crop_options':deepcopy(crop_options or {}),'paddy':deepcopy(paddy),'crop_host_assumptions':deepcopy(host_assumptions or {})}
    if 'rice'in identifiers and paddy is None:raise ValueError('Rice requires an explicitly configured flooded/nonflooded field component')
    soil=deepcopy(soil);kwargs={}
    if living_config:
        parameters=Parameters(**living_config['microbes']['parameters'])
        soil,cells=partition_humus(soil,living_config['microbes']['initial_humus_C_fraction'],parameters)
        for g in living_config['guilds']:
            if g['role']=='pathogen':continue
            c,n=g['initial']['C'],g['initial']['N']
            if c>soil.humus_c_kg_ha[0]or n>soil.humus_n_kg_ha[0]:raise ValueError('Living initial stock exceeds declared humus source')
            soil.humus_c_kg_ha[0]-=c;soil.humus_n_kg_ha[0]-=n
        kwargs={'decomposers':cells,'living_config':living_config};options['soil_biology']=asdict(parameters)
    cls=IntegratedSpecialKernel if living_config else SpecialKernel
    kernel=cls(physical.layers,crops,physical.processes,physical.latitude,profiles=ps,options=options,**kwargs)
    engine=EcosystemEngine(kernel);engine.physical.initialize_paddy(soil);plant=None
    if initial:
        if not initial.get('source'):raise ValueError('Explicit initial plant-stock provenance required')
        planting=initial['planting']
        if planting.crop not in ps or 'physiology_parameters'not in ps[planting.crop]:raise ValueError('Special initial organ state requires a matching special profile')
        pools=deepcopy(initial['pools'])
        plant=PlantState(planting,sum(p['dry']for p in pools.values()),sum(p['N']for p in pools.values()))
        engine.physical.initialize_organs(plant,crops[planting.crop]);plant.organ_state['pools']=pools
        s=plant.organ_state['special'];s['age_days']=float(initial['age_days'])
        if initial['age_days']<0:raise ValueError('Negative initial age')
        s['last_season_shoot_dry']=float(initial.get('prior_season_shoot_dry',0.))
        if not 0<=s['last_season_shoot_dry']<=pools['structural_shoot']['dry']:raise ValueError('Prior-season shoot substock exceeds actual structure')
        if ps[planting.crop]['physiology_family']in ['successor_herb','CAM_rosette']:
            s['corm_dry']=pools['structural_shoot']['dry']*.4;s['corm_N']=pools['structural_shoot']['N']*.4
            s['stems']=[{'id':0,'dry':pools['structural_shoot']['dry']*.6,'N':pools['structural_shoot']['N']*.6,
                         'gdd':initial.get('primary_stem_gdd',0.),'age_days':initial.get('primary_stem_age_days',0.),'flowered':False}]
        plant.organ_state['initialization_source']=deepcopy(initial['source']);validate_organs(plant)
        if pools['grain']['dry']or pools['grain']['N']:raise ValueError('Initial fruit requires explicit cohort measurements; unsupported initial fruit stocks refused')
    state=engine.initialize(soil,plant,provenance='Explicit special-crop initial stocks/soil and experimental assumptions; not measured unless source says so')
    return engine,state


def capture(engine,state):
    k=engine.physical;living=isinstance(k,IntegratedSpecialKernel)
    return {'ecosystem':asdict(state),'paddy':deepcopy(k.paddy),
        'decomposers':asdict(k.decomposers)if living else None,'guild_state':deepcopy(k.guild_state)if living else None,
        'diagnostics':{name:deepcopy(getattr(k,name))for name in ['product_events','growth_days','growth_stats','lifecycle',
            'stage_snapshots','growth_budgets','special_transfers','physiology_trace']}}


def restore(physical,soil,identifiers,payload,**kwargs):
    engine,_=build(physical,soil,identifiers,**kwargs);k=engine.physical
    state=EcosystemState(**deepcopy(payload['ecosystem']))
    if state.engine_identity!=engine.identity:raise ValueError('Special crop/configuration/code checkpoint mismatch')
    k.paddy=deepcopy(payload['paddy'])
    k.validate_paddy(SoilState(**state.soil))
    if isinstance(k,IntegratedSpecialKernel):
        k.decomposers=Decomposers(**payload['decomposers']);k.decomposers.validate(k.layers,k.bio_parameters.microbial_cn)
        k.guild_state=deepcopy(payload['guild_state']);k.validate_guild_state()
    elif payload['decomposers']is not None or payload['guild_state']is not None:raise ValueError('Cannot restore living state in standard mode')
    for name,value in payload['diagnostics'].items():setattr(k,name,deepcopy(value))
    if state.plant:
        plant=decode_plant(state.plant)
        if k._profile(plant.planting.crop):k.validate_special(plant)
    if capture(engine,state)!=payload:raise ArithmeticError('Special checkpoint serialization lost state')
    return engine,state


def run(physical,soil,weather,plantings,events,identifiers,*,subtypes=None,living_config=None,crop_options=None,paddy=None,
        host_assumptions=None,initial=None,resume=True):
    old=original_profiles()
    if all(old[c]['crop_parameters']is not None for c in identifiers):
        if any(x is not None for x in [initial,paddy,crop_options,subtypes]):raise ValueError('Legacy-only execution uses its original configuration interface')
        return legacy.run(physical,soil,weather,plantings,events,identifiers,living_config=living_config,host_assumptions=host_assumptions,resume=resume)
    weather=list(weather)
    kwargs=dict(subtypes=subtypes,living_config=living_config,crop_options=crop_options,paddy=paddy,host_assumptions=host_assumptions,initial=initial)
    engine,state=build(physical,soil,identifiers,**kwargs);start=capture(engine,state)
    if initial and initial['planting'].date>=weather[0].date:raise ValueError('Existing-plant initial state needs establishment before the first simulated day')
    daily=[];harvests=[];maxima={'water':0.,'carbon':0.,'nitrogen':0.};restored=0
    cut=len(weather)//2;parts=[weather[:cut],weather[cut:]]if resume else[weather]
    for part in parts:
        result=engine.run(part,state,plantings,events,keep_daily=True);state=result['checkpoint']
        daily+=result['daily'];harvests+=result['harvests']
        for key,value in result['max_daily_balance_error'].items():maxima[key]=max(maxima[key],value)
        if resume:
            payload=json.loads(json.dumps(capture(engine,state)))
            engine,state=restore(physical,soil,identifiers,payload,**kwargs);restored+=1
    final=engine.physical.inventories(SoilState(**state.soil),decode_plant(state.plant));totals=state.cumulative_fluxes
    residual={k:final[k]-state.initial_inventory[k]-totals[k+'_inputs']+totals[k+'_outputs']for k in maxima}
    if max(map(abs,residual.values()))>1e-6:raise ArithmeticError('Special crop whole-run conservation failed')
    return {'initial_state':start,'final_state':capture(engine,state),'harvests':harvests,
        'product_harvests':deepcopy(engine.physical.product_events),'daily':daily,'conservation':residual,
        'maximum_daily_error':maxima,'checkpoint_restores':restored,'independent_validation':False,'farmer_recommendation':None}
