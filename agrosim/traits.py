"""Declarative interaction contracts; no uncalibrated organism-to-yield rules.

The registry describes what evidence a flux would require. It does not infer local
presence, densities, emergence dates or rates from a taxonomic label.
"""
from copy import deepcopy
import math
from dataclasses import dataclass,asdict,field


@dataclass(frozen=True)
class OrganismTraits:
    identifier:str
    taxon:str
    stages:tuple[str,...]
    feeding_resource_by_stage:dict
    compatible_host_traits:dict
    life_cycle_years:tuple[int,...]
    resource_requirements:dict
    interactions:tuple[dict,...]
    parameters:dict
    references:tuple[str,...]
    evidence_class:str='literature_parameterized'

    def validate(self):
        if not self.identifier or not self.taxon or not self.stages:raise ValueError('Organism identity and life stages are required')
        if len(set(self.stages))!=len(self.stages):raise ValueError('Life stages must be unique')
        if set(self.compatible_host_traits)-set(self.stages):raise ValueError('Host compatibility has an undefined stage')
        for hosts in self.compatible_host_traits.values():
            if any(value is not None and type(value)is not bool for value in hosts.values()):raise ValueError('Host compatibility must be true, false or unknown')
        if self.evidence_class not in ['empirically_validated','calibrated_not_independently_validated','literature_parameterized','exploratory_scenario','unsupported_disabled']:raise ValueError('Unknown trait evidence class')
        if set(self.feeding_resource_by_stage)-set(self.stages):raise ValueError('Feeding stage is undefined')
        if any(year<=0 for year in self.life_cycle_years):raise ValueError('Invalid life cycle')
        for interaction in self.interactions:
            if interaction['stage']not in self.stages:raise ValueError('Interaction stage is undefined')
            if interaction['kind']not in ['feeding','predation','life_cycle_transition','mortality_to_residue']:raise ValueError('Unsupported interaction contract')
            for name in interaction['required_parameters']:
                if name not in self.parameters:raise ValueError('Undeclared biological parameter: '+name)
        for name,value in self.parameters.items():
            if not {'value','unit','state_origin'}<=value.keys():raise ValueError('Parameter value, unit and origin required: '+name)
            if value['state_origin']=='unknown'and value['value']is not None:raise ValueError('Unknown biological parameter cannot carry a numeric value')
            if value['value']is not None:
                if isinstance(value['value'],(float,int))and (isinstance(value['value'],bool)or not math.isfinite(value['value'])or value['value']<0):raise ValueError('Invalid biological parameter quantity')
                if not value.get('source_reference'):raise ValueError('Known biological parameter requires provenance')


class TraitRegistry:
    def __init__(self,organisms=()):
        self.organisms={}
        for organism in organisms:self.register(organism)

    def register(self,organism):
        organism.validate()
        if organism.identifier in self.organisms:raise ValueError('Duplicate organism identity')
        self.organisms[organism.identifier]=deepcopy(organism)

    def assess(self,identifier,host_traits=(),local_presence=None):
        organism=self.organisms[identifier];host=set(host_traits);rows=[]
        for interaction in organism.interactions:
            compatibility=organism.compatible_host_traits.get(interaction['stage'],{})
            matched=[compatibility[t]for t in host if t in compatibility]
            compatible=True if True in matched else False if matched and all(v is False for v in matched)else None
            missing=[name for name in interaction['required_parameters']if organism.parameters[name]['value']is None]
            # A registry entry is a contract, never a quantitative dynamics implementation.
            rows.append({**deepcopy(interaction),'host_compatible':compatible,'missing_parameters':missing,
                'prediction_enabled':False,'status':'unsupported_disabled',
                'reasons':(['Local presence is unknown']if local_presence is None else [])+
                    (['Crop/stage host compatibility is unknown']if interaction['kind']=='feeding'and compatible is None else [])+
                    (['Quantitative rates or organism stocks are missing']if missing else [])+
                    ['No calibrated conservative organism-flux solver is registered'],
                'yield_penalty':None,'resource_flux':None})
        return {'organism':asdict(organism),'local_presence':local_presence,'interactions':rows,
            'local_population_prediction':None,'emergence_prediction':None,'applied_to_crop_growth':False}

    def manifest(self):return {key:asdict(value)for key,value in self.organisms.items()}


def cicada_registry():
    """Periodical Magicicada test case, not a universal family-wide host claim."""
    unknown=lambda unit:{'value':None,'unit':unit,'state_origin':'unknown'}
    organism=OrganismTraits('periodical_cicada','Magicicada spp.',('egg','subterranean_nymph','adult'),
        {'subterranean_nymph':'root_xylem','adult':'shoot_xylem'},
        {'egg':{'woody_shoot':True},'subterranean_nymph':{'woody_root':True},'adult':{'woody_shoot':True}},
        (13,17),{'feeding':'xylem water and solutes; no calibrated host resource flux',
                 'decomposition':'carcass dry matter, C and N must enter tracked residue pools once'},
        ({'kind':'feeding','stage':'subterranean_nymph','target':'compatible_root_xylem',
          'required_parameters':['local_density','xylem_intake','assimilation_efficiency','body_c','body_n']},
         {'kind':'feeding','stage':'adult','target':'compatible_shoot_xylem',
          'required_parameters':['local_density','adult_intake','body_c','body_n']},
         {'kind':'predation','stage':'adult','target':'bird_or_other_predator',
          'required_parameters':['local_density','predator_density','predation_rate','body_c','body_n']},
         {'kind':'life_cycle_transition','stage':'subterranean_nymph','target':'adult',
          'required_parameters':['brood_age','emergence_threshold','survival_rate']},
         {'kind':'mortality_to_residue','stage':'adult','target':'surface_residue',
          'required_parameters':['local_density','mortality_rate','body_c','body_n']}),
        {name:unknown(unit)for name,unit in {'local_density':'individuals/ha','xylem_intake':'mm/individual/day',
          'adult_intake':'mm/individual/day','assimilation_efficiency':'fraction','body_c':'kg C/individual',
          'body_n':'kg N/individual','predator_density':'individuals/ha','predation_rate':'1/day',
          'brood_age':'years','emergence_threshold':'degree C at declared soil depth','survival_rate':'fraction',
          'mortality_rate':'1/day'}.items()},
        ('https://pmc.ncbi.nlm.nih.gov/articles/PMC1794023/',
         'https://doi.org/10.1146/annurev-ento-072121-061108',
         'https://www.epa.gov/safepestcontrol/cicadas'))
    return TraitRegistry([organism])
