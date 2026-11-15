"""Nonvisual multi-year ecosystem comparisons with explicit experimental state."""
from copy import deepcopy
import csv
from dataclasses import asdict, replace
from datetime import date, timedelta
import json
from pathlib import Path

from ..ecosystem import EcosystemEngine, EcosystemState
from ..evaluator.audit import EvaluationKernel, reconcile
from ..evaluator.engine import BOUNDARY, FLUXES, digest, ledger, normalize, pool_ledgers, snapshot
from ..io import load_config, resolve, sha256, write_json
from ..simulation import prepare, schedule
from .evidence import interaction_contracts, mechanism_inventory
from .soil_biology import Decomposers, Parameters, SoilBiologyKernel, partition_humus

BIO_FLUXES = [k + '_kg_ha' for k in [
    'decomposer_assimilated_C', 'decomposer_assimilated_N', 'decomposer_turnover_C', 'decomposer_turnover_N',
    'decomposer_to_humus_C', 'decomposer_to_humus_N', 'decomposer_to_residue_C', 'decomposer_to_residue_N',
    'residue_consumed_C', 'residue_consumed_N', 'humus_respired_C', 'residue_respired_C',
    'humus_mineralized_N', 'residue_mineralized_N', 'residue_immobilized_N']]
UNSUPPORTED = {key: None for key in ['pest_density', 'predator_density', 'pathogen_damage', 'weed_biomass',
    'taxonomic_microbe_abundance', 'validated_microbial_activity', 'calibrated_prediction_interval', 'farmer_recommendation']}


def take_snapshot(engine, state):
    value = snapshot(engine, state)
    microbes = getattr(engine.physical, 'decomposers', None)
    value['functional_decomposers'] = asdict(microbes) if microbes is not None else None
    return value


def organic_subledgers(before, after, flux, harvests, legacy_control=False):
    if legacy_control:
        result = pool_ledgers(before, after, flux, harvests)
        for resource in ['carbon', 'nitrogen']:
            a, b = [sum(state['functional_decomposers'][resource + '_kg_ha']) for state in [before, after]]
            result['decomposer_' + resource] = {'initial': a, 'final': b, 'inputs': {}, 'outputs': {},
                                                'error': reconcile(b - a, 'inactive control decomposers')}
        return result
    result = {}
    for resource, suffix in [('carbon', 'C'), ('nitrogen', 'N')]:
        key = resource + '_kg_ha'; s = suffix.lower()
        initial_m, final_m = [sum(x['functional_decomposers'][key]) for x in [before, after]]
        growth = flux.get(f'decomposer_assimilated_{suffix}_kg_ha', 0.)
        turnover = flux.get(f'decomposer_turnover_{suffix}_kg_ha', 0.)
        result['decomposer_' + resource] = {'initial': initial_m, 'final': final_m,
            'inputs': {'assimilation': growth}, 'outputs': {'turnover': turnover},
            'error': reconcile(final_m - initial_m - growth + turnover, 'decomposer ' + resource)}
        initial_r, final_r = [sum(x['soil'][f'residue_{s}_kg_ha']) + x['soil'][f'surface_residue_{s}_kg_ha'] for x in [before, after]]
        returned = sum(t['material_kg_ha'][resource] for h in harvests for t in h['material_transfers']
                       if t['destination'] == 'surface_residue' or t['destination'].startswith('soil_residue_layer_'))
        turn_r = flux.get(f'decomposer_to_residue_{suffix}_kg_ha', 0.)
        consumed = flux.get(f'residue_consumed_{suffix}_kg_ha', 0.)
        result['residue_' + resource] = {'initial': initial_r, 'final': final_r,
            'inputs': {'harvest_returns': returned, 'decomposer_turnover': turn_r}, 'outputs': {'consumed': consumed},
            'error': reconcile(final_r - initial_r - returned - turn_r + consumed, 'residue ' + resource)}
        initial_h, final_h = [sum(x['soil'][f'humus_{s}_kg_ha']) for x in [before, after]]
        turn_h = flux.get(f'decomposer_to_humus_{suffix}_kg_ha', 0.)
        loss = flux.get('humus_respired_C_kg_ha' if suffix == 'C' else 'humus_mineralized_N_kg_ha', 0.)
        result['humus_' + resource] = {'initial': initial_h, 'final': final_h,
            'inputs': {'decomposer_turnover': turn_h}, 'outputs': {'respiration' if suffix == 'C' else 'mineralization': loss},
            'error': reconcile(final_h - initial_h - turn_h + loss, 'humus ' + resource)}
    return result


def run_case(physical, soil, weather, plants, events, parameters, initial_fraction, selected, output):
    options = {'root_beta_per_m': 4, 'organ_model': False, 'temperature_plateau': False,
               'observed_harvest_sowing_dates': [str(p.date) for p in plants], 'harvest_calendar_role': 'planned'}
    if parameters is not None:
        soil, microbes = partition_humus(soil, initial_fraction, parameters)
        options['soil_biology'] = asdict(parameters)
        physical = SoilBiologyKernel(physical.layers, physical.crops, physical.processes, physical.latitude,
                                     options=options, decomposers=microbes)
    else:
        physical = EvaluationKernel(physical.layers, physical.crops, physical.processes, physical.latitude, options=options)
    engine = EcosystemEngine(physical)
    state = engine.initialize(soil)
    initial = take_snapshot(engine, state)
    stops = {weather[-1].date, selected} | {date(y, 12, 31) for y in range(weather[0].date.year, weather[-1].date.year + 1)}
    stops |= {p.date - timedelta(days=1) for p in plants} | {p.harvest_deadline for p in plants}
    stops = sorted(d for d in stops if weather[0].date <= d <= weather[-1].date)
    periods, harvests, daily = [], [], []
    maxima = dict.fromkeys(BOUNDARY, 0.)
    cursor = 0; inspect_state = None
    for i, end in enumerate(stops):
        days = []
        while cursor < len(weather) and weather[cursor].date <= end:
            days.append(weather[cursor]); cursor += 1
        before = take_snapshot(engine, state); old_flux = dict(state.cumulative_fluxes)
        result = engine.run(days, state, plants, events, keep_daily=True)
        state = result['checkpoint']; after = take_snapshot(engine, state)
        flux = {key: state.cumulative_fluxes.get(key, 0.) - old_flux.get(key, 0.) for key in FLUXES + BIO_FLUXES}
        period = {'start': str(days[0].date), 'end': str(end), 'initial_state': before, 'final_state': after,
                  'ledger': ledger(before, after, flux), 'physical_fluxes': flux,
                  'biological_subledgers': organic_subledgers(before, after, flux, result['harvests'], parameters.legacy_residue_control) if parameters else None}
        # The extra functional stocks are required parts of a V2 checkpoint.
        payload = {'schema_version': 2, 'ecosystem': asdict(state), 'decomposers': after['functional_decomposers']}
        checkpoint = f'checkpoint-{i:02d}.json'; write_json(output / checkpoint, payload)
        saved = json.loads((output / checkpoint).read_text())
        state = EcosystemState(**saved['ecosystem'])
        if parameters is not None:
            engine.physical.decomposers = Decomposers(**saved['decomposers'])
            engine.physical.decomposers.validate(engine.physical.layers, parameters.microbial_cn)
        if take_snapshot(engine, state) != after:
            raise ArithmeticError('Checkpoint lost physical or decomposer state')
        period['checkpoint'] = checkpoint; periods.append(period)
        if end == selected: inspect_state = after
        harvests.extend(result['harvests'])
        fields = ['date', 'crop', 'soil_water_mm', 'snow_mm', 'soil_mineral_n_kg_ha', 'soil_organic_c_kg_ha',
                  'residue_c_kg_ha', 'plant_biomass_dry_kg_ha', 'water_stress_factor', 'n_growth_factor',
                  'water_balance_error', 'carbon_balance_error', 'nitrogen_balance_error',
                  'functional_decomposer_C_kg_ha', 'functional_decomposer_N_kg_ha'] + FLUXES + BIO_FLUXES
        daily.extend({k: r.get(k) for k in fields} for r in result['daily'])
        for key in maxima: maxima[key] = max(maxima[key], result['max_daily_balance_error'][key])
    if state.plant is not None or len(harvests) != len(plants):
        raise ValueError('Every planned crop must be harvested within the inspected scenario')
    with (output / 'daily.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(daily[0])); writer.writeheader(); writer.writerows(daily)
    final = take_snapshot(engine, state); flux = {k: state.cumulative_fluxes.get(k, 0.) for k in FLUXES + BIO_FLUXES}
    return {'initial_state': initial, 'final_state': final, 'selected_date_state': inspect_state,
            'harvests': harvests, 'periods': periods, 'physical_fluxes': flux,
            'ledger': ledger(initial, final, flux), 'maximum_daily_balance_error': maxima,
            'biological_subledgers': organic_subledgers(initial, final, flux, harvests, parameters.legacy_residue_control) if parameters else None,
            'checkpoint_engine_identity': engine.identity, 'daily_rows': len(daily),
            'active_ecological_interactions': ['experimental functional residue-decomposer C/N exchange'] if parameters and not parameters.legacy_residue_control else [],
            'parameters': asdict(parameters) if parameters else None, 'unsupported': dict(UNSUPPORTED),
            'evidence_class': 'hypothetical_unvalidated_biological_scenario' if parameters else 'stable_V1_physical_scenario',
            'explanation': {'mineral_N_change': sum(final['soil']['mineral_n_kg_ha']) - sum(initial['soil']['mineral_n_kg_ha']),
                'mineral_N_sources': {k: flux[k] for k in ['fertilizer_n_kg_ha', 'deposited_n_kg_ha', 'mineralized_n_kg_ha']},
                'mineral_N_sinks': {k: flux[k] for k in ['immobilized_n_kg_ha', 'plant_uptake_n_kg_ha', 'leached_n_kg_ha', 'denitrified_n_kg_ha']},
                'total_C_change': final['inventory']['carbon'] - initial['inventory']['carbon'],
                'total_C_sources': {k: flux[k] for k in ['seed_c_kg_ha', 'net_assimilated_c_kg_ha']},
                'total_C_sinks': {k: flux[k] for k in ['respired_c_kg_ha', 'harvested_c_kg_ha']}}}


def state_summary(run):
    s = run['final_state']['soil']; b = run['final_state']['functional_decomposers']
    return {'water_mm': sum(s['water_mm']), 'mineral_N_kg_ha': sum(s['mineral_n_kg_ha']),
            'humus_C_kg_ha': sum(s['humus_c_kg_ha']),
            'residue_C_kg_ha': sum(s['residue_c_kg_ha']) + s['surface_residue_c_kg_ha'],
            'functional_decomposer_C_kg_ha': sum(b['carbon_kg_ha']) if b else None,
            'respiration_C_kg_ha': run['physical_fluxes']['respired_c_kg_ha'],
            'uptake_N_kg_ha': run['physical_fluxes']['plant_uptake_n_kg_ha']}


def contrast(a, b, kind):
    if a['weather_sha256'] != b['weather_sha256']:
        raise ValueError('Unmatched environmental comparison')
    aa, bb = state_summary(a), state_summary(b)
    return {'kind': kind, 'baseline': a['case_id'], 'alternative': b['case_id'],
            'matched_weather': True, 'matched_management': a['management'] == b['management'],
            'matched_initial_total_water_C_N': all(abs(a['initial_state']['inventory'][k] - b['initial_state']['inventory'][k]) < 1e-7
                                                 for k in ['water', 'carbon', 'nitrogen']),
            'initial_pool_partition_identical': a['initial_state']['soil'] == b['initial_state']['soil'],
            'differences': {k: bb[k] - aa[k] if aa[k] is not None and bb[k] is not None else None for k in aa},
            'harvest_contrasts': [{'date': y['planted'], 'baseline_crop': x['crop'], 'alternative_crop': y['crop'],
                'dry_yield_difference_kg_ha': y['grain_dry_kg_ha'] - x['grain_dry_kg_ha'] if x['crop'] == y['crop'] else None}
                for x, y in zip(a['harvests'], b['harvests'])],
            'field_causal_effect_validated': False,
            'interpretation': 'Computed intervention only. New functional biomass is an explicit hypothetical humus partition; not inferred field microbial abundance.'}


def inspect_ecosystem(config_path, output, selected_date=None):
    config, base = load_config(config_path)
    scenario_path = resolve(base, config['scenario_config'])
    c, scenario_base = load_config(scenario_path); c, registry = normalize(c)
    physical, soil, weather, provenance, area = prepare(c, scenario_base)
    selected = date.fromisoformat(selected_date or config['inspect_date'])
    if not date.fromisoformat(c['start']) <= selected <= date.fromisoformat(c['end']):
        raise ValueError('Inspection date must be inside the scenario')
    original = list(weather)
    for t in c.get('climate_sensitivities', []):
        for name, days in original:
            weather.append((name + '__' + t['id'], [replace(d, rain_mm=d.rain_mm * t['rain_multiplier'],
                tmin_C=d.tmin_C + t['temperature_delta_C'], tmax_C=d.tmax_C + t['temperature_delta_C']) for d in days]))
    profiles = config['profiles']
    ids = [p['id'] for p in profiles]
    if len(set(ids)) != len(ids) or 'stable_V1' not in ids:
        raise ValueError('Unique profiles and a stable_V1 reference required')
    if len(profiles) * len(weather) * len(c['strategies']) > 200:
        raise ValueError('Inspector budget is at most200 matched scenarios')
    schedules = []
    for strategy in c['strategies']:
        controls = deepcopy(c)
        for crop, changes in strategy.get('management_by_crop', {}).items():
            controls['management_by_crop'][registry.require_quantitative(crop)].update(changes)
        plants, events, resources = schedule(strategy['sequence'], controls, physical.crops, area)
        if any(not date.fromisoformat(c['start']) <= p.date <= p.harvest_deadline <= date.fromisoformat(c['end']) for p in plants):
            raise ValueError('Crop calendar outside scenario')
        schedules.append((strategy, plants, events, resources))
    # Validate before starting or creating a directory; no implicit coefficients.
    resolved = []
    for profile in profiles:
        if profile['id'] == 'stable_V1':
            if profile.get('parameters') or profile.get('initial_humus_C_fraction', 0):
                raise ValueError('Stable V1 profile cannot activate biology')
            resolved.append((profile['id'], None, 0.))
        else:
            if profile.get('state_origin') != 'scenario_assumed' or profile.get('mode') != 'experimental':
                raise ValueError('Unvalidated soil biology requires an explicitly hypothetical experimental state')
            parameters = Parameters(**profile['parameters'])
            fraction = profile['initial_humus_C_fraction']
            partition_humus(soil, fraction, parameters)  # fail closed on missing/overdrawn initial material
            resolved.append((profile['id'], parameters, fraction))
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'configuration.json', config)
    write_json(out / 'mechanism_inventory.json', mechanism_inventory())
    write_json(out / 'interaction_contracts.json', interaction_contracts())
    write_json(out / 'crop_catalog.json', registry.catalog)
    cases, forcing = [], []
    for name, days in weather:
        values = [{**asdict(d), 'date': str(d.date)} for d in days]
        forcing.append({'scenario_id': name, 'sha256': digest(values), 'days': len(values)})
        for strategy, plants, events, resources in schedules:
            for profile_id, parameters, fraction in resolved:
                case_id = f'case-{len(cases):03d}'; destination = out / case_id; destination.mkdir()
                write_json(destination / 'weather.json', values)
                write_json(destination / 'management.json', [asdict(e) for e in events])
                result = run_case(physical, soil, days, plants, events, parameters, fraction, selected, destination)
                cases.append({'case_id': case_id, 'profile': profile_id, 'strategy_id': strategy['id'],
                              'sequence': strategy['sequence'], 'weather_id': name, 'weather_sha256': digest(values),
                              'management': [asdict(e) | {'date': str(e.date)} for e in events], 'resources': resources, **result})
    contrasts = []
    for b in cases:
        if b['profile'] != 'stable_V1':
            a = next(r for r in cases if r['profile'] == 'stable_V1' and r['strategy_id'] == b['strategy_id'] and r['weather_id'] == b['weather_id'])
            contrasts.append(contrast(a, b, 'biological_extension_minus_stable_V1'))
            if b['profile'] != 'partition_only' and 'partition_only' in ids:
                p = next(r for r in cases if r['profile'] == 'partition_only' and r['strategy_id'] == b['strategy_id'] and r['weather_id'] == b['weather_id'])
                contrasts.append(contrast(p, b, 'active_process_minus_initial_partition_knockout'))
        for a in cases:
            if a['profile'] == b['profile'] and a['weather_id'] == b['weather_id'] and a['strategy_id'] < b['strategy_id']:
                contrasts.append(contrast(a, b, 'rotation'))
    report = {'schema_version': 2, 'status': 'experimental_ecosystem_inspection_not_field_validation',
              'location': c['location'], 'inspect_date': str(selected), 'scenario_configuration_sha256': digest(c),
              'forcing': forcing, 'NASA_provenance': provenance, 'climate_sensitivities': c.get('climate_sensitivities', []),
              'cases': cases, 'comparisons': contrasts, 'validated_observations_used_to_initialize_scenarios': [],
              'unsupported': dict(UNSUPPORTED), 'model_promoted': False,
              'limitations': ['Functional decomposer biomass is a declared hypothetical stock, not a measured local population or species composition.',
                  'CUE, uptake capacity, turnover and substrate initialization are not identified from current KBS assays.',
                  'Existing V1 hydraulic and crop-growth accuracy failures remain; conservation alone does not validate biology.',
                  'The initial humus-to-biomass partition itself changes background mineralization; partition-only knockout separates that effect.',
                  'Weather is retrospective history replay with explicit drought/wet/heat perturbations, not an operational future forecast.',
                  'Rotation packages can differ in crop-specific management; same-crop contrasts do not prove causal field rotation benefits.']}
    write_json(out / 'report.json', report)
    with (out / 'states.csv').open('w') as f:
        fields = ['case_id', 'profile', 'strategy_id', 'weather_id', 'date', 'water_mm', 'mineral_N_kg_ha',
                  'humus_C_kg_ha', 'residue_C_kg_ha', 'functional_decomposer_C_kg_ha', 'functional_decomposer_N_kg_ha']
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for case in cases:
            for period in case['periods']:
                state = period['final_state']; s = state['soil']; mb = state['functional_decomposers']
                writer.writerow({**{k: case[k] for k in fields[:4]}, 'date': state['date'],
                    'water_mm': sum(s['water_mm']), 'mineral_N_kg_ha': sum(s['mineral_n_kg_ha']),
                    'humus_C_kg_ha': sum(s['humus_c_kg_ha']),
                    'residue_C_kg_ha': sum(s['residue_c_kg_ha']) + s['surface_residue_c_kg_ha'],
                    'functional_decomposer_C_kg_ha': sum(mb['carbon_kg_ha']) if mb else None,
                    'functional_decomposer_N_kg_ha': sum(mb['nitrogen_kg_ha']) if mb else None})
    transfers = []
    for case in cases:
        for h in case['harvests']:
            for t in h['material_transfers']:
                transfers.append({'case_id': case['case_id'], 'date': h['harvested'], **t})
    write_json(out / 'harvest_transfers.json', transfers)
    with (out / 'process_transfers.csv').open('w') as f:
        fields = ['case_id', 'source', 'destination', 'carbon_kg_ha', 'nitrogen_kg_ha', 'water_mm', 'evidence_class']
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for case in cases:
            flux = case['physical_fluxes']
            movements = [('soil_mineral_N', 'plant', 0., flux['plant_uptake_n_kg_ha'], 0.),
                         ('soil_water', 'atmosphere_transpiration', 0., 0., flux['transpiration_mm']),
                         ('soil_water', 'atmosphere_evaporation', 0., 0., flux['evaporation_mm'])]
            if case['parameters'] is not None and not case['parameters']['legacy_residue_control']:
                movements += [
                    ('residue', 'functional_decomposers', flux['decomposer_assimilated_C_kg_ha'],
                     flux['residue_consumed_N_kg_ha'] - flux['residue_mineralized_N_kg_ha'], 0.),
                    ('soil_mineral_N', 'functional_decomposers', 0., flux['residue_immobilized_N_kg_ha'], 0.),
                    ('residue', 'soil_mineral_N', 0., flux['residue_mineralized_N_kg_ha'], 0.),
                    ('residue', 'atmosphere_CO2', flux['residue_respired_C_kg_ha'], 0., 0.),
                    ('functional_decomposers', 'humus', flux['decomposer_to_humus_C_kg_ha'], flux['decomposer_to_humus_N_kg_ha'], 0.),
                    ('functional_decomposers', 'soil_residue', flux['decomposer_to_residue_C_kg_ha'], flux['decomposer_to_residue_N_kg_ha'], 0.),
                    ('humus', 'atmosphere_CO2', flux['humus_respired_C_kg_ha'], 0., 0.),
                    ('humus', 'soil_mineral_N', 0., flux['humus_mineralized_N_kg_ha'], 0.)]
            for source, destination, cflow, nflow, wflow in movements:
                writer.writerow(dict(zip(fields, [case['case_id'], source, destination, cflow, nflow, wflow, case['evidence_class']])))
    lines = ['AgroSim V2 numerical ecosystem inspector',
             f'Cases: {len(cases)}; selected date: {selected}; matched comparisons: {len(contrasts)}.',
             'All quantities below are computed scenarios; no ecological process is empirically promoted.']
    for case in cases:
        summary = state_summary(case); e = case['explanation']
        chosen = case['selected_date_state']['soil']
        lines.append(f'{case["case_id"]} {case["profile"]} {case["strategy_id"]} {case["weather_id"]}: '
                     f'at {selected}, water={sum(chosen["water_mm"]):.6f}mm, '
                     f'mineral N={sum(chosen["mineral_n_kg_ha"]):.6f}kg/ha, '
                     f'humus C={sum(chosen["humus_c_kg_ha"]):.6f}kg/ha.')
        lines.append(f'{case["case_id"]} {case["profile"]}: final mineral N={summary["mineral_N_kg_ha"]:.6f}kg/ha; '
                     f'change={e["mineral_N_change"]:.6f}; sources={sum(e["mineral_N_sources"].values()):.6f}, '
                     f'sinks={sum(e["mineral_N_sinks"].values()):.6f}.')
    lines += ['State/flux explanations derive from report.json ledgers. Unsupported populations and effects are null.'] + report['limitations']
    (out / 'INSPECTION.txt').write_text('\n'.join(lines) + '\n')
    write_json(out / 'artifact_manifest.json', {'sha256': {str(p.relative_to(out)): sha256(p) for p in sorted(out.rglob('*')) if p.is_file()}})
    return {'status': report['status'], 'cases': len(cases), 'comparisons': len(contrasts),
            'report': str(out / 'report.json'), 'model_promoted': False}
