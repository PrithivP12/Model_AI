"""Explicit horizon-to-stock conversion; no unmeasured P/K/pH yield modifiers."""
from dataclasses import dataclass
from .processes import Layer, SoilState, Simulator
from .io import number


@dataclass(frozen=True)
class HorizonLayer(Layer):
    root_penetrability: float = 1.0

    def validate(self):
        super().validate()
        number(self.root_penetrability, 'root_penetrability', 0, 1)


class DepthSimulator(Simulator):
    def root_fractions(self, depth):
        fractions = []
        top = 0.0
        accessible = 1.0
        for layer in self.layers:
            accessible *= getattr(layer, 'root_penetrability', 1.0)
            fractions.append(max(0, min(1, (depth-top)/layer.thickness_m)) * accessible)
            top += layer.thickness_m
        return fractions


def compile_horizons(horizons):
    if not horizons:
        raise ValueError('At least one explicit soil horizon is required')
    layers, water, mineral, carbon, nitrogen, temperature = [], [], [], [], [], []
    previous = 0.0
    for h in horizons:
        top = number(h['top_cm'], 'top_cm', 0)
        bottom = number(h['bottom_cm'], 'bottom_cm', 0)
        if abs(top-previous)>1e-8 or bottom<=top:
            raise ValueError('Soil horizons must be contiguous, ordered and start at zero')
        thickness = (bottom-top)/100
        coarse = number(h['coarse_fragment_fraction'], 'coarse fragment fraction', 0, .95)
        fine = 1-coarse
        bulk = number(h['fine_earth_bulk_density_g_cm3'], 'fine earth bulk density', .1, 2.65)
        # Bulk density is per fine-earth volume, coarse fragments occupy explicit volume.
        mass = bulk*1000 * thickness*10000 * fine
        theta = [number(h[k], k, 0, .8) for k in ['theta_residual','theta_wp','theta_fc','theta_sat']]
        layer = HorizonLayer(thickness, theta[1]*fine, theta[2]*fine, theta[3]*fine,
                             h.get('drainage_fraction_day', .5), theta[0]*fine,
                             h.get('root_penetrability', 1.0))
        layer.validate()
        initial = number(h['theta_initial'], 'theta_initial', theta[0], theta[3])
        water.append(initial*fine*thickness*1000)
        mineral.append(number(h['mineral_n_mg_N_kg'], 'mineral N', 0)*mass/1e6)
        carbon.append(number(h['organic_c_g_kg'], 'organic C', 0, 500)*mass/1000)
        nitrogen.append(number(h['organic_n_g_kg'], 'organic N', 0, 100)*mass/1000)
        temperature.append(number(h['temperature_C'], 'soil temperature', -80, 80))
        layers.append(layer)
        previous = bottom
    state = SoilState(water, mineral, carbon, nitrogen, [0.]*len(layers), [0.]*len(layers), temperature)
    state.validate(layers)
    return layers, state
