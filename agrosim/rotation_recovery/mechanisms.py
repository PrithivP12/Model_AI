"""Observed harvest scheduling as a conservative alternative to immediate residue return."""
from ..intelligence.physical import PhysicalCandidate
from ..processes import bounded


class ReconstructionKernel(PhysicalCandidate):
    def _scheduled(self,plant):
        return str(plant.planting.date)in self.options.get('observed_harvest_sowing_dates',[])

    def _grow(self,s,plant,day,et0,flux):
        crop=self.crops[plant.planting.crop]
        if self._scheduled(plant)and (plant.growth_ended or plant.gdd>=crop.maturity_gdd):
            plant.growth_ended=True;plant.growing_days+=1
            depth=min(sum(l.thickness_m for l in self.layers),max(.05,crop.max_root_depth_m*bounded(plant.gdd/crop.maturity_gdd/.5)))
            flux['root_depth_m']=depth;flux['lai']=0.
            # No extra assimilation/uptake is credited while mature/dead biomass stands.
            # Dead-canopy shade, grain drying/loss and root turnover need separate data.
            return 0.,depth
        return super()._grow(s,plant,day,et0,flux)

    def harvest_reason(self,plant,day,killed):
        if not self._scheduled(plant):return super().harvest_reason(plant,day,killed)
        crop=self.crops[plant.planting.crop]
        if killed or plant.gdd>=crop.maturity_gdd:plant.growth_ended=True
        return 'observed_harvest_date'if day.date==plant.planting.harvest_deadline else None

    def _harvest(self,s,plant,day,depth,reason,flux):
        before_c=s.surface_residue_c_kg_ha;before_n=s.surface_residue_n_kg_ha
        result=super()._harvest(s,plant,day,depth,reason,flux)
        result['shoot_residue_c_added_kg_ha']=s.surface_residue_c_kg_ha-before_c
        result['shoot_residue_n_added_kg_ha']=s.surface_residue_n_kg_ha-before_n
        return result
