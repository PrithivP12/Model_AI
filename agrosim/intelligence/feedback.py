"""Bounded sensitivity of existing residue/N feedback, not empirical accuracy."""
from pathlib import Path
from dataclasses import replace
import json
import numpy as np
import pandas as pd
from scipy.stats import qmc,spearmanr
from ..processes import Layer,SoilState,Planting,Management,Processes,default_crops
from ..soil import DepthSimulator
from ..simulation import prepare
from ..io import load_config,write_json
from ..ecosystem_experiment import residue_loop_experiment

RANGES={'initial_mineral_n':(20,160),'fertilizer_maize':(0,240),'organic_c':(15000,80000),
        'initial_residue_cn':(20,80),'residue_decay_day':(.002,.016),'humus_decay_day':(.0000125,.00005),
        'fixation_max_day':(0,5),'corn_n_fraction':(.012,.024)}


def evaluate(parameters,weather,decay=True,sequence=('soybeans','corn'),dilution=False):
    crops=default_crops();crops['soybeans']=replace(crops['soybeans'],fixation_max_n_kg_ha_day=parameters['fixation_max_day'])
    crops['corn']=replace(crops['corn'],plant_n_fraction=parameters['corn_n_fraction'])
    layers=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
    weights=np.array([.4,.3,.3]);carbon=parameters['organic_c']*weights
    soil=SoilState([75,80,150],list(parameters['initial_mineral_n']*weights),list(carbon),list(carbon/12),[0]*3,[0]*3,[12]*3,
        surface_residue_c_kg_ha=2000,surface_residue_n_kg_ha=2000/parameters['initial_residue_cn'])
    processes=Processes(residue_decay_per_day_20C=parameters['residue_decay_day']if decay else 0,
        humus_decay_per_day_20C=parameters['humus_decay_day'],maize_n_dilution_a=.034 if dilution else 0)
    engine=DepthSimulator(layers,crops,processes,40.061188);plantings=[];management=[]
    from datetime import date
    for year,crop in zip([2027,2028],sequence):
        plantings.append(Planting(date(year,5,15),crop,date(year,10,15)))
        management.append(Management(date(year,5,15),parameters['fertilizer_maize']if crop=='corn'else 0,20))
    r=engine.run(weather,soil,plantings,management,keep_daily=True);harvest={int(h['planted'][:4]):h for h in r['harvests']};y2=[d for d in r['daily']if d['date']>='2028-01-01'];first=harvest[2027]
    # Actual modeled residue added on harvest, before any later decay.
    day1=next(i for i,d in enumerate(r['daily'])if d['date']==first['harvested'])
    residue_c=r['daily'][day1]['residue_c_kg_ha']-r['daily'][day1-1]['residue_c_kg_ha']if day1>0 else 0.
    return {'first_year_grain':first['grain_dry_kg_ha'],'next_corn_yield':harvest[2028]['grain_dry_kg_ha'],
        'next_corn_n_uptake':harvest[2028]['soil_uptake_n_kg_ha'],'first_year_fixed_n':first['fixed_n_kg_ha'],
        'year2_mineralized_n':sum(d['mineralized_n_kg_ha']for d in y2),'year2_immobilized_n':sum(d['immobilized_n_kg_ha']for d in y2),
        'n_leached':r['totals']['leached_n_kg_ha'],'final_residue_c':r['daily'][-1]['residue_c_kg_ha'],
        'balance_error':max(abs(v)for v in r['whole_run_balance_error'].values()),'max_daily_balance_error':max(r['max_daily_balance_error'].values()),
        'negative_n':min(r['final_state']['mineral_n_kg_ha'])<0,'harvest_residue_c_increment':residue_c}


def run_feedback(root,output,draws=96):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed feedback sensitivity')
    config,base=load_config(root/'configs/ecosystem.yaml');_,_,scenarios,provenance,_=prepare(config,base)
    weather=[w for w in scenarios[0][1]if w.date.year<=2028]
    protocol={'seed':817,'latin_hypercube_draws':draws,'ranges':RANGES,'simulation_years':[2027,2028],
        'weather':'One existing common NASA POWER historic scenario; not a forecast or measured trial weather',
        'comparisons':['soybean-maize with/without residue decay','maize-maize with residue decay','soybean-maize optional critical-N dilution'],
        'units':'N kg/ha; C kg/ha; rates per day; C:N kg C/kg N',
        'range_status':'Explicit sensitivity assumptions, not probability distributions or regionally calibrated crop parameters',
        'no_new_empirical_validation':True,'no_yield_multiplier':True,
        'references':['https://efotg.sc.egov.usda.gov/references/public/MN/340_MN_GD_Agronomy_Technical_Note_2024.pdf','https://extensionpubs.unl.edu/publication/ec155/2023/pdf/view/ec155-2023.pdf']}
    write_json(out/'protocol_frozen.json',protocol)
    points=qmc.LatinHypercube(len(RANGES),seed=817).random(draws);low,high=np.array(list(RANGES.values())).T;samples=qmc.scale(points,low,high)
    rows=[]
    for i,sample in enumerate(samples):
        p=dict(zip(RANGES,map(float,sample)))
        on=evaluate(p,weather,True);off=evaluate(p,weather,False);cc=evaluate(p,weather,True,('corn','corn'));diluted=evaluate(p,weather,True,dilution=True)
        rows.append({'scenario':i,**p,'decay_on_yield':on['next_corn_yield'],'decay_off_yield':off['next_corn_yield'],
            'decomposition_yield_difference':on['next_corn_yield']-off['next_corn_yield'],
            'continuous_maize_yield':cc['next_corn_yield'],'soybean_minus_maize_history':on['next_corn_yield']-cc['next_corn_yield'],
            'dilution_n_demand_yield':diluted['next_corn_yield'],'year2_mineralized_n':on['year2_mineralized_n'],
            'year2_immobilized_n':on['year2_immobilized_n'],'first_year_fixed_n':on['first_year_fixed_n'],
            'maximum_balance_error':max(r['balance_error']for r in [on,off,cc,diluted]),
            'maximum_daily_balance_error':max(r['max_daily_balance_error']for r in [on,off,cc,diluted]),'negative_n':any(r['negative_n']for r in [on,off,cc,diluted])})
        if i%24==0:print('feedback scenario',i,flush=True)
    frame=pd.DataFrame(rows);frame.to_csv(out/'sensitivity.csv',index=False)
    summary={name:{'minimum':float(frame[name].min()),'median':float(frame[name].median()),'maximum':float(frame[name].max()),
        'p05':float(frame[name].quantile(.05)),'p95':float(frame[name].quantile(.95))}for name in ['decay_on_yield','decay_off_yield','decomposition_yield_difference','soybean_minus_maize_history','first_year_fixed_n','dilution_n_demand_yield']}
    correlations={name:float(spearmanr(frame[name],frame.decomposition_yield_difference).statistic)for name in RANGES}
    original=residue_loop_experiment()
    default=dict(initial_mineral_n=80,fertilizer_maize=160,organic_c=45000,initial_residue_cn=50,residue_decay_day=.008,humus_decay_day=.000025,fixation_max_day=5,corn_n_fraction=.018)
    rescue={}
    for name,changed in [('realistic_anchor',{}),('no_fertilizer',{'fertilizer_maize':0}),('higher_residue_cn',{'initial_residue_cn':80}),('no_fixation',{'fixation_max_day':0})]:
        p={**default,**changed};rescue[name]={'parameters':p,'decay_on':evaluate(p,weather,True),'decay_off':evaluate(p,weather,False)}
    report={'status':'bounded_feedback_sensitivity_completed','protocol':protocol,'summary_ranges':summary,
        'zero_decay_off_yield_fraction':float((frame.decay_off_yield<=1e-6).mean()),
        'decomposition_can_reduce_yield_fraction':float((frame.decomposition_yield_difference<0).mean()),
        'history_benefit_can_reverse_fraction':float((frame.soybean_minus_maize_history<0).mean()),
        'maximum_ledger_error':float(frame.maximum_balance_error.max()),'maximum_daily_error':float(frame.maximum_daily_balance_error.max()),
        'any_negative_n':bool(frame.negative_n.any()),'spearman_sensitivity_not_causality':correlations,
        'anchor_scenarios':rescue,'previous_zero_yield_case':{'decomposition_off_yield':original['runs']['retained_no_residue_decay']['year_2_grain_kg_ha'],
            'explanation':'The original fixture starts with 5 kg/ha mineral N, zero SOM, disables humus mineralization, gives no fertilizer, and uses soybean HI .15. No-decomposition removes the only sustained N-recycling pathway after first crop N uptake/export/leaching. It is an intentional nutrient-starvation mechanism check, not typical agriculture.'},
        'biological_plausibility':'Feedback direction changes with initial N, fertilizer, C:N and turnover; numerical stability does not establish correct magnitude. Fixed whole-plant N demand and soybean fixation remain uncertain.',
        'empirical_multi_year_n_validation':False,'recommendation':None,'weather_provenance':provenance[0]}
    write_json(out/'report.json',report);return report
