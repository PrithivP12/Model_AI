"""Four declared weather regimes, priorities and exact model-world interaction."""
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from datetime import date
import numpy as np
from ..io import load_config,write_json
from ..transfer_recovery.strategy import compare_evidence
from ..transfer_recovery.kernel import TransferKernel
from ..rotation_recovery.weather import historical_weather
from ..processes import Layer,SoilState,Planting,Management,Processes
from ..ecosystem import EcosystemEngine


def factorial_contrasts(values):
    """2x2 exact identity. Conditional effects and interaction are not independent."""
    base=values['retain_decay'];export=values['export_decay'];nodecay=values['retain_no_decay'];both=values['export_no_decay']
    interaction=both-export-nodecay+base
    return {'residue_export_effect_with_decay':export-base,'decay_disable_effect_with_retention':nodecay-base,
        'interaction':interaction,'joint_difference':both-base,
        'identity_residual':(export-base)+(nodecay-base)+interaction-(both-base),
        'semantics':'Exact model-world factorial decomposition at the declared baseline, not an identified field causal effect or unique pathway allocation.'}


def run_factorial(root,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    w=historical_weather(root/'reports/rotation-recovery/weather-exact','ARL',date(2013,12,31))
    l=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
    s=SoilState([75,80,150],[40,30,30],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)
    ps=[Planting(date(y,5,15),'soybeans'if y==2011 else'corn',date(y,10,15))for y in [2011,2012,2013]]
    es=[Management(p.date,0 if p.crop=='soybeans'else 140,0)for p in ps];runs={}
    for name,export,decay in [('retain_decay',False,True),('export_decay',True,True),('retain_no_decay',False,False),('export_no_decay',True,False)]:
        p=[replace(x,aboveground_residue_removal_fraction=1)if export and x.date.year==2011 else x for x in ps]
        k=TransferKernel(l,processes=Processes(maize_n_dilution_a=.034,residue_decay_per_day_20C=.008 if decay else 0.),
            latitude=43.29925,options={'root_beta_per_m':4,'observed_harvest_sowing_dates':[str(x.date)for x in p]})
        e=EcosystemEngine(k);run=e.run(w,e.initialize(s),p,es,keep_daily=False);state=run['checkpoint']
        runs[name]={'2012_grain':next(h['grain_dry_kg_ha']for h in run['harvests']if h['planted'].startswith('2012')),
            '2013_grain':next(h['grain_dry_kg_ha']for h in run['harvests']if h['planted'].startswith('2013')),
            'final_mineral_N':sum(state.soil['mineral_n_kg_ha']),'final_water':sum(state.soil['water_mm']),
            'evaporation':state.cumulative_fluxes['evaporation_mm'],'mineralized_N':state.cumulative_fluxes['mineralized_n_kg_ha'],
            'WCN_ledger_error':run['whole_run_balance_error']}
    contrasts={key:factorial_contrasts({name:r[key]for name,r in runs.items()})for key in runs['retain_decay']if key!='WCN_ledger_error'}
    if any(abs(x['identity_residual'])>1e-7 for x in contrasts.values()):raise ArithmeticError('Factorial identity failed')
    report={'status':'controlled_residue_decay_interaction_executed','runs':runs,'contrasts':contrasts,'interaction_is_reported_not_hidden':True,
        'real_world_accuracy_evidence':False,'all_other_inputs_identical':True,'recommendation':None}
    write_json(out/'report.json',report);return report


def reweight_priorities(result,priorities):
    allowed={'water_supply':-1,'N_loss':-1,'soil_organic_C':1,'scenario_net_return':1}
    if not priorities or set(priorities)-set(allowed):raise ValueError('Unsupported priority')
    rows=deepcopy(result['summaries'])
    for r in rows:
        if any(r[k]is None for k in priorities):raise ValueError('Priority lacks output')
        own=[r[k]*allowed[k]for k in priorities]
        r['scenario_pareto']=not any(all(a>=b for a,b in zip([x[k]*allowed[k]for k in priorities],own))and any(a>b for a,b in zip([x[k]*allowed[k]for k in priorities],own))for x in rows)
    return {'farmer_priorities':priorities,'summaries':rows,'physical_runs_recomputed':False,'forced_recommendation':None}


def run_demo(root,context,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    config,base=load_config(root/'configs/evidence_demo.yaml');result=compare_evidence(config,base)
    result['NASA_context']=context;result['priority_views']=[reweight_priorities(result,p)for p in [['water_supply','N_loss'],['scenario_net_return'],['soil_organic_C','N_loss']]]
    result['weather_regimes']={'normal':'Unperturbed historical replay, not a normal-climate probability label.','dry':'Rain multiplied0.7, other weather unchanged.','wet':'Rain multiplied1.3.','heat':'Tmin/Tmax increased3C, rain unchanged.'}
    result['soil_and_terrain']={'soil':config['soil'],'terrain':next(x for x in context['sources']if x['source']=='srtm'),
        'soil_is_measured_for_demo_field':False,'slope_available':False}
    result['attribution_limit']='Per-output common-weather differences are exact model predictions. Mechanism contributions interact; separate2x2 intervention experiment quantifies its declared interaction only.'
    write_json(out/'demonstration.json',result);return result
