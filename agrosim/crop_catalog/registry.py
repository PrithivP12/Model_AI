"""Data-driven taxonomy, capability requirements and contextual support levels."""
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import hashlib,json,re
from ..processes import default_crops,Processes

SHARED_CAPABILITIES={'soil_water_uptake','mineral_N_uptake','radiation_growth','root_development',
    'plant_CN_inventory','residue_decomposition','persistent_soil_state'}
BASE_CAPABILITIES=SHARED_CAPABILITIES|{'annual_thermal_time','grain_harvest'}
IMPLEMENTED_CAPABILITIES=BASE_CAPABILITIES|{'legume_fixation','grain_legume_thermal_time','conservative_CN_transfers','optional_maize_source_sink'}
ARCHETYPES={
 'c3_cereal':{'requirements':sorted(BASE_CAPABILITIES),'interfaces_implemented':True,'scope':'Existing spring wheat parameterization only; species/genotype photoperiod/vernalization not generalized.'},
 'c4_cereal':{'requirements':sorted(BASE_CAPABILITIES),'interfaces_implemented':True,'scope':'Existing maize parameterization only. Maize-specific N dilution/reproduction not shared automatically.'},
 'grain_legume':{'requirements':sorted(BASE_CAPABILITIES|{'legume_fixation','grain_legume_thermal_time'}),'interfaces_implemented':True,'scope':'Existing soybean parameterization only. Fixation parameters and grain organs cannot be borrowed silently.'},
 'forage_legume':{'requirements':sorted(SHARED_CAPABILITIES|{'legume_fixation','perennial_regrowth','repeated_cutting'}),'interfaces_implemented':False,'scope':'Regrowth, reserve/dormancy and repeated harvest unimplemented.'},
 'oilseed':{'requirements':sorted(SHARED_CAPABILITIES|{'broadleaf_oilseed_phenology','seed_harvest'}),'interfaces_implemented':False,'scope':'Oilseed reproductive allocation and calendars unimplemented.'},
 'root_tuber':{'requirements':sorted(SHARED_CAPABILITIES|{'storage_organ_allocation','storage_organ_harvest'}),'interfaces_implemented':False,'scope':'Storage organs, propagules and harvest removal need a specific conserved module.'},
 'leafy_vegetable':{'requirements':sorted(SHARED_CAPABILITIES|{'leaf_harvest'}),'interfaces_implemented':False,'scope':'Marketable leaf harvest and partial/regrowth fate unimplemented.'},
 'fruiting_vegetable':{'requirements':sorted(SHARED_CAPABILITIES|{'fruit_allocation','repeated_harvest'}),'interfaces_implemented':False,'scope':'Fruit development and harvest cohorts unimplemented.'},
 'forage_grass':{'requirements':sorted(SHARED_CAPABILITIES|{'repeated_cutting'}),'interfaces_implemented':False,'scope':'Whole-crop termination works as an operation; repeated forage production is unimplemented.'},
 'perennial_tree':{'requirements':sorted(SHARED_CAPABILITIES|{'woody_perennial','fruit_allocation'}),'interfaces_implemented':False,'scope':'Woody stores, dormancy, age, fruit allocation and repeated harvest unimplemented. Group includes other perennial fruits as a software category, not tree taxonomy.'},
 'wetland':{'requirements':sorted(SHARED_CAPABILITIES|{'flooded_soil','anaerobic_nitrogen'}),'interfaces_implemented':False,'scope':'Flooding/waterlogging/redox processes unimplemented.'},
 'specialized':{'requirements':sorted(SHARED_CAPABILITIES|{'specialized_phenology'}),'interfaces_implemented':False,'scope':'Crop-specific organs, harvest and resource equations require evidence.'},
}
LEVELS=['cataloged','scenario_compatible','mechanistically_implemented','calibrated','independently_validated']


class UnsupportedCropError(ValueError):pass


def load_catalog(path=None):
    return json.loads((Path(path)if path else Path(__file__).with_name('data')/'catalog.json').read_text())


class CropRegistry:
    def __init__(self,path=None,catalog=None,expected_count=92):
        self.catalog=deepcopy(catalog)if catalog is not None else load_catalog(path)
        self.crops={r['identifier']:r for r in self.catalog['crops']}
        if len(self.crops)!=len(self.catalog['crops']):raise ValueError('Duplicate crop identifiers')
        self.validate(expected_count)

    def validate(self,expected_count=92):
        if len(self.crops)!=expected_count:raise ValueError(f'Expected exactly{expected_count} distinct agricultural entries')
        taxa=set();names=set();accepted=set()
        for identifier,c in self.crops.items():
            if not re.fullmatch('[a-z][a-z0-9_]*',identifier):raise ValueError('Stable lowercase crop identifier required')
            taxon=c['taxonomy'];name=taxon['canonical_name']
            if not isinstance(name,str)or len(name.split())<2 or not name[0].isupper()or taxon['rank']not in ['SPECIES','SUBSPECIES']:raise ValueError('Explicit matched species taxonomy required')
            if name in taxa or taxon['accepted_key']in accepted:raise ValueError('Synonym or cultivar counted as a second crop')
            taxa.add(name);accepted.add(taxon['accepted_key'])
            key=c['common_name'].casefold()
            if key in names:raise ValueError('Duplicate agricultural common name')
            names.add(key)
            if not taxon.get('source_url')or not taxon.get('response_sha256')or not taxon.get('family'):raise ValueError('Traceable primary taxonomy/family required')
            if not c['archetypes']or set(c['archetypes'])-set(ARCHETYPES):raise ValueError('Unknown process archetype')
            if set(c['harvested_organs'])-{'grain','seed','shoot','stem','leaf','storage_root','tuber','corm','bulb','fruit','inflorescence','fiber'}:raise ValueError('Unknown harvested organ')
            for trait in c['traits'].values():
                if not {'value','status','source'}<=set(trait):raise ValueError('Trait value/status/provenance required')
                if trait['value']is not None and (trait['status']=='unknown'or not trait['source']):raise ValueError('Unsourced or unknown quantitative trait cannot have a value')
            life=c['traits']['life_cycle']['value'];photosynthesis=c['traits']['photosynthesis']['value']
            if life is not None and any(x not in ['annual','biennial','perennial']for x in life):raise ValueError('Invalid plant life cycle')
            if photosynthesis not in [None,'C3','C4','CAM']:raise ValueError('Unsupported photosynthetic label')
            fixation=c['traits']['fixation']['value']
            if fixation is True and taxon['family']!='Fabaceae':raise ValueError('No unsupported N fixation outside the represented legume scope')
            kernel=c['implementation'].get('kernel_key')
            if c['taxonomy']['family']!='Poaceae'and any(g in c['archetypes']for g in ['c3_cereal','c4_cereal','forage_grass']):raise ValueError('Grass/cereal archetype requires Poaceae in this catalog; broadleaf covers need their own scope')
            if kernel and kernel not in default_crops():raise ValueError('Catalog points to an absent crop equation')
            if kernel and c['implementation'].get('life_cycle_scope')!='annual_single_harvest':raise ValueError('Existing engine cannot imply perennial regrowth')
            if kernel=='soybeans'and fixation is not True:raise ValueError('Soybean fixation scope must be explicit')
            if kernel and not set(c['required_processes'])<=IMPLEMENTED_CAPABILITIES:raise ValueError('Unsupported processes attached to quantitative crop')
        return True

    def resolve(self,identifier):
        if not isinstance(identifier,str):raise UnsupportedCropError('A crop identifier or unambiguous name is required')
        if identifier in self.crops:return self.crops[identifier]
        candidates=[c for c in self.crops.values()if identifier.casefold()in {c['common_name'].casefold(),c['taxonomy']['canonical_name'].casefold(),*[a.casefold()for a in c['aliases']]}]
        if len(candidates)!=1:raise UnsupportedCropError('Unknown or ambiguous crop identity: '+str(identifier))
        return candidates[0]

    def selection_catalog(self):
        """User-requested crop choices; retain the original species inventory."""
        from .choices import CropChoiceRegistry
        return CropChoiceRegistry(base=self)

    def support(self,identifier,*,profile='default',region=None,genotype=None):
        c=self.resolve(identifier);levels=['cataloged'];envelope=c.get('suitability',{})
        if any(envelope.get(k)is not None for k in ['temperature_min_C','temperature_max_C','annual_rain_min_mm','annual_rain_max_mm']):levels.append('scenario_compatible')
        missing=set(c['required_processes'])-IMPLEMENTED_CAPABILITIES
        implemented=bool(c['implementation'].get('kernel_key'))and not missing
        if implemented:levels.append('mechanistically_implemented')
        # A calibration result qualifies only for its exact crop/profile/domain,
        # verified parameter mapping and compatible targets. It does not promote
        # the generic species entry or change default simulation coefficients.
        calibration=[e for e in c['evidence']if e['kind']=='calibration'and e['profile']==profile and e['region']==region
            and e['genotype']==genotype and e.get('parameter_mapping_verified')and e.get('compatible_observations')and self.evidence_verified(e)]
        if implemented and calibration:levels.append('calibrated')
        independent=[e for e in c['evidence']if e['kind']=='validation'and e['profile']==profile and e['region']==region and e['genotype']==genotype
            and e.get('outcomes_untouched_at_freeze')and e.get('compatible_observations')and e.get('acceptance_gate_passed')and self.evidence_verified(e)]
        if implemented and calibration and independent:levels.append('independently_validated')
        return {'identifier':c['identifier'],'support_level':levels[-1],'satisfied_levels':levels,'profile':profile,
            'region':region,'genotype':genotype,'quantitative_eligible':implemented,'missing_processes':sorted(missing),
            'parameter_profile_missing':c['implementation'].get('kernel_key')is None,
            'farmer_recommendation_eligible':False,'classification_is_performance_guarantee':False}

    def evidence_verified(self,evidence):
        reference=evidence.get('artifact');digest=evidence.get('sha256')
        if not reference or not digest:return False
        root=Path(__file__).resolve().parents[2];path=(root/reference).resolve()
        if not path.is_relative_to(root)or not path.is_file():return False
        return hashlib.sha256(path.read_bytes()).hexdigest()==digest

    def require_quantitative(self,identifier):
        c=self.resolve(identifier);status=self.support(c['identifier'])
        if not status['quantitative_eligible']:raise UnsupportedCropError(f"{c['common_name']} is {status['support_level']}; quantitative simulation unavailable. Missing crop profile/processes: "+', '.join(status['missing_processes']or['crop-specific parameters and process verification']))
        return c['implementation']['kernel_key']

    def crop_parameters(self,identifier,*,profile='default',region=None,genotype=None):
        crop=default_crops()[self.require_quantitative(identifier)]
        if profile=='default':return crop
        status=self.support(identifier,profile=profile,region=region,genotype=genotype)
        if 'calibrated'not in status['satisfied_levels']:raise UnsupportedCropError('No verified calibration for that crop/profile/region/genotype')
        evidence=next(e for e in self.resolve(identifier)['evidence']if e['kind']=='calibration'and e['profile']==profile and e['region']==region and e['genotype']==genotype and self.evidence_verified(e))
        return replace(crop,**evidence['crop_parameter_overrides'])

    def make_ecosystem(self,identifiers,layers,*,latitude=0.,processes=None,profile='default',region=None,genotype=None):
        from ..soil import DepthSimulator
        from ..ecosystem import EcosystemEngine
        identifiers=list(identifiers)
        if not identifiers:raise UnsupportedCropError('At least one explicit supported crop required')
        crops={self.require_quantitative(c):self.crop_parameters(c,profile=profile,region=region,genotype=genotype)for c in identifiers}
        if profile!='default':
            if processes is not None:raise ValueError('A contextual calibration may not silently override its process parameters')
            if len(crops)!=1:raise UnsupportedCropError('This historical calibration covers maize only')
            from ..intelligence.physical import PhysicalCandidate
            evidence=next(e for e in self.resolve(identifiers[0])['evidence']if e['kind']=='calibration'and e['profile']==profile and e['region']==region and e['genotype']==genotype and self.evidence_verified(e))
            extra=evidence['extra_process_requirements'];proc=Processes(**{k:v for k,v in extra.items()if k!='root_beta_per_m'})
            return EcosystemEngine(PhysicalCandidate(layers,crops,proc,latitude,options={'root_beta_per_m':extra['root_beta_per_m']}))
        return EcosystemEngine(DepthSimulator(layers,crops,processes or Processes(),latitude))

    def feasibility(self,identifier,*,mean_temperature_C=None,annual_rain_mm=None):
        c=self.resolve(identifier);e=c['suitability'];checks={}
        for name,value,lo,hi in [('temperature',mean_temperature_C,e.get('temperature_min_C'),e.get('temperature_max_C')),
            ('annual_rain',annual_rain_mm,e.get('annual_rain_min_mm'),e.get('annual_rain_max_mm'))]:
            if value is None or lo is None or hi is None:checks[name]=None
            else:
                if isinstance(value,bool)or not isinstance(value,(int,float))or not __import__('math').isfinite(value):raise ValueError('Finite environmental scenario required')
                checks[name]=lo<=value<=hi
        return {'identifier':c['identifier'],'envelope_checks':checks,'status':'descriptive_species_envelope_only',
            'yield':None,'soil_health_prediction':None,'irrigation_requirement':None,
            'scope':'EcoCrop species-level suitability ranges; annual precipitation is not crop water demand or irrigation need. Unknown checks remain null.'}
