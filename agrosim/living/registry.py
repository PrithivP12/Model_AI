"""Evidence-qualified functional contracts; numerical bounds are scenario domains.

Sources motivate response FORMS, not the invented guild's numerical coefficients.
No Tier A/B local biological measurements or field-calibrated rates are available.
"""
from dataclasses import dataclass, asdict, fields
from copy import deepcopy
from ..io import number
from ..traits import OrganismTraits

SOURCES = {
 'holling': {'url': 'https://doi.org/10.4039/Ent91293-5', 'context': 'Holling (1959), saturation/handling-limited functional response; not biomass-rate calibration for these aggregate guilds'},
 'stoichiometry': {'url': 'https://doi.org/10.1111/j.1469-8137.2012.04225.x', 'context': 'Manzoni et al. (2012), microbial C-use efficiency and stoichiometry; not local initial biomass or these coefficients'},
 'environment': {'url': 'https://doi.org/10.1038/nature04514', 'context': 'Davidson and Janssens (2006), environmental/substrate constraints on decomposition; not these temperature cardinals or oxygen factors'},
 'scenario': {'url': None, 'context': 'Explicit exploratory scenario domain chosen before execution for bounded numerical demonstrations; no probability, field calibration or empirical plausibility claim'}
}
# name: (unit, low, reference, high); values are NOT literature confidence bounds.
COMMON = {
 'temp_min': ('deg C', 0., 3., 6.), 'temp_opt': ('deg C', 20., 25., 30.),
 'temp_max': ('deg C', 38., 42., 46.), 'mortality': ('1/day', .001, .004, .012),
}
PARAMETERS = {
 'weed': {**COMMON, 'cn': ('kg C/kg N', 20., 30., 45.), 'sla': ('m2/kg dry', 15., 22., 30.),
          'rue': ('g dry/MJ PAR', 1., 2., 3.), 'root_depth': ('m', .15, .3, .6),
          'root_fraction': ('fraction vegetation C/N', .1, .2, .35),
          'kc': ('dimensionless', .7, 1., 1.2), 'extinction': ('dimensionless', .4, .6, .8),
          'water_per_C': ('mm/(kg C/ha)', .006, .012, .024), 'emergence_doy': ('day of year', 60., 100., 140.)},
 'herbivore': {**COMMON, 'cn': ('kg C/kg N', 6., 8., 10.), 'max_feed': ('kg food C/kg consumer C/day', .1, .5, 1.),
               'half_food': ('kg food C/ha', 10., 60., 200.), 'assimilation': ('fraction consumed C', .15, .3, .45),
               'respiration': ('fraction unassimilated C', .2, .5, .8), 'stage_opt': ('fraction crop thermal maturity', .15, .35, .65),
               'stage_width': ('fraction crop thermal maturity', .2, .4, .6)},
 'predator': {**COMMON, 'cn': ('kg C/kg N', 5., 7., 10.), 'max_feed': ('kg prey C/kg predator C/day', .1, .4, .8),
              'half_food': ('kg prey C/ha', .1, .5, 2.), 'assimilation': ('fraction consumed C', .2, .35, .5),
              'respiration': ('fraction unassimilated C', .2, .5, .8)},
 'detritivore': {**COMMON, 'cn': ('kg C/kg N', 8., 12., 18.), 'max_feed': ('kg residue C/kg consumer C/day', .1, .4, .8),
                 'half_food': ('kg residue C/ha', 50., 300., 900.), 'assimilation': ('fraction consumed C', .1, .2, .35),
                 'respiration': ('fraction unassimilated C', .1, .3, .6)},
 'pathogen': {**COMMON, 'infection': ('1/day at full host exposure', .01, .05, .1),
              'necrosis': ('fraction green leaf/day/pressure', .002, .01, .03),
              'half_lai': ('m2 leaf/m2 ground', .2, 1., 2.), 'stage_opt': ('fraction crop maturity', .3, .6, .8),
              'stage_width': ('fraction crop maturity', .2, .4, .6)},
}
INITIAL_DOMAINS = {'weed': (.5, 2., 8.), 'herbivore': (.1, .5, 2.), 'predator': (.02, .1, .4),
                   'detritivore': (2., 8., 32.), 'pathogen': (.02, .1, .3)}


def registry():
    result = []
    for role, parameters in PARAMETERS.items():
        for name, (unit, low, ref, high) in parameters.items():
            result.append({'id': f'{role}.{name}', 'guild': role, 'unit': unit, 'low': low, 'reference': ref, 'high': high,
                'range_evidence_tier': 'D', 'status': 'exploratory', 'source': 'scenario',
                'applicability': 'hypothetical annual-crop Illinois scenario only; not a transferable field coefficient',
                'correlations': ['temp_min < temp_opt < temp_max', 'initial N = initial C / cn'],
                'identifiability': 'not identified from independent measurements; joint feeding/assimilation and initial-biomass confounding'})
        low, ref, high = INITIAL_DOMAINS[role]
        result.append({'id': f'{role}.initial', 'guild': role, 'unit': 'dimensionless pressure' if role == 'pathogen' else 'kg C/ha',
                       'low': low, 'reference': ref, 'high': high, 'range_evidence_tier': 'D', 'status': 'exploratory',
                       'source': 'scenario', 'correlations': ['N fixed by declared C:N for biomass guilds'],
                       'identifiability': 'no local abundance observations; assigned stock/index, not estimated population'})
    for name, domain, unit in [('cue', (.3, .45, .6), 'fraction C retained'), ('uptake_per_C_day20', (.1, .3, .6), '1/day'),
                               ('turnover_day20', (.005, .02, .05), '1/day'), ('microbial_cn', (6., 8., 12.), 'kg C/kg N')]:
        result.append({'id': 'microbes.'+name, 'guild': 'microbes', 'unit': unit, 'low': domain[0], 'reference': domain[1], 'high': domain[2],
                       'range_evidence_tier': 'D', 'source': 'scenario', 'status': 'exploratory',
                       'form_source': 'stoichiometry', 'correlations': ['initial microbial N = C / microbial_cn'],
                       'identifiability': 'not fitted; substrate quantity, accessibility, biomass and uptake confounded'})
    return {'parameters': result, 'sources': SOURCES, 'response_form_support': 'Selected saturating-consumption and stoichiometric forms Tier C; other functional forms and all numeric ranges Tier D',
            'fixed_exploratory_assumptions': {
                'plant_C_fraction': {'value': .45, 'unit': 'kg C/kg dry', 'origin': 'existing illustrative simulator conversion'},
                'microbe_heat_inhibition_start_C': {'value':35.,'unit':'deg C','tier':'D'},
                'microbe_heat_shutdown_C': {'value':50.,'unit':'deg C','tier':'D'},
                'microbe_cold_shutdown_C': {'value':0.,'unit':'deg C','tier':'D'},
                'microbe_Q10': {'value':2.,'unit':'dimensionless','origin':'existing illustrative default; config may override'},
                'canopy_order': {'value':'vegetation canopy, then crop remainder','tier':'D'},
                'weed_root_shoot_composition': {'value':'same assigned C:N; root fraction partitions existing stock','tier':'D'},
                'disease_moisture': {'value':'topsoil wetness proxy, not leaf-wetness observation','tier':'D'},
                'sub_daily_life_stage_and_immigration': {'value':None,'status':'unavailable'},
                'empirical_numeric_bounds': None},
            'local_parameter_validation': False, 'scenario_domains_are_probability_distributions': False}


@dataclass
class Guild:
    identity: str
    role: str
    hosts: list
    resources: list
    parameters: dict
    initial: dict
    provenance: str
    evidence_tier: str

    def validate(self):
        if not self.identity or self.role not in PARAMETERS or not self.provenance or self.evidence_tier != 'D':
            raise ValueError('Explicit supported functional guild and exploratory provenance required')
        if set(self.parameters) != set(PARAMETERS[self.role]):
            raise ValueError(f'Complete explicit parameter set required for {self.identity}')
        if not self.resources or any(h not in ['corn', 'soybeans', 'spring_wheat'] for h in self.hosts):
            raise ValueError('Resource and supported host identities required')
        for name, value in self.parameters.items():
            domain = PARAMETERS[self.role][name]
            number(value, f'{self.identity}.{name}', domain[1], domain[3])
        p = self.parameters
        if not p['temp_min'] < p['temp_opt'] < p['temp_max']:
            raise ValueError('Ordered temperature cardinals required')
        if self.role == 'pathogen':
            if set(self.initial) != {'pressure'}: raise ValueError('Pathogen requires explicit dimensionless pressure')
            number(self.initial['pressure'], 'initial pressure', 0, 1)
        else:
            if set(self.initial) != {'C', 'N'}: raise ValueError('Explicit guild C and N stocks required')
            c = number(self.initial['C'], 'initial C', 0); n = number(self.initial['N'], 'initial N', 0)
            if abs(c - n*p['cn']) > 1e-8: raise ValueError('Initial C:N must match guild composition')
        return self

    def traits(self):
        stage = 'aggregate_active_biomass' if self.role != 'pathogen' else 'pressure'
        params = {k: {'value': v, 'unit': PARAMETERS[self.role][k][0], 'state_origin': 'scenario_assumed',
                      'source_reference': self.provenance} for k, v in self.parameters.items()}
        interactions = []
        if self.role != 'pathogen':
            interactions.append({'kind':'mortality_to_residue','stage':stage,'target':'soil_or_surface_residue',
                                 'required_parameters':['mortality']})
        if self.role in ['herbivore','detritivore','predator']:
            interactions.append({'kind':'predation' if self.role=='predator' else 'feeding', 'stage':stage,
                                 'target':','.join(self.resources),'required_parameters':['max_feed','half_food','assimilation','cn']})
        trait = OrganismTraits(self.identity, f'hypothetical functional {self.role} guild', (stage,),
            {stage: ','.join(self.resources)}, {stage: {h: True for h in self.hosts}}, (),
            {'resources': self.resources, 'units': 'pressure' if self.role == 'pathogen' else 'kg C and N/ha'},
            tuple(interactions), params, (self.provenance,), 'exploratory_scenario')
        trait.validate()
        return asdict(trait)


def validate_config(config):
    if config.get('mode') not in ['stable', 'experimental']: raise ValueError('Explicit stable/experimental mode required')
    if config['mode'] == 'stable': return []
    if config.get('state_origin') != 'scenario_assumed' or not config.get('provenance'):
        raise ValueError('Experimental initial state assumptions and provenance must be declared')
    if set(config['enabled']) != set(PARAMETERS) | {'microbes'} or any(type(v) is not bool for v in config['enabled'].values()):
        raise ValueError('Explicit boolean activation for each functional process required')
    guilds = [Guild(**deepcopy(g)).validate() for g in config['guilds']]
    if len({g.identity for g in guilds}) != len(guilds) or {g.role for g in guilds} != set(PARAMETERS):
        raise ValueError('Unique guilds covering each reference role required')
    lookup = {g.identity: g for g in guilds}
    for g in guilds:
        expected = {'weed': ['solar_water_mineral_N'], 'herbivore': ['green_leaf'],
                    'detritivore': ['surface_residue'], 'pathogen': ['green_leaf']}
        if g.role in expected and g.resources != expected[g.role]: raise ValueError('Unsupported resource compatibility')
        if g.role == 'predator' and any(r not in lookup or lookup[r].role != 'herbivore' for r in g.resources):
            raise ValueError('Predators require explicitly compatible herbivore guilds')
    if not config.get('microbes') or set(config['microbes']) != {'parameters', 'initial_humus_C_fraction', 'provenance'}:
        raise ValueError('Explicit decomposer parameters, stock partition and provenance required')
    if not config['microbes']['provenance']: raise ValueError('Microbial initialization provenance required')
    from ..ecology_v2.soil_biology import Parameters as MicrobialParameters
    if set(config['microbes']['parameters']) != {f.name for f in fields(MicrobialParameters)}:
        raise ValueError('Every microbial process parameter must be explicit in experimental mode; legacy defaults are not imputed')
    MicrobialParameters(**config['microbes']['parameters'])
    design=config.get('design',{})
    valid={p['id'] for p in registry()['parameters']}
    for key in ['response_coordinates','screen_coordinates']:
        if not design.get(key) or len(set(design[key]))!=len(design[key]) or set(design[key])-valid:
            raise ValueError('Explicit unique supported sensitivity coordinates required')
    years=design.get('stability_years')
    if type(years) is not int or not 4<=years<=10: raise ValueError('Long-run numerical design requires4–10 whole years')
    for event in config.get('weed_removal', []):
        from datetime import date
        date.fromisoformat(event['date']); number(event['fraction'], 'weed removal fraction', 0, 1)
        if event.get('destination') != 'surface_residue': raise ValueError('Only declared in-field weed termination supported')
    return guilds
