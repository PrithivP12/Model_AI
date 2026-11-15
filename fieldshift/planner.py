"""Exploratory crop-sequence screening from sourced evidence.

The screening objective is explicit and untrained. No sequence benefits, yield gains,
microbiome transitions, pathogen survival or pollination effects are invented.
"""
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import argparse
import json
import math
import numpy as np
import pandas as pd
from . import sources

DEFAULT_CROPS=['Zea mays','Glycine max','Triticum aestivum','Pisum sativum','Brassica napus','Medicago sativa']


@dataclass
class Field:
    latitude:float
    longitude:float
    decision_date:str
    crops:list
    seasons:int=3
    growing_months:tuple=(4,5,6,7,8,9,10)
    soil_ph:float|None=None
    soil_ph_source:str|None=None
    soil_ph_sample_date:str|None=None
    previous_crop:str|None=None
    require_adjacent_change:bool=True
    shared_pathogen_weight:float=0.0

    def validate(self):
        if not math.isfinite(self.latitude) or not -90<=self.latitude<=90:raise ValueError('Invalid latitude')
        if not math.isfinite(self.longitude) or not -180<=self.longitude<=180:raise ValueError('Invalid longitude')
        pd.Timestamp(self.decision_date)
        if not 1<=self.seasons<=5:raise ValueError('Use 1-5 screening seasons')
        if not 2<=len(set(self.crops))<=10 or len(self.crops)!=len(set(self.crops)):raise ValueError('Use 2-10 distinct crop species')
        if not self.growing_months or any(m not in range(1,13) for m in self.growing_months):raise ValueError('Invalid growing months')
        if self.soil_ph is not None and (not math.isfinite(self.soil_ph) or not 0<=self.soil_ph<=14):raise ValueError('Invalid soil pH')
        if self.soil_ph is not None and (not self.soil_ph_source or not self.soil_ph_sample_date):raise ValueError('A pH measurement needs a source and sample date')
        if self.soil_ph_sample_date and pd.Timestamp(self.soil_ph_sample_date)>=pd.Timestamp(self.decision_date):raise ValueError('Soil measurement must precede decision')
        if not 0<=self.shared_pathogen_weight<=1:raise ValueError('Pathogen weight must be between 0 and 1')


def envelope(value,minimum,optimum_low,optimum_high,maximum):
    values=[value,minimum,optimum_low,optimum_high,maximum]
    if any(v is None or not np.isfinite(v) for v in values):return None
    if not minimum<=optimum_low<=optimum_high<=maximum:return None
    if value<minimum or value>maximum:return 0.
    if optimum_low<=value<=optimum_high:return 1.
    if value<optimum_low:return float((value-minimum)/(optimum_low-minimum)) if optimum_low>minimum else 1.
    return float((maximum-value)/(maximum-optimum_high)) if maximum>optimum_high else 1.


def screen_crop(t,weather,field):
    if t.get('status')!='available':return {'status':'unknown','screening_index':None}
    if weather.get('status')!='available' or weather['missing_fraction']>.05:
        return {'status':'unknown','screening_index':None,'reason':'Insufficient NASA weather coverage'}
    temp=np.mean([weather['monthly_normals'][str(m)]['temperature_C'] for m in field.growing_months])
    components={'temperature':envelope(temp,t['TMIN'],t['TOPMN'],t['TOPMX'],t['TMAX']),
                'annual_rainfall':envelope(weather['annual_rain_mm_mean'],t['RMIN'],t['ROPMN'],t['ROPMX'],t['RMAX']),
                'soil_ph':envelope(field.soil_ph,t['PHMIN'],t['PHOPMN'],t['PHOPMX'],t['PHMAX'])}
    known=[v for v in components.values() if v is not None]
    # Conservative bottleneck; unknown factors remain explicit and are never set to zero.
    score=min(known) if known else None
    return {'status':'partial' if len(known)<3 else 'screened','screening_index':score,
            'component_indices':components,'unknown_components':[c for c,v in components.items() if v is None],
            'growing_months':list(field.growing_months),'mean_growing_temperature_C':float(temp),
            'method':'Minimum of piecewise-linear FAO species-envelope indices; unvalidated screening index, not a probability or yield prediction.',
            'unmodeled':['irrigation','cultivar maturity','daily frost events','planting/harvest calendar','nutrient balance','prices','crop disease incidence']}


def shared_host_evidence(a,b,pathogen_data):
    aa=pathogen_data.get(a,{});bb=pathogen_data.get(b,{})
    if aa.get('status')!='available' or bb.get('status')!='available':
        return {'status':'unknown','overlap_index':None,'shared_species':[]}
    x=set(aa['studied_pathogen_species']);y=set(bb['studied_pathogen_species'])
    return {'status':'documented_host_overlap','overlap_index':len(x&y)/len(x|y) if x|y else None,
            'shared_species':sorted(x&y),'caution':'Shared studied pathogens do not quantify soil carryover or sequence effects.'}


def rank_sequences(field,screens,pathogen_data):
    allowed=[n for n in field.crops if screens[n].get('screening_index') is not None and screens[n]['screening_index']>0]
    if len(allowed)<2:return {'status':'insufficient_suitable_candidates','plans':[]}
    plans=[]
    for sequence in product(allowed,repeat=field.seasons):
        extended=([field.previous_crop] if field.previous_crop else [])+list(sequence)
        if field.require_adjacent_change and any(a==b for a,b in zip(extended,extended[1:])):continue
        transitions=[shared_host_evidence(a,b,pathogen_data) for a,b in zip(extended,extended[1:])]
        overlaps=[t['overlap_index'] for t in transitions if t['overlap_index'] is not None]
        climate=float(np.mean([screens[n]['screening_index'] for n in sequence]))
        # Unknown disease evidence cannot receive an implicit zero-risk advantage.
        complete=len(overlaps)==len(transitions)
        penalty=float(np.mean(overlaps)) if overlaps and complete else None
        score=climate
        if field.shared_pathogen_weight and penalty is None:continue
        if field.shared_pathogen_weight:score-=field.shared_pathogen_weight*penalty
        plans.append({'crops':list(sequence),'screening_objective':score,'mean_environmental_index':climate,
                      'shared_studied_pathogen_index':penalty,'transitions':transitions})
    plans.sort(key=lambda x:(-x['screening_objective'],x['crops']))
    return {'status':'exploratory_hypotheses','evaluated_sequences':len(plans),'plans':plans[:10],
            'objective':{'environmental_weight':1.0,'shared_pathogen_weight':field.shared_pathogen_weight,
                         'weights_learned_from_rotation_data':False},
            'constraints':{'adjacent_crop_must_change':field.require_adjacent_change},
            'ties':'Equal objectives are tied; alphabetical presentation is not evidence that one sequence is better.',
            'confidence':'Sequence benefits are unvalidated. No yields, profit gains, disease reduction, or optimality are claimed.',
            'limitations':'The same historical climate is used for every screening season. Nutrient/microbial transitions are unknown. No cumulative sequence advantage is modeled.'}


def safe_call(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except (requests_error_types()) as e:
        return {'status':'unavailable','error':str(e)[:300],'missing_is_not_zero':True}


def requests_error_types():
    import requests
    return (requests.RequestException,ValueError,KeyError,RuntimeError,OSError)


def build(field,offline=False):
    field.validate();print('Loading crop traits and pathogen evidence',flush=True)
    trait=sources.traits(field.crops);pathogen=sources.pathogens(field.crops)
    print('Loading NASA weather history',flush=True)
    weather_result=safe_call(sources.nasa_power,field.latitude,field.longitude,field.decision_date,offline=offline)
    weather=weather_result[0] if isinstance(weather_result,tuple) else weather_result
    print('Loading global microbiome reference context',flush=True)
    microbes=safe_call(sources.microbiome_context,field.latitude,field.longitude,field.decision_date)
    print('Loading nearby pollinator records',flush=True)
    pollinators=safe_call(sources.local_pollinators,field.latitude,field.longitude,field.decision_date,offline=offline)
    print('Loading crop-pollinator interaction evidence',flush=True)
    associations=safe_call(sources.plant_pollinator_interactions,field.crops,pollinators.get('sampled_species',[]),offline=offline)
    screens={n:screen_crop(trait[n],weather,field) for n in field.crops}
    model_report=sources.ROOT/'g2f/results/report.json'
    if model_report.exists():
        model=json.loads(model_report.read_text());candidate=model['tasks']['preplant'];baseline=model['baselines']['historical_site_mean']
        passed=candidate['test']['mae']<baseline['mae'] and candidate['test']['r2']>0
        yield_status={'status':'research_component_available' if passed else 'predictions_withheld_failed_validation',
                      'scope':'Maize only; parental pedigree proxy; no SNP markers',
                      'validation':candidate['test'],'historical_site_baseline':baseline,
                      'field_yield_prediction':None,'used_in_sequence_ranking':False}
    else:yield_status={'status':'not_trained','field_yield_prediction':None,'used_in_sequence_ranking':False}
    report={'version':'0.3','status':'research_prototype','field':field.__dict__,
            'rotation_databases_used':False,'weather':weather,'crop_traits':trait,'crop_screening':screens,
            'plant_pathogen_evidence':pathogen,'microbiome_reference_context':microbes,
            'pollinator_occurrences':pollinators,'crop_pollinator_evidence':associations,
            'maize_yield_model':yield_status,'sequence_screening':rank_sequences(field,screens,pathogen),
            'soil_disease_model':{'status':'separate_assay_based_component','used_for_this_field':False,
                'reason':'Requires compatible measured microbial profiles and soil chemistry; no field sample was supplied.',
                'measured_pilot_R2':.24450547164172975},
            'interpretation':'Sources are combined as a traceable evidence report. They are not falsely represented as one jointly trained multimodal model.'}
    for path in sorted((sources.ROOT/'all-sources').glob('*/integration_report.json')):
        coverage=json.loads(path.read_text());query=coverage.get('query',{})
        if (query.get('latitude'),query.get('longitude'),query.get('decision_date'))==(field.latitude,field.longitude,field.decision_date):
            report['challenge_source_integration']=coverage
            table=path.parent/'observations.csv'
            if table.exists():
                obs=pd.read_csv(table).astype(object)
                report['challenge_observations']=obs.where(pd.notna(obs),None).to_dict('records')
            break
    return report


def markdown(report):
    f=report['field'];lines=['# Field Shift: exploratory crop strategy report','',
        f"Location: {f['latitude']}, {f['longitude']}. Decision date: {f['decision_date']}.",'',
        '**Research screening only. Crop rotation databases were excluded. Sequence benefits are not validated.**','',
        '| Crop | Environmental screening index | Missing components |','|---|---:|---|']
    for n,s in report['crop_screening'].items():
        v=s.get('screening_index');lines.append(f"| {n} | {v:.3f} | {', '.join(s.get('unknown_components',[])) or 'None among the three tested'} |" if v is not None else f'| {n} | Unknown | Insufficient data |')
    seq=report['sequence_screening'];lines+=['','## Candidate sequences','',
        'These are tied or ranked by an explicit environmental screening objective, not predicted rotation yield or causal benefits.','']
    for p in seq.get('plans',[])[:5]:lines.append(f"- {' → '.join(p['crops'])}: screening objective {p['screening_objective']:.3f}.")
    lines+=['','## Evidence and limits','',
        '- NASA weather provides historical climate context, not future weather.',
        '- FAO EcoCrop contributes species-level environmental envelopes.',
        '- PHI-base contributes experimentally studied host–pathogen relationships; field occurrence and carryover remain unknown.',
        '- GBIF and GloBI contribute sampled pollinator occurrences and plant associations; observation counts are not abundance.',
        '- EMP reference samples are not measurements of this field’s microbiome.',
        f"- Maize ML component: {report['maize_yield_model']['status']}. It is not used to score sequences.",
        '- The soil disease model requires actual compatible assays and is not applied to this field.',
        '- No economic, nutrient-transition, water-balance or microbiome-transition model has been validated.','',
        'See the accompanying JSON for source links, record counts, missing-data states, and component validation.']
    coverage=report.get('challenge_source_integration')
    if coverage:
        lines+=['','## Complete challenge source checklist','',
                'Every listed resource has an explicit access result. A catalogue record is not counted as a physical observation. Newly extracted observations have not retrained the existing models.','',
                '| Resource | Observations | Access result |','|---|---:|---|']
        for row in coverage['families']:
            statuses='; '.join(k+': '+v for k,v in row['product_statuses'].items())
            lines.append(f"| {row['source']} | {row['physical_observation_count']} | {statuses} |")
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--field',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    field=Field(**json.loads(args.field.read_text()));report=build(field,offline=args.offline)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'field_report.json').write_text(json.dumps(report,indent=2,default=str,allow_nan=False))
    (args.output/'field_report.md').write_text(markdown(report))
    # A traceable fusion table, explicitly WITHOUT fabricated training outcomes.
    evidence=[]
    for crop,screen in report['crop_screening'].items():
        disease=report['plant_pathogen_evidence'][crop]
        poll=report['crop_pollinator_evidence'].get(crop,{})
        evidence.append({'crop_species':crop,'latitude':field.latitude,'longitude':field.longitude,
                         'decision_date':field.decision_date,'environmental_screening_index':screen.get('screening_index'),
                         'ph_observed_at_field':field.soil_ph,'pathogen_gene_experiment_records':disease.get('records'),
                         'sampled_global_plant_pollinator_interactions':poll.get('global_interaction_records_returned'),
                         'sampled_local_visitor_species_overlap':len(poll['overlap_with_sampled_local_species']) if 'overlap_with_sampled_local_species' in poll else None,
                         'field_microbiome_measured':False,'rotation_benefit_label':None,
                         'yield_prediction_validated':False})
    pd.DataFrame(evidence).to_csv(args.output/'evidence_table.csv',index=False)
    print('Saved',args.output/'field_report.md',flush=True)


if __name__=='__main__':main()
