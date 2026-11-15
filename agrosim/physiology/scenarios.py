"""Explicit controlled process fixtures, not reconstructed real-world fields."""
from copy import deepcopy
from datetime import date,timedelta
import math

from ..processes import Simulator,Layer,SoilState,WeatherDay,Planting,Management
from ..biological_recovery.plant import POOLS
from .profiles import resolve,SUBTYPES

PROVENANCE='Tier D controlled hypothetical physiology demonstration; no local observations or fitted field yield'
DEFAULT_SUBTYPES={'scallion':'fistulosum','brachiaria':'brizantha','ryegrass':'perenne'}


def scenario(identifier,context='favorable',*,days=None,subtype=None):
    subtype=subtype or DEFAULT_SUBTYPES.get(identifier);p=resolve(identifier,subtype=subtype)
    family=p.get('physiology_family');perennial=p['archetype_parameters']['perennial']
    days=days or(1096 if perennial else 240)
    start=date(2020,1,1);end=start+timedelta(days=days-1)
    layers=[Layer(.3,.1,.3,.5),Layer(.9,.1,.3,.5)]
    soil=SoilState([90.,270.],[150.,100.],[18000.,12000.],[1500.,1000.],[400.,300.],[10.,8.],[20.,20.],500.,10.)
    weather=[]
    for i in range(days):
        d=start+timedelta(days=i);phase=2*math.pi*(d.timetuple().tm_yday-15)/365.25
        if identifier in ['apple','grape','strawberry','ryegrass','scallion']:mean=15.-10.*math.cos(phase)
        elif identifier in ['mango','orange','lemon','avocado','olive']:mean=23.-5.*math.cos(phase)
        else:mean=27.-2.*math.cos(phase)
        rain=5.;solar=20.
        if context=='drought':rain=0.
        if context=='heat':mean=p['crop_parameters']['maximum_temp_C']+4
        if context=='cold':mean=max(-50.,p['crop_parameters']['kill_minimum_temp_C']-4)
        weather.append(WeatherDay(d,rain,mean-3,mean+3,solar,4.))
    if context=='drought':soil.water_mm=[l.mm(l.wilting_point)for l in layers]
    plants=[Planting(start,identifier,end+timedelta(days=1)if perennial else end)]
    events=[Management(date(y,1,1),100.,0.)for y in range(start.year,end.year+1)]
    settings={'pollination':{'status':'assumed_satisfied','source':PROVENANCE,'sex':'female'if identifier=='date'else'hermaphrodite'if identifier=='papaya'else'compatible cultivar/pollen assumed'}}
    if identifier=='strawberry':settings['flowering_type']='day_neutral'
    if identifier=='pineapple':settings.update(night_VPD_kPa=.7,night_VPD_source=PROVENANCE,
        flower_induction_dates=[str(start+timedelta(days=d))for d in [500,1000]])
    kwargs={'subtypes':{identifier:subtype}if subtype else None,'crop_options':{identifier:settings}if 'physiology_parameters'in p else None,
            'initial':None,'paddy':None}
    # Mature initial trees are explicit stocks. New plantings remain a different
    # scenario; age alone never adds wood, roots, foliage or fruit.
    if 'physiology_parameters'in p and identifier not in ['rice','pineapple']:
        established=date(2012,1,1)if identifier in ['date','coconut']else date(2016,1,1)
        plants=[Planting(established,identifier,end+timedelta(days=1))]
        masses={'green_leaf':1200.,'structural_shoot':6000.,'root':3000.,'labile_N':1000.}
        if identifier in ['strawberry','passion_fruit','banana','papaya']:
            masses={'green_leaf':500.,'structural_shoot':800.,'root':600.,'labile_N':200.}
        ratios={'green_leaf':.025,'structural_shoot':.004,'root':.012,'labile_N':.015}
        pools={k:{'dry':masses.get(k,0.),'N':masses.get(k,0.)*ratios.get(k,0.)}for k in POOLS}
        kwargs['initial']={'planting':plants[0],'pools':pools,'age_days':(start-established).days,
            'prior_season_shoot_dry':masses['structural_shoot']*.2,'primary_stem_gdd':1200.,'primary_stem_age_days':200.,'source':PROVENANCE}
    if identifier=='rice':
        kwargs['paddy']={'provenance':PROVENANCE,'initial_pond_mm':30. if context!='nonflooded'else 0.,'bund_height_mm':80. if context!='nonflooded'else 0.,
            'infiltration_limit_mm_day':12.,'percolation_limit_mm_day':2.,'initial_ammonium_fraction':.8,
            'fertilizer_form':'ammonium_equivalent','nitrification_day':.03,'denitrification_day':.04,'redox_relaxation_day':.1,
            'NH4_mobility':.1,'NO3_mobility':1.,'oxic_microsite_fraction':.05,
            'drain_dates':[str(start+timedelta(days=i))for i in [45,46,47,80,81,82]]if context=='intermittent'else[]}
        if context not in ['drought','nonflooded','heat','cold']:
            events +=[Management(start+timedelta(days=i),0.,15.)for i in range(days)if context!='intermittent'or i%30<15]
    return p,Simulator(layers,latitude=40. if identifier in ['apple','grape','strawberry','ryegrass','scallion']else 20.),soil,weather,plants,events,kwargs
