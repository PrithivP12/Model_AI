"""Isolated soil-water formulations; every flux passes the original mass ledger."""
from dataclasses import replace
import math
from ..soil import DepthSimulator
from ..processes import bounded


class DiagnosticSimulator(DepthSimulator):
    def __init__(self,*args,options=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.options=options or {};self.captured=[];self.surface_depletion=0.;self.wetted_fraction=.4

    def run(self,weather,initial_state,*args,**kwargs):
        # On observed-profile initialization De is explicitly reinitialized, not a water source.
        layer=self.layers[0];theta=initial_state.water_mm[0]/(layer.thickness_m*1000)
        self.surface_depletion=max(0,(layer.field_capacity-theta)*min(.1,layer.thickness_m)*1000)
        result=super().run(weather,initial_state,*args,**kwargs);self.captured.append(result)
        return result

    def _hydrology(self,s,day,irrigation,flux):
        original=self.layers
        if self.options.get('no_drainage'):
            self.layers=tuple(replace(l,drainage_fraction_day=0)for l in original)
        try:super()._hydrology(s,day,irrigation,flux)
        finally:self.layers=original

    def _grow(self,s,plant,day,et0,flux):
        before=list(s.water_mm)
        cover,depth=super()._grow(s,plant,day,et0,flux)
        if self.options.get('no_transpiration'):
            s.water_mm[:]=before
            plant.transpiration_mm-=flux['transpiration_mm'];flux['transpiration_mm']=0.
        return cover,depth

    def soil_evaporation_demand(self,s,day,et0,cover,plant,flux):
        if self.options.get('no_evaporation'):return 0.
        if self.options.get('evaporation')!='fao56_dual':
            return super().soil_evaporation_demand(s,day,et0,cover,plant,flux)
        # FAO-56 equations 71–77, simplified Kcmax=1.2 without a wind/RH adjustment.
        layer=self.layers[0];ze=min(.1,layer.thickness_m)
        self.tew=max(1,1000*(layer.field_capacity-.5*layer.wilting_point)*ze)
        rew=min(9,.8*self.tew)
        irrigation=flux['irrigation_mm'];rain=(flux['rain_mm']if day.tmean_C>0 else 0.)+flux.get('snow_melt_mm',0.)
        if rain>0:self.wetted_fraction=1.
        elif irrigation>0:self.wetted_fraction=bounded(.1*math.sqrt(irrigation),.1,1.)
        self.surface_depletion=bounded(self.surface_depletion-rain-irrigation/self.wetted_fraction,0,self.tew)
        kr=1. if self.surface_depletion<=rew else bounded((self.tew-self.surface_depletion)/(self.tew-rew))
        kcb=self.crops[plant.planting.crop].kc_mid*cover if plant else 0.
        kcmax=max(1.2,kcb+.05);self.few=min(1-cover,self.wetted_fraction)
        ke=min(kr*(kcmax-kcb),self.few*kcmax)
        # Residue shade is explicit and borrowed from the trial's methodology, not fitted.
        return max(0,ke*et0*(1-self.options.get('residue_shade_fraction',.25)))

    def after_soil_evaporation(self,flux):
        if self.options.get('evaporation')=='fao56_dual'and self.few>1e-10:
            self.surface_depletion=min(self.tew,self.surface_depletion+flux['evaporation_mm']/self.few)
