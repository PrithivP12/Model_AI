"""Isolated nonlinear development hypothesis, not a promoted cultivar model.

Wang & Engel (1998), Agricultural Systems 58:1–24,
https://doi.org/10.1016/S0308-521X(98)00028-6.
Only the temperature beta response is reproduced. Photoperiod/vernalization
and stage-specific coefficients are NOT inferred. Existing growth cardinals
are explicitly transferred, not measured development temperatures.
"""
import math
from ..evidence_readiness.identifiability import ShapeKernel


def development_temperature_response(temperature,base,optimum,maximum):
    if not all(math.isfinite(v)for v in [temperature,base,optimum,maximum])or not base<optimum<maximum:
        raise ValueError('Ordered finite development cardinal temperatures required')
    if temperature<=base or temperature>=maximum:return 0.
    alpha=math.log(2)/math.log((maximum-base)/(optimum-base))
    x=((temperature-base)/(optimum-base))**alpha
    return min(1.,max(0.,2*x-x*x))


class V11Kernel(ShapeKernel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.options.get('phenology_response','legacy')not in ['legacy','wang_engel_diagnostic']:
            raise ValueError('Unknown V1.1 phenology formulation')
        if self.options.get('phenology_response')=='wang_engel_diagnostic'and self.options.get('organ_model'):
            raise ValueError('Nonlinear development is not qualified for the experimental organ engine')

    def configuration_signature(self):
        return {**super().configuration_signature(),'version':'V1.1-unpromoted',
                'development_response':self.options.get('phenology_response','legacy')}

    def _diagnostic_growth(self,soil,plant,day,et0,flux):
        before=plant.gdd
        result=super()._diagnostic_growth(soil,plant,day,et0,flux)
        if self.options.get('phenology_response')=='wang_engel_diagnostic'and plant.gdd>before:
            crop=self.crops[plant.planting.crop]
            response=development_temperature_response(day.tmean_C,crop.base_temp_C,crop.optimum_temp_C,crop.maximum_temp_C)
            # Same peak degree-day rate; this scaling is a diagnostic transfer,
            # not a newly calibrated stage-duration or maturity observation.
            plant.gdd=before+(crop.optimum_temp_C-crop.base_temp_C)*response
        return result
