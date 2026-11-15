"""Persistent daily coupling around the existing conservative crop/soil kernel.

Biological pressure indices carry no W/N/C mass and are never called abundance.
All optional growth effects modify bounded net-assimilation fluxes before growth.
"""
from dataclasses import asdict,dataclass,field,replace
from datetime import date,timedelta
from copy import deepcopy
from pathlib import Path
import hashlib,json,math,inspect
from .processes import SoilState,PlantState,Planting,CARBON_FRACTION,bounded
from .soil import DepthSimulator
from .io import number,write_json
from .traits import cicada_registry

EVIDENCE_CLASSES={'empirically_validated','calibrated_not_independently_validated',
                 'literature_parameterized','exploratory_scenario','unsupported_disabled'}


@dataclass
class FluxCorrection:
    """Small bounded statistical correction to net assimilation; untrained by default."""
    coefficients:dict=field(default_factory=dict)
    intercept:float=0.
    fitted_artifact_id:str|None=None
    minimum:float=.5
    maximum:float=1.5

    def __post_init__(self):
        number(self.minimum,'minimum flux multiplier',.1,1.)
        number(self.maximum,'maximum flux multiplier',1.,2.)
        number(self.intercept,'flux correction intercept',-10,10)
        allowed={'relative_soil_water','mineral_n_index','phenology_fraction'}
        if set(self.coefficients)-allowed:raise ValueError('Unrecognized learned state input')
        for value in self.coefficients.values():number(value,'correction coefficient',-10,10)
        if self.coefficients and not self.fitted_artifact_id:raise ValueError('A fitted artifact identity is required for learned coefficients')

    def multiplier(self,features):
        if not self.fitted_artifact_id:return 1.
        raw=self.intercept+sum(value*number(features[name],name,0,1)for name,value in self.coefficients.items())
        return bounded(1+raw,self.minimum,self.maximum)

    def evidence(self):
        return {'trained':bool(self.fitted_artifact_id),'artifact_id':self.fitted_artifact_id,
            'evidence_class':'exploratory_scenario'if self.fitted_artifact_id else 'unsupported_disabled',
            'correction_target':'bounded net-assimilation multiplier, not an additive state increment',
            'accuracy_established':False,
            'reason':'No identified daily assimilation targets are present in the present yield/profile tables; no new component has been trained.'if not self.fitted_artifact_id else 'Caller-supplied coefficients require separate validation.'}


@dataclass
class EcosystemState:
    soil:dict
    plant:dict|None=None
    biological:dict=field(default_factory=dict)
    last_date:str|None=None
    crop_history:list=field(default_factory=list)
    cumulative_fluxes:dict=field(default_factory=dict)
    initial_inventory:dict=field(default_factory=dict)
    initialization_adjustments:list=field(default_factory=list)
    engine_identity:str|None=None
    analysis_adjustments:list=field(default_factory=list)

    def checkpoint(self,path):write_json(path,{'schema_version':1,'state':asdict(self)})

    @classmethod
    def restore(cls,path):
        value=json.loads(Path(path).read_text())
        if value.get('schema_version')!=1:raise ValueError('Unsupported ecosystem checkpoint schema')
        return cls(**value['state'])


def decode_plant(value):
    if value is None:return None
    data=deepcopy(value);p=data.pop('planting')
    p['date']=date.fromisoformat(p['date']);p['harvest_deadline']=date.fromisoformat(p['harvest_deadline'])
    return PlantState(Planting(**p),**data)


def encode_plant(plant):
    if plant is None:return None
    data=asdict(plant)
    for key in ['date','harvest_deadline']:data['planting'][key]=str(data['planting'][key])
    return data


class CoupledPhysicalKernel(DepthSimulator):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.growth_multiplier=1.

    def _grow(self,s,plant,day,et0,flux):
        key=plant.planting.crop;original=self.crops[key]
        self.crops[key]=replace(original,rue_g_dry_per_MJ_PAR=original.rue_g_dry_per_MJ_PAR*self.growth_multiplier)
        try:return super()._grow(s,plant,day,et0,flux)
        finally:self.crops[key]=original


class EcosystemEngine:
    """One-day operator splitting: lagged biology -> physical fluxes -> biology.

    This avoids a same-day circular solve and cannot subtract the same carbon twice.
    Biological mechanisms have caller-declared state origins and explicit ranges.
    """
    def __init__(self,physical,biology=None,correction=None,traits=None):
        self.physical=physical.clone_for_ecosystem()if hasattr(physical,'clone_for_ecosystem')else CoupledPhysicalKernel(physical.layers,physical.crops,physical.processes,physical.latitude)
        self.config=deepcopy(biology or {});self.correction=correction or FluxCorrection()
        self.traits=traits or cicada_registry()
        if self.config.get('allow_exploratory_growth_feedback',False):
            raise ValueError('Arbitrary organism pressure-to-yield factors are disabled; calibrated resource-flux coupling is required')
        for key in ['habitat_index','slope_fraction']:
            if key in self.config:number(self.config[key],key,0,1)
        self.identity=self.configuration_identity()
        for kind in ['pathogens','pests','enemies']:
            for identifier,spec in self.config.get(kind,{}).items():
                if spec.get('state_origin')not in ['observed','estimated','simulated','scenario_assumed','unknown']:
                    raise ValueError('State origin required for '+identifier)
                if spec.get('evidence_class')not in EVIDENCE_CLASSES:raise ValueError('Scientific evidence class required')
                if not spec.get('parameter_source'):raise ValueError('Parameter source or scenario declaration required')
                initial=spec.get('initial_index')
                if spec['state_origin']=='unknown'and initial is not None:raise ValueError('Unknown presence cannot have a numeric abundance/pressure')
                if initial is not None:number(initial,'initial pressure',0,1)
                for name,upper in [('reproduction_day',.5),('mortality_day',1.),('suppression_coefficient',2.),('damage_coefficient',1.)]:
                    if name in spec:number(spec[name],name,0,upper)
                for weight in spec.get('host_weights',{}).values():number(weight,'host compatibility',0,1)

    def configuration_identity(self):
        physical=self.physical
        value={'layers':[asdict(l)for l in physical.layers],
            'crops':{k:asdict(v)for k,v in physical.crops.items()},'processes':asdict(physical.processes),
            'latitude':physical.latitude,'biology':self.config,'correction':asdict(self.correction),
            'traits':self.traits.manifest(),
            'physical_signature':physical.configuration_signature()if hasattr(physical,'configuration_signature')else {},
            'physical_implementation_sha256':hashlib.sha256(Path(inspect.getsourcefile(type(physical))).read_bytes()).hexdigest(),
            'engine_code':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()for name in ['ecosystem.py','processes.py','soil.py','traits.py']}}
        return hashlib.sha256(json.dumps(value,sort_keys=True,default=lambda x:asdict(x)).encode()).hexdigest()

    def initialize(self,soil,plant=None,provenance=None):
        soil.validate(self.physical.layers)
        biology={kind:{key:spec.get('initial_index')for key,spec in self.config.get(kind,{}).items()}
                 for kind in ['pathogens','pests','enemies']}
        return EcosystemState(asdict(soil),plant=encode_plant(plant),biological=biology,
            initial_inventory=self.physical.inventories(soil,plant),engine_identity=self.identity,
            initialization_adjustments=[{'kind':'initial_condition','state_origin':provenance or 'scenario_assumed_unless_supplied_with_measured_provenance',
                                         'initial_inventory':self.physical.inventories(soil,plant),
                                         'water_mm':sum(soil.water_mm),'mineral_n_kg_ha':sum(soil.mineral_n_kg_ha)}])

    def evidence(self):
        values={
          'water_crop_feedback':{'evidence_class':'literature_parameterized','status':'implemented_but_fails_current_water_persistence_benchmark'},
          'nitrogen_crop_feedback':{'evidence_class':'literature_parameterized','status':'demand/uptake/fixation coupled; parameters not regionally validated'},
          'residue_water_carbon_nitrogen_feedback':{'evidence_class':'literature_parameterized','status':'temperature/moisture decay, mulch cover and N immobilization/mineralization'},
          'cover_termination':{'evidence_class':'exploratory_scenario','status':'all biomass returned to residue when explicit termination mode is used'},
          'terrain_water_redistribution':{'evidence_class':'unsupported_disabled','status':'single-column runoff only; no lateral routing claimed'},
          'erosion_susceptibility':{'evidence_class':'exploratory_scenario','status':'cover/rain/runoff/slope proxy; not soil loss or SOC export'},
          'learned_flux':self.correction.evidence(),
          'organism_traits':{'evidence_class':'literature_parameterized','status':'declarative contracts; quantitative organism dynamics disabled'}}
        for kind in ['pathogens','pests','enemies']:
            for key,spec in self.config.get(kind,{}).items():values[kind+':'+key]={**spec,'local_abundance_measured':False,
                'numeric_state_semantics':'unitless pressure proxy, not count per area'}
        return values

    def _feedback(self,state,soil,plant):
        factor=1.;contributions={}
        # Organism effects require resource-linked, calibrated fluxes; no arbitrary yield factors.
        depth=sum(l.thickness_m for l in self.physical.layers)
        water=sum(soil.water_mm)/(depth*1000)
        learned=self.correction.multiplier({'relative_soil_water':bounded(water/.5),
           'mineral_n_index':bounded(sum(soil.mineral_n_kg_ha)/200),
           'phenology_fraction':bounded(plant.gdd/self.physical.crops[plant.planting.crop].maturity_gdd)if plant else 0.})
        return bounded(factor,.1,1)*learned,contributions,learned

    def _update_biology(self,state,day,crop,flux,soil):
        wet=bounded(sum(soil.water_mm)/sum(l.mm(l.field_capacity)for l in self.physical.layers))
        temperature=bounded(1-abs(day.tmean_C-25)/20)
        habitat=self.config.get('habitat_index',0)
        old=deepcopy(state.biological)
        for kind in ['pathogens','pests','enemies']:
            for key,spec in self.config.get(kind,{}).items():
                value=old[kind][key]
                if value is None:continue
                host=spec.get('host_weights',{}).get(crop,0.)if crop else 0.
                rate=spec.get('reproduction_day',0)*host*wet*temperature
                if kind=='enemies':
                    prey=max([v for v in old['pests'].values()if v is not None]or[0.])
                    rate=spec.get('reproduction_day',0)*habitat*prey*temperature
                grown=1/(1+(1/value-1)*math.exp(-rate))if value>0 else 0.
                mortality=spec.get('mortality_day',0.)
                if kind=='pathogens'and spec.get('mechanism')=='residue':
                    mortality+=min(.1,flux['respired_c_kg_ha']/max(1,sum(soil.residue_c_kg_ha)+soil.surface_residue_c_kg_ha))
                if kind=='pathogens'and spec.get('mechanism')not in ['residue','soil']:
                    raise ValueError('Pathogen mechanism must be residue or soil reservoir')
                if kind=='pests':
                    enemy=max([v for v in old['enemies'].values()if v is not None]or[0.])
                    mortality+=spec.get('suppression_coefficient',0)*enemy
                state.biological[kind][key]=bounded(grown*math.exp(-mortality))

    def run(self,weather,state,plantings=(),management=(),keep_daily=True):
        weather=list(weather);state=deepcopy(state)
        if not weather:raise ValueError('Weather cannot be empty')
        if self.configuration_identity()!=self.identity:raise ValueError('Engine configuration changed after construction; create a new engine and explicit initial state')
        if state.engine_identity!=self.identity:raise ValueError('Checkpoint engine/configuration identity mismatch')
        for i,day in enumerate(weather):
            day.validate()
            previous=date.fromisoformat(state.last_date)if i==0 and state.last_date else weather[i-1].date if i else None
            if previous is not None and day.date!=previous+timedelta(days=1):raise ValueError('Checkpoint/weather dates must be contiguous')
        if set(state.biological)!=set(['pathogens','pests','enemies']):raise ValueError('Invalid biological checkpoint groups')
        for kind,values in state.biological.items():
            if set(values)!=set(self.config.get(kind,{})):raise ValueError('Invalid biological checkpoint identities')
            for key,value in values.items():
                if value is not None:number(value,'checkpoint pressure',0,1)
                if self.config[kind][key]['state_origin']=='unknown'and value is not None:raise ValueError('Unknown state became numeric')
        scheduled=sorted(plantings,key=lambda p:p.date)
        for i,p in enumerate(scheduled):
            if i and p.date<=scheduled[i-1].harvest_deadline:raise ValueError('Overlapping crop strategy slots')
        daily=[];harvests=[];max_errors={k:0. for k in ['water','nitrogen','carbon']}
        for day in weather:
            soil=SoilState(**state.soil);plant=decode_plant(state.plant)
            todays=[p for p in scheduled if p.date==day.date]
            if todays and plant:raise ValueError('Planting overlaps an active crop')
            # A new sowing uses the current crop's host response, without adding seed to state twice.
            prospective=plant or (PlantState(todays[0],0.,0.)if todays else None)
            factor,mechanisms,learned=self._feedback(state,soil,prospective)
            self.physical.growth_multiplier=factor
            result=self.physical.run([day],soil,todays,[m for m in management if m.date==day.date],
                keep_daily=True,initial_plant=plant,allow_unharvested=True)
            row=result['daily'][0];new_soil=SoilState(**result['final_state']);crop=row['crop']
            self._update_biology(state,day,crop,row,new_soil)
            state.soil=result['final_state'];state.plant=encode_plant(result['active_plant_state']);state.last_date=str(day.date)
            for h in result['harvests']:
                harvests.append(h);state.crop_history.append({'crop':h['crop'],'planted':h['planted'],'harvested':h['harvested'],
                    'dry_grain_kg_ha':h['grain_dry_kg_ha']})
            for key,value in result['totals'].items():
                state.cumulative_fluxes[key]=state.cumulative_fluxes.get(key,0)+value
            for key,value in result['max_daily_balance_error'].items():max_errors[key]=max(max_errors[key],value)
            cover=1-math.exp(-.6*row['lai'])
            erosion=bounded(self.config.get('slope_fraction',0)*(1-cover)*(1-math.exp(-(day.rain_mm+row['runoff_mm'])/25)))
            if keep_daily:daily.append({**row,'biological_pressure_indices':deepcopy(state.biological),
                'net_assimilation_multiplier':factor,'lagged_biological_growth_factors':mechanisms,'learned_flux_multiplier':learned,
                'erosion_susceptibility_index':erosion,'erosion_mass_loss_computed':False,
                'microbial_function_proxy':row['respired_c_kg_ha']/max(1,sum(new_soil.humus_c_kg_ha)+sum(new_soil.residue_c_kg_ha)),
                'pollination_yield_factor':1. if crop in ['corn','spring_wheat']else None,
                'local_pollinator_abundance':None})
        final=self.physical.inventories(SoilState(**state.soil),decode_plant(state.plant))
        analysis={key:sum(a['analysis_increment'].get(key,0.) for a in state.analysis_adjustments if a.get('applied'))for key in final}
        if any(a.get('physical_flux',False)for a in state.analysis_adjustments):raise ValueError('Observation corrections cannot be labeled physical fluxes')
        if not all(math.isfinite(v)for v in analysis.values()):raise ValueError('Nonfinite observation adjustment')
        ledger={key:final[key]-state.initial_inventory[key]-state.cumulative_fluxes.get(key+'_inputs',0)+state.cumulative_fluxes.get(key+'_outputs',0)-analysis[key]
                for key in final}
        if max(abs(v)for v in ledger.values())>1e-6:raise ArithmeticError('Persistent ecosystem stock ledger failed')
        return {'status':'coupled_ecosystem_research_simulation','checkpoint':state,'daily':daily,'harvests':harvests,
            'final_inventory':final,'whole_run_balance_error':ledger,'max_daily_balance_error':max_errors,
            'analysis_cumulative_increments':analysis,
            'evidence':self.evidence(),'trait_interactions':{key:self.traits.assess(key,('annual_crop',))for key in self.traits.organisms},'recommendation':None,'global_accuracy_established':False,
            'biology_feedback_enabled':self.config.get('allow_exploratory_growth_feedback',False),
            'update_order':['lagged biological effects and bounded learned flux','physical forcing/hydrology',
                'soil decomposition/mineralization','plant water/N demand and net assimilation','soil evaporation/harvest',
                'biological pressure update from resulting physical state','ledger verification/checkpoint'],
            'limits':['Biological indices are simulated proxies, not local organism counts.',
                'Organism damage feedback is disabled; no arbitrary pressure-to-yield factors are used.',
                'No explicit weed competition, lateral terrain routing, measured microbial populations or erosion mass loss is claimed.']}
