"""Machine-readable mechanism/evidence inventory; interfaces are not populations."""
from dataclasses import asdict, dataclass

PAPER = 'https://doi.org/10.1111/j.1469-8137.2012.04225.x'


def mechanism_inventory():
    # Category is the strongest currently justified empirical/software status,
    # not an assertion that categories are mutually exclusive scientifically.
    rows = [
        ('photosynthesis_biomass', 2, ['plant biomass dry kg/ha', 'thermal time', 'LAI'],
         'dB = intercepted PAR * RUE * temperature * water * N limitation',
         ['RUE', 'extinction coefficient', 'temperature cardinals', 'specific leaf area'],
         ['net_assimilated_c_kg_ha', 'growth_dry_kg_ha'], 'agrosim/processes.py:_grow',
         'Net radiation-use growth, not biochemical photosynthesis; existing yield/rotation failures retained.'),
        ('root_growth_water_extraction', 2, ['root depth m', 'layer water mm'],
         'Thermal-time root depth; bounded accessible extraction and optional exponential root weighting',
         ['max root depth', 'root_beta', 'horizon penetrability'], ['transpiration_mm', 'water_stress_factor'],
         'agrosim/intelligence/physical.py:extract_root_water', 'Root biomass/depth are not field validated; no lateral water routing.'),
        ('carbon_allocation', 3, ['aggregate plant dry matter', 'optional organ dry matter and N'],
         'Fixed root fraction and HI; optional conservative organ/source-sink transfers',
         ['root fraction', 'HI', 'optional source-sink coefficient'], ['grain', 'root and shoot dry matter'],
         'agrosim/biological_recovery/plant.py', 'Optional maize mechanisms improve some targets and degrade others; no promotion.'),
        ('nitrogen_uptake_fixation', 2, ['soil mineral N', 'plant N'],
         'Demand/soil-limited uptake plus bounded legume fixation; C growth limited by tissue N',
         ['plant N concentration', 'fixation capacity'], ['plant_uptake_n_kg_ha', 'fixed_n_kg_ha'],
         'agrosim/processes.py:_grow', 'Fixation and root-N allocation lack matched independent measurements.'),
        ('soil_mineral_nitrogen', 1, ['layer mineral N kg/ha'],
         'dN = fertilizer + deposition + mineralization - immobilization - uptake - leaching - denitrification',
         ['supplied fertilizer', 'deposition', 'transport parameters'], ['layer mineral N'],
         'agrosim/evaluator/engine.py:ledger', 'Numerical ledger verified; not separated ammonium/nitrate or field validation.'),
        ('organic_matter', 2, ['layer humus C and N'],
         'First-order humus decay plus retained residue C/N; fixed stoichiometric humus formation',
         ['humus decay', 'humification fraction', 'humus C:N'], ['humus C/N', 'respired C'],
         'agrosim/processes.py:_decomposition', 'No mineral-associated versus particulate SOM or validated sequestration accounting.'),
        ('residue_decomposition', 2, ['surface and layer residue C/N'],
         'D = C*(1-exp(-k*temperature*moisture*oxygen)); N demand can reduce the transfer',
         ['residue decay', 'Q10', 'humification fraction'], ['residue loss', 'respiration'],
         'agrosim/processes.py:_decomposition', 'Residue production is not observed residue return; maize substrate C:N mismatch remains.'),
        ('mineralization_immobilization', 2, ['organic N', 'mineral N'],
         'Net residue N exchange = consumed substrate N - N required for retained C',
         ['substrate C:N', 'humus C:N', 'retention fraction'], ['mineralized_n_kg_ha', 'immobilized_n_kg_ha'],
         'agrosim/processes.py:_decomposition', 'Net exchange is not isotope-measured gross mineralization or microbial community activity.'),
        ('soil_moisture_temperature', 2, ['layer water mm', 'snow mm', 'soil temperature C'],
         'Daily snow/infiltration/runoff/drainage/ET balance; depth-dependent temperature relaxation',
         ['hydraulic bounds', 'drainage fraction', 'infiltration limit'], ['water profile', 'ET', 'soil temperature'],
         'agrosim/processes.py:_hydrology', 'Water persistence baseline failures remain; temperature relaxation is not a solved soil heat equation.'),
        ('plant_litter_transfers', 1, ['plant C/N', 'layer/root and surface/shoot residues'],
         'Harvest material is routed exactly once into grain export, shoot export, shoot return and root return',
         ['root fraction', 'removal fraction', 'scheduled harvest'], ['material_transfers'],
         'agrosim/nutrient_recovery/material.py', 'Conservation verified; continuous root turnover unsupported and actual retained material often unknown.'),
        ('microbial_biomass_stable_V1', 5, [], 'No explicit microbial biomass stock in V1', [], [],
         'agrosim/ecosystem.py', 'Legacy respiration/SOM proxy is not microbial population or measured biomass.'),
        ('functional_decomposers_V2', 3, ['decomposer C kg/ha', 'decomposer N kg/ha'],
         'Residue uptake -> CUE-limited C retention and N demand -> biomass; turnover -> humus/residue; remainder -> CO2',
         ['CUE', 'fixed biomass C:N', 'turnover', 'uptake capacity', 'initial biomass', 'N-limitation formulation'],
         ['respiration', 'mineralization/immobilization', 'functional biomass', 'turnover'],
         'agrosim/ecology_v2/soil_biology.py', 'Experimental hypotheses only; parameters/initial biomass not identified from current assays.'),
        ('pest_predator_interactions', 4, [], 'Trait/host/resource contracts; no active supported populations',
         ['missing local states, recruitment, mortality, feeding and detection rates'], [], 'agrosim/resource_exchange.py',
         'Legacy unitless logistic pressure experiments are not abundance models and remain inactive.'),
        ('pathogens', 4, [], 'Potential host/residue exposure evidence contracts only',
         ['missing inoculum, infection, weather response and damage observations'], [], 'agrosim/rotation_diagnosis/research.py',
         'No sequence-derived disease penalty; symptom ratings do not establish pathogen population.'),
        ('weed_competition', 5, [], 'No shared-light/water/N competition solver for weed cohorts', [], [],
         'agrosim/rotation_diagnosis/research.py', 'No weed biomass, density or competitive yield adjustment enabled.'),
        ('soil_fauna', 4, [], 'Declarative organism/stage/resource interfaces only', [], [],
         'agrosim/traits.py', 'Cicada representation is an architecture example, not a local abundance or pest prediction.'),
        ('nutrient_losses', 2, ['mineral N', 'water'], 'Advective N leaching and bounded saturation/temperature denitrification',
         ['mixing', 'denitrification coefficient'], ['leached_n_kg_ha', 'denitrified_n_kg_ha'],
         'agrosim/processes.py', 'No separate nitrification, N2O fraction or ammonia volatilization.'),
        ('long_term_memory', 1, ['full soil/plant history and cumulative fluxes; explicit V2 decomposer checkpoint'],
         'Conservative state continuation; no year reset or history bonus', ['initial states and management scenarios'],
         ['checkpoints', 'preplant state', 'multi-year differences'], 'agrosim/ecosystem.py:EcosystemEngine',
         'Persistence is numerical, not a verified long-term ecological trajectory.'),
    ]
    names = {1: 'implemented_numerically_tested', 2: 'parameterized_not_empirically_validated',
             3: 'experimental_or_partial', 4: 'software_interface_only', 5: 'not_implemented'}
    return {'schema_version': 1, 'categories': names, 'classification_is_accuracy_guarantee': False,
            'mechanisms': [{'id': k, 'category': names[cat], 'category_number': cat, 'state_variables': states,
                'equation': eq, 'parameters': params, 'outputs': outputs, 'implementation': source,
                'unsupported_assumptions': limit, 'independently_validated': False,
                'evidence': ['reports/ecosystem-evaluator-v1/delivery/READINESS.md',
                             'reports/nutrient-crop-recovery/delivery/FINDINGS.md'] + ([PAPER] if k == 'functional_decomposers_V2' else [])}
                           for k, cat, states, eq, params, outputs, source, limit in rows]}


@dataclass(frozen=True)
class InteractionContract:
    identifier: str
    kind: str
    mechanism: str
    state_unit: str | None
    initialization_reference: str | None
    rate_reference: str | None
    observation_operator: str | None
    measurement_unit: str | None
    field_damage_reference: str | None = None

    def assess(self):
        if self.kind not in ['functional_process', 'population_model', 'potential_interaction']:
            raise ValueError('Explicit functional/population/potential distinction required')
        if self.mechanism not in ['decomposition', 'feeding', 'predation', 'competition',
                                  'recruitment', 'mortality', 'habitat_dependence', 'organic_return']:
            raise ValueError('Unknown interaction mechanism')
        missing = [name for name in ['state_unit', 'initialization_reference', 'rate_reference', 'observation_operator', 'measurement_unit']
                   if getattr(self, name) is None]
        if self.measurement_unit == 'adults/trap/week' and self.state_unit != 'adults/trap/week':
            missing.append('independent_trap_catchability_and_exposure_operator')
        if self.measurement_unit == 'microgram/g dry soil' and self.state_unit != self.measurement_unit:
            missing.append('measured_bulk_density_and_depth_equivalent_operator')
        return {**asdict(self), 'missing_evidence': missing,
                'quantitative_dynamics_enabled': False, 'crop_damage_enabled': False,
                'reason': 'Contract only; no new organism solver or inferred local population is activated.'}


def interaction_contracts():
    return [InteractionContract('KBS_sticky_trap_predators', 'potential_interaction', 'predation',
              None, None, None, 'count at known trap station integrated over actual deployment interval',
              'adults/trap/week').assess(),
            InteractionContract('residue_functional_decomposers', 'functional_process', 'decomposition',
              'kg C/ha and kg N/ha', 'explicit hypothetical partition of existing humus', PAPER,
              'sample-depth-equivalent biomass/CO2 and net inorganic N; current assays do not identify initial substrate',
              'microgram/g dry soil').assess()]
