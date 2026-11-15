"""One-composite source/sink candidate on the conserved organ-transfer engine.

The maturity-fraction window is an explicit scenario proxy, not observed R1.
Capacity has units kg dry grain/ha. Kernel number/weight are not identified.
"""
from ..evidence_readiness.identifiability import ShapeKernel
from ..io import number
from ..processes import bounded
from pathlib import Path
import hashlib,inspect


class ReproductiveKernel(ShapeKernel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.sink_coefficient=number(self.options.get('sink_coefficient',2.),'sink composite',.05,12)
        self.window_start=number(self.options.get('sink_window_start',.35),'sink window start',.1,.5)
        self.window_end=number(self.options.get('sink_window_end',.6),'sink window end',.5,.8)
        if self.window_start>=self.window_end:raise ValueError('Empty reproductive window')
        if not self.options.get('organ_model'):raise ValueError('Explicit source/sink requires conserved organ accounting')

    def configuration_signature(self):
        signature=super().configuration_signature()
        signature['reproductive_inheritance_sources']={Path(inspect.getsourcefile(cls)).name:
            hashlib.sha256(Path(inspect.getsourcefile(cls)).read_bytes()).hexdigest()
            for cls in type(self).__mro__ if cls.__module__.startswith('agrosim.')}
        return signature

    def run(self,*args,**kwargs):
        result=super().run(*args,**kwargs)
        # Daily sink capacity/window assimilation are states, and backlog demand
        # is not an actual material transfer. Never accumulate them as fluxes.
        for key in ['reproductive_capacity_kg_dry_ha','reproductive_window_assimilate_kg_dry_ha','grain_sink_demand_kg_dry_ha']:
            result['totals'].pop(key,None)
        return result

    def _grow(self,soil,plant,day,et0,flux):
        previous=plant.organ_state.get('pools',{}).get('grain',{'dry':0.,'N':0.}).copy()
        result=super()._grow(soil,plant,day,et0,flux)
        grain=plant.organ_state['pools']['grain']
        flux['grain_internal_transfer_dry_kg_ha']=grain['dry']-previous['dry']
        flux['grain_internal_transfer_n_kg_ha']=grain['N']-previous['N']
        return result

    def grain_demand(self,plant,crop,day,flux,progress,filling,new_above):
        scope=self.options.get('source_sink_sowing_dates')
        if crop.name!='corn'or(scope is not None and str(plant.planting.date)not in scope):
            return super().grain_demand(plant,crop,day,flux,progress,filling,new_above)
        organs=plant.organ_state
        record=organs.setdefault('reproductive_sink',{'previous_progress':0.,'window_assimilate_dry_kg_ha':0.,
            'capacity_dry_kg_ha':None,'capacity_set_date':None,'potential_released_dry_kg_ha':0.})
        previous=record['previous_progress']
        if progress<previous-1e-12:raise ArithmeticError('Reproductive development went backwards')
        overlap=max(0,min(progress,self.window_end)-max(previous,self.window_start))
        fraction=overlap/max(1e-12,progress-previous)
        # Actual net growth already reflects temperature/water/N limitation.
        # No second empirical stress penalty is applied to this same source.
        record['window_assimilate_dry_kg_ha']+=flux['growth_dry_kg_ha']*(1-crop.root_biomass_fraction)*fraction
        if progress>=self.window_end and record['capacity_dry_kg_ha']is None:
            record['capacity_dry_kg_ha']=self.sink_coefficient*record['window_assimilate_dry_kg_ha']
            record['capacity_set_date']=str(day.date)
        capacity=record['capacity_dry_kg_ha']or 0.
        fill_progress=bounded((progress-self.window_end)/(1-self.window_end))
        released=capacity*fill_progress
        # Unmet earlier sink demand may be filled later, subject to actual C
        # donors, published remobilization limits and tissue N floors upstream.
        demand=max(0,released-organs['pools']['grain']['dry'])
        record['potential_released_dry_kg_ha']=released;record['previous_progress']=progress
        flux.update(reproductive_capacity_kg_dry_ha=capacity,
            reproductive_window_assimilate_kg_dry_ha=record['window_assimilate_dry_kg_ha'],
            grain_sink_demand_kg_dry_ha=demand)
        return demand
