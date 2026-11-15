"""Rank/folded split R-hat and autocorrelation ESS for scalar chains.

Following the definitions in the Stan reference manual's Posterior Analysis
chapter; these diagnose MCMC computation, not ecological predictive validity.
"""
import numpy as np
from scipy.special import ndtri
from scipy.stats import rankdata


def split(x):
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or x.shape[0] < 2 or x.shape[1] < 8 or not np.isfinite(x).all():
        raise ValueError('At least two finite chains with eight draws each required')
    half = x.shape[1] // 2
    return np.concatenate([x[:, :half], x[:, -half:]], axis=0)


def rank_normalize(x):
    ranks = rankdata(x.ravel(), method='average').reshape(x.shape)
    return ndtri((ranks - .375) / (x.size + .25))


def rhat(x):
    n = x.shape[1]
    within = np.var(x, axis=1, ddof=1).mean()
    between = n * np.var(x.mean(axis=1), ddof=1)
    if within == 0:
        return 1. if between == 0 else None
    return float(np.sqrt(((n - 1) * within / n + between / n) / within))


def ess(x):
    m, n = x.shape
    centered = x - x.mean(axis=1, keepdims=True)
    fft = np.fft.rfft(centered, n=2 * n, axis=1)
    cov = np.fft.irfft(fft * np.conjugate(fft), axis=1)[:, :n] / n
    within = np.var(x, axis=1, ddof=1).mean()
    variance = (n - 1) * within / n + np.var(x.mean(axis=1), ddof=1)
    if variance == 0:
        return float(m * n)
    rho = 1 - (within - cov.mean(axis=0)) / variance
    rho[0] = 1.
    # Geyer's initial-positive, initial-monotone paired sequence.
    pairs = []
    for i in range(0, n - 1, 2):
        pair = rho[i] + rho[i + 1]
        if pair < 0: break
        pairs.append(min(pair, pairs[-1]) if pairs else pair)
    tau = max(1., -1 + 2 * sum(pairs))
    return float(min(m * n, m * n / tau))


def diagnose(chains, labels):
    chains = np.asarray(chains)
    if chains.ndim != 3 or chains.shape[2] != len(labels):
        raise ValueError('Chains must be chain x draw x named parameter')
    rows = []
    for i, name in enumerate(labels):
        raw = chains[:, :, i]
        x = split(raw); z = rank_normalize(x)
        ranks = [rhat(z), rhat(rank_normalize(np.abs(x - np.median(x))))]
        rh = max(ranks) if all(v is not None for v in ranks) else None
        bulk = ess(z)
        low, high = np.quantile(x, [.05, .95])
        tail = min(ess((x <= low).astype(float)), ess((x >= high).astype(float)))
        rows.append({'parameter': name, 'Rhat_rank_folded_split': rh, 'ESS_bulk': bulk, 'ESS_tail': tail,
                     'mean': float(raw.mean()), 'sd': float(raw.std(ddof=1)),
                     'q05': float(np.quantile(raw, .05)), 'q95': float(np.quantile(raw, .95)),
                     'mean_MCSE': float(raw.std(ddof=1) / np.sqrt(bulk)),
                     'passed': rh is not None and rh <= 1.01 and min(bulk, tail) >= 400})
    return {'parameters': rows, 'all_passed': all(r['passed'] for r in rows),
            'maximum_Rhat': max((r['Rhat_rank_folded_split'] for r in rows if r['Rhat_rank_folded_split'] is not None), default=None),
            'minimum_bulk_ESS': min(r['ESS_bulk'] for r in rows), 'minimum_tail_ESS': min(r['ESS_tail'] for r in rows)}
