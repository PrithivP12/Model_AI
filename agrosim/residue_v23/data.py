"""Bolin observation adapter: direct litter fractions are never renamed POM."""
from collections import Counter, defaultdict
import csv
import io
import json
import math
from pathlib import Path
import zipfile

from ..io import sha256, write_json

OUTCOMES = ['litter C', 'POC', 'litter C and POC', 'MAOC', 'POC and MAOC', 'DOC',
            'litter N', 'PON', 'litter N and PON', 'MAON', 'PON and MAON', 'DON']


def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError): return None


def archive_rows(path):
    with zipfile.ZipFile(path) as archive:
        member = next(i for i in archive.infolist() if i.filename.endswith('/Data.csv') and not i.filename.startswith('__MACOSX/'))
        return list(csv.DictReader(io.StringIO(archive.read(member).decode('utf-8-sig'))))


def recover(root, output):
    root = Path(root); output = Path(output); output.mkdir(parents=True, exist_ok=False)
    phase = root / 'reports/ecosystem-v23'; raw = phase / 'raw'
    protocol = json.loads((phase / 'protocol_frozen.json').read_text())
    source = json.loads((raw / 'manifest.json').read_text())
    archive = raw / 'manzoni-2024-residue-stabilization-1.zip'
    if sha256(archive) != source['sha256']: raise ValueError('Frozen raw archive changed')
    metadata = defaultdict(list)
    for i, row in enumerate(csv.DictReader((raw / 'Metadata.csv').open(encoding='utf-8-sig')), 2):
        metadata[row['source ID'], row['treatment or site ID']].append({'row': i, **row})
    rows, excluded, reserved, inventory, seen = [], Counter(), [], [], {}
    for i, row in enumerate(archive_rows(archive), 2):
        sid, tid = row['source ID'], row['treatment or site ID']
        group = protocol['group_assignment'][sid]
        matches = metadata.get((sid, tid), [])
        target_present = bool(row['litter C'].strip())
        item = {'source_data_row': i, 'source_id': sid, 'treatment_id': tid, 'source_citation': row['source'],
                'group': group['group'], 'fold': group['fold'], 'residue': row['species or residue type'],
                'residue_id': row['litter ID'], 'organ': row['organ'],
                'outcome_columns_present': [key for key in OUTCOMES if row.get(key, '').strip()]}
        # Reserved numerical outcome values are never passed to a converter.
        if group['fold'] == 'final_reserved':
            reserved.append(item); continue
        if len(matches) != 1:
            excluded['missing_or_ambiguous_metadata_join'] += 1; inventory.append({**item, 'eligible': False, 'reason': 'metadata_join'}); continue
        meta = matches[0]
        time, temp, cn = number(row['time']), number(meta['temperature']), number(row['litter C to N ratio'])
        reason = None
        if meta['incubation'] != '1': reason = 'not_laboratory_incubation'
        elif meta['method'] not in ['1', '2', '4']: reason = 'not_supported_isotope_method'
        elif time is None or time <= 0: reason = 'missing_or_nonpositive_time'
        elif temp is None or not 0 <= temp <= 45: reason = 'missing_or_outside_temperature_scope'
        elif cn is None or cn <= 0: reason = 'missing_or_invalid_residue_CN'
        elif not row['species or residue type'].strip(): reason = 'missing_residue_identity'
        elif not target_present: reason = 'direct_litter_C_not_measured'
        if reason:
            excluded[reason] += 1; inventory.append({**item, 'eligible': False, 'reason': reason}); continue
        y = number(row['litter C'])
        if y is None or not 0 <= y <= 1:
            excluded['nonfinite_or_out_of_bounds_litter_C'] += 1
            inventory.append({**item, 'eligible': False, 'reason': 'out_of_bounds_litter_C'}); continue
        key = (sid, tid, row['litter ID'], row['fertilized'], row['organ'], row['species or residue type'], time)
        if key in seen:
            if seen[key] != y: raise ValueError('Conflicting direct residue C for the same study/treatment/residue/time')
            excluded['identical_duplicate_observation'] += 1; continue
        seen[key] = y
        inventory.append({**item, 'eligible': True, 'reason': None})
        rows.append({**item, 'time_days': time, 'temperature_C': temp, 'residue_CN': cn,
            'fertilized': row['fertilized'], 'fractionation_method': meta['fractionation method'],
            'tracing_method': meta['method'], 'metadata_source_row': meta['row'],
            'sand_percent': number(meta['sand']), 'silt_clay_percent': number(meta['silt and clay']), 'SOC_percent': number(meta['SOC']),
            'observed_direct_residue_C_fraction': y,
            'observed_direct_residue_N_fraction': number(row['litter N']) if row['litter N'].strip() else None,
            'sample_time_role': 'days since addition, not reset to first measured value',
            'observation_denominator': 'originally added residue C or N independently; C/N fractions not interchangeable',
            'raw_archive_sha256': source['sha256']})
    summary = {'source': source, 'eligible_direct_C_rows': len(rows),
        'fold_rows': dict(Counter(r['fold'] for r in rows)),
        'fold_groups': {fold: sorted({r['group'] for r in rows if r['fold'] == fold}) for fold in ['training', 'development']},
        'group_times': {g: sorted({r['time_days'] for r in rows if r['group'] == g}) for g in sorted({r['group'] for r in rows})},
        'exclusions': dict(excluded), 'reserved_rows_not_numerically_decoded': len(reserved),
        'reserved_sources': sorted({r['source_id'] for r in reserved}, key=int),
        'metadata_ambiguous_keys': [list(k) for k, v in metadata.items() if len(v) != 1],
        'unsupported_targets': {key: None for key in ['MAOC', 'POC', 'DOC', 'MAON', 'PON', 'DON', 'residue_N_prediction_error', 'CO2_prediction_error']},
        'mapping': {'direct_litter_C': 'remaining explicitly separated residue carbon / originally added residue C',
            'combined_litter_and_POC': 'Not a direct litter target; do not add overlapping columns or substitute stable model humus for MAOM.',
            'nitrogen': 'Direct isotope-derived residueN fractions inventoried only; unknown mineralN/exchange and stable-residue isotope mixing prevent a validated N trajectory.'},
        'field_rotation_validation': False, 'final_numeric_outcomes_opened': False}
    write_json(output / 'observations.json', rows)
    write_json(output / 'inventory.json', inventory)
    write_json(output / 'reserved_identity_manifest.json', reserved)
    write_json(output / 'audit.json', summary)
    return rows, summary
