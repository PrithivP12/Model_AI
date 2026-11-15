"""Frozen direct-residue baseline evaluation; no final-study fitting or scoring."""
from collections import defaultdict
from pathlib import Path
import csv
import json

import numpy as np
from scipy.optimize import minimize_scalar

from ..io import sha256, write_json
from ..processes import Processes
from .data import recover


def thermal_days(rows, capped=False):
    temperature = np.array([r['temperature_C'] for r in rows])
    factor = 2. ** ((temperature - 20.) / 10.)
    if capped: factor = np.where(temperature > 0, np.minimum(factor, 4.), 0.)
    return np.array([r['time_days'] for r in rows]) * factor


def equal_group_loss(values, rows):
    return float(np.mean([np.mean([v for v, r in zip(values, rows) if r['group'] == g]) for g in sorted({r['group'] for r in rows})]))


def fit_first_order(training):
    if not training or any(r['fold'] != 'training' for r in training):
        raise ValueError('Baseline fit accepts only frozen training study groups')
    y = np.array([r['observed_direct_residue_C_fraction'] for r in training]); time = thermal_days(training)
    objective = lambda logk: equal_group_loss((np.exp(-np.exp(logk) * time) - y) ** 2, training)
    fit = minimize_scalar(objective, bounds=(np.log(1e-6), np.log(.3)), method='bounded',
                          options={'maxiter': 200, 'xatol': 1e-9})
    if not fit.success: raise ArithmeticError('Single-rate training fit failed')
    return {'k20_per_day': float(np.exp(fit.x)), 'training_study_equal_MSE': float(fit.fun),
            'optimizer_converged': bool(fit.success), 'evaluations': int(fit.nfev),
            'training_sources': sorted({r['source_id'] for r in training}, key=int),
            'training_groups': sorted({r['group'] for r in training}), 'Q10_fixed': 2.,
            'temperature_response_estimated_from_controlled_contrasts': False,
            'parameter_is_biological_truth': False}


def scores(rows, prediction):
    observed = np.array([r['observed_direct_residue_C_fraction'] for r in rows]); e = np.array(prediction) - observed
    overall = {'study_equal_MAE': equal_group_loss(abs(e), rows),
               'study_equal_RMSE': float(np.sqrt(equal_group_loss(e * e, rows))),
               'study_equal_bias': equal_group_loss(e, rows),
               'row_MAE': float(np.mean(abs(e))), 'row_RMSE': float(np.sqrt(np.mean(e * e))),
               'n_rows': len(rows), 'n_study_families': len({r['group'] for r in rows})}
    for label, getter, bins in [
        ('CN', lambda r: r['residue_CN'], [('<=25', -np.inf, 25), ('25–60', 25, 60), ('>60', 60, np.inf)]),
        ('temperature', lambda r: r['temperature_C'], [('<15', -np.inf, 14.999999), ('15–25', 14.999999, 25), ('>25', 25, np.inf)])]:
        overall['by_' + label] = {}
        for name, lo, hi in bins:
            indices = [i for i, r in enumerate(rows) if lo < getter(r) <= hi]
            overall['by_' + label][name] = ({'n_rows': len(indices), 'n_groups': len({rows[i]['group'] for i in indices}),
                'study_equal_MAE': equal_group_loss(abs(e[indices]), [rows[i] for i in indices]),
                'study_equal_bias': equal_group_loss(e[indices], [rows[i] for i in indices])} if indices else
                {'n_rows': 0, 'study_equal_MAE': None, 'study_equal_bias': None})
    overall['by_source'] = {}
    for source in sorted({r['source_id'] for r in rows}, key=int):
        ix = np.array([r['source_id'] == source for r in rows]); err = e[ix]
        overall['by_source'][source] = {'n': int(ix.sum()), 'MAE': float(abs(err).mean()),
                                      'RMSE': float(np.sqrt(np.mean(err * err))), 'bias': float(err.mean())}
    return overall


def carbon_partition(remaining):
    """A normalized tracer accounting identity, NOT an inferred respiration rate."""
    if not np.isfinite(remaining) or not 0 <= remaining <= 1:
        raise ValueError('Invalid remaining tracer fraction')
    return {'initial_residue_C_fraction': 1., 'remaining_residue_C_fraction': float(remaining),
            'transformed_or_exported_unresolved_C_fraction': float(1 - remaining),
            'carbon_accounting_error': float(remaining + (1 - remaining) - 1),
            'CO2_C_fraction': None, 'MAOC_fraction': None, 'microbial_C_fraction': None,
            'residue_N_prediction': None, 'physical_soil_transfer_enabled': False}


def paired_study_uncertainty(rows, prediction, reference):
    groups = sorted({r['group'] for r in rows})
    d = np.abs(np.array(prediction) - [r['observed_direct_residue_C_fraction'] for r in rows]) - np.abs(
        np.array(reference) - [r['observed_direct_residue_C_fraction'] for r in rows])
    values = np.array([np.mean([v for v, r in zip(d, rows) if r['group'] == g]) for g in groups])
    result = {'study_equal_MAE_difference': float(values.mean()), 'independent_group_count': len(groups),
              'bootstrap95': None, 'uncertainty_identifiable': False}
    if len(groups) >= 2:
        rng = np.random.default_rng(20261115)
        draws = rng.choice(values, size=(2000, len(values)), replace=True).mean(axis=1)
        result.update(bootstrap95=np.quantile(draws, [.025, .975]).tolist(), uncertainty_identifiable=True)
    else: result['reason'] = 'One held-out study family cannot estimate between-study variability; a degenerate bootstrap interval would be misleading.'
    return result


def run(root, output):
    root = Path(root); output = Path(output); output.mkdir(parents=True, exist_ok=False)
    phase = root / 'reports/ecosystem-v23'
    rows, audit = recover(root, output / 'data')
    training = [r for r in rows if r['fold'] == 'training']; development = [r for r in rows if r['fold'] == 'development']
    if not training or not development: raise ValueError('No eligible train/development residue observations; numerical scoring unavailable')
    if {r['group'] for r in training} & {r['group'] for r in development}: raise AssertionError('Study-family leakage')
    fit = fit_first_order(training); write_json(output / 'first_order_fit_frozen.json', fit)
    constant = equal_group_loss([r['observed_direct_residue_C_fraction'] for r in training], training)
    groups_with_times = {fold: [g for g in audit['fold_groups'][fold] if len(audit['group_times'][g]) >= 2]
                        for fold in ['training', 'development']}
    candidate_identifiable = len(groups_with_times['training']) >= 3 and len(groups_with_times['development']) >= 2
    # The exact frozen gate fails for the supplied archive. Do not quietly fit
    # another outcome/aggregation, change the split, or unseal final studies.
    if candidate_identifiable:
        raise ValueError('This bounded baseline-only replay is frozen for the insufficient-group branch; additional candidate work needs a new pre-outcome protocol')
    predictions, metrics = [], {}
    for fold, subset in [('training', training), ('development', development)]:
        time = thermal_days(subset)
        model = {'persistence': np.ones(len(subset)), 'training_constant': np.full(len(subset), constant),
            'fitted_first_order': np.exp(-fit['k20_per_day'] * time),
            'AgroSim_optimal_nonNlimited_projection': np.exp(-Processes().residue_decay_per_day_20C * thermal_days(subset, capped=True))}
        metrics[fold] = {name: scores(subset, pred) for name, pred in model.items()}
        for name, pred in model.items():
            for row, value in zip(subset, pred):
                predictions.append({**row, 'model': name, 'predicted_fraction': float(value),
                    'balance': carbon_partition(float(value)),
                    'projection_remaining_range_unknown_modifiers': [float(value), 1.] if name.startswith('AgroSim') else None})
    # Training-only grouped sensitivity exposes variation, without creating
    # row-level pseudo-replication or replacing the fixed development cohort.
    checks = []
    for group in sorted({r['group'] for r in training}):
        fit_rows = [r for r in training if r['group'] != group]; held = [r for r in training if r['group'] == group]
        fitted = fit_first_order(fit_rows)
        pred = np.exp(-fitted['k20_per_day'] * thermal_days(held))
        checks.append({'held_training_group': group, 'fit_groups': fitted['training_groups'],
                       'fitted_k20': fitted['k20_per_day'], 'metrics': scores(held, pred),
                       'scope': 'Training-only study-group sensitivity, not independent final validation'})
    dev_pred = [p['predicted_fraction'] for p in predictions if p['fold'] == 'development' and p['model'] == 'fitted_first_order']
    dev_project = [p['predicted_fraction'] for p in predictions if p['fold'] == 'development' and p['model'].startswith('AgroSim')]
    report = {'status': 'direct_residue_C_baseline_evaluation_candidate_gate_failed',
        'unit': 'fraction of originally added residue C', 'target': 'Direct separated litter C, never POC/MAOC substitution',
        'fit': fit, 'metrics': metrics, 'training_group_sensitivity': checks,
        'fitted_vs_frozen_projection': paired_study_uncertainty(development, dev_pred, dev_project),
        'candidate': {'evaluated': False, 'error': None, 'gate_passed': False,
            'eligible_training_trajectory_groups': groups_with_times['training'],
            'eligible_development_trajectory_groups': groups_with_times['development'],
            'reason': 'Frozen gate requires at least3training and2development study families with multiple times. Only one development family with a single150day endpoint; no nonlinear candidate or identifiable temperature-response fit.'},
        'existing_full_simulator_error': None,
        'existing_projection_scope': 'Same frozen first-order rate/Q10, nonNlimited optimal-moisture assumption only; unobserved environmental/N modifiers bound remainingC between projection and1. Not an initialized AgroSim lab experiment.',
        'residue_N_trajectory_error': None, 'observed_soil_fraction_errors': None,
        'unsupported_relabelings': ['humus is not MAOM', 'residue is not combined residue+POC', '1-remaining is not measured CO2',
                                   'residue-derived isotope N is not total mineral-N immobilization'],
        'final_numeric_outcomes_opened': False, 'independent_process_acceptance': False,
        'candidate_promoted': False, 'stable_defaults_changed': False, 'coupling_activated': False,
        'decision': 'Requires more compatible independent study trajectories; no new process candidate earned promotion.',
        'temperature_response_limit': 'Q10=2 fixed from existing assumption. Source-level incubation temperatures are confounded with study/residue; no controlled held-out temperature contrasts identify a new response.',
        'secondary_dataset_used': False,
        'limitations': ['Only3training families and1development family after strict lab/isotope/direct-residue support; repeated rows are not independent studies.',
            'Study15 development has3different residues at150days only; endpoint prediction is not temporal-trajectory validation.',
            'Growth efficiency, respiration, DOC/POM/MAOM transfers and N immobilization are not identifiable from direct residue disappearance alone.',
            'Collation uses operationally different fractionation methods; tracer fractions can overlap and are not jointly forced tosum1.',
            'No missing moisture/mineral-N/microbial stocks are fabricated. Relative input1 only establishes a normalized tracer denominator, not initial field soil.',
            'Plant/grass residues beyond executable crops are experimental materials, not new crop simulation profiles or global rotation claims.',
            'No final reserved studies were used for fitting, selection or scoring; insufficiency does not authorize moving them into development.']}
    write_json(output / 'report.json', report)
    write_json(output / 'predictions.json', predictions)
    write_json(output / 'protocol.json', json.loads((phase / 'protocol_frozen.json').read_text()))
    with (output / 'predictions.csv').open('w') as stream:
        keys = ['source_data_row', 'source_id', 'source_citation', 'group', 'fold', 'treatment_id', 'residue', 'residue_CN',
                'temperature_C', 'time_days', 'observed_direct_residue_C_fraction', 'model', 'predicted_fraction']
        writer = csv.DictWriter(stream, fieldnames=keys); writer.writeheader()
        writer.writerows({key: p[key] for key in keys} for p in predictions)
    write_json(output / 'implementation_manifest.json', {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    write_json(output / 'artifact_manifest.json', {'sha256': {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}})
    return report
