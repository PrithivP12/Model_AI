"""Exact paired-error decomposition and conditional management/state diagnosis."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from ..io import write_json


def analyze_pairs(root,cross_dir,output):
    root=Path(root);cross=Path(cross_dir);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    pairs=pd.read_csv(cross/'paired_predictions.csv');actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    records=json.loads((root/'reports/rotation-recovery/reproduced-final/input-audit/records.json').read_text())
    rows=[];summary={}
    for variant in ['root_reference','plateau','plateau_fitted_RUE','plateau_training_energy_RUE']:
        pred=json.loads((cross/(variant+'_rotation_records.json')).read_text());states=json.loads((cross/(variant+'_rotation_states.json')).read_text())
        for pair in pairs.to_dict('records'):
            sides={}
            for side,col in [('reference','reference_records'),('candidate','candidate_records')]:
                ids=pair[col].split(';');sides[side]={'predicted':np.mean([pred[r]['prediction']for r in ids]),
                    'observed':np.mean([float(actual.loc[r,'observed_yield_dry_kg_ha'])for r in ids]),
                    'pretarget_water':np.mean([sum(s['pretarget_state']['water_mm'])for r in ids for s in states[r]]),
                    'pretarget_N':np.mean([sum(s['pretarget_state']['mineral_n_kg_ha'])for r in ids for s in states[r]]),
                    'pretarget_residue_C':np.mean([sum(s['pretarget_state']['residue_c_kg_ha'])+s['pretarget_state']['surface_residue_c_kg_ha']for r in ids for s in states[r]])}
                for role,index in [('current',-1),('previous',-2)]:
                    dose=[records[r]['timeline'][index]['fertilizer_total_if_recorded_complete']for r in ids]
                    sides[side][role+'_reported_N']=float(np.mean(dose))if all(x is not None for x in dose)else None
            a=sides['reference'];b=sides['candidate'];ea=a['predicted']-a['observed'];eb=b['predicted']-b['observed']
            contrast=b['predicted']-a['predicted'];observed=b['observed']-a['observed']
            error=contrast-observed
            if abs(error-(eb-ea))>1e-7 or abs(observed-pair['observed_difference'])>1e-7:raise ArithmeticError('Paired error identity failed')
            row={'variant':variant,'pair_id':pair['pair_id'],'farm':pair['farm_id'],'year':pair['year'],
                'reference_error':ea,'candidate_error':eb,'contrast_error':error,'common_mode_error':(ea+eb)/2,
                'observed_contrast':observed,'predicted_contrast':contrast,'wrong_sign':np.sign(contrast)!=np.sign(observed),
                'N_delta':b['pretarget_N']-a['pretarget_N'],'water_delta':b['pretarget_water']-a['pretarget_water'],
                'residue_C_delta':b['pretarget_residue_C']-a['pretarget_residue_C']}
            for role in ['current','previous']:
                key=role+'_reported_N';row[key+'_difference']=None if a[key]is None or b[key]is None else b[key]-a[key]
            rows.append(row)
        d=pd.DataFrame([r for r in rows if r['variant']==variant]);summary[variant]={
            'pair_count':len(d),'reference_yield_MAE':float(d.reference_error.abs().mean()),'candidate_yield_MAE':float(d.candidate_error.abs().mean()),
            'common_mode_error_MAE':float(d.common_mode_error.abs().mean()),'contrast_MAE':float(d.contrast_error.abs().mean()),
            'error_covariance':float(np.cov(d.reference_error,d.candidate_error,ddof=0)[0,1]),
            'error_correlation':float(d.reference_error.corr(d.candidate_error)),
            'exact_identity':'contrast error = candidate current-yield error minus reference current-yield error',
            'current_N_dose_difference_known':int(d.current_reported_N_difference.notna().sum()),
            'current_N_dose_difference_nonzero':int((d.current_reported_N_difference.fillna(0).abs()>1e-6).sum())}
    frame=pd.DataFrame(rows);frame.to_csv(out/'paired_error_components.csv',index=False)
    byfarm=[]
    for (variant,farm),g in frame.groupby(['variant','farm'],sort=True):
        byfarm.append({'variant':variant,'farm':farm,'pairs':len(g),'contrast_MAE':float(g.contrast_error.abs().mean()),'sign_agreement':float((~g.wrong_sign).mean()),
            'current_N_dose_difference_known':int(g.current_reported_N_difference.notna().sum())})
    pd.DataFrame(byfarm).to_csv(out/'farm_error_components.csv',index=False)
    report={'status':'paired_current_error_and_carryover_context_audited','models':summary,
        'cause_identified':False,'limitations':['Signed state differences are modeled, not measured carryover.','Common-mode cancellation explains why better absolute yield need not improve contrasts.','Management doses/availability are incomplete; dose differences do not identify a pure rotation effect.','Prior crop/grain/tissue errors remain in the previous biological-stage diagnostics with explicit timing and support.'],
        'observed_contrast_precision_source':'reports/rotation-recovery/reproduced-final/experimental_contrast_precision.csv; previously exposed descriptive replicate intervals, not refitted here.',
        'recommendation':None}
    write_json(out/'report.json',report);return report
