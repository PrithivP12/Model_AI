"""Proper Gaussian hierarchical posterior, sampled without new dependencies.

Signed rates retain negative net immobilization. This is a statistical model of
one observation protocol; its random effects are not physical nutrient stocks.
"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import json

import numpy as np
from scipy.linalg import cho_factor, cho_solve, solve_triangular

from ..io import number, write_json
from .diagnostics import diagnose

UNIT = 'microgram N/g dry soil/day'
FEATURES = ['log_duration', 'season_sin', 'season_cos']
GROUPS = ['treatment', 'plot', 'year']


def predictor_values(row):
    start, end = date.fromisoformat(row['date']), date.fromisoformat(row['end_date'])
    duration = (end - start).days
    number(row['duration_days'], 'documented incubation duration', 1, 366)
    if duration != row['duration_days'] or start.year != row['year']:
        raise ValueError('Date/duration/year identity mismatch')
    if not row['treatment'] or not row['replicate']:
        raise ValueError('Observed treatment and replicate identity required')
    phase = 2 * np.pi * start.timetuple().tm_yday / 365.25
    return [np.log(duration / 21.), np.sin(phase), np.cos(phase)]


def plot_key(row):
    return ':'.join([row['treatment'], row['replicate'], row.get('station', ''), row.get('sub_plot', '')])


def group_key(row, group):
    return plot_key(row) if group == 'plot' else str(row[group])


def gaussian_block(xtx, xty, variances, residual_variance, rng):
    """One exact conditional coefficient draw; no explicit covariance inverse."""
    precision = xtx / residual_variance + np.diag(1 / variances)
    factor, lower = cho_factor(precision, lower=True, check_finite=False)
    mean = cho_solve((factor, lower), xty / residual_variance, check_finite=False)
    noise = solve_triangular(factor.T, rng.normal(size=len(mean)), lower=False, check_finite=False)
    return mean + noise


def slice_log_scale(current, log_density, rng, width=1.):
    """Stepping-out slice update on a log SD, with explicit tail bracketing."""
    threshold = log_density(current) - rng.exponential()
    lo = current - rng.uniform() * width; hi = lo + width
    for _ in range(200):
        if log_density(lo) <= threshold: break
        lo -= width
    else: raise ArithmeticError('Slice left tail failed to bracket')
    for _ in range(200):
        if log_density(hi) <= threshold: break
        hi += width
    else: raise ArithmeticError('Slice right tail failed to bracket')
    for _ in range(1000):
        proposal = rng.uniform(lo, hi)
        if log_density(proposal) >= threshold: return proposal
        if proposal < current: lo = proposal
        else: hi = proposal
    raise ArithmeticError('Slice shrinkage did not converge')


def half_t_log_scale(t, rv, vv, sigma2, *, df=3., scale=.5):
    """Noncentered conditional posterior, including log-SD Jacobian.

    eta=u/tau is held fixed; rv=r.dot(Zeta), vv=(Zeta).dot(Zeta).
    The Gaussian eta prior is constant. Auxiliary variance is marginalized and
    then redrawn, so this interweaving step targets the same half-t posterior.
    """
    if t > 350: return -np.inf  # Outside representable squared scale.
    tau = np.exp(t)
    return -(tau * tau * vv - 2 * tau * rv) / (2 * sigma2) - (df + 1) / 2 * np.log1p(tau * tau / (df * scale * scale)) + t


class HierarchicalNitrogenModel:
    def __init__(self):
        self.metadata = None
        self.posterior = None

    def design(self, rows):
        if self.metadata is None: raise ValueError('Unfitted model')
        raw = np.array([predictor_values(r) for r in rows])
        numeric = (raw - np.array(self.metadata['feature_center'])) / np.array(self.metadata['feature_scale'])
        result = np.zeros((len(rows), len(self.metadata['coefficient_labels'])))
        result[:, 0] = 1.; result[:, 1:4] = numeric
        unknown = {g: [] for g in GROUPS}
        for g in GROUPS:
            levels = self.metadata['levels'][g]; offset = self.metadata['offsets'][g]
            lookup = {k: i for i, k in enumerate(levels)}
            for i, row in enumerate(rows):
                key = group_key(row, g)
                if key in lookup: result[i, offset + lookup[key]] = 1.
                else: unknown[g].append((i, key))
        return result, unknown

    def fit(self, rows, *, chains=4, warmup=2000, draws=4000, seed=20261115, interweave=True):
        rows = list(rows)
        if not rows or any(r.get('fold') != 'training' or r['year'] > 2009 for r in rows):
            raise ValueError('Fitter accepts only the frozen training period, never development/final observations')
        if len({r['year'] for r in rows}) < 3 or len(rows) < 20:
            raise ValueError('Insufficient repeated groups for declared year hierarchy')
        ids = [(r['date'], r['end_date'], plot_key(r)) for r in rows]
        if len(set(ids)) != len(ids): raise ValueError('Duplicate assay identities cannot enter fitting twice')
        y = np.array([number(r['netmineralization'], 'observed signed net rate') for r in rows])
        raw = np.array([predictor_values(r) for r in rows])
        scale = raw.std(axis=0); scale[scale < 1e-12] = 1.
        levels = {g: sorted({group_key(r, g) for r in rows}) for g in GROUPS}
        offsets = {}; labels = ['intercept'] + FEATURES
        for g in GROUPS:
            offsets[g] = len(labels); labels.extend(g + ':' + k for k in levels[g])
        ys = float(y.std()); ys = ys if ys > 1e-12 else 1.
        self.metadata = {'schema_version': 1, 'unit': UNIT, 'target_mean': float(y.mean()), 'target_scale': ys,
            'feature_center': raw.mean(axis=0).tolist(), 'feature_scale': scale.tolist(), 'levels': levels,
            'offsets': offsets, 'coefficient_labels': labels, 'posterior_labels': labels + ['sigma'] + ['tau_' + g for g in GROUPS],
            'training_record_ids': [r['source_row'] for r in rows], 'training_years': sorted({r['year'] for r in rows}),
            'training_identities': [list(i) for i in ids], 'predictor_names': list(FEATURES) + GROUPS,
            'site_count': 1, 'free_running_soil_prediction_supported': False, 'simulator_coupling_enabled': False,
            'observation_protocol': '0–25cm root-excluded moisture-isolated KBS buried-bag net inorganic-N rate',
            'priors': {'intercept_SD': 5., 'numeric_beta_SD': 1., 'group_SD_half_t_df': 3.,
                       'group_SD_half_t_scale': .5, 'residual_variance_inverse_gamma': [2., 1.]},
            'sampler': {'method': 'joint conditional Gaussian / inverse-gamma auxiliary Gibbs' + (' / centered-noncentered slice interweaving' if interweave else ''), 'seed': seed,
                        'chains': chains, 'warmup': warmup, 'draws_per_chain': draws}}
        x, _ = self.design(rows); z = (y - y.mean()) / ys
        xtx, xty = x.T @ x, x.T @ z
        indices = {g: np.arange(offsets[g], offsets[g] + len(levels[g])) for g in GROUPS}
        result = np.empty((chains, draws, len(labels) + 4))
        for chain in range(chains):
            rng = np.random.default_rng(seed + chain * 100003)
            sigma2 = float(np.exp(rng.normal(0, .4)))
            tau2 = {g: float(np.exp(rng.normal(np.log(.25), .5))) for g in GROUPS}
            auxiliary = {g: 4. for g in GROUPS}
            for iteration in range(warmup + draws):
                variances = np.ones(len(labels)); variances[0] = 25.
                for g in GROUPS: variances[indices[g]] = tau2[g]
                beta = gaussian_block(xtx, xty, variances, sigma2, rng)
                residual = z - x @ beta
                sigma2 = (1. + .5 * (residual @ residual)) / rng.gamma(2. + len(z) / 2.)
                for g in GROUPS:
                    u = beta[indices[g]]; nu = 3.; A = .5
                    tau2[g] = (nu / auxiliary[g] + .5 * (u @ u)) / rng.gamma((nu + len(u)) / 2.)
                    auxiliary[g] = (nu / tau2[g] + 1 / A ** 2) / rng.gamma((nu + 1) / 2.)
                if interweave:
                    for g in GROUPS:
                        ix = indices[g]
                        eta = beta[ix] / np.sqrt(tau2[g])
                        v = x[:, ix] @ eta
                        r = z - x @ beta + x[:, ix] @ beta[ix]
                        rv, vv = float(r @ v), float(v @ v)
                        density = lambda t: half_t_log_scale(t, rv, vv, sigma2)
                        log_tau = slice_log_scale(.5 * np.log(tau2[g]), density, rng)
                        tau2[g] = float(np.exp(2 * log_tau)); beta[ix] = np.sqrt(tau2[g]) * eta
                        auxiliary[g] = (3. / tau2[g] + 4.) / rng.gamma(2.)
                if iteration >= warmup:
                    result[chain, iteration - warmup] = np.r_[beta, np.sqrt(sigma2), [np.sqrt(tau2[g]) for g in GROUPS]]
        if not np.isfinite(result).all(): raise ArithmeticError('Nonfinite posterior draw')
        self.posterior = result
        return self

    def diagnostics(self):
        if self.posterior is None: raise ValueError('Unfitted model')
        return diagnose(self.posterior, self.metadata['posterior_labels'])

    def predict(self, rows, *, seed=8675309):
        rows = list(rows)
        x, unknown = self.design(rows)
        posterior = self.posterior.reshape(-1, self.posterior.shape[-1])
        p = len(self.metadata['coefficient_labels']); scale = self.metadata['target_scale']
        base = posterior[:, :p] @ x.T
        latent = base.copy(); rng = np.random.default_rng(seed)
        unknown_variance = {g: np.zeros(len(rows)) for g in GROUPS}
        for j, g in enumerate(GROUPS):
            for key in sorted({v for _, v in unknown[g]}):
                locations = [i for i, v in unknown[g] if v == key]
                # One draw per unseen group and posterior draw, shared by all
                # its observations; never condition on held-out group outcomes.
                effect = rng.normal(size=len(posterior)) * posterior[:, p + 1 + j]
                latent[:, locations] += effect[:, None]
                unknown_variance[g][locations] = np.mean(posterior[:, p + 1 + j] ** 2) * scale ** 2
        predictive = latent + rng.normal(size=latent.shape) * posterior[:, p, None]
        origin = self.metadata['target_mean']
        point = origin + base.mean(axis=0) * scale
        conditional = origin + np.quantile(base, [.05, .95], axis=0) * scale
        latent_interval = origin + np.quantile(latent, [.05, .95], axis=0) * scale
        interval = origin + np.quantile(predictive, [.05, .95], axis=0) * scale
        output = []
        for i, row in enumerate(rows):
            output.append({'source_row': row.get('source_row'), 'date': row['date'], 'end_date': row['end_date'],
                'treatment': row['treatment'], 'replicate': row['replicate'], 'year': row['year'],
                'point': float(point[i]), 'conditional_mean_90': conditional[:, i].tolist(),
                'latent_new_group_90': latent_interval[:, i].tolist(), 'posterior_predictive_90': interval[:, i].tolist(),
                'unit': UNIT, 'parameter_variance': float(np.var(base[:, i], ddof=1) * scale ** 2),
                'unseen_group_variance': {g: float(unknown_variance[g][i]) for g in GROUPS},
                'residual_variance': float(np.mean(posterior[:, p] ** 2) * scale ** 2),
                'probability_negative_net_rate': float(np.mean(predictive[:, i] < 0)),
                'scope': 'offline_KBS_buried_bag_rate_not_whole_field_N_flux', 'simulator_coupling_enabled': False})
        return output

    def save(self, directory):
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / 'model.json', self.metadata)
        np.save(directory / 'posterior.npy', self.posterior, allow_pickle=False)

    @classmethod
    def load(cls, directory):
        model = cls(); directory = Path(directory)
        model.metadata = json.loads((directory / 'model.json').read_text())
        if model.metadata['unit'] != UNIT or model.metadata['simulator_coupling_enabled']:
            raise ValueError('Unsupported model unit/coupling metadata')
        model.posterior = np.load(directory / 'posterior.npy', allow_pickle=False)
        if model.posterior.shape[2] != len(model.metadata['posterior_labels']) or not np.isfinite(model.posterior).all():
            raise ValueError('Invalid posterior artifact')
        return model

    def apply_to_soil(self, *args, **kwargs):
        raise ValueError('Offline observation model: no identified conserved material transfer; direct soil N injection disabled')
