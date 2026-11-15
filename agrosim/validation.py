"""Recompute existing external checks without fitting against exposed tests."""
import json
import numpy as np
import pandas as pd
from .io import resolve,sha256


def validate(config,base):
    root=resolve(base,config['project_root'])
    protected={
        'rotation-pilot/results/selection_frozen.json':'fa630ccc8fe0cd081be840bcd241beee8eaf50782e2bc9506dd41e79ff546d69',
        'rotation-pilot/results/report.json':'00d20d47f2927ad86a77fff3106a7d6c6fc909b040dbd489ae1abf4410839074'}
    for name,expected in protected.items():
        if sha256(root/name)!=expected:raise ValueError('Protected legacy test artifact changed: '+name)
    lirf=json.loads((root/'crop-response-lirf/report.json').read_text())
    frozen=root/'crop-response-lirf/heldout_predictions_frozen.json'
    if sha256(frozen)!=lirf['heldout_predictions_sha256']:raise ValueError('LIRF frozen predictions changed')
    p=pd.DataFrame(json.loads(frozen.read_text()));y=np.array(lirf['observed_yields_dry_kg_ha'])
    checks={}
    for key,column in [('water_response','water_response_prediction'),('linear_water_baseline','linear_prediction'),('constant_baseline','mean_prediction')]:
        mae=float(np.mean(abs(y-p[column].to_numpy())))
        if abs(mae-lirf['heldout'][key]['mae_kg_dry_ha'])>1e-7:raise ValueError('Saved validation metric cannot be reproduced')
        checks[key]={'mae_kg_dry_ha':mae,'rows':len(y),'independent_sites':1}
    morrow=json.loads((root/'rotation-validation-v03/report.json').read_text())
    rotation=json.loads((root/'rotation-learning-v1/results/report.json').read_text())
    return {'status':'existing_evidence_recomputed','protected_test_artifacts_unchanged':True,
        'lirf_observed_season_response':checks,'morrow_legacy_engine_comparison':morrow['summary'],
        'cscap_legacy_learner_primary':rotation['test']['unseen_farms_future_year'],
        'legacy_cscap_model_comparison_caveat':'Ambiguous site-year fertilizer totals invalidate numerical management inputs to complex candidates. Selected family-only baseline does not use doses, but selection comparisons require the corrected new pipeline.',
        'current_depth_python_engine_independently_calibrated':False,'fresh_final_test_performed':False,
        'worldwide_rotation_accuracy_established':False,'recommendation':None,
        'limits':['These historical tests have already been exposed; no new model is tuned on them.',
            'LIRF validates a separate empirical water-response component, not the daily multi-year engine.',
            'Morrow checks an older JavaScript engine at one site; this does not validate the new Python depth variant.',
            'CSCAP baseline did not beat the overall mean baseline, and intervals made no decisive recommendations.']}
