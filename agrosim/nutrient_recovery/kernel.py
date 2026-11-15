"""Optional transfer bridge and training-bounded maturity grain-N remobilization."""
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
import hashlib
from ..processes import SoilState,CARBON_FRACTION
from ..reproductive_recovery.kernel import ReproductiveKernel
from ..biological_recovery.plant import validate_organs
from ..io import number
from .material import CN,harvest_transfer


class MaterialKernel(ReproductiveKernel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.grain_N_ceiling=self.options.get('maturity_grain_N_ceiling')
        if self.grain_N_ceiling is not None:number(self.grain_N_ceiling,'training-constrained maize grain N ceiling',.005,.03)

    def configuration_signature(self):
        signature=super().configuration_signature()
        signature['plant_material_sources']={str(p.relative_to(Path(__file__).resolve().parents[2])):
            hashlib.sha256(p.read_bytes()).hexdigest()for p in [Path(__file__).resolve(),Path(__file__).with_name('material.py')]}
        return signature

    def _grow(self,s,plant,day,et0,flux):
        crop=self.crops[plant.planting.crop];before=plant.gdd
        result=super()._grow(s,plant,day,et0,flux)
        if crop.name=='corn'and self.grain_N_ceiling is not None and before<crop.maturity_gdd<=plant.gdd:
            pools=plant.organ_state['pools'];grain=pools['grain']
            demand=max(0,grain['dry']*self.grain_N_ceiling-grain['N']);moved=0.
            # Available labile N first, then explicit tissue reserves above the
            # inherited floors. No root donor or N/biomass multiplier is added.
            for key,floor in [('labile_N',0.),('senesced_leaf',.003),('structural_shoot',.003),('green_leaf',.01)]:
                available=max(0,pools[key]['N']-pools[key]['dry']*floor)
                amount=min(demand,available);pools[key]['N']-=amount;grain['N']+=amount;demand-=amount;moved+=amount
            flux['maturity_internal_N_to_grain_kg_ha']=moved
            plant.organ_state['maturity_N_remobilization']={'date':str(day.date),'transferred_N':moved,
                'unmet_demand':demand,'grain_concentration_ceiling':self.grain_N_ceiling,
                'evidence':'Caller-supplied grain concentration ceiling; source/bounds must be retained in the experiment protocol. Conditional sensitivity, not fitted NHI or measured daily remobilization.'}
            validate_organs(plant)
            self.stage_snapshots[str(plant.planting.date)]=self.stage_state(plant,day)
        return result

    def _harvest(self,s,plant,day,depth,reason,flux):
        if plant.organ_state.get('material_harvest_applied'):raise ValueError('Harvest material was already transferred')
        validate_organs(plant);pools=plant.organ_state['pools']
        # Obtain the preserved metadata on a clone; no cloned soil/plant stock is
        # committed. Only the explicit account transaction below changes state.
        copied=deepcopy(s);metadata=super()._harvest(copied,deepcopy(plant),day,depth,reason,flux)
        terminate=plant.planting.harvest_mode=='terminate_to_residue'
        grain=CN(pools['grain']['dry']*CARBON_FRACTION,pools['grain']['N'])
        root=CN(pools['root']['dry']*CARBON_FRACTION,pools['root']['N'])
        shoot=CN(sum(v['dry']for k,v in pools.items()if k not in ['grain','root'])*CARBON_FRACTION,
            sum(v['N']for k,v in pools.items()if k not in ['grain','root']))
        # A terminated crop returns grain as unharvested shoot material. This
        # reproduces existing termination/removal semantics explicitly.
        if terminate:shoot=CN(shoot.carbon+grain.carbon,shoot.nitrogen+grain.nitrogen);grain=CN(0,0)
        weights=[f*l.thickness_m for f,l in zip(self.root_fractions(depth),self.layers)]
        new,export,audit=harvest_transfer(s,grain=grain,shoot=shoot,roots=root,root_weights=weights,
            shoot_removed=plant.planting.aboveground_residue_removal_fraction,basis='Simulated organ stocks; caller-declared residue-return scenario, not observed residue return.')
        for field in fields(SoilState):setattr(s,field.name,getattr(new,field.name))
        if max(abs(export.carbon-flux['harvested_c_kg_ha']),abs(export.nitrogen-flux['harvested_n_kg_ha']))>1e-7:raise AssertionError('Harvest bridge changed conserved export')
        for key in ['surface_residue_c_kg_ha','surface_residue_n_kg_ha']:
            if abs(getattr(s,key)-getattr(copied,key))>1e-7:raise AssertionError('Harvest bridge changed reference routing')
        for key in ['residue_c_kg_ha','residue_n_kg_ha']:
            if max(abs(a-b)for a,b in zip(getattr(s,key),getattr(copied,key)))>1e-7:raise AssertionError('Root residue routing changed')
        plant.organ_state['material_harvest_applied']=True
        metadata['material_transfers']=audit
        metadata['exported_grain_CN']={'carbon':grain.carbon,'nitrogen':grain.nitrogen}
        return metadata
