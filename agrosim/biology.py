"""Evidence-gated ecological scenarios. Indices never masquerade as populations."""
from dataclasses import dataclass
import math
from .io import number


@dataclass(frozen=True)
class Evidence:
    source: str
    local_status: str
    parameter_status: str

    def validate(self):
        if not self.source:raise ValueError('Biological relationship/parameter source required')
        if self.local_status not in ['observed','assumed_scenario','unknown']:raise ValueError('Unknown biological presence status')
        if self.parameter_status not in ['field_calibrated','literature_transfer','assumed_scenario']:raise ValueError('Parameter evidence status required')


def residue_pathogen(initial_index,crops,hosts,annual_survival,host_amplification,evidence):
    """Residue-borne reservoir with saturating host input; no disease-severity claim."""
    evidence.validate()
    if evidence.local_status=='unknown':return {'status':'unsupported_local_presence','trajectory':None}
    value=number(initial_index,'initial pressure index',0,1);survival=number(annual_survival,'residue survival',0,1)
    amplification=number(host_amplification,'host amplification',0,10);rows=[]
    for crop in crops:
        if crop not in hosts:return {'status':'unknown_host_compatibility','crop':crop,'trajectory':None}
        host=number(hosts[crop],'host compatibility',0,1)
        remaining=value*survival
        value=1-(1-remaining)*math.exp(-amplification*host)
        rows.append({'crop':crop,'pressure_index':value,'mechanism':'residue reservoir survival and host-associated input'})
    return {'status':'modeled_index','trajectory':rows,'evidence':evidence.__dict__,
            'infection_probability':None,'yield_loss':None,'validated':False,
            'parameter_evidence_status_declared_by_caller':evidence.parameter_status}


def soil_pathogen(initial_index,crops,hosts,half_life_years,host_growth,evidence):
    """Persistent soil reservoir, parameterized separately from residue decomposition."""
    half=number(half_life_years,'soil reservoir half life',.01,100)
    result=residue_pathogen(initial_index,crops,hosts,math.exp(-math.log(2)/half),host_growth,evidence)
    if result.get('trajectory'):
        for row in result['trajectory']:row['mechanism']='persistent soil reservoir with explicit half life; host-specific input'
    return result


def annual_rootworm(initial_index,crops,egg_survival,host_multiplier,rotation_resistance,evidence):
    """Annual maize-associated pressure scenario; rotation resistance is explicit."""
    evidence.validate()
    if evidence.local_status=='unknown':return {'status':'unsupported_local_presence','trajectory':None}
    value=number(initial_index,'initial pest pressure',0,1);survival=number(egg_survival,'egg survival',0,1)
    growth=number(host_multiplier,'host multiplier',0,20);resistant=number(rotation_resistance,'rotation-resistance scenario',0,1)
    rows=[]
    for crop in crops:
        if crop not in ['corn','soybeans','spring_wheat']:return {'status':'unsupported_host','trajectory':None}
        host=1 if crop=='corn'else resistant
        larvae=value*survival*host
        value=1-math.exp(-growth*larvae)
        rows.append({'crop':crop,'larval_pressure_index':larvae,'next_year_egg_pressure_index':value})
    return {'status':'modeled_pressure_index','trajectory':rows,'evidence':evidence.__dict__,
            'actual_insects_per_area':None,'yield_loss':None}


def predator_suppression(pest_index,observed_activity,coefficient,evidence):
    evidence.validate()
    if evidence.local_status!='observed'or evidence.parameter_status!='field_calibrated':
        return {'status':'unsupported_without_paired_field_calibration','suppressed_index':None}
    value=number(pest_index,'pest index',0,1);activity=number(observed_activity,'method-specific observed activity',0)
    coefficient=number(coefficient,'paired-field suppression coefficient',0)
    return {'status':'conditional_field_calibration','suppressed_index':value*math.exp(-coefficient*activity)}


def pollination_scenario(crop,dependence=None,reference_service=None,scenario_service=None,evidence=None):
    # These grain crops do not require animal pollination.
    if crop in ['corn','spring_wheat','wheat','rice','barley','oats','sorghum']:
        return {'status':'animal_pollination_not_required','relative_yield_factor':1.,'local_pollinator_abundance':None}
    if dependence is None or reference_service is None or scenario_service is None or evidence is None:
        return {'status':'unsupported_crop_specific_response','relative_yield_factor':None}
    evidence.validate()
    if evidence.local_status!='observed':return {'status':'no_local_service_observation','relative_yield_factor':None}
    d=number(dependence,'crop animal-pollination dependence',0,1)
    reference=number(reference_service,'reference service index',0,1);scenario=number(scenario_service,'scenario service index',0,1)
    denominator=1-d+d*reference
    if denominator<=0:raise ValueError('A zero-reference production scenario cannot define a relative factor')
    return {'status':'conditional_response_scenario','relative_yield_factor':(1-d+d*scenario)/denominator,
            'baseline_service_already_accounted_for':True,'local_pollinator_abundance':None,
            'applied_to_physical_simulator':False}


def run_biology(config):
    crops=config['sequence'];outputs={}
    for entry in config.get('diseases',[]):
        e=Evidence(**entry['evidence'])
        if entry['mechanism']=='residue':value=residue_pathogen(entry['initial_index'],crops,entry['hosts'],entry['annual_survival'],entry['host_amplification'],e)
        elif entry['mechanism']=='soil':value=soil_pathogen(entry['initial_index'],crops,entry['hosts'],entry['half_life_years'],entry['host_growth'],e)
        else:raise ValueError('Unsupported pathogen mechanism; do not reuse a generic persistence law')
        outputs[entry['id']]=value
    for entry in config.get('pests',[]):
        if entry['mechanism']!='annual_rootworm':raise ValueError('Unsupported pest mechanism')
        outputs[entry['id']]=annual_rootworm(entry['initial_index'],crops,entry['egg_survival'],entry['host_multiplier'],entry['rotation_resistance'],Evidence(**entry['evidence']))
    return {'status':'research_ecological_scenarios','sequence':crops,'biological_states':outputs,
            'pollination':[pollination_scenario(c)for c in crops],
            'yield_adjustments_applied':False,'recommendation':None,
            'soil_biology':'Demand-limited legume fixation and temperature/moisture-sensitive residue decomposition are in the daily N/C engine; microbial community abundance is not reconstructed.'}
