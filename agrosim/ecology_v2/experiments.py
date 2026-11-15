"""Predeclared soil-process hypotheses and identifiability, not synthetic proof."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from itertools import product
from pathlib import Path
import csv

import numpy as np

from ..evaluator.audit import EvaluationKernel
from ..io import write_json
from ..processes import Layer, Processes, SoilState, WeatherDay
from .soil_biology import Decomposers, Parameters, SoilBiologyKernel


def incubation(*, parameters=None, substrate_C=900., substrate_CN=70., mineral_N=10.,
               microbial_C=40., temperature=25., water_theta=.25, days=21, residue_decay=.03):
    """An explicit controlled batch, without roots, rainfall, leaching or inputs.

    Units kg C/N per hectare in a nominal0–25cm batch; not mapped to KBS soil mass.
    Soil water is held fixed as in a closed moisture assay. No yield is fitted.
    """
    layers = [Layer(.25, .1, .3, .5)]
    soil = SoilState([water_theta * 250], [mineral_N], [0.], [0.], [substrate_C],
                     [substrate_C / substrate_CN], [temperature])
    processes = Processes(residue_decay_per_day_20C=residue_decay, daily_n_deposition_kg_ha=0,
                          denitrification_fraction_day=0)
    if parameters is None:
        # Match initial total C/N; this hypothetical reserve resides in legacy
        # humus instead of an explicit decomposer pool in the stable reference.
        soil.humus_c_kg_ha = [microbial_C]
        soil.humus_n_kg_ha = [microbial_C / Parameters().microbial_cn]
        kernel = EvaluationKernel(layers, processes=processes, options={})
    else:
        mb = Decomposers([microbial_C], [microbial_C / parameters.microbial_cn], 'scenario_assumed',
                         'Declared synthetic controlled incubation, not measured field or KBS initial biomass')
        kernel = SoilBiologyKernel(layers, processes=processes, options={'soil_biology': asdict(parameters)}, decomposers=mb)
    before = kernel.inventories(soil, None); totals = {}; daily = []
    for i in range(days):
        day = WeatherDay(date(2000, 1, 1) + timedelta(days=i), 0, temperature, temperature, 0)
        flux = {k: 0. for k in ['respired_c_kg_ha', 'mineralized_n_kg_ha', 'immobilized_n_kg_ha', 'denitrified_n_kg_ha']}
        kernel._decomposition(soil, day, flux)
        for k, v in flux.items():
            if not k.startswith('functional_decomposer'): totals[k] = totals.get(k, 0.) + v
        daily.append({'day': i + 1, 'mineral_N': sum(soil.mineral_n_kg_ha), **flux})
    after = kernel.inventories(soil, None)
    errors = {'carbon': after['carbon'] - before['carbon'] + totals['respired_c_kg_ha'],
              'nitrogen': after['nitrogen'] - before['nitrogen'] + totals['denitrified_n_kg_ha'],
              'water': after['water'] - before['water']}
    if max(abs(x) for x in errors.values()) > 1e-7:
        raise ArithmeticError('Incubation conservation failure')
    return {'respiration_C': totals['respired_c_kg_ha'], 'net_mineral_N': sum(soil.mineral_n_kg_ha) - mineral_N,
            'final_mineral_N': sum(soil.mineral_n_kg_ha), 'residue_C': sum(soil.residue_c_kg_ha),
            'humus_C': sum(soil.humus_c_kg_ha),
            'functional_decomposer_C': sum(kernel.decomposers.carbon_kg_ha) if parameters else None,
            'initial_inventory': before, 'final_inventory': after, 'balance_error': errors, 'fluxes': totals}


def identifiability():
    base = dict(substrate_C=900., substrate_CN=70., mineral_N=10., microbial_C=40., days=10, residue_decay=.03)
    parameters = Parameters()
    coordinates = ['cue', 'turnover_day20', 'substrate_C', 'microbial_C', 'residue_decay']
    x = [parameters.cue, parameters.turnover_day20, base['substrate_C'], base['microbial_C'], base['residue_decay']]
    def forward(v):
        p = replace(parameters, cue=v[0], turnover_day20=v[1])
        r = incubation(parameters=p, **{**base, 'substrate_C': v[2], 'microbial_C': v[3], 'residue_decay': v[4]})
        return np.array([r['respiration_C'], r['net_mineral_N']])
    columns = []
    for i, v in enumerate(x):
        h = max(1e-7, abs(v) * 1e-4); lo = list(x); hi = list(x); lo[i] -= h; hi[i] += h
        columns.append((forward(hi) - forward(lo)) / (2 * h) * v)
    jacobian = np.array(columns).T
    singular = np.linalg.svd(jacobian, compute_uv=False)
    rank = int(np.linalg.matrix_rank(jacobian))
    return {'independent_targets': ['10-day control CO2-C', '10-day control net mineral N'],
            'unknowns': coordinates, 'local_scaled_jacobian': jacobian.tolist(), 'singular_values': singular.tolist(),
            'rank': rank, 'parameter_count': len(x), 'null_dimensions_at_least': len(x) - rank,
            'measured_CFI_biomass_adds_independent_target': False,
            'conclusion': 'At one assay endpoint, at least three parameter/initialization directions cannot be identified even before adding substrate N, moisture or inorganic-N uncertainty. Repeated observations with unknown sample-specific initial substrates do not eliminate this confounding.',
            'mechanistic_parameters_fitted': False, 'initialization_from_outcome_permitted': False}


def run_experiments(output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    rows = []
    for cn, quantity, mineral, biology in product([25., 70.], [300., 900.], [0., 50.],
                                                ['stable_V1', 'throughput_limited', 'overflow_respiration']):
        p = None if biology == 'stable_V1' else Parameters(formulation=biology)
        result = incubation(parameters=p, substrate_C=quantity, substrate_CN=cn, mineral_N=mineral)
        rows.append({'residue_CN': cn, 'returned_C_kg_ha': quantity, 'initial_mineral_N': mineral,
                     'model': biology, **result})
    interactions = []
    for quantity, model in product([300., 900.], ['stable_V1', 'throughput_limited', 'overflow_respiration']):
        def get(cn, n):
            return next(r for r in rows if r['returned_C_kg_ha'] == quantity and r['model'] == model
                        and r['residue_CN'] == cn and r['initial_mineral_N'] == n)
        interactions.append({'model': model, 'returned_C_kg_ha': quantity,
            'CN_by_initial_N_interaction': {target: (get(70, 50)[target] - get(25, 50)[target]) -
                                           (get(70, 0)[target] - get(25, 0)[target])
                                          for target in ['net_mineral_N', 'respiration_C']}})
    environment = []
    for model, temperature, theta in product(['stable_V1', 'throughput_limited', 'overflow_respiration'],
                                              [-5., 5., 25., 40.], [.04, .15, .3, .48]):
        p = None if model == 'stable_V1' else Parameters(formulation=model)
        r = incubation(parameters=p, temperature=temperature, water_theta=theta, days=180)
        environment.append({'model': model, 'temperature_C': temperature, 'water_theta': theta, **r})
    report = {'status': 'controlled_hypothesis_experiments_not_empirical_validation',
        'factorial': rows, 'nonadditive_interactions': interactions, 'environment_sensitivity': environment,
        'identifiability': identifiability(), 'measured_vs_returned_residue':
            'C:N25/70 and300/900kgC/ha are independently assigned hypothetical returned material, not an imposition of measured standing production onto unknown actual residue.',
        'controlled_inputs': {'temperature_C': 25., 'water_theta': .25, 'duration_days': 21,
            'initial_functional_C_or_equivalent_humus_C': 40., 'residue_decay_per_day20': .03,
            'initial_total_C_N_matched_across_formulations': True, 'pool_partition_is_observed': False,
            'note': 'Reference stores the same non-residue C/N reserve in humus; candidate stores it in functional decomposers. Initial partition is an explicit hypothesis.'},
        'equation_comparison': {'stable_V1': 'Fixed humification fraction/C:N with inorganic-N limitation on total residue throughput.',
            'throughput_limited': 'Explicit decomposer pool with fixed CUE; reduce substrate uptake when N cannot supply retained biomass.',
            'overflow_respiration': 'Explicit pool with N-limited C retention and respiration of unretained consumed C; throughput is not curtailed by inorganic N.',
            'selection': 'Neither extension selected for empirical use; unknown assay initialization prevents ranking against observations.'}}
    write_json(output / 'experiments.json', report)
    flat = [{k: r[k] for k in ['model', 'residue_CN', 'returned_C_kg_ha', 'initial_mineral_N',
                               'respiration_C', 'net_mineral_N', 'final_mineral_N', 'residue_C', 'humus_C', 'functional_decomposer_C']} for r in rows]
    with (output / 'residue_factorial.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(flat[0])); writer.writeheader(); writer.writerows(flat)
    return report
