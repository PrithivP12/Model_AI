"""Frozen-cohort empirical evaluation; no target-driven feature/model search."""
from collections import Counter, defaultdict
import csv
from pathlib import Path
import json

import numpy as np

from ..ecology_v2.observations import metrics, read_development
from ..io import sha256, write_json
from .bayes import FEATURES, GROUPS, UNIT, HierarchicalNitrogenModel, plot_key
from .diagnostics import diagnose


def observation_operator(*, start, end, top_cm, bottom_cm, method, net_change_ug_g):
    """A closed-bag signed rate, not an open-column ecosystem flux."""
    from datetime import date
    from ..io import number
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if method != 'KBS_in_situ_buried_bag' or (top_cm, bottom_cm) != (0, 25) or days <= 0:
        raise ValueError('Incompatible incubation method, dates or soil-depth support')
    number(net_change_ug_g, 'net inorganic N change')
    return {'rate': net_change_ug_g / days, 'unit': UNIT, 'root_uptake_included': False,
            'fertilizer_rainfall_leaching_exchange_included': False, 'areal_flux': None}


def data_audit(root):
    source = root / 'reports/ecosystem-v2/raw/kbs_mineralization.csv'
    rows, quality = read_development(source, 'mineralization')
    # Correlated assay components are checked but never passed as predictors.
    composition = [r['netmineralization'] - r['netnitrification'] - r['netammonification'] for r in rows
                   if all(r.get(k) is not None for k in ['netmineralization', 'netnitrification', 'netammonification'])]
    allowed = ['source_row', 'source_sha256', 'date', 'end_date', 'duration_days', 'year', 'treatment',
               'replicate', 'station', 'group', 'fold', 'netmineralization']
    observations = [{k: r[k] for k in allowed} for r in rows if r['netmineralization'] is not None]
    prior = json.loads((root / 'reports/ecosystem-v2/verified-final/observations/development_observations.json').read_text())['buried_bags']
    old = {r['source_row']: r['netmineralization'] for r in prior if r['netmineralization'] is not None}
    if old != {r['source_row']: r['netmineralization'] for r in observations}:
        raise ValueError('Frozen V2 observation cohort or target changed')
    return observations, {'source_url': 'https://lter.kbs.msu.edu/datatables/56', 'raw_sha256': sha256(source),
        'unit': UNIT, 'depth_support': '0–25cm composite, documented table/protocol support; not a measured per-row depth covariate',
        'method': 'In-situ buried bags, moisture isolated and root excluded, actual start/end dates',
        'same_V2_observation_cohort': True, 'quality': quality,
        'missing_targets_excluded': len(rows) - len(observations),
        'component_identity_max_rounding_residual': float(max(abs(x) for x in composition)),
        'available_predictors': ['treatment', 'replicate/station identity', 'year', 'start calendar date', 'actual duration'],
        'unavailable_predictors': ['initial substrate C/N', 'independent microbial C/N', 'matched bag moisture',
                                   'matched bag temperature', 'matched initial mineral N', 'matched bulk density'],
        'excluded_target_leakage_columns': ['NetNitrification', 'NetAmmonification'],
        'areal_conversion_performed': False, 'final_outcomes_numerically_decoded': False,
        'publication_permission_obtained': False}


def sampling_diagnostics(model, training):
    d = model.diagnostics()
    # Check actual conditional expected-rate combinations, not coefficients only.
    representatives = [next(r for r in training if r['treatment'] == t) for t in sorted({r['treatment'] for r in training})]
    x, _ = model.design(representatives)
    p = len(model.metadata['coefficient_labels'])
    values = np.einsum('cdp,np->cdn', model.posterior[:, :, :p], x)
    pred = diagnose(values, ['expected_rate:' + r['treatment'] for r in representatives])
    d['conditional_mean_diagnostics'] = pred
    d['all_passed'] = d['all_passed'] and pred['all_passed']
    return d


def fit_certified(training, directory, *, seed=20261115):
    directory.mkdir(parents=True, exist_ok=False)
    attempts = []
    for warmup, draws in [(2000, 4000), (4000, 8000)]:
        model = HierarchicalNitrogenModel().fit(training, warmup=warmup, draws=draws, seed=seed)
        d = sampling_diagnostics(model, training)
        name = f'draws-{draws}'
        model.save(directory / name)
        write_json(directory / (name + '-diagnostics.json'), d)
        attempts.append({'warmup': warmup, 'draws': draws, 'certified': d['all_passed']})
        if d['all_passed']: break
    return model, d, attempts


def baseline_predictions(training, test):
    grand = float(np.mean([r['netmineralization'] for r in training]))
    values = defaultdict(list)
    for r in training: values[r['treatment']].append(r['netmineralization'])
    means = {k: float(np.mean(v)) for k, v in values.items()}
    output = {}
    for name in ['grand_mean', 'treatment_mean']:
        center = lambda r: grand if name == 'grand_mean' else means.get(r['treatment'], grand)
        residuals = [r['netmineralization'] - center(r) for r in training]
        lo, hi = np.quantile(residuals, [.05, .95])
        output[name] = [{'source_row': r['source_row'], 'point': center(r),
                         'posterior_predictive_90': [center(r) + float(lo), center(r) + float(hi)],
                         'interval_kind': 'training_residual_envelope_not_Bayesian_or_conformal'} for r in test]
    return output


def score(rows, predictions):
    y = np.array([r['netmineralization'] for r in rows]); p = np.array([r['point'] for r in predictions])
    result = metrics(y, p)
    intervals = np.array([r['posterior_predictive_90'] for r in predictions])
    covered = (intervals[:, 0] <= y) & (y <= intervals[:, 1])
    result.update(predictive_90_coverage=float(covered.mean()), mean_predictive_90_width=float(np.diff(intervals, axis=1).mean()))
    result['sign_subsets'] = {}
    for label, mask in [('negative', y < 0), ('zero', y == 0), ('positive', y > 0)]:
        if mask.any():
            subset = metrics(y[mask], p[mask]); subset['coverage_90'] = float(covered[mask].mean())
            subset['predicted_negative_fraction'] = float(np.mean(p[mask] < 0))
        else: subset = {'n': 0, 'MAE': None, 'RMSE': None, 'bias': None, 'coverage_90': None}
        result['sign_subsets'][label] = subset
    for key in ['year', 'treatment']:
        result['by_' + key] = {}
        for value in sorted({r[key] for r in rows}):
            ix = np.array([r[key] == value for r in rows])
            result['by_' + key][str(value)] = {**metrics(y[ix], p[ix]), 'coverage_90': float(covered[ix].mean())}
    if 'probability_negative_net_rate' in predictions[0]:
        q = np.array([r['probability_negative_net_rate'] for r in predictions])
        result['negative_event_Brier_score'] = float(np.mean((q - (y < 0)) ** 2))
        mean_intervals = np.array([r['conditional_mean_90'] for r in predictions])
        result['mean_conditional_mean_90_width'] = float(np.diff(mean_intervals, axis=1).mean())
        result['mean_variance_components'] = {'parameter': float(np.mean([r['parameter_variance'] for r in predictions])),
            'unseen_year': float(np.mean([r['unseen_group_variance']['year'] for r in predictions])),
            'unseen_plot': float(np.mean([r['unseen_group_variance']['plot'] for r in predictions])),
            'unseen_treatment': float(np.mean([r['unseen_group_variance']['treatment'] for r in predictions])),
            'residual': float(np.mean([r['residual_variance'] for r in predictions]))}
    return result


def paired_uncertainty(rows, candidate, baseline, draws=2000):
    diff = np.array([abs(c['point'] - r['netmineralization']) - abs(b['point'] - r['netmineralization'])
                     for r, c, b in zip(rows, candidate, baseline)])
    years = sorted({r['year'] for r in rows}); rng = np.random.default_rng(314159)
    nested, crossed = [], []
    blocks = [('MCSE:' + r['replicate']) if r['treatment'] in {'T' + str(i) for i in range(1, 9)}
              else r['group'] for r in rows]
    block_ids = sorted(set(blocks))
    for _ in range(draws):
        ix = []
        for year in rng.choice(years, len(years), replace=True):
            plots = sorted({r['group'] for r in rows if r['year'] == year})
            for group in rng.choice(plots, len(plots), replace=True):
                ix.extend(i for i, r in enumerate(rows) if r['year'] == year and r['group'] == group)
        nested.append(float(np.mean(diff[ix])))
        yw = dict(zip(years, rng.multinomial(len(years), np.ones(len(years)) / len(years))))
        bw = dict(zip(block_ids, rng.multinomial(len(block_ids), np.ones(len(block_ids)) / len(block_ids))))
        weights = np.array([yw[r['year']] * bw[b] for r, b in zip(rows, blocks)])
        if weights.sum(): crossed.append(float(np.average(diff, weights=weights)))
    return {'candidate_minus_baseline_MAE': float(diff.mean()),
            'year_then_plot_bootstrap_95': [float(v) for v in np.quantile(nested, [.025, .975])],
            'crossed_year_replicate_block_bootstrap_95': [float(v) for v in np.quantile(crossed, [.025, .975])],
            'development_year_count': len(years), 'site_count': 1,
            'limitations': 'Only five exposed development years; conditional within-site uncertainty, not regional transfer or a causal treatment effect.'}


def run(root, output):
    root = Path(root).resolve(); output = Path(output); output.mkdir(parents=True, exist_ok=False)
    observations, audit = data_audit(root)
    train = [r for r in observations if r['fold'] == 'training']; dev = [r for r in observations if r['fold'] == 'development']
    write_json(output / 'observation_audit.json', audit)
    write_json(output / 'observations.json', observations)
    protocol = root / 'reports/ecosystem-v21/protocol_frozen.json'
    write_json(output / 'protocol.json', json.loads(protocol.read_text()))
    write_json(output / 'sampling_amendment.json', json.loads((root / 'reports/ecosystem-v21/sampling_amendment.json').read_text()))
    model, diagnostic, attempts = fit_certified(train, output / 'primary-fit')
    # Freeze the complete candidate before its first new development prediction.
    write_json(output / 'candidate_frozen.json', {'protocol_sha256': sha256(protocol),
        'training_record_ids': model.metadata['training_record_ids'], 'sampling_certified': diagnostic['all_passed'],
        'development_used_for_fit_or_selection': False, 'final_outcomes_opened': False,
        'code_sha256': {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        'posterior_sha256': sha256(output / 'primary-fit' / f'draws-{attempts[-1]["draws"]}' / 'posterior.npy'),
        'metadata_sha256': sha256(output / 'primary-fit' / f'draws-{attempts[-1]["draws"]}' / 'model.json')})
    predictions = baseline_predictions(train, dev); predictions['Bayesian'] = model.predict(dev)
    scores = {name: score(dev, pred) for name, pred in predictions.items()}
    # Reproduce exact previously reported grand/treatment means on the same rows.
    old = json.loads((root / 'reports/ecosystem-v2/verified-final/observations/audit.json').read_text())['baselines']['netmineralization']['models']
    for name, previous in [('grand_mean', 'training_mean'), ('treatment_mean', 'training_treatment_mean')]:
        for key in ['MAE', 'RMSE', 'bias']:
            if abs(scores[name][key] - old[previous][key]) > 1e-12:
                raise AssertionError('Historical baseline/cohort changed')
    uncertainty = {name: paired_uncertainty(dev, predictions['Bayesian'], predictions[name]) for name in ['grand_mean', 'treatment_mean']}
    write_json(output / 'development_predictions.json', {name: [{'observed': r['netmineralization'], **p} for r, p in zip(dev, pred)] for name, pred in predictions.items()})
    # Internal new-plot checks use only the original training period. They are
    # diagnostics, never a model or prior selection criterion.
    assignment = {}
    for t in sorted({r['treatment'] for r in train}):
        for i, key in enumerate(sorted({plot_key(r) for r in train if r['treatment'] == t})):
            assignment[key] = i % 3
    cv = []
    for fold in range(3):
        fitting = [r for r in train if assignment[plot_key(r)] != fold]
        held = [r for r in train if assignment[plot_key(r)] == fold]
        if {plot_key(r) for r in fitting} & {plot_key(r) for r in held}: raise AssertionError('Plot leakage')
        m, d, a = fit_certified(fitting, output / f'training-new-plot-fold-{fold}', seed=20261115 + fold + 1)
        pred = baseline_predictions(fitting, held); pred['Bayesian'] = m.predict(held, seed=8675309 + fold)
        cv.append({'fold': fold, 'fitting_rows': len(fitting), 'held_rows': len(held),
                   'held_plot_ids': sorted({plot_key(r) for r in held}),
                   'scores': {name: score(held, p) for name, p in pred.items()},
                   'sampling_certified': d['all_passed'], 'attempts': a,
                   'scope': 'new plots within observed training years; no independent-site or future-year claim'})
    write_json(output / 'training_plot_group_checks.json', cv)
    useful = (diagnostic['all_passed'] and all(f['sampling_certified'] for f in cv)
        and uncertainty['treatment_mean']['year_then_plot_bootstrap_95'][1] < 0
        and uncertainty['treatment_mean']['crossed_year_replicate_block_bootstrap_95'][1] < 0
        and scores['Bayesian']['RMSE'] <= 1.05 * scores['treatment_mean']['RMSE']
        and .85 <= scores['Bayesian']['predictive_90_coverage'] <= .98)
    report = {'status': 'empirically_tested_single_site_offline_nitrogen_process_model',
        'target': 'net soil N mineralization in isolated KBS buried bags', 'unit': UNIT,
        'training_n': len(train), 'development_n': len(dev), 'scores': scores, 'paired_uncertainty': uncertainty,
        'primary_sampling': {'certified': diagnostic['all_passed'], 'attempts': attempts,
                             'maximum_Rhat': diagnostic['maximum_Rhat'], 'minimum_bulk_ESS': diagnostic['minimum_bulk_ESS'],
                             'minimum_tail_ESS': diagnostic['minimum_tail_ESS']},
        'training_group_checks': cv, 'useful_development_improvement_gate_passed': useful,
        'retained_estimator': 'Bayesian_offline_candidate' if useful else 'training_treatment_mean',
        'mechanistic_comparison': {'observable_operator': 'Actual-duration0–25cm root-excluded moisture-isolated net inorganic N change/days',
            'prediction_error': None, 'reason': 'No independently matched initial substrate/microbial C/N, bag moisture/temperature and soil mass. No target inversion or arbitrary initialization.',
            'areal_flux': None, 'V2_microbes_remain_experimental': True},
        'simulation_interface': {'mode': 'offline_process_analysis_only', 'coupling_enabled': False,
            'soil_N_injection_enabled': False, 'reason': 'Calendar/treatment/history are insufficient environmental/material inputs for a free-running conserved soil flux.'},
        'independent_final_test_evaluated': False, 'global_transfer_established': False,
        'recommendation': None,
        'limitations': ['Development2010–14 was already exposed in V2. Frozen model evaluation is not a pristine final test.',
            'One station and only seven supported training years; treatment includes different land uses and is not a causal agronomic N credit.',
            'Plot reuse supports known-plot temporal prediction, not held-out farm generalization. Internal folds test new plots in known years only.',
            'Calendar season is a proxy, not measured moisture or temperature; duration is known assay timing.',
            'Gaussian residuals and limited extreme events can misrepresent tails and negative mineralization; negative-subset failures remain visible.',
            'Posterior intervals describe this statistical hierarchy and observation variability, not validated physical process uncertainty or global probabilities.',
            'No training, calibration or forecast uses simulator-generated labels. Reserved later numeric outcomes remain unopened.']}
    write_json(output / 'report.json', report)
    flat = []
    for name, pred in predictions.items():
        for row, p in zip(dev, pred):
            flat.append({'model': name, 'source_row': row['source_row'], 'date': row['date'], 'year': row['year'],
                'treatment': row['treatment'], 'plot': plot_key(row), 'observed': row['netmineralization'],
                'predicted': p['point'], 'lower90': p['posterior_predictive_90'][0], 'upper90': p['posterior_predictive_90'][1], 'unit': UNIT})
    with (output / 'development_predictions.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat[0])); writer.writeheader(); writer.writerows(flat)
    write_json(output / 'artifact_manifest.json', {'sha256': {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}})
    return report
