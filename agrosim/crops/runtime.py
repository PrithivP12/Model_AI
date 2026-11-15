"""One shared API and explicit checkpoints for additive crop profiles."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import json

from ..ecology_v2.soil_biology import Parameters,Decomposers,partition_humus
from ..ecosystem import EcosystemEngine,EcosystemState,decode_plant
from ..evaluator.audit import EvaluationKernel
from ..processes import SoilState
from .kernel import CropKernel,IntegratedCropKernel
from .profiles import CropRegistry88


def build(physical,soil,identifiers,*,living_config=None,host_assumptions=None):
    registry=CropRegistry88();rows=[registry.require(c)for c in identifiers]
    profiles={r['crop_parameters']['name']:r for r in rows};crops=registry.parameters(identifiers)
    has_new=any(p['implementation_status']=='EXECUTABLE_EXPERIMENTAL'for p in rows)
    options={'organ_model':bool(living_config)or has_new,'temperature_plateau':False,'root_beta_per_m':4}
    if not has_new:
        options['organ_model']=False
        # Reference path exactly retains the original aggregate solver/defaults.
    soil=deepcopy(soil)
    if living_config:
        p=Parameters(**living_config['microbes']['parameters']);soil,microbes=partition_humus(soil,living_config['microbes']['initial_humus_C_fraction'],p)
        for g in living_config['guilds']:
            if g['role']=='pathogen':continue
            c,n=g['initial']['C'],g['initial']['N']
            if c>soil.humus_c_kg_ha[0]or n>soil.humus_n_kg_ha[0]:raise ValueError('Insufficient declared initial humus partition for guild stocks')
            soil.humus_c_kg_ha[0]-=c;soil.humus_n_kg_ha[0]-=n
        options.update(organ_model=True,soil_biology=asdict(p),crop_host_assumptions=deepcopy(host_assumptions or {}))
        kernel=IntegratedCropKernel(physical.layers,crops,physical.processes,physical.latitude,options=options,
                                    profiles=profiles,decomposers=microbes,living_config=living_config)
    else:kernel=CropKernel(physical.layers,crops,physical.processes,physical.latitude,options=options,profiles=profiles)
    engine=EcosystemEngine(kernel)
    return engine,engine.initialize(soil,provenance='Explicit supplied scenario soil/management and crop profile, not auto-inferred farm truth')


def checkpoint(engine,state):
    k=engine.physical
    return {'ecosystem':asdict(state),'guild_state':deepcopy(k.guild_state)if isinstance(k,IntegratedCropKernel)else None,
            'decomposers':asdict(k.decomposers)if isinstance(k,IntegratedCropKernel)else None,
            'diagnostics':{name:deepcopy(getattr(k,name))for name in ['product_events','growth_days','growth_stats','lifecycle','stage_snapshots','growth_budgets']}}


def restore(physical,soil,identifiers,payload,*,living_config=None,host_assumptions=None):
    engine,_=build(physical,soil,identifiers,living_config=living_config,host_assumptions=host_assumptions)
    state=EcosystemState(**deepcopy(payload['ecosystem']));k=engine.physical
    if state.engine_identity!=engine.identity:raise ValueError('Crop profile/configuration/implementation checkpoint mismatch')
    if isinstance(k,IntegratedCropKernel):
        k.guild_state=deepcopy(payload['guild_state']);k.validate_guild_state()
        k.decomposers=Decomposers(**payload['decomposers']);k.decomposers.validate(k.layers,k.bio_parameters.microbial_cn)
    elif payload['decomposers']is not None or payload['guild_state']is not None:raise ValueError('Living checkpoint requires living mode')
    for name,value in payload['diagnostics'].items():setattr(k,name,deepcopy(value))
    if checkpoint(engine,state)!=payload:raise ArithmeticError('Crop checkpoint lost state')
    return engine,state


def run(physical,soil,weather,plantings,events,identifiers,*,living_config=None,host_assumptions=None,resume=True):
    weather=list(weather);engine,state=build(physical,soil,identifiers,living_config=living_config,host_assumptions=host_assumptions)
    initial=checkpoint(engine,state);middle=len(weather)//2
    if not resume:parts=[weather]
    else:parts=[weather[:middle],weather[middle:]]
    daily=[];harvests=[];maxima={'water':0.,'carbon':0.,'nitrogen':0.};restores=0
    for part in parts:
        result=engine.run(part,state,plantings,events,keep_daily=True);state=result['checkpoint'];daily+=result['daily'];harvests+=result['harvests']
        for key,value in result['max_daily_balance_error'].items():maxima[key]=max(maxima[key],value)
        payload=json.loads(json.dumps(checkpoint(engine,state)))
        if resume:engine,state=restore(physical,soil,identifiers,payload,living_config=living_config,host_assumptions=host_assumptions);restores+=1
    # Legacy reference profiles report product in grain harvests; new types have
    # separate executed product events and keep non-grain legacy fields at zero.
    products=deepcopy(engine.physical.product_events)
    for h in harvests:
        if h['crop']in ['corn','soybeans','spring_wheat']:
            products.append({'crop':h['crop'],'date':h['harvested'],'product_id':h['crop']+'_legacy_grain',
                             'product_dry_kg_ha':h['grain_dry_kg_ha'],'product_N_kg_ha':h['grain_n_kg_ha'],'mode':'legacy_grain'})
    totals=state.cumulative_fluxes
    final_inventory=engine.physical.inventories(SoilState(**state.soil),decode_plant(state.plant))
    residual={key:final_inventory[key]-state.initial_inventory[key]-totals.get(key+'_inputs',0)+totals.get(key+'_outputs',0)for key in maxima}
    if max(map(abs,residual.values()))>1e-6:raise ArithmeticError('Crop integration whole-run material failure')
    return {'initial_state':initial,'final_state':checkpoint(engine,state),'harvests':harvests,'product_harvests':products,
            'daily':daily,'conservation':residual,'maximum_daily_error':maxima,'checkpoint_restores':restores,
            'growth_days':deepcopy(engine.physical.growth_days),'independent_validation':False,'farmer_recommendation':None}
