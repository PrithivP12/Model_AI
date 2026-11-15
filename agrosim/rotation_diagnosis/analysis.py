"""Per-pair state paths, effect calibration and assumption envelopes, no fit."""
from pathlib import Path
import gzip,json
import numpy as np
import pandas as pd
from ..io import write_json
from ..rotation_recovery.experiment import scores
from .audit import load
from .panel import SPECS


def analyze(root,panel_dir,out):
    root=Path(root);panel_dir=Path(panel_dir);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    fixed,records,_,old,_=load(root);paired=pd.read_csv(panel_dir/'paired_predictions.csv')
    links=json.loads((panel_dir/'run_references.json').read_text())
    with gzip.open(panel_dir/'unique_runs.json.gz','rt')as f:cache=json.load(f)
    rows=[];memory_for_quality=[]
    for rid in sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode())):
        for n in ['40','120']:memory_for_quality.append({'record_id':rid,'harvests':cache[links['baseline:'+rid][n]]['harvests']})
    for p in fixed.to_dict('records'):
        arms={name:p[col].split(';')for name,col in [('reference','reference_records'),('candidate','candidate_records')]}
        if any(len(ids)!=1 for ids in arms.values()):raise ValueError('Independent-arm state envelope requires explicit grouped-arm handling')
        ids={name:items[0]for name,items in arms.items()};ys={};states={}
        for side,rid in ids.items():
            variants=[cache[links['baseline:'+rid][n]]for n in ['40','120']];ys[side]=[v['prediction']for v in variants]
            expected=float(json.loads((panel_dir/'baseline_record_predictions.json').read_text())[rid]['prediction'])
            if abs(np.mean(ys[side])-expected)>1e-8:raise AssertionError('Diagnostic state links do not reproduce treatment yields')
            target=records[rid]['year'];sow=str(records[rid]['timeline'][-1]['planting_date']or old[rid]['target_sowing'])
            before=str((pd.Timestamp(sow)-pd.Timedelta(days=1)).date())
            preseason=[]
            for v in variants:
                samples=[r for seg in v['segments']for r in seg.get('sampled_end_of_day_states',[])if r['date']==before]
                preseason.append(samples[0]if samples else None)
            states[side]={'Jan1_water':np.mean([sum(v['Jan1_state']['water_mm'])for v in variants]),
                'Jan1_N':np.mean([sum(v['Jan1_state']['mineral_n_kg_ha'])for v in variants]),
                'preplant_water':None if any(r is None for r in preseason)else float(np.mean([r['soil_water_mm']for r in preseason])),
                'preplant_N':None if any(r is None for r in preseason)else float(np.mean([r['soil_mineral_n_kg_ha']for r in preseason]))}
        combined=np.array([[b-a for a in ys['reference']]for b in ys['candidate']])
        row={'pair_id':p['pair_id'],'farm':p['farm_id'],'observed_contrast':p['observed_difference'],
            'shared_N40_contrast':ys['candidate'][0]-ys['reference'][0],
            'shared_N120_contrast':ys['candidate'][1]-ys['reference'][1],
            'independent_arm_N_min':float(combined.min()),'independent_arm_N_max':float(combined.max()),
            'independent_arm_N_can_reverse':bool(combined.min()< -1e-6 and combined.max()>1e-6)}
        for key in states['reference']:
            a,b=states['reference'][key],states['candidate'][key]
            row['candidate_minus_reference_'+key]=None if a is None or b is None else float(b-a)
        rows.append(row)
    state=pd.DataFrame(rows);state.to_csv(out/'paired_state_paths_and_N_envelopes.csv',index=False)
    # Descriptive effect-magnitude calibration only. The slope is NOT a learned
    # correction or multiplier and is never passed back into a simulator.
    summaries={}
    for name in ['reference','source_sink','baseline','preserve_known_partial_prior_N']:
        y=paired.observed_difference.to_numpy();pred=paired[name].to_numpy();variance=np.var(pred)
        summaries[name]={**scores(paired,name),'observed_mean':float(y.mean()),'predicted_mean':float(pred.mean()),
            'observed_mean_absolute_effect':float(np.mean(abs(y))),'predicted_mean_absolute_effect':float(np.mean(abs(pred))),
            'observed_SD':float(np.std(y)),'predicted_SD':float(np.std(pred)),
            'correlation':float(np.corrcoef(y,pred)[0,1])if variance>0 else None,
            'descriptive_observed_on_prediction_slope':float(np.mean((pred-pred.mean())*(y-y.mean()))/variance)if variance>0 else None,
            'slope_used_as_correction':False}
    panel=pd.read_csv(panel_dir/'ranking_sensitivity.csv')
    panel['model_assumption_class']=np.select([panel.assumption_min>1e-6,panel.assumption_max< -1e-6,panel.assumption_sign_flip],
        ['stable_positive','stable_negative','reverses'],default='includes_abstention')
    panel['recommendation_supported']=False;panel.to_csv(out/'decision_support_classes.csv',index=False)
    masks=panel.groupby('model_assumption_class').size().to_dict()
    report={'status':'signed_errors_state_paths_and_envelopes_audited','calibration_descriptions':summaries,
        'shared_start_N_sign_reversals':int(((state.shared_N40_contrast*state.shared_N120_contrast)< -1e-12).sum()),
        'independent_start_N_sign_reversals':int(state.independent_arm_N_can_reverse.sum()),
        'N_envelope_semantics':'All four existing40/120kg/ha arm combinations, with no assumed treatment-state correlation. Not a probability, interval coverage claim or inferred soil-N credit.',
        'assumption_classes':masks,'all_field_recommendations_unsupported':True,
        'state_difference_means':{c:float(state[c].mean())for c in state if c.startswith('candidate_minus_reference_')},
        'maximum_field_rotation_memory_known':False,'new_independent_accuracy':None}
    from .quality import quality_audit
    quality_audit(root,out/'stable-production-quality',memory=memory_for_quality,model_identifier='stable_reference')
    write_json(out/'report.json',report);return report
