"""Exact initial-condition joins; no nearest dates, outcome inversion or filling."""
from collections import Counter, defaultdict
import csv
from datetime import date
from pathlib import Path
import math

from ..io import sha256, write_json
from ..nitrogen_ml.evaluate import data_audit


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError): return None


def stamp(value):
    value = str(value or '').strip()
    try: return date.fromisoformat(value[:10]).isoformat()
    except ValueError: return None


def text(row, *keys):
    for key in keys:
        if key in row: return (row[key] or '').strip()
    return ''


def identity(when, treatment, replicate, station='', subplot=''):
    if not when or not treatment or not replicate: return None
    return (when, treatment, replicate, station, subplot)


def guarded_rows(path, date_columns, *, year_column=None, maximum_year=2014):
    """Date/identity decoding precedes every numeric measurement conversion."""
    with Path(path).open() as stream:
        reader = csv.DictReader(line for line in stream if line.strip() and not line.startswith('#'))
        for index, row in enumerate(reader, 1):
            when = stamp(text(row, *date_columns)) if date_columns else None
            if when:
                year = int(when[:4])
            elif year_column:
                y = text(row, year_column)
                # Identity-only year, not a measured ecological quantity.
                try: year = int(float(y))
                except ValueError: continue
            else: continue
            if year > maximum_year: continue
            yield index, when, row


def unique_index(records):
    groups = defaultdict(list)
    for key, value, provenance in records: groups[key].append((value, provenance))
    unique, ambiguous = {}, {}
    for key, values in groups.items():
        signatures = {tuple(v.items()) for v, _ in values}
        if len(signatures) == 1:
            unique[key] = {'values': values[0][0], 'source_records': [p for _, p in values]}
        else:
            ambiguous[key] = {'reason': 'Conflicting measurements for identical support; no arbitrary averaging',
                              'source_records': [p for _, p in values]}
    return unique, ambiguous


def initial_covariates(path, kind):
    records, quality = [], Counter()
    digest = sha256(path)
    for index, when, row in guarded_rows(path, ['Date', 'date']):
        quality['pre2015_rows'] += 1
        key = identity(when, text(row, 'Treatment', 'treatment'), text(row, 'Replicate', 'replicate'),
                       text(row, 'station'), text(row, 'sub_plot'))
        if key is None: quality['missing_identity'] += 1; continue
        if key[3] or key[4]:
            quality['noncomposite_or_subplot'] += 1; continue
        if kind == 'inorganic_N':
            campaign = text(row, 'Campaign')
            if '21' in campaign or 'final' in campaign.casefold():
                quality['possible_endpoint_campaign_excluded'] += 1; continue
            top, bottom = numeric(row.get('top_depth_cm')), numeric(row.get('bottom_depth_cm'))
            if (top is not None or bottom is not None) and (top, bottom) != (0., 25.):
                quality['depth_mismatch'] += 1; continue
            no3, nh4 = numeric(row.get('Nitrate')), numeric(row.get('Ammonium'))
            if no3 is None or nh4 is None or min(no3, nh4) < 0:
                quality['missing_or_negative_concentration'] += 1; continue
            values = {'nitrate_ug_g': no3, 'ammonium_ug_g': nh4}
            support = '0–25cm directly recorded' if top is not None else '0–25cm inherited from pre2015 table/protocol metadata'
        else:
            moisture = numeric(row.get('moisture'))
            if moisture is None or not 0 <= moisture <= 3:
                quality['missing_or_invalid_water_content'] += 1; continue
            values = {'water_g_g': moisture}; campaign = None
            support = '0–25cm composite; gravimetric g water/g dry soil, documented table/protocol support'
        provenance = {'file': Path(path).name, 'source_row': index, 'sha256': digest, 'date': when,
                      'campaign': campaign, 'support': support, 'observation_role': 'T0 only when exact target start-date identity matches'}
        if kind == 'water': provenance['source_comments'] = text(row, 'comments')
        records.append((key, values, provenance))
    unique, ambiguous = unique_index(records)
    quality.update(unique_identities=len(unique), ambiguous_identities=len(ambiguous))
    return unique, ambiguous, dict(quality)


def context_records(path, dates, fields, *, year_column=None):
    rows = []
    for index, when, raw in guarded_rows(path, dates, year_column=year_column):
        rows.append({'source_row': index, 'date': when,
            'treatment': text(raw, 'treatment', 'Treatment'), 'replicate': text(raw, 'replicate', 'Replicate'),
            'station': text(raw, 'station'), 'subplot': text(raw, 'sub_plot'),
            'values': {key: numeric(raw.get(key)) for key in fields}})
    return rows


def recover(root, output):
    root = Path(root); output = Path(output); output.mkdir(parents=True, exist_ok=False)
    raw = root / 'reports/ecosystem-v22/raw'
    sources = __import__('json').loads((raw / 'manifest.json').read_text())
    for key, item in sources.items():
        if item.get('sha256') and sha256(raw / f'table-{key}.csv') != item['sha256']:
            raise ValueError('Recovered raw asset hash changed')
    targets, target_audit = data_audit(root)
    n, n_ambig, n_quality = initial_covariates(raw / 'table-55.csv', 'inorganic_N')
    w, w_ambig, w_quality = initial_covariates(raw / 'table-30.csv', 'water')
    joined, missing = [], []
    for row in targets:
        key = identity(row['date'], row['treatment'], row['replicate'], row['station'], '')
        nr, wr = n.get(key), w.get(key)
        out = {**row, 'join_key': list(key), 'initial_N_provenance': nr, 'initial_water_provenance': wr,
               'nitrate_ug_g': nr['values']['nitrate_ug_g'] if nr else None,
               'ammonium_ug_g': nr['values']['ammonium_ug_g'] if nr else None,
               'water_g_g': wr['values']['water_g_g'] if wr else None,
               'complete_new_predictors': nr is not None and wr is not None}
        joined.append(out)
        if not out['complete_new_predictors']:
            missing.append({'source_row': row['source_row'], 'key': list(key),
                'N_status': 'ambiguous' if key in n_ambig else 'absent' if nr is None else 'matched',
                'water_status': 'ambiguous' if key in w_ambig else 'absent' if wr is None else 'matched'})
    temperature = context_records(raw / 'table-167.csv', ['date'], ['soil_temperature'])
    carbon = context_records(raw / 'table-164.csv', ['sample_date'], ['top_depth', 'bottom_depth', 'n_percent', 'c_percent', 'total_bulk_density'])
    density = context_records(raw / 'table-308.csv', [], ['total_bulk_density'], year_column='year')
    management = context_records(raw / 'table-159.csv', ['obs_date'], ['n_rate_kg_ha'])
    context = {}
    for name, rows in [('temperature_10cm', temperature), ('total_C_N_cores', carbon)]:
        keys = {(r['date'], r['treatment'], r['replicate']) for r in rows}
        matches = [r['source_row'] for r in targets if (r['date'], r['treatment'], r['replicate']) in keys]
        context[name] = {'pre2015_rows': len(rows), 'same_plot_date_candidate_targets': matches,
                         'verified_bag_equivalent_predictor': False,
                         'reason': '10cm point temperature during chamber sampling, not a bag trajectory' if name.startswith('temperature')
                         else 'Core-specific depth/section/station and total C/N; date/plot alone does not identify bag substrate or full0–25cm stock'}
    mgmt = []
    for target in targets:
        events = [r['source_row'] for r in management if r['date'] and r['date'][:4] == str(target['year'])
                  and r['date'] <= target['date'] and r['treatment'] == target['treatment'] and r['replicate'] == target['replicate']]
        if events: mgmt.append({'target_source_row': target['source_row'], 'record_ids': events})
    context['bulk_density_cores'] = {'pre2015_rows': len(density), 'exact_date_matching_possible': False,
        'reason': 'Table308 has year/core/section without a measurement date; not assigned across years or bag volumes'}
    context['fertilizer_log'] = {'pre2015_rows': len(management), 'same_year_pre_T0_exact_plot_candidates': mgmt,
        'validated_absence_is_zero': False, 'used_as_predictor': False,
        'reason': 'Recorded applications provide management context, not missing-event completion, actual retained residue, or N placed inside isolated bags'}
    counts = {}
    for fold in ['training', 'development']:
        rows = [r for r in joined if r['fold'] == fold]
        counts[fold] = {'original_n': len(rows), 'N_only': sum(r['nitrate_ug_g'] is not None for r in rows),
            'water_only': sum(r['water_g_g'] is not None for r in rows),
            'both_complete': sum(r['complete_new_predictors'] for r in rows),
            'complete_years': sorted({r['year'] for r in rows if r['complete_new_predictors']})}
    date_gaps = []
    for row in joined:
        if row['initial_N_provenance'] is not None: continue
        earlier = [key for key in n if key[1:] == tuple(row['join_key'][1:]) and key[0] <= row['date'] and key[0][:4] == str(row['year'])]
        key = max(earlier, key=lambda k: k[0]) if earlier else None
        date_gaps.append({'target_source_row': row['source_row'], 'initial_analysis_date': row['date'],
            'latest_same_plot_collection_date': key[0] if key else None,
            'lag_days_for_diagnosis_only': (date.fromisoformat(row['date']) - date.fromisoformat(key[0])).days if key else None,
            'excluded_from_verified_join': True,
            'reason': 'No published sample-ID/collection-to-analysis crosswalk. Earlier field values are not relabeled as bag initial conditions.'})
    report = {'schema_version': 1, 'target_audit': target_audit, 'source_quality': {'N': n_quality, 'water': w_quality},
        'coverage': counts, 'context_compatibility': context,
        'strict_join': 'Exact ISO T0 date, MCSE experiment, treatment, replicate, empty composite station and subplot; no temporal tolerance, nearest year, imputation or target inversion.',
        'initial_N_shared_measurement_warning': 'Observed T0N is a valid pre-incubation predictor, but the net-change target subtracts initial N. Measurement error can create mathematical coupling/regression to the mean; predictive association is not a causal microbial mechanism.',
        'reserved_numeric_measurements_decoded': False, 'new_process_chosen_for_possible_test': 'net nitrogen mineralization only',
        'mechanistic_initialization_complete': False,
        'missing_mechanistic_inputs': ['Independent contemporary microbial C/N', 'Actual decomposable/returned substrate mass and composition',
                                      'Bag temperature trajectory', 'Matched bag soil mass/depth/retention support'],
        'raw_download_bytes': sum(s.get('bytes', 0) for s in sources.values())}
    write_json(output / 'join_audit.json', report)
    write_json(output / 'joined_observations.json', joined)
    write_json(output / 'unmatched_or_ambiguous.json', missing)
    write_json(output / 'date_gap_diagnostics_not_joins.json', date_gaps)
    write_json(output / 'context_provenance.json', {'temperature': temperature, 'C_N_cores': carbon, 'density_cores': density,
                                                 'management': management})
    return report
