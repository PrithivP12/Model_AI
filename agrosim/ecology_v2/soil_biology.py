"""Compact C/N decomposer candidate, not an inferred taxonomic population.

Stoichiometric mass balances motivated by Manzoni et al. (2012),
doi:10.1111/j.1469-8137.2012.04225.x. This is not that paper's simulator.
All coefficients/initial stocks are explicit experimental scenarios, not fits.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import math

from ..evaluator.audit import EvaluationKernel, organic, reconcile
from ..io import number
from ..processes import bounded


@dataclass(frozen=True)
class Parameters:
    cue: float = .45
    microbial_cn: float = 8.
    turnover_day20: float = .02
    uptake_per_C_day20: float = .3
    turnover_humus_fraction: float = .25
    formulation: str = 'throughput_limited'
    legacy_residue_control: bool = False

    def __post_init__(self):
        number(self.cue, 'C assimilation fraction', .01, .9)
        number(self.microbial_cn, 'functional biomass C:N', 2, 30)
        number(self.turnover_day20, 'turnover day20', 0, .5)
        number(self.uptake_per_C_day20, 'uptake per C day20', 0, 5)
        number(self.turnover_humus_fraction, 'turnover humus fraction', 0, 1)
        if self.formulation not in ['throughput_limited', 'overflow_respiration']:
            raise ValueError('Unsupported C/N limitation formulation')
        if not isinstance(self.legacy_residue_control, bool):
            raise ValueError('Legacy partition control must be an explicit boolean')
        if self.legacy_residue_control and (self.turnover_day20 or self.uptake_per_C_day20):
            raise ValueError('Partition-only legacy control requires zero biological uptake/turnover')


@dataclass
class Decomposers:
    carbon_kg_ha: list
    nitrogen_kg_ha: list
    origin: str
    source: str

    def validate(self, layers, cn):
        if self.origin not in ['observed', 'scenario_assumed'] or not self.source:
            raise ValueError('Explicit observed or hypothetical decomposer-stock provenance required')
        if len(self.carbon_kg_ha) != len(layers) or len(self.nitrogen_kg_ha) != len(layers):
            raise ValueError('One functional biomass stock per soil layer required')
        for c, n in zip(self.carbon_kg_ha, self.nitrogen_kg_ha):
            number(c, 'decomposer carbon', 0); number(n, 'decomposer nitrogen', 0)
            if abs(c - n * cn) > 1e-7:
                raise ValueError('Initial functional biomass must match the explicitly fixed C:N')


def partition_humus(soil, fraction, parameters):
    """Conservative scenario initialization, not extra C/N or an observation."""
    number(fraction, 'hypothetical initial decomposer C fraction', 0, .1)
    s = deepcopy(soil)
    c = [v * fraction for v in s.humus_c_kg_ha]
    n = [v / parameters.microbial_cn for v in c]
    if any(required > existing for required, existing in zip(n, s.humus_n_kg_ha)):
        raise ValueError('Insufficient humus N for supplied partition; cannot invent initial N')
    s.humus_c_kg_ha = [v - x for v, x in zip(s.humus_c_kg_ha, c)]
    s.humus_n_kg_ha = [v - x for v, x in zip(s.humus_n_kg_ha, n)]
    return s, Decomposers(c, n, 'scenario_assumed',
                         f'Explicit {fraction} fraction of initial humus C reclassified; N at scenario C:N {parameters.microbial_cn}; not a field estimate')


class SoilBiologyKernel(EvaluationKernel):
    """Existing crop/water solver with an optional explicit functional C/N pool.

The new pool is included once in inventories and must be saved alongside every
ecosystem checkpoint. Soil and plant types, growth and hydrology are unchanged.
"""

    def __init__(self, *args, decomposers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.bio_parameters = Parameters(**self.options.get('soil_biology', {}))
        self.decomposers = deepcopy(decomposers)
        if self.decomposers is not None:
            self.decomposers.validate(self.layers, self.bio_parameters.microbial_cn)

    def clone_for_ecosystem(self):
        clone = type(self)(self.layers, deepcopy(self.crops), self.processes, self.latitude,
                           options=deepcopy(self.options), decomposers=self.decomposers)
        clone.capture_runs = False
        return clone

    def inventories(self, soil, plant):
        if self.decomposers is None:
            raise ValueError('Unknown decomposer state: quantitative soil biology disabled')
        value = super().inventories(soil, plant)
        value['carbon'] += sum(self.decomposers.carbon_kg_ha)
        value['nitrogen'] += sum(self.decomposers.nitrogen_kg_ha)
        return value

    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        # Standing biomass telemetry is a stock, never an integrated flux.
        for key in ['functional_decomposer_C_kg_ha', 'functional_decomposer_N_kg_ha']:
            result['totals'].pop(key, None)
        return result

    def decomposition_environment(self, s, i, day):
        """Legacy response exactly retained; optional kernels may override it."""
        p = self.processes; layer = self.layers[i]
        temp = bounded(p.decomposition_q10 ** ((s.temperature_C[i] - 20) / 10), 0, 4) if s.temperature_C[i] > 0 else 0.
        wet = bounded((s.water_mm[i] - layer.mm(layer.residual_water_content)) /
                      max(1, layer.mm(layer.field_capacity - layer.residual_water_content)))
        oxygen = 1 - .7 * bounded((s.water_mm[i] - layer.mm(layer.field_capacity)) /
                                  layer.mm(layer.saturation - layer.field_capacity))
        return temp * wet * oxygen, temp

    def _decomposition(self, s, day, flux):
        if self.decomposers is None:
            raise ValueError('No quantitative process without explicit functional biomass')
        if self.bio_parameters.legacy_residue_control:
            # Isolate initialization: biomass is held fixed, while ALL legacy
            # soil decomposition equations remain active and unchanged.
            EvaluationKernel._decomposition(self, s, day, flux)
            flux['functional_decomposer_C_kg_ha'] = sum(self.decomposers.carbon_kg_ha)
            flux['functional_decomposer_N_kg_ha'] = sum(self.decomposers.nitrogen_kg_ha)
            return
        before = deepcopy(s); old = deepcopy(self.decomposers)
        p = self.processes; b = self.bio_parameters; cells = self.decomposers
        extra = dict.fromkeys(['decomposer_assimilated_C', 'decomposer_assimilated_N',
                              'decomposer_turnover_C', 'decomposer_turnover_N',
                              'decomposer_to_humus_C', 'decomposer_to_humus_N',
                              'decomposer_to_residue_C', 'decomposer_to_residue_N',
                              'residue_consumed_C', 'residue_consumed_N',
                              'humus_respired_C', 'residue_respired_C',
                              'humus_mineralized_N', 'residue_mineralized_N', 'residue_immobilized_N'], 0.)
        for i, layer in enumerate(self.layers):
            s.temperature_C[i] += (day.tmean_C - s.temperature_C[i]) / (3 + 4 * i)
            modifier, temp = self.decomposition_environment(s, i, day)
            # Existing background humus decay remains distinct from new residue
            # assimilation. Each source is consumed exactly once.
            f = -math.expm1(-p.humus_decay_per_day_20C * modifier)
            hc, hn = s.humus_c_kg_ha[i] * f, s.humus_n_kg_ha[i] * f
            s.humus_c_kg_ha[i] -= hc; s.humus_n_kg_ha[i] -= hn
            s.mineral_n_kg_ha[i] += hn
            extra['humus_respired_C'] += hc; extra['humus_mineralized_N'] += hn
            flux['respired_c_kg_ha'] += hc; flux['mineralized_n_kg_ha'] += hn
            # Cold/dry shutdown applies to turnover too. This is a rate
            # modifier, not a measured dormant population or freeze mortality.
            f = -math.expm1(-b.turnover_day20 * modifier)
            tc, tn = cells.carbon_kg_ha[i] * f, cells.nitrogen_kg_ha[i] * f
            cells.carbon_kg_ha[i] -= tc; cells.nitrogen_kg_ha[i] -= tn
            humc, humn = tc * b.turnover_humus_fraction, tn * b.turnover_humus_fraction
            s.humus_c_kg_ha[i] += humc; s.humus_n_kg_ha[i] += humn
            s.residue_c_kg_ha[i] += tc - humc; s.residue_n_kg_ha[i] += tn - humn
            for k, v in [('decomposer_turnover_C', tc), ('decomposer_turnover_N', tn),
                         ('decomposer_to_humus_C', humc), ('decomposer_to_humus_N', humn),
                         ('decomposer_to_residue_C', tc - humc), ('decomposer_to_residue_N', tn - humn)]:
                extra[k] += v
            # Shared capacity prevents each substrate independently using the
            # same decomposer uptake capacity. Layer residue precedes mulch.
            capacity = cells.carbon_kg_ha[i] * b.uptake_per_C_day20 * modifier
            for surface in ([False, True] if i == 0 else [False]):
                sc = s.surface_residue_c_kg_ha if surface else s.residue_c_kg_ha[i]
                sn = s.surface_residue_n_kg_ha if surface else s.residue_n_kg_ha[i]
                if sc <= 0 or capacity <= 0:
                    continue
                dc = min(sc * -math.expm1(-p.residue_decay_per_day_20C * modifier), capacity)
                n_per_c = sn / sc
                if b.formulation == 'throughput_limited':
                    requirement = b.cue / b.microbial_cn - n_per_c
                    if requirement > 0:
                        dc = min(dc, s.mineral_n_kg_ha[i] / requirement)
                    assimilated = dc * b.cue
                else:
                    # Alternative hypothesis: excess substrate C is respired
                    # rather than restricting uptake under inorganic-N scarcity.
                    assimilated = min(dc * b.cue, (dc * n_per_c + s.mineral_n_kg_ha[i]) * b.microbial_cn)
                dn = dc * n_per_c; an = assimilated / b.microbial_cn
                change = dn - an
                if change < -s.mineral_n_kg_ha[i] - 1e-9:
                    raise ArithmeticError('Microbial mineral N overdraw')
                if surface:
                    s.surface_residue_c_kg_ha -= dc; s.surface_residue_n_kg_ha -= dn
                else:
                    s.residue_c_kg_ha[i] -= dc; s.residue_n_kg_ha[i] -= dn
                s.mineral_n_kg_ha[i] = max(0., s.mineral_n_kg_ha[i] + change)
                cells.carbon_kg_ha[i] += assimilated; cells.nitrogen_kg_ha[i] += an
                capacity -= dc
                flux['respired_c_kg_ha'] += dc - assimilated
                flux['mineralized_n_kg_ha'] += max(0., change)
                flux['immobilized_n_kg_ha'] += max(0., -change)
                for k, v in [('decomposer_assimilated_C', assimilated), ('decomposer_assimilated_N', an),
                             ('residue_consumed_C', dc), ('residue_consumed_N', dn),
                             ('residue_respired_C', dc - assimilated),
                             ('residue_mineralized_N', max(0., change)), ('residue_immobilized_N', max(0., -change))]:
                    extra[k] += v
            saturation = bounded((s.water_mm[i] - layer.mm(layer.field_capacity)) /
                                 layer.mm(layer.saturation - layer.field_capacity))
            loss = s.mineral_n_kg_ha[i] * -math.expm1(-p.denitrification_fraction_day * saturation * temp)
            s.mineral_n_kg_ha[i] -= loss; flux['denitrified_n_kg_ha'] += loss
        # Passive identities include new biomass explicitly and prevent an
        # invisible source from satisfying the old soil-only budget.
        for res, suffix, old_bio, new_bio, output in [
                ('carbon', 'c', sum(old.carbon_kg_ha), sum(cells.carbon_kg_ha), flux['respired_c_kg_ha']),
                ('nitrogen', 'n', sum(old.nitrogen_kg_ha), sum(cells.nitrogen_kg_ha), flux['denitrified_n_kg_ha'])]:
            change = organic(s, res) - organic(before, res)
            change += sum(getattr(s, f'humus_{suffix}_kg_ha')) - sum(getattr(before, f'humus_{suffix}_kg_ha'))
            if res == 'nitrogen':
                change += sum(s.mineral_n_kg_ha) - sum(before.mineral_n_kg_ha)
            reconcile(change + new_bio - old_bio + output, f'biological {res}')
        cells.validate(self.layers, b.microbial_cn)
        flux.update({k + '_kg_ha': v for k, v in extra.items()})
        flux.update(functional_decomposer_C_kg_ha=sum(cells.carbon_kg_ha),
                    functional_decomposer_N_kg_ha=sum(cells.nitrogen_kg_ha))
