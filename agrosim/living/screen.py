"""Small controlled process domain, not an independent observation dataset."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
from itertools import product

from ..ecology_v2.soil_biology import Decomposers, Parameters
from ..processes import Layer, SoilState, Processes, WeatherDay
from .kernel import LivingKernel, consumption


def batch(config, *, temperature=25., theta=.3, residue_cn=70., mineral_n=10., substrate_c=900., microbial_c=40., days=21):
    cfg=deepcopy(config)
    for g in cfg['guilds']:
        g['initial']={'pressure':0.} if g['role']=='pathogen' else {'C':0.,'N':0.}
    cfg['weed_removal']=[]
    p=Parameters(**cfg['microbes']['parameters'])
    soil=SoilState([theta*250],[mineral_n],[0.],[0.],[substrate_c],[substrate_c/residue_cn],[temperature])
    k=LivingKernel([Layer(.25,.1,.3,.5)], processes=Processes(denitrification_fraction_day=0),
                   options={'organ_model':True,'soil_biology':asdict(p)}, living_config=cfg,
                   decomposers=Decomposers([microbial_c],[microbial_c/p.microbial_cn],'scenario_assumed','Controlled closed process screen; not observed microbial biomass'))
    before=k.inventories(soil,None); total={}
    for i in range(days):
        day=WeatherDay(date(2000,1,1)+timedelta(days=i),0,temperature,temperature,0,0)
        f={key:0. for key in ['respired_c_kg_ha','mineralized_n_kg_ha','immobilized_n_kg_ha','denitrified_n_kg_ha']}
        k._decomposition(soil,day,f)
        for name,value in f.items():
            if not name.startswith('functional_decomposer'): total[name]=total.get(name,0.)+value
    after=k.inventories(soil,None)
    errors={'C':after['carbon']-before['carbon']+total['respired_c_kg_ha'],
            'N':after['nitrogen']-before['nitrogen'],'water':after['water']-before['water']}
    if max(map(abs,errors.values()))>1e-7: raise ArithmeticError('Controlled batch material failure')
    return {'input':{'temperature_C':temperature,'theta':theta,'residue_CN':residue_cn,'initial_mineral_N':mineral_n,
                      'initial_substrate_C':substrate_c,'initial_microbe_C':microbial_c,'days':days},
            'respired_C':total['respired_c_kg_ha'],'residue_consumed_C':total.get('residue_consumed_C_kg_ha',0.),
            'net_mineral_N':sum(soil.mineral_n_kg_ha)-mineral_n,'final_microbe_C':sum(k.decomposers.carbon_kg_ha),
            'immobilized_N':total['immobilized_n_kg_ha'],'mineralized_N':total['mineralized_n_kg_ha'],
            'balance_error':errors}


def controlled_screen(config):
    environmental=[batch(config,temperature=t,theta=w) for t,w in product([5.,25.,45.],[.04,.3,.5])]
    quality=[batch(config,residue_cn=cn,mineral_n=n,substrate_c=s,microbial_c=b)
             for cn,n,s,b in product([25.,70.],[0.,50.],[300.,900.],[4.,40.])]
    def cell(cn,n,s,b): return next(x for x in quality if x['input']=={'temperature_C':25.,'theta':.3,'residue_CN':cn,'initial_mineral_N':n,'initial_substrate_C':s,'initial_microbe_C':b,'days':21})
    interactions=[]
    for s,b in product([300.,900.],[4.,40.]):
        interactions.append({'initial_substrate_C':s,'initial_microbe_C':b,
                             'CN_by_mineral_N':{k:(cell(70,50,s,b)[k]-cell(25,50,s,b)[k])-(cell(70,0,s,b)[k]-cell(25,0,s,b)[k])
                                               for k in ['net_mineral_N','residue_consumed_C','respired_C']}})
    predator=next(g for g in config['guilds'] if g['role']=='predator')['parameters']
    response=[{'prey_C':food,'predator_C':consumer,
               'consumed_C_per_day':consumption(food,consumer,predator['max_feed'],predator['half_food'],1.)}
              for food,consumer in product([0.,.01,.1,1.,10.,100.],[0.,.1,.2,1.])]
    return {'status':'controlled_model_sensitivity_not_field_data','environment_panel':environmental,
            'substrate_N_biomass_factorial':quality,'factorial_interactions':interactions,'predator_response_curve':response,
            'range_evidence':'All numeric initializations and domains Tier D, independent of observed outcomes',
            'support':'Nominal closed0–25cm soil batch; no drainage, field-weather exposure, plant uptake or new immigration',
            'organisms_other_than_decomposers':'Explicit zero stocks, not missing presumed populations',
            'interpretation':'Exact equation screen and conservation check; not a training dataset or validation of ecological response magnitudes'}
