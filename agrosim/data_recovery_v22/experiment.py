"""One fixed three-predictor ridge diagnostic within the old training archive."""
from collections import defaultdict
from pathlib import Path
import json
import numpy as np

from ..ecology_v2.observations import metrics
from ..io import write_json, sha256

FEATURES = ['log1p_initial_nitrate', 'log1p_initial_ammonium', 'initial_gravimetric_water']


def block(row):
    return ('MCSE:' + row['replicate']) if row['treatment'] in {'T' + str(i) for i in range(1, 9)} else row['group']


def raw_features(row):
    values = [row.get('nitrate_ug_g'), row.get('ammonium_ug_g'), row.get('water_g_g')]
    if any(v is None or not np.isfinite(v) or v < 0 for v in values):
        raise ValueError('All three independently recorded initial predictors required; no filling')
    return np.array([np.log1p(values[0]), np.log1p(values[1]), values[2]])


class ResidualRidge:
    def fit(self, rows, *, alpha=10.):
        if not rows or any(r['year'] != 2001 or r['fold'] != 'training' for r in rows):
            raise ValueError('Diagnostic fit must use only complete original-training2001 observations')
        if alpha != 10.: raise ValueError('Frozen one-candidate ridge alpha is10; no parameter search')
        identities = [tuple(r['join_key']) for r in rows]
        if len(identities) != len(set(identities)): raise ValueError('Duplicate measurement identities')
        x = np.array([raw_features(r) for r in rows]); y = np.array([r['netmineralization'] for r in rows])
        groups = {t: [i for i, r in enumerate(rows) if r['treatment'] == t] for t in {r['treatment'] for r in rows}}
        self.grand_mean = float(y.mean()); self.grand_x = x.mean(axis=0)
        self.means = {t: float(y[ix].mean()) for t, ix in groups.items()}
        self.centers = {t: x[ix].mean(axis=0) for t, ix in groups.items()}
        residual_x = np.array([v - self.centers[r['treatment']] for r, v in zip(rows, x)])
        self.scale = residual_x.std(axis=0); self.scale[self.scale < 1e-12] = 1.
        z = residual_x / self.scale
        residual_y = y - np.array([self.means[r['treatment']] for r in rows])
        self.beta = np.linalg.solve(z.T @ z + alpha * np.eye(3), z.T @ residual_y)
        self.training_ids = [r['source_row'] for r in rows]
        return self

    def predict(self, row):
        center = self.centers.get(row['treatment'], self.grand_x)
        return self.means.get(row['treatment'], self.grand_mean) + float(((raw_features(row) - center) / self.scale) @ self.beta)

    def artifact(self):
        return {'kind': 'fixed_within_treatment_residual_ridge', 'alpha': 10., 'features': FEATURES,
            'coefficient_per_training_residual_feature_SD': self.beta.tolist(), 'feature_scale': self.scale.tolist(),
            'training_feature_centers': {k: v.tolist() for k, v in sorted(self.centers.items())},
            'training_treatment_means': dict(sorted(self.means.items())), 'training_record_ids': self.training_ids,
            'training_grand_mean': self.grand_mean, 'unit': 'microgram N/g dry soil/day',
            'source_initial_N_inferred_from_target': False, 'simulator_coupling_enabled': False,
            'physical_or_causal_coefficients': False}


def estimate(model, row, name):
    if name == 'grand_mean': return model.grand_mean
    if name == 'treatment_mean': return model.means.get(row['treatment'], model.grand_mean)
    return model.predict(row)


def evaluate_recovery(join_directory, output, protocol_path):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    joined = json.loads((Path(join_directory) / 'joined_observations.json').read_text())
    train = [r for r in joined if r['year'] == 2001 and r['complete_new_predictors']]
    test = [r for r in joined if r['year'] == 2002 and r['complete_new_predictors']]
    protocol = json.loads(Path(protocol_path).read_text())
    if not protocol['decision_after_covariate_audit_before_candidate_fit']:
        raise ValueError('Evaluation protocol must be frozen before fitting')
    if len(train) < 20 or len(test) < 20:
        result = {'status': 'insufficient_compatible_observations', 'candidate_evaluated': False,
                  'primary2010_2014_predictive_error': None, 'model_promoted': False}
        write_json(output / 'report.json', result); return result
    model = ResidualRidge().fit(train)
    write_json(output / 'candidate_frozen.json', {'protocol_sha256': sha256(protocol_path),
        'model': model.artifact(), 'test_targets_used_for_fitting_or_selection': False,
        'source_sha256': sha256(Path(__file__)), 'later_outcomes_opened': False})
    names = ['grand_mean', 'treatment_mean', 'residual_ridge']
    residuals = defaultdict(list)
    for held_block in sorted({block(r) for r in train}):
        fitting = [r for r in train if block(r) != held_block]
        held = [r for r in train if block(r) == held_block]
        fold = ResidualRidge().fit(fitting)
        for name in names:
            residuals[name].extend(r['netmineralization'] - estimate(fold, r, name) for r in held)
    scores, predictions = {}, {}
    y = np.array([r['netmineralization'] for r in test])
    for name in names:
        p = np.array([estimate(model, r, name) for r in test])
        lo, hi = np.quantile(residuals[name], [.05, .95])
        covered = (p + lo <= y) & (y <= p + hi)
        result = {**metrics(y, p), 'coverage_90_residual_envelope': float(covered.mean()),
                  'mean_interval_width': float(hi - lo), 'calibrated_probability_guarantee': False}
        result['sign_subsets'] = {}
        for label, mask in [('negative', y < 0), ('zero', y == 0), ('positive', y > 0)]:
            result['sign_subsets'][label] = {**metrics(y[mask], p[mask]), 'predicted_negative_fraction': float(np.mean(p[mask] < 0)),
                                            'coverage_90': float(covered[mask].mean())} if mask.any() else {'n': 0, 'MAE': None, 'bias': None}
        scores[name] = result
        predictions[name] = [{'target_source_row': r['source_row'], 'join_key': r['join_key'],
             'observed': r['netmineralization'], 'predicted': float(v), 'lower90': float(v + lo), 'upper90': float(v + hi)} for r, v in zip(test, p)]
    difference = np.array([abs(a['predicted'] - a['observed']) - abs(b['predicted'] - b['observed'])
                           for a, b in zip(predictions['residual_ridge'], predictions['treatment_mean'])])
    rng = np.random.default_rng(20261115); blocks = sorted({block(r) for r in test}); samples = []
    for _ in range(2000):
        chosen = rng.choice(blocks, len(blocks), replace=True)
        ix = [i for b in chosen for i, r in enumerate(test) if block(r) == b]
        samples.append(float(difference[ix].mean()))
    result = {'status': 'exploratory2001_to_2002_diagnostic_inside_original_training_archive',
        'candidate_evaluated': True, 'unit': 'microgram N/g dry soil/day', 'fit_n': len(train), 'test_n': len(test),
        'fit_year': 2001, 'test_year': 2002, 'scores': scores,
        'candidate_minus_treatment_MAE': float(difference.mean()),
        'conditional_single_year_block_bootstrap_95': [float(v) for v in np.quantile(samples, [.025, .975])],
        'between_year_generalization_uncertainty_available': False,
        'primary2010_2014_predictive_error': None, 'independent_final_validation': False,
        'model_promoted': False, 'retained_estimator_for_existing_scope': 'V2.1training_treatment_mean',
        'simulator_coupling_enabled': False,
        'limitations': ['Only one diagnostic test year; this cannot establish reliable multi-year ecological transfer.',
            'Initial N is measured but is also subtracted in the net-change target; shared measurement error can contribute to association.',
            'Exact published date/plot/depth compatibility does not establish an independent laboratory aliquot or sample-batch identifier.',
            'No exact new covariate matches in2010–2014; never compare this46-observation score to the old265-observation score as accuracy improvement.',
            'No independently initialized microbial/substrate pools, temperature trajectory or conserved per-hectare biological transfer.',
            'Interval envelopes are trained out-of-block but lack exchangeability/coverage guarantees under year shift.']}
    write_json(output / 'predictions.json', predictions)
    write_json(output / 'report.json', result)
    return result
