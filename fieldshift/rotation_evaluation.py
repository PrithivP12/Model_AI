"""Independent rotation outcomes: evaluation only, never imported by model fitting.

The frozen crop-yield model is invariant to crop sequence. A separate fixed
diversification rule is tested transparently; it is not called learned ML accuracy.
"""
from pathlib import Path
from itertools import combinations
import hashlib
import json
import numpy as np
import pandas as pd
from .crop_performance import OUT, PROTOCOL

TEST=OUT.parent/'evaluation-only'
SEQUENCES={
    'CCC':['corn'],
    'CS':['corn','soybeans'],
    'COWwS':['corn','oats','winter_wheat','soybeans'],
    'CPWwS':['corn','peas','winter_wheat','soybeans'],
    'CSSwP':['corn','soybeans','spring_wheat','peas'],
    'CSSwSf':['corn','soybeans','spring_wheat','sunflower']
}
ADDENDUM={
    'original_protocol':'results/protocol.json remains unchanged',
    'fallback_reason':'Dryad catalogue is public, original download 403 and API download 401; no file obtained',
    'fallback_source':'USDA ARS / Ag Data Commons 10.15482/USDA.ADC/27207915.v1',
    'scope':'Brookings, South Dakota; one site, 2017–2020, four blocks, one four-year cycle',
    'when_added':'After yield model selection and final scoring, before computing external rotation pair metrics',
    'test_blinding':'Source abstract and workbook schema inspected; this is exploratory external evaluation, not a blind confirmatory test',
    'unchanged_rule':PROTOCOL['external_rotation_evaluation']['secondary_fixed_rule'],
    'primary_ml':'Sequence input absent: no differentiating predictions for the same site/year/crop under identical input history',
    'strict_pairing':'Same year, replication and exact applied nitrogen rate; standard Corn label only (CornHigh excluded)',
    'broad_pairing':'Same year and replication; includes CornHigh and differing nitrogen: management-system comparison, not isolated rotation effect',
    'outcomes_used_for_fit':False,'outcomes_used_for_tuning':False,
    'metric':'Conditional pairwise yield-direction agreement and decision coverage; ties abstain',
    'no_economic_claim':'Maize yield only; no full-cycle productivity, soil benefit, pollinator benefit or profit is predicted',
    'no_effect_size_claim':'Rule predicts order only; no nonzero yield bonus is calibrated'
}


def rule_score(sequence):
    if not sequence:raise ValueError('Empty crop sequence')
    return sum(c!='corn' for c in sequence)/len(sequence)


def make_pairs(data,strict):
    d=data[data.current_crop=='Corn'].copy() if strict else data.copy()
    group=['year','replication']+(['nitrogen_kg_ha'] if strict else [])
    rows=[]
    for key,g in d.groupby(group):
        for a,b in combinations(g.itertuples(),2):
            if a.rotation==b.rotation:raise ValueError('Duplicate rotation within comparison block')
            delta=rule_score(SEQUENCES[b.rotation])-rule_score(SEQUENCES[a.rotation])
            actual=b.yield_kg_ha-a.yield_kg_ha
            rows.append({'year':a.year,'replication':a.replication,'rotation_a':a.rotation,'rotation_b':b.rotation,
                         'nitrogen_a':a.nitrogen_kg_ha,'nitrogen_b':b.nitrogen_kg_ha,
                         'yield_a_kg_ha':a.yield_kg_ha,'yield_b_kg_ha':b.yield_kg_ha,
                         'observed_difference_Mg_ha':actual/1000,'predicted_ml_difference_Mg_ha':0.,
                         'rule_score_difference':delta,'rule_abstains':delta==0,
                         'observed_tie':abs(actual)<1e-8,
                         'rule_direction_correct':None if delta==0 or abs(actual)<1e-8 else bool(np.sign(delta)==np.sign(actual))})
    return pd.DataFrame(rows)


def summarize(pairs):
    decisions=pairs[~pairs.rule_abstains&~pairs.observed_tie]
    year=[]
    for _,g in decisions.groupby('year'):
        year.append(float(g.rule_direction_correct.astype(float).mean()))
    return {
        'pairs':len(pairs),'year_block_clusters':int(pairs[['year','replication']].drop_duplicates().shape[0]),
        'ml':{'sequence_decision_coverage':0.,'directional_accuracy':None,
              'reason':'No rotation treatment input or trained transition response; all same-context sequence predictions tie',
              'zero_rotation_effect_rmse_Mg_ha':float(np.sqrt(np.mean(pairs.observed_difference_Mg_ha**2))),
              'zero_rotation_effect_mae_Mg_ha':float(np.mean(abs(pairs.observed_difference_Mg_ha)))},
        'untrained_fixed_rule':{
            'decisive_non_tie_pairs':len(decisions),'score_ties':int(pairs.rule_abstains.sum()),
            'decision_coverage':float((~pairs.rule_abstains).mean()),
            'conditional_pairwise_agreement':float(decisions.rule_direction_correct.astype(float).mean()) if len(decisions) else None,
            'year_macro_agreement':float(np.mean(year)) if year else None,
            'by_year':{str(y):{'decisions':len(g),'agreement':float(g.rule_direction_correct.astype(float).mean())} for y,g in decisions.groupby('year')},
            'interpretation':'Descriptive exploratory agreement in one four-year experiment; pairs share plots, years and site and are not independent trials',
            'global_accuracy_established':False}
    }


def run():
    if not (OUT/'selection_frozen.json').exists() or not (OUT/'report.json').exists():
        raise RuntimeError('Freeze and score yield model before reading external rotation outcomes')
    addon=TEST/'evaluation_protocol_addendum.json'
    if addon.exists() and json.loads(addon.read_text())!=ADDENDUM:raise RuntimeError('Preserve existing external test protocol')
    addon.write_text(json.dumps(ADDENDUM,indent=2))
    path=TEST/'south_dakota_raw_values.csv'
    raw=pd.read_csv(path)
    raw.columns=raw.columns.str.strip()
    rename={'Year':'year','Experimental Plot Number':'plot','Replication':'replication',
            'Crop Rotation':'rotation','Current Crop':'current_crop','Crop Yield kgha':'yield_kg_ha',
            'Fertilizer Nitrogen kg/ha':'nitrogen_kg_ha'}
    d=raw.rename(columns=rename)[list(rename.values())]
    if d.duplicated(['year','plot']).any():raise ValueError('Duplicate experimental plot/year')
    d=d[d.current_crop.isin(['Corn','CornHigh'])].copy()
    for col in ['yield_kg_ha','nitrogen_kg_ha']:d[col]=pd.to_numeric(d[col],errors='raise')
    assert set(d.rotation)==set(SEQUENCES)
    assert len(d)==96 and d.yield_kg_ha.notna().all()
    strict=make_pairs(d,True);broad=make_pairs(d,False)
    strict.to_csv(TEST/'strict_pairs.csv',index=False);broad.to_csv(TEST/'management_system_pairs.csv',index=False)
    report={'source_doi':'10.15482/USDA.ADC/27207915.v1','used_in_training':False,'used_in_selection':False,
            'raw_sheet':'Master file, header row 7; 304 plot/year records',
            'raw_rows':len(raw),'maize_rows':len(d),'sites':1,'years':[2017,2018,2019,2020],
            'external_csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'model_selection_sha256':hashlib.sha256((OUT/'selection_frozen.json').read_bytes()).hexdigest(),
            'training_report_sha256':hashlib.sha256((OUT/'report.json').read_bytes()).hexdigest(),
            'strict_same_nitrogen':summarize(strict),'management_system_comparison':summarize(broad),
            'limitations':['The continuous-maize treatment is labeled CornHigh and has a different nitrogen regime; it is excluded from the strict comparison.',
                          'Equal applied nitrogen does not hold soil nitrogen supply constant across rotations.',
                          'The primary ML is structurally unable to rank same-crop rotation effects; zero effect is a diagnostic, not a fitted effect model.',
                          'The simple rule is not a NASA-informed or learned model and ties all four-year rotations.',
                          'One site and four years cannot support a reliable general crop-rotation accuracy percentage.',
                          'This trial evaluates maize harvests within rotations, not cumulative yields of all rotation crops.']}
    (TEST/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print(json.dumps(report,indent=2))
    return report


if __name__=='__main__':run()
