"""Small KBS process observation adapter with a closed final-outcome boundary."""
from collections import Counter, defaultdict
import csv
from datetime import date
from pathlib import Path
import math

import numpy as np

from ..io import number, sha256, write_json


def concentration_to_areal(value_ug_g, bulk_density_g_cm3, top_m, bottom_m):
    """No default bulk density or assumed observation depth is supplied."""
    number(value_ug_g, 'concentration')  # Net changes can be negative.
    number(bulk_density_g_cm3, 'measured bulk density', .1, 2.5)
    number(top_m, 'top depth', 0); number(bottom_m, 'bottom depth', top_m + .00001)
    return value_ug_g * bulk_density_g_cm3 * (bottom_m - top_m) * 10


def value(text):
    try:
        n = float(text)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def read_development(path, kind):
    """Check year identity before decoding outcome numbers; never expose finals."""
    rows, excluded, duplicates, seen, reserved = [], Counter(), [], {}, 0
    source_hash = sha256(path)
    with Path(path).open() as stream:
        reader = csv.DictReader(line for line in stream if line.strip() and not line.startswith('#'))
        for index, raw in enumerate(reader, 1):
            y = value(raw.get('Year', raw.get('year')))
            if y is None:
                excluded['missing_year'] += 1; continue
            y = int(y)
            # This boundary precedes every conversion of a measured outcome.
            if (kind == 'microbes' and y > 1994) or (kind == 'mineralization' and y > 2014):
                reserved += 1; continue
            train = y <= (1992 if kind == 'microbes' else 2009)
            treatment = raw.get('trt', raw.get('Treatment', '')).strip()
            rep = raw.get('rep', raw.get('Replicate', '')).strip()
            station = raw.get('stn', raw.get('station', '')).strip()
            stamp = raw.get('date', raw.get('T0_Date', '')).strip()
            key = (stamp, treatment, rep, station, raw.get('sub_plot', '').strip(),
                   raw.get('T21_Date', '').strip(), raw.get('Campaign', '').casefold().strip())
            if not treatment or not rep or not stamp:
                excluded['incomplete_plot_date_identity'] += 1; continue
            target_names = ['co2_c', 'co2_f', 'nc', 'nf', 'cfibio_c', 'cfibio_n'] if kind == 'microbes' else [
                'NetMineralization', 'NetNitrification', 'NetAmmonification']
            signature = tuple(raw.get(k, '').strip() for k in target_names)
            if key in seen:
                if signature != seen[key]:
                    raise ValueError('Conflicting process outcomes for one sample identity; curator resolution required')
                duplicates.append({'source_row': index, 'key': list(key),
                                   'same_reported_outcomes': True, 'description': raw.get('Description')})
                excluded['duplicate_sample_identity'] += 1; continue
            seen[key] = signature
            out = {'source_row': index, 'year': y, 'date': stamp, 'treatment': treatment,
                   'replicate': rep, 'station': station, 'group': treatment + ':' + rep,
                   'fold': 'training' if train else 'development', 'source_sha256': source_hash}
            if kind == 'microbes':
                for key in ['co2_c', 'co2_f', 'nc', 'nf', 'cfibio_c', 'cfibio_n']:
                    out[key] = value(raw.get(key))
                # Negative net N is valid; negative respiration is not converted
                # into a positive physical flux or clipped to zero.
                if out['co2_c'] is not None and out['co2_c'] < 0:
                    excluded['negative_respiration_target'] += 1; out['co2_c'] = None
            else:
                end = raw.get('T21_Date', '').strip()
                try: duration = (date.fromisoformat(end) - date.fromisoformat(stamp)).days
                except ValueError: duration = 0
                if duration <= 0:
                    excluded['invalid_incubation_dates'] += 1; continue
                out.update(end_date=end, duration_days=duration,
                           netmineralization=value(raw.get('NetMineralization')),
                           netnitrification=value(raw.get('NetNitrification')),
                           netammonification=value(raw.get('NetAmmonification')))
            rows.append(out)
    return rows, {'used_development_rows': len(rows), 'excluded': dict(excluded),
                  'duplicate_identities': duplicates, 'reserved_rows_not_numerically_decoded': reserved,
                  'fold_counts': dict(Counter(r['fold'] for r in rows)),
                  'year_counts': dict(Counter(r['year'] for r in rows)),
                  'one_site_not_independent_sites': True}


def metrics(observed, predicted):
    e = np.array(predicted) - np.array(observed)
    return {'n': len(e), 'MAE': float(np.mean(abs(e))), 'RMSE': float(np.sqrt(np.mean(e * e))), 'bias': float(np.mean(e))}


def baselines(rows, target):
    train = [r for r in rows if r['fold'] == 'training' and r.get(target) is not None]
    dev = [r for r in rows if r['fold'] == 'development' and r.get(target) is not None]
    if not train or not dev:
        return {'available': False, 'reason': 'No compatible training/development targets'}, []
    mean = float(np.mean([r[target] for r in train]))
    groups = defaultdict(list)
    for r in train: groups[r['treatment']].append(r[target])
    means = {k: float(np.mean(v)) for k, v in groups.items()}
    predictions = [{**{k: r[k] for k in ['source_row', 'date', 'year', 'treatment', 'replicate', 'group']},
                    'target': target, 'observed': r[target], 'zero': 0., 'training_mean': mean,
                    'training_treatment_mean': means.get(r['treatment'], mean)} for r in dev]
    result = {'available': True, 'training_n': len(train), 'development_n': len(dev),
              'models': {key: metrics([r['observed'] for r in predictions], [r[key] for r in predictions])
                         for key in ['zero', 'training_mean', 'training_treatment_mean']},
              'independent_environment_count': 1, 'final_test_evaluated': False,
              'development_years': sorted({r['year'] for r in dev})}
    # Paired uncertainty: resample years, then plot groups within sampled years.
    # This describes this one site's development split, not transfer uncertainty.
    rng = np.random.default_rng(271828)
    years = sorted({r['year'] for r in predictions})
    diffs = []
    for _ in range(500):
        sample = []
        for y in rng.choice(years, len(years), replace=True):
            yr = [r for r in predictions if r['year'] == y]
            ids = sorted({r['group'] for r in yr})
            for g in rng.choice(ids, len(ids), replace=True): sample.extend(r for r in yr if r['group'] == g)
        diffs.append(float(np.mean([abs(r['training_treatment_mean'] - r['observed']) - abs(r['training_mean'] - r['observed']) for r in sample])))
    result['treatment_minus_grand_MAE_95pct_cluster_interval'] = [float(x) for x in np.quantile(diffs, [.025, .975])]
    result['interval_scope'] = 'Year then treatment-replicate resampling at one exposed site; few development years limit inference.'
    return result, predictions


def audit_observations(raw_directory, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    assays, aq = read_development(Path(raw_directory) / 'kbs_microbes.csv', 'microbes')
    mineral, nq = read_development(Path(raw_directory) / 'kbs_mineralization.csv', 'mineralization')
    summary, predictions = {}, []
    for target, rows in [('co2_c', assays), ('nc', assays), ('netmineralization', mineral)]:
        summary[target], pred = baselines(rows, target); predictions.extend(pred)
    residuals = [r['cfibio_c'] - (1.73 * r['co2_f'] - .56 * r['co2_c']) for r in assays
                 if all(r[k] is not None for k in ['cfibio_c', 'co2_f', 'co2_c'])]
    # Match only exact dates, treatment, replicate and composite sample support.
    # Do not merge monthly assays onto a different field-incubation campaign.
    assay_keys = {(r['date'], r['treatment'], r['replicate']) for r in assays if r['station'] == 'C'}
    joins = sum((r['date'], r['treatment'], r['replicate']) in assay_keys for r in mineral if not r['station'])
    report = {'sources': {'assays': aq, 'mineralization': nq}, 'baselines': summary,
        'exact_plot_date_composite_join_candidates': joins,
        'join_is_mechanistic_initialization': False,
        'derived_microbial_C_formula_check': {'n': len(residuals),
            'median_absolute_residual_ug_g': float(np.median(np.abs(residuals))) if residuals else None,
            'reason': 'Published CFI biomass uses control and fumigated CO2; same-assay biomass initialization leaks respiration information.'},
        'physical_candidate_observation_errors': None,
        'mechanistic_validation_blockers': ['Unknown decomposable substrate C/N and initial independent functional microbial C/N.',
            'Assay moisture/incubation preparation are not fully identified by the tabular outcomes.',
            'Buried-bag0–25cm net mineralization excludes roots/moisture exchange and is not the unmodified open soil column.',
            'No matched bulk density is supplied; gravimetric concentrations are not converted using an invented soil mass.',
            'CFI-derived biomass, respiration and N endpoints are not independent targets; no joint multi-parameter fitting.'],
        'scope': 'Observation QA and chronological process baselines only; not validation of the experimental decomposer candidate.',
        'final_outcomes_opened': False, 'publication_permission_obtained': False,
        'source_terms': 'https://lter.kbs.msu.edu/data/terms-of-use/; local personal/class evaluation only, not publication authorization'}
    write_json(output / 'audit.json', report)
    write_json(output / 'development_observations.json', {'assays': assays, 'buried_bags': mineral})
    if predictions:
        with (output / 'baseline_predictions.csv').open('w') as f:
            writer = csv.DictWriter(f, fieldnames=list(predictions[0])); writer.writeheader(); writer.writerows(predictions)
    return report
