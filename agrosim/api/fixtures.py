"""Repository-authored offline examples. Adoption is always explicit."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date,timedelta
from pathlib import Path
import json

from ..crops.profiles import profiles as old_profiles
from ..crops.validation import scenario as old_scenario
from ..physiology.scenarios import scenario as special_scenario
from ..physiology.profiles import SUBTYPES
from .models import SimulationRequest

SOURCE={'origin':'hypothetical','description':'Repository-authored controlled reference scenario; soil, weather, age and management are illustrative, not field measurements.'}

def scenario_ids():
    rows=[]
    for key in old_profiles():
        if key in SUBTYPES:
            rows.extend({'id':f'controlled_{key}_{subtype}_v1','crop_id':key,'subtype':subtype,'source':SOURCE}for subtype in SUBTYPES[key])
        else:rows.append({'id':f'controlled_{key}_v1','crop_id':key,'subtype':None,'source':SOURCE})
    return rows+[{'id':'controlled_rotation_v1','crop_id':None,'subtype':None,'source':SOURCE}]

def expand(request:SimulationRequest):
    if not request.reference_scenario_id:return request
    if not request.adopt_reference_assumptions:raise ValueError('Reference scenario requires explicit adoption of hypothetical inputs')
    row=next((r for r in scenario_ids()if r['id']==request.reference_scenario_id),None)
    if row is None:raise ValueError('Unknown reference scenario')
    old=old_profiles();key=row['crop_id']or'corn';profile=old[key]
    initial=None;paddy=None;options={}
    if profile['crop_parameters']:
        physical_latitude=40.0
        physical,soil,days,plants,events=old_scenario(profile,days=1096 if row['crop_id']is None else 360)
        # Old fixtures begin April1. Three-year reference rotation is declared
        # separately, with one complete annual crop per nonoverlapping slot.
        if row['crop_id']is None:
            from ..processes import WeatherDay,Planting,Management
            days=[WeatherDay(date(2020,1,1)+timedelta(days=i),5.,17.,23.,20.,4.)for i in range(1096)]
            plants=[Planting(date(y,1,1),crop,date(y,12,31))for y,crop in zip([2020,2021,2022],['corn','soybeans','spring_wheat'])]
            events=[Management(p.date,100. if p.crop!='soybeans'else 0.,0.)for p in plants]
    else:
        profile,physical,soil,days,plants,events,kwargs=special_scenario(key,subtype=row['subtype'])
        physical_latitude=physical.latitude
        initial=kwargs['initial'];paddy=kwargs['paddy'];options=kwargs['crop_options']or{}
        for item in options.values():
            if 'pollination'in item:item['pollination']['sex']='female'if key=='date'else'hermaphrodite'if key=='papaya'else'compatible_cultivar'
    reverse={'soybeans':'soybean','spring_wheat':'wheat'}
    plant_rows=[{'crop_id':reverse.get(p.crop,p.crop),'subtype':row['subtype'],'planted':str(p.date),
        'harvest_deadline':str(p.harvest_deadline),'residue_removal_fraction':p.aboveground_residue_removal_fraction,'harvest_mode':p.harvest_mode}for p in plants]
    base={'farm':request.farm.model_dump(mode='json'),'scenario_id':request.scenario_id,
        'reference_scenario_id':request.reference_scenario_id,'adopt_reference_assumptions':True,
        'allow_experimental_profiles':request.allow_experimental_profiles,'start':str(days[0].date),'end':str(days[-1].date),
        'plantings':plant_rows,'soil':{'layers':[asdict(l)for l in physical.layers],'initial_state':asdict(soil),'source':SOURCE},
        'weather':{'kind':'inline','source':SOURCE,'days':[asdict(d)|{'date':str(d.date)}for d in days]},
        'management':[asdict(m)|{'date':str(m.date),'source':SOURCE}for m in events],
        'crop_options':options,'paddy':paddy,'mode':request.mode,'living':request.living.model_dump(mode='json')if request.living else None,
        'finance':request.finance.model_dump(mode='json')if request.finance else None}
    if initial:
        base['initial_plant']={k:v for k,v in initial.items()if k not in ['planting','source']}
        base['initial_plant'].update(crop_id=key,source=SOURCE)
    supplied=request.model_dump(mode='json',exclude_unset=True)
    for k in ['start','end','plantings','soil','weather','management','crop_options','paddy','initial_plant']:
        if k in supplied and supplied[k]not in [None,[],{}]:base[k]=supplied[k]
    return SimulationRequest.model_validate(base)

def living_fixture():
    return deepcopy(json.loads(Path(__file__).with_name('data').joinpath('living_fixture.json').read_text()))
