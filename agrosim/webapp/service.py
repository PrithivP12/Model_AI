"""Deterministic application adapter; UI preferences never enter physical equations."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import threading
from collections import OrderedDict
from ..io import load_config, number
from ..simulation import prepare
from ..transfer_recovery.strategy import compare_evidence
from ..evidence_readiness.demo import reweight_priorities

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = ['date','crop','soil_water_mm','soil_mineral_n_kg_ha','soil_organic_c_kg_ha',
    'residue_c_kg_ha','plant_biomass_dry_kg_ha','lai','root_depth_m','water_stress_factor',
    'n_growth_factor','transpiration_mm','evaporation_mm','drainage_mm','runoff_mm',
    'mineralized_n_kg_ha','immobilized_n_kg_ha','leached_n_kg_ha','fixed_n_kg_ha',
    'respired_c_kg_ha','water_layer_0_mm','water_layer_1_mm','water_layer_2_mm']


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def validate_controls(value):
    allowed={'location','sequence','corn_n','irrigation_mm','residue_retained_percent'}
    if not isinstance(value,dict) or set(value)-allowed:raise ValueError('Unsupported simulation input')
    if value.get('location','ILLINOIS_DEMO')!='ILLINOIS_DEMO':
        raise ValueError('Only the prepared Illinois location is supported; global predictions are unavailable.')
    sequence=value.get('sequence',['corn','soybeans','corn'])
    if not isinstance(sequence,list)or len(sequence)!=3 or any(c not in ['corn','soybeans']for c in sequence):
        raise ValueError('Choose exactly three supported crops: corn or soybeans.')
    return {'location':'ILLINOIS_DEMO','sequence':sequence,
        'corn_n':number(value.get('corn_n',140),'corn fertilizer N',0,150),
        'irrigation_mm':number(value.get('irrigation_mm',20),'net irrigation per season',0,22.5),
        'residue_retained_percent':number(value.get('residue_retained_percent',100),'shoot residue retained percent',0,100)}


def unique_strategies(current):
    pool=[['corn','corn','corn'],['soybeans','corn','soybeans'],['corn','soybeans','corn'],['soybeans','soybeans','corn']]
    alternatives=[s for s in pool if s!=current][:2]
    return [{'id':name,'sequence':sequence}for name,sequence in zip(
        ['current_CSC','continuous_maize','soy_corn_soy'],[current,*alternatives])]


def build_demo(controls=None,root=ROOT):
    root=Path(root);controls=validate_controls(controls or {})
    config,base=load_config(root/'configs/evidence_demo.yaml')
    config=deepcopy(config);config['keep_daily']=True
    config['strategies']=unique_strategies(controls['sequence'])
    config['management_by_crop']['corn']['fertilizer_n_kg_ha']=controls['corn_n']
    for management in config['management_by_crop'].values():
        management['irrigation_mm']=controls['irrigation_mm']
        management['residue_removal_fraction']=1-controls['residue_retained_percent']/100
    result=compare_evidence(config,base)
    physical,_,_,_,_=prepare(config,base)
    for run in result['runs']:
        run['trajectory']={'columns':COLUMNS,'rows':[[row[k]for k in COLUMNS]for row in run.pop('daily')]}
        run['root_fraction_by_crop']={name:crop.root_biomass_fraction for name,crop in physical.crops.items()if name in ['corn','soybeans']}
        run['root_shoot_semantics']='Fixed accounting partition of simulated total biomass, not independently measured organ pools.'
    context_path=root/'reports/evidence-readiness/reproduced-final/NASA-context/report.json'
    if context_path.exists():context=json.loads(context_path.read_text())
    else:context={'sources':[],'status':'unavailable','reason':'Prepared NASA context file missing; no satellite state updates applied.'}
    factorial_path=root/'reports/evidence-readiness/reproduced-final/factorial/report.json'
    external_path=root/'reports/product-validation/external-audit/report.json'
    result.update(app_schema='agrosim-app-v1',controls=controls,location=config['location'],slots=config['slots'],
        strategy_labels={s['id']:('Your current rotation'if i==0 else
            'Continuous corn'if s['sequence']==['corn']*3 else
            'Soybean-led rotation'if s['sequence']==['soybeans','corn','soybeans']else
            ' → '.join('Corn'if c=='corn'else'Soybean'for c in s['sequence']))for i,s in enumerate(config['strategies'])},
        soil=config['soil'],economics_assumptions=config['economics'],NASA_context=context,
        scenario_assumptions={'normal':{'rain_multiplier':1,'temperature_delta_C':0},
            **{x['id']:{k:v for k,v in x.items()if k!='id'}for x in config['climate_sensitivities']}},
        factorial=json.loads(factorial_path.read_text())if factorial_path.exists()else None,
        external_validation=json.loads(external_path.read_text())if external_path.exists()else None,
        metric_definitions={'water_supply':'Gross irrigation supply m³/ha across three seasons (efficiency 0.9).',
            'N_loss':'Mean modeled leaching + denitrification kg N/ha across 12 equal-weight sensitivity runs.',
            'soil_organic_C':'Mean final humus C kg C/ha; residue C is separate. Not verified sequestration.',
            'scenario_net_return':'Minimum net return USD/ha across the declared runs, under supplied prices/costs; not profit or a forecast.'},
        boundary={'prediction_type':'Hypothetical management with archived historical weather mapped to 2027–2029.',
            'rotation_direction_baseline_percent':67.0,'joint_plateau_direction_percent':42.45283018867924,
            'rotation_comparisons':106,'independent_rotation_validation':False,'recommendation':None,
            'scenario_ranges_are_confidence_intervals':False,
            'field_advice':'Experimental exploration. Verify farm inputs and consult local agricultural expertise.'})
    result['scientific_source_hashes']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((root/'agrosim').rglob('*.py'))}
    result['result_id']=hashlib.sha256(canonical(result)).hexdigest()
    return result


class DemoService:
    """Bounded, serialized local computations; priorities only postprocess cached data."""
    def __init__(self,prepared=None,builder=build_demo):
        self.builder=builder;self.cache=OrderedDict();self.lock=threading.Lock();self.physical_builds=0
        if prepared is not None:
            value=json.loads(Path(prepared).read_text())
            if value.get('app_schema')!='agrosim-app-v1':raise ValueError('Invalid prepared demonstration')
            digest=value.pop('result_id');actual=hashlib.sha256(canonical(value)).hexdigest();value['result_id']=digest
            if actual!=digest:raise ValueError('Prepared demonstration integrity check failed')
            self.cache[canonical(validate_controls(value['controls']))]=value

    def simulation(self,controls):
        controls=validate_controls(controls);key=canonical(controls)
        with self.lock:
            if key not in self.cache:
                self.cache[key]=self.builder(controls);self.physical_builds+=1
                while len(self.cache)>4:self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            return self.cache[key]

    def priorities(self,result_id,priorities):
        with self.lock:
            result=next((v for v in self.cache.values()if v['result_id']==result_id),None)
            if result is None:raise ValueError('Result expired; reload the simulation before changing priorities.')
            if not isinstance(priorities,list)or any(not isinstance(p,str)for p in priorities):raise ValueError('Invalid priorities')
            return {**reweight_priorities(result,priorities),'result_id':result_id}
