"""Evidence-only replacements; unresolved fields remain null outside scenarios."""
from datetime import date
import json
import numpy as np
from ..processes import Layer,SoilState,Planting,Management,WeatherDay
from ..intelligence.rotation import climate_weather
from .weather import historical_weather


def guard_preplant(inputs,decision_date):
    cutoff=date.fromisoformat(decision_date)
    for item in inputs:
        if item.get('role')=='hypothetical_scenario'and item.get('source_kind')=='declared_assumption':
            if item.get('observation_end')is not None:raise ValueError('An observation cannot be relabeled as a pure assumption')
            available=item.get('available_at')
            if not available or date.fromisoformat(available[:10])>cutoff:raise ValueError('Scenario assumption lacks a valid decision-time declaration')
            continue
        end=item.get('observation_end');available=item.get('available_at')
        if not end or not available:raise ValueError('Unknown observation/availability date cannot enter a preplant prediction')
        if date.fromisoformat(end[:10])>cutoff or date.fromisoformat(available[:10])>cutoff:raise ValueError('Postdecision information cannot enter a preplant prediction')
        if item.get('role')=='archived_actual_management'and not item.get('documented_preplant_plan',False):raise ValueError('Actual operation is not an archived preplant plan')
    return True


def hydraulic_profile(soil_records,first_sowing):
    profile=[];evidence=[]
    for lo,hi in [(0,30),(30,60),(60,120)]:
        source=[]
        # Latest dated values per exact depth before the first modeled crop.
        groups={}
        for r in soil_records:
            if not r['sample_date']or r['sample_date']>first_sowing or not r['depth_cm']:continue
            v=r['values'];a,b=r['depth_cm']
            if not all(v.get(k)is not None for k in ['SOIL02','SOIL31','SOIL32']):continue
            wp,fc,sat=v['SOIL32'],v['SOIL31'],v['SOIL02']
            if not 0<wp<fc<sat<=.8:continue
            key=(a,b)
            if key not in groups or r['sample_date']>groups[key]['sample_date']:groups[key]=r
        boundaries={float(lo),float(hi)}
        for a,b in groups:
            if min(hi,b)>max(lo,a):boundaries.update([max(lo,a),min(hi,b)])
        total=np.zeros(3);covered=0.;used=[];bounds=sorted(boundaries)
        for a,b in zip(bounds,bounds[1:]):
            candidates=[r for (x,y),r in groups.items()if x<=a and y>=b]
            if not candidates:continue
            # Narrowest support, then latest sampling date; never double-count overlaps.
            r=min(candidates,key=lambda r:(r['depth_cm'][1]-r['depth_cm'][0],-date.fromisoformat(r['sample_date']).toordinal()))
            v=r['values'];total+=np.array([v['SOIL32'],v['SOIL31'],v['SOIL02']])*(b-a);covered+=b-a;used.append(r['source_row'])
        if covered>=hi-lo-1e-8:
            wp,fc,sat=total/covered;layer=Layer((hi-lo)/100,float(wp),float(fc),float(sat),residual_water_content=min(.025,float(wp)*.25))
            profile.append(layer);evidence.append({'top_cm':lo,'bottom_cm':hi,'source_rows':sorted(set(used)),'status':'dated measured retention, assumed temporally constant during reconstruction'})
        else:
            profile.append(Layer((hi-lo)/100,.12,.28,.45));evidence.append({'top_cm':lo,'bottom_cm':hi,'source_rows':[],'status':'unknown; retained frozen hypothetical loam scenario, not a field observation'})
    return profile,evidence


def prepare_record(root,record,old_trajectory,soil_records,weather_dir,variant,initial_n,pulsed):
    daily=variant!='B_replay'
    dates=variant in ['C_dates','C_N','C_soil']
    nitrogen=variant in ['C_N','C_soil']
    fieldsoil=variant=='C_soil'
    year=record['year'];end=date(year,12,31)
    weather=historical_weather(weather_dir,record['site_id'],end)if daily else climate_weather(root,record['site_id'],date(2011,1,1),end,pulsed)[0]
    layers,soil_evidence=hydraulic_profile(soil_records,record['timeline'][0]['planting_date'])if fieldsoil else ([Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)],[])
    # Starting water/N are explicit nuisance scenarios, never evidence replacements.
    water_fraction=[75/84,80/84,150/168]
    state=SoilState([l.mm(l.field_capacity)*f for l,f in zip(layers,water_fraction)],
        [initial_n*.4,initial_n*.3,initial_n*.3],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)
    plantings=[];events=[];evidence=[]
    for season in record['timeline']:
        y=season['year'];crop=season['crop'];is_target=y==year
        fallback_sow=date.fromisoformat(old_trajectory['target_sowing'])if is_target else date(y,5,15)
        sow=date.fromisoformat(season['planting_date'])if dates and season['planting_date']else fallback_sow
        harvest=date.fromisoformat(season['harvest_date'])if dates and season['harvest_date']else date(y,10,15)
        if harvest<sow:raise ValueError('Harvest before planting')
        plantings.append(Planting(sow,crop,harvest))
        total=season['fertilizer_total_if_recorded_complete']
        if nitrogen and total is not None:
            selected=[Management(date.fromisoformat(e['date']),e['n_kg_ha'],0)for e in season['fertilizer_events']if '2011-01-01'<=e['date']<=str(end)]
            basis='reported dated events; logging completeness not independently established'
        elif is_target:
            selected=[Management(date.fromisoformat(t),v,0)for t,v in old_trajectory['target_N_events']];basis='frozen target recorded events'
        else:
            selected=[Management(sow,140 if crop=='corn'else 0,0)];basis='unresolved: retained frozen hypothetical N scenario, not an observed field dose'
        events.extend(selected)
        evidence.append({'year':y,'crop':crop,'sowing_observed':dates and season['planting_date']is not None,
            'harvest_observed':dates and season['harvest_date']is not None,'N_basis':basis,
            'prestart_recorded_N_kg_ha':sum(e['n_kg_ha']for e in season['fertilizer_events']if e['date']<'2011-01-01'),
            'prestart_N_status':'outside simulation; starting inventory unknown, no double-counted addition',
            'irrigation_status':'unknown; frozen no-application scenario retained, not claimed observed zero'})
    return weather,layers,state,plantings,events,{'mode':'conditional_retrospective_reconstruction'if daily else 'hypothetical_weather_and_management_scenario',
        'initial_water_status':'unknown; explicit frozen nuisance scenario','initial_N_status':'unknown; explicit frozen nuisance scenario',
        'season_inputs':evidence,'soil_inputs':soil_evidence,'future_inputs_permitted_for_retrospective_only':True}
