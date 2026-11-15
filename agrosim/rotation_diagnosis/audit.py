"""Trace every treatment package and enforce paired yield identities."""
from pathlib import Path
from dataclasses import asdict
from collections import Counter
import json
import numpy as np
import pandas as pd
from ..io import write_json,sha256
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import scores


def load(root):
    root=Path(root);base=root/'reports/rotation-recovery/reproduced-final/input-audit'
    fixed=pd.read_csv(root/'reports/reproductive-recovery/final/rotation/paired_predictions.csv')
    records=json.loads((base/'records.json').read_text());soils=json.loads((base/'soil_observations.json').read_text())
    old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    return fixed,records,soils,old,actual


def package_summary(record):
    season=record['timeline'][-1]
    varieties=sorted({v['variety']for v in season['planting_records']if v.get('variety')})
    return {'current_crop':season['crop'],'sequence':'>'.join(s['crop']for s in record['timeline']),
        'previous_crop':record['timeline'][-2]['crop'],'current_N_total':season['fertilizer_total_if_recorded_complete'],
        'current_N_status':season['fertilizer_record_status'],'current_N_events':[(e['date'],e['n_kg_ha'])for e in season['fertilizer_events']],
        'sowing':season['planting_date'],'harvest':season['harvest_date'],'varieties':varieties,
        'tillage':record['tillage'],'drainage':record['drainage'],'nitrogen_treatment':record['nitrogen_treatment']}


def reconstruct(root,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    fixed,records,soils,old,actual=load(root);ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    stable=json.loads((root/'reports/evidence-readiness/reproduced-final/evaluation/reference_rotation_records.json').read_text())
    source=json.loads((root/'reports/reproductive-recovery/final/rotation/source_sink_record_predictions.json').read_text())
    weather_manifest=json.loads((root/'reports/rotation-recovery/weather-exact/manifest.json').read_text())
    record_audit={}
    for rid in ids:
        rec=records[rid];w,l,s,plants,events,evidence=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',40,True)
        record_audit[rid]={'record_id':rid,**rec,'summary':package_summary(rec),'recorded_yield_dry_kg_ha':float(actual.loc[rid,'observed_yield_dry_kg_ha']),
            'observed_before_2011_history_complete':bool(actual.loc[rid,'history_complete_before_2011']),
            'stable_prediction':stable[rid]['prediction'],'source_sink_prediction':source[rid]['prediction'],
            'simulation_inputs':{'plantings':[asdict(p)for p in plants],'N_irrigation_events':[asdict(e)for e in events],
                'soil_layers':[asdict(x)for x in l],'initial_state_example_N40':asdict(s),'evidence':evidence,
                'residue_retention':'unverified all-retained shoot scenario; roots returned by kernel',
                'weather':{'normalized_file':'reports/rotation-recovery/weather-exact/'+rec['site_id']+'.csv',
                    'normalized_sha256':sha256(root/'reports/rotation-recovery/weather-exact'/(rec['site_id']+'.csv')),
                    'first':str(w[0].date),'last':str(w[-1].date),'days':len(w),'source_metadata':weather_manifest[rec['site_id']]}}}
    comparisons=[];pair_rows=[]
    for p in fixed.to_dict('records'):
        sides={name:p[col].split(';')for name,col in [('reference','reference_records'),('candidate','candidate_records')]}
        if any(len(x)!=1 for x in sides.values()):raise ValueError('This strict management comparison requires singleton treatment arms; preserve grouped pairs for separate handling')
        a,b=[record_audit[sides[x][0]]for x in ['reference','candidate']];x,y=a['summary'],b['summary']
        obs=b['recorded_yield_dry_kg_ha']-a['recorded_yield_dry_kg_ha'];pred=b['source_sink_prediction']-a['source_sink_prediction']
        if abs(obs-p['observed_difference'])>1e-7 or abs(pred-p['source_sink'])>1e-7:raise AssertionError('Fixed contrast identity failed')
        same_N=x['current_N_total']is not None and y['current_N_total']is not None and abs(x['current_N_total']-y['current_N_total'])<1e-8
        same_schedule=x['current_N_events']==y['current_N_events']and same_N
        same_dates=x['sowing']is not None and x['harvest']is not None and x['sowing']==y['sowing']and x['harvest']==y['harvest']
        same_hybrid=bool(x['varieties'])and x['varieties']==y['varieties']
        row={'pair_id':p['pair_id'],'farm_id':p['farm_id'],'site_id':p['site_id'],'year':int(p['year']),'fold':int(p['fold']),
            'reference_record':a['record_id'],'candidate_record':b['record_id'],'reference_sequence':x['sequence'],'candidate_sequence':y['sequence'],
            'same_current_N_total':same_N,'same_current_N_schedule':same_schedule,'same_current_calendar':same_dates,'same_recorded_hybrid':same_hybrid,
            'relatively_comparable_current_package':same_schedule and same_dates and same_hybrid,
            'unknown_current_N':x['current_N_total']is None or y['current_N_total']is None,
            'candidate_minus_reference_current_N':None if x['current_N_total']is None or y['current_N_total']is None else y['current_N_total']-x['current_N_total'],
            'observed_difference':obs,'source_sink':pred,'reference':p['reference'],'history_baseline':p['history_baseline'],
            'source_sink_error':pred-obs,'common_yield_error':((b['source_sink_prediction']-b['recorded_yield_dry_kg_ha'])+(a['source_sink_prediction']-a['recorded_yield_dry_kg_ha']))/2,
            'nitrogen_regime':x['nitrogen_treatment']}
        pair_rows.append(row);comparisons.append({**row,'reference_package':a['summary'],'candidate_package':b['summary'],
            'causal_rotation_effect_identified':False,'comparability_limit':'Equal recorded current operations do not establish equal prior states, genotype performance, pest pressure, complete management or randomized causal support.'})
    table=pd.DataFrame(pair_rows);table.to_csv(out/'pair_audit.csv',index=False)
    write_json(out/'records.json',record_audit);write_json(out/'pairs.json',comparisons)
    descriptions=[]
    for variable in ['farm_id','nitrogen_regime','same_current_N_schedule','same_current_calendar','same_recorded_hybrid','relatively_comparable_current_package']:
        for value,g in table.groupby(variable,dropna=False):
            descriptions.append({'group':variable,'value':str(value),'pairs':len(g),'observed_mean':float(g.observed_difference.mean()),
                'predicted_mean':float(g.source_sink.mean()),**scores(g,'source_sink')})
    pd.DataFrame(descriptions).to_csv(out/'descriptive_subsets.csv',index=False)
    report={'status':'all_fixed_treatment_packages_reconstructed','pairs':len(table),'records':len(ids),'farms':int(table.farm_id.nunique()),
        'current_N_totals_differ_or_unknown':int((~table.same_current_N_total).sum()),
        'known_current_N_total_differences':int((~table.same_current_N_total&~table.unknown_current_N).sum()),
        'unknown_current_N_pairs':int(table.unknown_current_N.sum()),'recorded_hybrid_not_confirmed_equal':int((~table.same_recorded_hybrid).sum()),
        'current_calendar_not_confirmed_equal':int((~table.same_current_calendar).sum()),
        'relatively_comparable_current_package_pairs':int(table.relatively_comparable_current_package.sum()),
        'nitrogen_regimes':table.nitrogen_regime.value_counts().to_dict(),'full_fixed_cohort_metrics':scores(table,'source_sink'),
        'observed_mean':float(table.observed_difference.mean()),'predicted_mean':float(table.source_sink.mean()),
        'mean_differential_error':float(table.source_sink_error.mean()),'mean_common_error':float(table.common_yield_error.mean()),
        'all_source_identities_verified':True,'pure_rotation_causal_identification':False,
        'unknowns':['Pre2011 state/history','Initial matched-depth water and mineral N','Actual returned residues/root inputs',
            'Complete irrigation/operation logs','Genotype physiology and disease/pest/weed measurements'],
        'subsets_are_descriptive_only_primary_pairs_unchanged':True,'new_independent_accuracy':None}
    write_json(out/'report.json',report);return report
