"""A bounded evaluation adapter around AgroSim's existing conservative kernel.

No fitting, synthetic training, outcome assimilation or organism dynamics occur.
Weather, soil, management, checkpoints and all reported physical fluxes are saved.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path
import hashlib
import importlib.metadata
import json
import re
import sys

from ..crop_catalog.registry import CropRegistry
from ..data.power import MAPPING
from ..ecosystem import EcosystemEngine, EcosystemState, decode_plant
from ..io import load_config, sha256, write_json
from ..processes import SoilState
from ..simulation import prepare, schedule
from .audit import EvaluationKernel, TOLERANCE, reconcile

BOUNDARY = {
    'water': ('mm', ['rain_mm', 'irrigation_mm'],
              ['runoff_mm', 'drainage_mm', 'evaporation_mm', 'transpiration_mm']),
    'nitrogen': ('kg N/ha', ['fertilizer_n_kg_ha', 'deposited_n_kg_ha', 'seed_n_kg_ha', 'fixed_n_kg_ha'],
                 ['leached_n_kg_ha', 'denitrified_n_kg_ha', 'harvested_n_kg_ha']),
    'carbon': ('kg C/ha', ['seed_c_kg_ha', 'net_assimilated_c_kg_ha'],
               ['respired_c_kg_ha', 'harvested_c_kg_ha']),
}
INTERNAL = ['plant_uptake_n_kg_ha', 'mineralized_n_kg_ha', 'immobilized_n_kg_ha',
            'residue_C_decomposed_kg_ha', 'residue_N_decomposed_kg_ha',
            'humus_C_net_change_kg_ha', 'humus_N_net_change_kg_ha', 'snow_melt_mm']
FLUXES = sorted({k for _, ins, outs in BOUNDARY.values() for k in ins + outs} | set(INTERNAL))
UNSUPPORTED = {k: None for k in ['pest_abundance', 'pathogen_damage', 'microbial_abundance',
                               'pollinator_abundance', 'organism_yield_effect', 'erosion_mass',
                               'field_prediction_interval', 'validated_rotation_benefit',
                               'farmer_recommendation']}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def snapshot(engine, state):
    soil = SoilState(**state.soil)
    return {'date': state.last_date, 'soil': deepcopy(state.soil), 'plant': deepcopy(state.plant),
            'inventory': engine.physical.inventories(soil, decode_plant(state.plant)),
            'plant_available_water_above_wilting_mm':
                [max(0., w - layer.mm(layer.wilting_point)) for w, layer in zip(soil.water_mm, engine.physical.layers)],
            'crop_history': deepcopy(state.crop_history)}


def ledger(before, after, flux):
    result = {}
    for resource, (unit, inputs, outputs) in BOUNDARY.items():
        ins = {k: flux.get(k, 0.) for k in inputs}
        outs = {k: flux.get(k, 0.) for k in outputs}
        initial, final = before['inventory'][resource], after['inventory'][resource]
        error = reconcile(final - initial - sum(ins.values()) + sum(outs.values()), resource)
        result[resource] = {'unit': unit, 'initial': initial, 'final': final, 'inputs': ins,
                            'outputs': outs, 'error': error, 'tolerance': TOLERANCE}
    # The mineral pool is a sub-ledger: fixation first enters the plant, not soil.
    initial = sum(before['soil']['mineral_n_kg_ha'])
    final = sum(after['soil']['mineral_n_kg_ha'])
    ins = {k: flux.get(k, 0.) for k in ['fertilizer_n_kg_ha', 'deposited_n_kg_ha', 'mineralized_n_kg_ha']}
    outs = {k: flux.get(k, 0.) for k in ['immobilized_n_kg_ha', 'plant_uptake_n_kg_ha', 'leached_n_kg_ha', 'denitrified_n_kg_ha']}
    result['mineral_nitrogen'] = {'unit': 'kg N/ha', 'initial': initial, 'final': final,
        'inputs': ins, 'outputs': outs,
        'error': reconcile(final - initial - sum(ins.values()) + sum(outs.values()), 'mineral nitrogen'),
        'tolerance': TOLERANCE}
    return result


def pool_ledgers(before, after, flux, harvests):
    """Resolve organic sub-pools without counting internal returns as new inputs."""
    result = {}
    for resource, suffix, field in [('carbon', 'c', 'carbon'), ('nitrogen', 'n', 'nitrogen')]:
        transfers = [t for h in harvests for t in h['material_transfers']]
        for pool in ['surface_residue', 'soil_residue']:
            def amount(state):
                s = state['soil']
                return s[f'surface_residue_{suffix}_kg_ha'] if pool == 'surface_residue' else sum(s[f'residue_{suffix}_kg_ha'])
            destinations = (lambda t: t['destination'] == 'surface_residue') if pool == 'surface_residue' else (
                lambda t: t['destination'].startswith('soil_residue_layer_'))
            returned = sum(t['material_kg_ha'][field] for t in transfers if destinations(t))
            # Per-layer decay is not instrumented separately; the supported
            # decomposition output is for combined surface + soil residue.
            result[f'{pool}_{resource}'] = {'initial': amount(before), 'final': amount(after),
                                          'harvest_returns': returned,
                                          'unit': 'kg C/ha' if suffix == 'c' else 'kg N/ha'}
        residue = [result[f'{pool}_{resource}'] for pool in ['surface_residue', 'soil_residue']]
        initial, final = sum(r['initial'] for r in residue), sum(r['final'] for r in residue)
        returned = sum(r['harvest_returns'] for r in residue)
        decomposed = flux[f'residue_{suffix.upper()}_decomposed_kg_ha']
        result[f'combined_residue_{resource}'] = {'initial': initial, 'final': final,
            'inputs': {'harvest_root_and_shoot_returns': returned}, 'outputs': {'decomposed': decomposed},
            'error': reconcile(final - initial - returned + decomposed, f'residue {resource}'),
            'unit': 'kg C/ha' if suffix == 'c' else 'kg N/ha', 'tolerance': TOLERANCE}
        humus_initial, humus_final = [sum(s['soil'][f'humus_{suffix}_kg_ha']) for s in [before, after]]
        delta = flux[f'humus_{suffix.upper()}_net_change_kg_ha']
        result[f'humus_{resource}'] = {'initial': humus_initial, 'final': humus_final, 'net_internal_transfer': delta,
            'error': reconcile(humus_final - humus_initial - delta, f'humus {resource}'),
            'unit': 'kg C/ha' if suffix == 'c' else 'kg N/ha', 'tolerance': TOLERANCE}
    return result


def normalize(config):
    c = deepcopy(config)
    registry = CropRegistry()
    # The evaluator is deliberately the stable three-profile demonstration. It
    # cannot silently activate any optional physiology or unsupported biology.
    for key in ['biology', 'organisms', 'correction']:
        if c.get(key):
            raise ValueError(f'{key} is disabled in Ecosystem Evaluator V1')
    profile = c.get('biological_profile', {})
    if set(profile) - {'organ_model', 'temperature_plateau'} or any(profile.values()):
        raise ValueError('V1 uses the existing stable, aggregate three-crop profile')
    def crop(name):
        return registry.require_quantitative(name)
    def keyed(mapping):
        result = {}
        for name, value in mapping.items():
            key = crop(name)
            if key in result:
                raise ValueError('Duplicate crop aliases in configuration')
            result[key] = value
        return result
    for key in ['management_by_crop', 'seeds_available_dry_kg', 'crop_parameters']:
        if key in c:
            c[key] = keyed(c[key])
    for slot in c['slots']:
        slot['allowed_crops'] = [crop(n) for n in slot['allowed_crops']]
        if 'management_overrides' in slot:
            slot['management_overrides'] = keyed(slot['management_overrides'])
    ids = []
    for strategy in c['strategies']:
        name = strategy['id']
        if not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9_-]+', name):
            raise ValueError('Strategy identifiers must use letters, digits, underscores or hyphens')
        ids.append(name)
        strategy['sequence'] = [crop(n) for n in strategy['sequence']]
    if len(ids) < 3 or len(set(ids)) != len(ids):
        raise ValueError('At least three uniquely named strategies required')
    if c['current_strategy'] not in ids:
        raise ValueError('Current strategy must be included')
    if date.fromisoformat(c['end']).year <= date.fromisoformat(c['start']).year:
        raise ValueError('V1 evaluation requires a multi-year rotation')
    transforms = c.get('climate_sensitivities', [])
    tids = [t['id'] for t in transforms]
    if len(set(tids)) != len(tids) or any(not re.fullmatch('[A-Za-z0-9_-]+', t) for t in tids):
        raise ValueError('Unique safe climate sensitivity identifiers required')
    if any('__' in item['id'] for item in c['weather_scenarios']):
        raise ValueError('Double underscore is reserved for climate sensitivity identifiers')
    if any(not 0 <= t['rain_multiplier'] <= 3 or not -10 <= t['temperature_delta_C'] <= 10 for t in transforms):
        raise ValueError('Climate sensitivity outside existing scenario bounds')
    return c, registry


def simulate(physical, soil, days, plants, events, directory):
    options = {'root_beta_per_m': 4, 'organ_model': False, 'temperature_plateau': False,
               'observed_harvest_sowing_dates': [str(p.date) for p in plants],
               'harvest_calendar_role': 'planned'}
    engine = EcosystemEngine(EvaluationKernel(physical.layers, physical.crops, physical.processes,
                                             physical.latitude, options=options))
    state = engine.initialize(soil)
    initial = snapshot(engine, state)
    boundaries = {days[-1].date} | {date(y, 12, 31) for y in range(days[0].date.year, days[-1].date.year + 1)}
    boundaries |= {p.date - timedelta(days=1) for p in plants} | {p.harvest_deadline for p in plants}
    boundaries = sorted(d for d in boundaries if days[0].date <= d <= days[-1].date)
    periods, harvests, daily, preplant = [], [], [], []
    maximum = dict.fromkeys(BOUNDARY, 0.)
    cursor = 0
    for index, stop in enumerate(boundaries):
        chunk = []
        while cursor < len(days) and days[cursor].date <= stop:
            chunk.append(days[cursor]); cursor += 1
        if not chunk:
            continue
        before = snapshot(engine, state)
        old_flux = dict(state.cumulative_fluxes)
        result = engine.run(chunk, state, plants, events, keep_daily=True)
        state = result['checkpoint']
        after = snapshot(engine, state)
        flux = {k: state.cumulative_fluxes.get(k, 0.) - old_flux.get(k, 0.) for k in FLUXES}
        checkpoint = f'checkpoint-{index:02d}.json'
        if directory is not None:
            state.checkpoint(directory / checkpoint)
            restored = EcosystemState.restore(directory / checkpoint)
            if asdict(restored) != asdict(state):
                raise ArithmeticError('Checkpoint serialization changed ecosystem state')
            state = restored  # All later growth actually uses the restored state.
        periods.append({'start': str(chunk[0].date), 'end': str(stop), 'initial_state': before,
                        'final_state': after, 'ledger': ledger(before, after, flux),
                        'pool_ledgers': pool_ledgers(before, after, flux, result['harvests']),
                        'internal_fluxes': {k: flux[k] for k in INTERNAL}, 'checkpoint': checkpoint})
        for p in plants:
            if stop == p.date - timedelta(days=1):
                preplant.append({'planting_date': str(p.date), 'crop': p.crop, 'state': after})
        harvests.extend(result['harvests'])
        # Only physical telemetry is exported. Legacy numerical organism proxies
        # are not presented as measured or supported ecological quantities.
        fields = ['date', 'crop', 'soil_water_mm', 'snow_mm', 'soil_mineral_n_kg_ha',
                  'soil_organic_c_kg_ha', 'residue_c_kg_ha', 'plant_biomass_dry_kg_ha',
                  'lai', 'water_stress_factor', 'n_growth_factor', 'root_depth_m',
                  'water_balance_error', 'carbon_balance_error', 'nitrogen_balance_error'] + FLUXES
        daily.extend({k: row[k] for k in fields if k in row} for row in result['daily'])
        for key in maximum:
            maximum[key] = max(maximum[key], result['max_daily_balance_error'][key])
    if state.plant is not None or len(harvests) != len(plants):
        raise ValueError('A complete V1 rotation must harvest every supplied crop within the scenario')
    final = snapshot(engine, state)
    flux = {k: state.cumulative_fluxes.get(k, 0.) for k in FLUXES}
    if directory is not None:
        with (directory / 'daily.jsonl').open('w') as stream:
            for row in daily:
                stream.write(json.dumps(row, allow_nan=False) + '\n')
    return {'initial_state': initial, 'final_state': final, 'periods': periods,
            'preplant_states': preplant, 'harvests': harvests,
            'ledger': ledger(initial, final, flux), 'physical_fluxes': flux,
            'pool_ledgers': pool_ledgers(initial, final, flux, harvests),
            'maximum_daily_balance_error': maximum,
            'mechanism_evidence': engine.evidence(), 'engine_identity': engine.identity,
            'daily_rows': len(daily), 'trait_architecture': engine.traits.manifest(),
            'unsupported': dict(UNSUPPORTED)}


def differences(a, b):
    def totals(run):
        s = run['final_state']['soil']
        return {'soil_water_mm': sum(s['water_mm']), 'mineral_N_kg_ha': sum(s['mineral_n_kg_ha']),
                'humus_C_kg_ha': sum(s['humus_c_kg_ha']),
                'residue_C_kg_ha': sum(s['residue_c_kg_ha']) + s['surface_residue_c_kg_ha'],
                'soil_uptake_N_kg_ha': run['physical_fluxes']['plant_uptake_n_kg_ha'],
                'ET_mm': run['physical_fluxes']['transpiration_mm'] + run['physical_fluxes']['evaporation_mm']}
    x, y = totals(a), totals(b)
    return {k: y[k] - x[k] for k in x}


def compare(a, b, kind):
    if kind == 'rotation' and a['weather_sha256'] != b['weather_sha256']:
        raise ArithmeticError('Rotation comparisons must have identical daily forcing')
    crops = []
    for x, y in zip(a['harvests'], b['harvests']):
        same = x['crop'] == y['crop'] and x['planted'] == y['planted']
        crops.append({'planting_date': y['planted'], 'baseline_crop': x['crop'], 'alternative_crop': y['crop'],
                      'grain_dry_difference_kg_ha': y['grain_dry_kg_ha'] - x['grain_dry_kg_ha'] if same else None,
                      'reason': 'Same crop and calendar; simulated scenario difference, not a validated causal effect' if same
                      else 'Unlike crops are not compared as a yield improvement'})
    following = []
    for x, y in zip(a['preplant_states'], b['preplant_states']):
        following.append({'planting_date': y['planting_date'], 'baseline_crop': x['crop'], 'alternative_crop': y['crop'],
                          'soil_water_difference_mm': sum(y['state']['soil']['water_mm']) - sum(x['state']['soil']['water_mm']),
                          'mineral_N_difference_kg_ha': sum(y['state']['soil']['mineral_n_kg_ha']) - sum(x['state']['soil']['mineral_n_kg_ha']),
                          'preceding_histories': [x['state']['crop_history'], y['state']['crop_history']]})
    return {'kind': kind, 'baseline': a['run_id'], 'alternative': b['run_id'],
            'matched_daily_weather': a['weather_sha256'] == b['weather_sha256'],
            'matched_initial_soil': a['initial_state'] == b['initial_state'],
            'final_and_flux_differences': differences(a, b), 'crop_contrasts': crops,
            'following_crop_entry_differences': following,
            'causal_rotation_effect_validated': False,
            'interpretation': 'Water, uptake, fixation, decomposition and actual residue returns alter persistent pools; '
                              'crop-specific supplied fertilizer can also differ. These are scenario comparisons, '
                              'not estimates of the isolated previous-crop treatment effect.'}


def evaluate(config, base, output=None):
    """Evaluate without fitting. With output, save all forcing and restart artifacts."""
    c, registry = normalize(config)
    physical, soil, weather, provenance, area = prepare(c, base)
    # Whole daily lists are transformed once, then reused by every alternative.
    base_weather = list(weather)
    for t in c.get('climate_sensitivities', []):
        for name, days in base_weather:
            weather.append((name + '__' + t['id'], [replace(d, rain_mm=d.rain_mm * t['rain_multiplier'],
                tmin_C=d.tmin_C + t['temperature_delta_C'], tmax_C=d.tmax_C + t['temperature_delta_C']) for d in days]))
    if len(weather) * len(c['strategies']) > 100:
        raise ValueError('V1 budget is at most 100 complete rotation scenarios')
    # Fail before creating output, rather than silently omitting infeasible crops.
    schedules = []
    for strategy in c['strategies']:
        controls = deepcopy(c)
        for name, changes in strategy.get('management_by_crop', {}).items():
            controls['management_by_crop'][registry.require_quantitative(name)].update(changes)
        plants, events, resources = schedule(strategy['sequence'], controls, physical.crops, area)
        for i, p in enumerate(plants):
            if not days[0].date <= p.date <= p.harvest_deadline <= days[-1].date:
                raise ValueError('Crop calendar is outside complete scenario weather')
            if i and p.date <= plants[i - 1].harvest_deadline:
                raise ValueError('Overlapping crop slots')
        schedules.append((strategy, plants, events, resources))
    directory = Path(output) if output is not None else None
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / 'configuration.json', c)
        write_json(directory / 'crop_catalog.json', registry.catalog)
        (directory / 'inputs').mkdir()
    runs, sources = [], []
    for p, item in zip(provenance, c['weather_scenarios']):
        p = deepcopy(p)
        p['path'] = item['path']  # Preserve the supplied reference, never output-directory paths.
        asset = Path(item['path']) if Path(item['path']).is_absolute() else Path(base) / item['path']
        copied = 'inputs/POWER-' + p['sha256'] + '.json'
        if directory is not None:
            (directory / copied).write_bytes(asset.read_bytes())
        p['preserved_raw_asset'] = copied
        p.update(role='historical-weather scenario forcing, not matched farm observations',
                 spatial_support='NASA POWER coarse parameter-specific grid sampled at supplied point; no field-scale resolution claimed',
                 temporal_support='Daily UTC; rain rate integrated over one day; solar energy integrated over one day',
                 variables={name: {'model_variable': var, 'normalized_unit': unit} for name, (var, unit) in MAPPING.items()},
                 units_note='Solar kW-hr/m^2/day is multiplied by 3.6 when necessary; temperature in C; no SMAP depth substitution',
                 independent_field_validation=False)
        sources.append(p)
    for name, days in weather:
        values = [{**asdict(d), 'date': str(d.date)} for d in days]
        forcing_hash = digest(values)
        for strategy, plants, events, resources in schedules:
            run_id = f'run-{len(runs):03d}'
            destination = directory / run_id if directory is not None else None
            if destination is not None:
                destination.mkdir()
                write_json(destination / 'weather.json', values)
                write_json(destination / 'management.json', [asdict(e) for e in events])
                write_json(destination / 'plantings.json', [asdict(p) for p in plants])
            result = simulate(physical, soil, days, plants, events, destination)
            runs.append({'run_id': run_id, 'strategy_id': strategy['id'], 'sequence': strategy['sequence'],
                         'scenario_id': name, 'weather_sha256': forcing_hash, 'resources': resources,
                         'management': [asdict(e) | {'date': str(e.date)} for e in events], **result})
    comparisons = [compare(a, b, 'rotation') for a, b in combinations(runs, 2) if a['scenario_id'] == b['scenario_id']]
    sensitivities = []
    for b in runs:
        if '__' in b['scenario_id']:
            a = next(r for r in runs if r['strategy_id'] == b['strategy_id'] and r['scenario_id'] == b['scenario_id'].split('__')[0])
            sensitivities.append(compare(a, b, 'climate_sensitivity'))
    catalog = [{'identifier': k, 'common_name': v['common_name'], **registry.support(k)} for k, v in registry.crops.items()]
    report = {'schema_version': 1, 'product': 'AgroSim Ecosystem Evaluator V1',
              'status': 'experimental_physical_ecosystem_evaluation', 'location': c['location'],
              'prediction_horizon': c['prediction_horizon'], 'decision_date': c['decision_date'],
              'area_ha': area, 'configuration_sha256': digest(c),
              'scenario_mode': 'historical weather replay and declared perturbations; no future-weather forecast',
              'source_provenance': sources, 'climate_sensitivities': c.get('climate_sensitivities', []),
              'physical_configuration': {'layers': [asdict(l) for l in physical.layers],
                  'crops': {k: asdict(v) for k, v in physical.crops.items()}, 'processes': asdict(physical.processes)},
              'catalog': catalog, 'runs': runs, 'rotation_comparisons': comparisons,
              'climate_sensitivity_comparisons': sensitivities,
              'evidence': {'computed_physical_stocks_and_fluxes': 'All run states, transfers and differences are simulations under explicit inputs.',
                  'validated_observations': [],
                  'NASA_forcing_quality': 'Adapter checks units, date continuity, coordinates, missing values and physical ranges; this is not field validation.',
                  'experimental_process_assumptions': {'soil_provenance': c['soil']['provenance'],
                      'crop_calendar': 'Supplied planned dates, not measured maturity or farm observations',
                      'management': 'Supplied crop-specific fertilizer/irrigation and residue removal; not inferred missing farm records',
                      'residue_partition': 'Existing fixed root fraction and retained shoot/root allocation, not measured root or litter production',
                      'decomposition': 'Existing temperature/moisture/oxygen-modified C/N decay and humification; not independently validated multi-year rates'},
                  'unsupported_ecological_quantities': dict(UNSUPPORTED)},
              'accounting': {'stocks': 'Water includes snow; total N includes humus, residue, mineral and live plant N; C includes humus, residue and live plant C.',
                  'carbon_basis': 'Dry matter uses existing C fraction 0.45; net assimilation is the C input, not gross photosynthesis.',
                  'internal_transfers': 'Root/shoot returns, mineralization, immobilization, uptake and snowmelt are internal; never added twice to boundary fluxes.',
                  'water_supply': 'Soil ledger uses field-applied mm; management supply reports gross m3 at configured efficiency. Off-field losses are outside soil boundary.',
                  'units': 'Stocks and transfers per hectare (C/N in kg/ha; water in mm), except explicitly named management supply kg/m3 totals.',
                  'tolerance': TOLERANCE},
              'reproduction': {'python': sys.version.split()[0],
                  'dependencies': {name: importlib.metadata.version(name) for name in ['numpy', 'pandas', 'PyYAML']},
                  'implementation_sha256': {str(p.relative_to(Path(__file__).parents[1])): sha256(p)
                      for p in sorted(Path(__file__).parents[1].rglob('*.py'))},
                  'catalog_sha256': digest(registry.catalog), 'stochastic_components': False,
                  'fitting_or_benchmark_access': False},
              'limitations': ['Conservation and deterministic execution verify numerical accounting, not real-world predictive accuracy.',
                  'Existing water and rotation-effect failures remain unresolved; no benchmark is retuned in V1.',
                  'No independent matched observations are assimilated or validated by this scenario demonstration.',
                  'Scenario perturbations have no estimated probability and their spread is not a prediction interval.',
                  'Default crop profiles, soil initial conditions and management are not global or farmer calibration.',
                  'Unlike-crop yields are not summed or presented as an improvement; no universal best rotation is selected.'],
              'unsupported': dict(UNSUPPORTED), 'farmer_recommendation_eligible': False}
    validate_report(report)
    if directory is not None:
        write_json(directory / 'report.json', report)
        files = {str(p.relative_to(directory)): sha256(p) for p in sorted(directory.rglob('*')) if p.is_file()}
        write_json(directory / 'artifact_manifest.json', {'sha256': files})
    return report


def validate_report(report):
    """Fail closed on accounting, eligibility and comparison-contract violations."""
    if len(report['catalog']) != 92 or sum(c['quantitative_eligible'] for c in report['catalog']) != 3:
        raise ValueError('The V1 crop-support boundary changed')
    if any(v is not None for v in report['unsupported'].values()) or report['farmer_recommendation_eligible']:
        raise ValueError('Unsupported output or farmer recommendation activated')
    if len({r['strategy_id'] for r in report['runs']}) < 3:
        raise ValueError('Fewer than three feasible strategies')
    for run in report['runs']:
        for scope in [run] + run['periods']:
            for name, row in scope['ledger'].items():
                error = row['final'] - row['initial'] - sum(row['inputs'].values()) + sum(row['outputs'].values())
                reconcile(error, name)
                reconcile(error - row['error'], 'reported ledger error')
            for name, row in scope['pool_ledgers'].items():
                if 'inputs' in row:
                    error = row['final'] - row['initial'] - sum(row['inputs'].values()) + sum(row['outputs'].values())
                elif 'net_internal_transfer' in row:
                    error = row['final'] - row['initial'] - row['net_internal_transfer']
                else:
                    continue
                reconcile(error, name)
                reconcile(error - row['error'], 'reported organic pool error')
        for h in run['harvests']:
            c = sum(t['material_kg_ha']['carbon'] for t in h['material_transfers'])
            n = sum(t['material_kg_ha']['nitrogen'] for t in h['material_transfers'])
            reconcile(c - h['biomass_dry_kg_ha'] * .45, 'reported harvest C')
            reconcile(n - h['plant_total_N'], 'reported harvest N')
        if any(v is not None for v in run['unsupported'].values()):
            raise ValueError('Unsupported ecological output activated')
    for contrast in report['rotation_comparisons']:
        if not contrast['matched_daily_weather'] or not contrast['matched_initial_soil']:
            raise ValueError('Unmatched rotation comparison')
    return True


def write_evaluation(config_path, output):
    config, base = load_config(config_path)
    return evaluate(config, base, output)
