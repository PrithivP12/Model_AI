from copy import deepcopy
from .io import load_config, resolve
from .simulation import compare


def sensitivity(config,base):
    original,sim_base=load_config(resolve(base,config['simulation_config']))
    cases=[('base',original)]
    for name,stress in [('drought',{'rain_multiplier':.5}),('heat',{'temperature_delta_C':4}),
                        ('excess_rain',{'rain_multiplier':2})]:
        changed=deepcopy(original);changed['stress_test']=stress;cases.append((name,changed))
    if 'layers'in original['soil']:
        for depth in [.3,1.]:
            changed=deepcopy(original);changed['soil']['layers'][-1]['root_penetrability']=depth
            cases.append((f'deep_root_penetrability_{depth}',changed))
    for scale in [.75,1.25]:
        changed=deepcopy(original)
        if 'initial_state'in changed['soil']:
            changed['soil']['initial_state']['mineral_n_kg_ha']=[v*scale for v in changed['soil']['initial_state']['mineral_n_kg_ha']]
        else:
            for h in changed['soil']['horizons']:h['mineral_n_mg_N_kg']*=scale
        cases.append((f'initial_mineral_n_{scale}',changed))
    changed=deepcopy(original)
    for m in changed['management_by_crop'].values():m['fertilizer_n_kg_ha']*=.5
    cases.append(('half_fertilizer',changed))
    rows=[]
    for name,scenario in cases:
        result=compare(scenario,sim_base)
        rows.append({'case':name,'summaries':result['summaries'],'contrasts':result['contrasts'],
            'max_balance_error':{pool:max(r['simulation']['max_daily_balance_error'][pool]for r in result['runs'])
                                 for pool in ['water','nitrogen','carbon']}})
    return {'status':'uncalibrated_sensitivity_experiment','design':'deterministic one-factor-at-a-time; common climate scenarios',
        'cases':rows,'confidence_intervals':None,'recommendation':None,
        'limitations':['Perturbation magnitudes are assumed stress tests, not probabilities or field-calibrated parameter distributions.',
            'No disease or genotype stress test is claimed without a supported response model.']}
