"""Daily physiological telemetry; existing physical equations remain unchanged."""
from ..biological_recovery.plant import BiologicalKernel


class TransferKernel(BiologicalKernel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.capture_runs=True

    def _grow(self,soil,plant,day,et0,flux):
        crop=self.crops[plant.planting.crop]
        gdd=plant.gdd;progress=gdd/crop.maturity_gdd
        lai=self.canopy_lai(plant,crop)
        thermal=self.growth_temperature_factor(day,crop)
        result=super()._grow(soil,plant,day,et0,flux)
        # Telemetry refers to pre-growth stage and factors actually supplied.
        flux.update(phenology_fraction_before_growth=progress,
            active_growth_day=float(not plant.growth_ended and progress<1),
            gdd_before_growth=gdd, growth_temperature_factor=thermal,
            plant_total_N_kg_ha=plant.n_kg_ha,
            canopy_LAI_before_growth=lai,
            soil_water_before_evaporation_mm=sum(soil.water_mm))
        return result
