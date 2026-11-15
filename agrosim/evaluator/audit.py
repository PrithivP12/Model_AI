"""Passive instrumentation: no crop, hydrology or decomposition equations change."""
from copy import deepcopy

from ..biological_recovery.plant import BiologicalKernel
from ..nutrient_recovery.material import CN, MaterialLedger
from ..processes import CARBON_FRACTION

TOLERANCE = 1e-6


def reconcile(error, label):
    if abs(error) > TOLERANCE:
        raise ArithmeticError(f'{label} does not reconcile: {error}')
    return error


def organic(soil, resource):
    suffix = 'c' if resource == 'carbon' else 'n'
    return sum(getattr(soil, f'residue_{suffix}_kg_ha')) + getattr(soil, f'surface_residue_{suffix}_kg_ha')


class EvaluationKernel(BiologicalKernel):
    """Record actual material movements made by the unchanged legacy equations."""

    def _decomposition(self, soil, day, flux):
        before = deepcopy(soil)
        super()._decomposition(soil, day, flux)
        dc = organic(before, 'carbon') - organic(soil, 'carbon')
        dn = organic(before, 'nitrogen') - organic(soil, 'nitrogen')
        hc = sum(soil.humus_c_kg_ha) - sum(before.humus_c_kg_ha)
        hn = sum(soil.humus_n_kg_ha) - sum(before.humus_n_kg_ha)
        mn = sum(soil.mineral_n_kg_ha) - sum(before.mineral_n_kg_ha)
        # These are internal transfers, never additional boundary inputs/outputs.
        flux.update(residue_C_decomposed_kg_ha=dc, residue_N_decomposed_kg_ha=dn,
                    humus_C_net_change_kg_ha=hc, humus_N_net_change_kg_ha=hn)
        reconcile(-dc + hc + flux['respired_c_kg_ha'], 'decomposition carbon')
        reconcile(-dn + hn + mn + flux['denitrified_n_kg_ha'], 'decomposition nitrogen')

    def _harvest(self, soil, plant, day, depth, reason, flux):
        before = deepcopy(soil)
        plant_c = plant.biomass_dry_kg_ha * CARBON_FRACTION
        plant_n = plant.n_kg_ha
        h = super()._harvest(soil, plant, day, depth, reason, flux)
        # Scheduled dates are supplied scenario operations, not measured dates.
        if h['reason'] == 'observed_harvest_date':
            h['reason'] = 'planned_scenario_harvest_date'
        ledger = MaterialLedger({'plant': CN(plant_c, plant_n), 'grain_export': CN(0, 0),
                                 'shoot_export': CN(0, 0), 'surface_residue': CN(0, 0),
                                 **{f'soil_residue_layer_{i}': CN(0, 0) for i in range(len(soil.water_mm))}})
        ledger.transfer('grain-export', 'plant', 'grain_export',
                        CN(h['grain_dry_kg_ha'] * CARBON_FRACTION, h['grain_n_kg_ha']),
                        basis='Computed grain at supplied scenario harvest date')
        for i, (c, n) in enumerate(zip(soil.residue_c_kg_ha, soil.residue_n_kg_ha)):
            ledger.transfer(f'root-return-{i}', 'plant', f'soil_residue_layer_{i}',
                            CN(max(0., c - before.residue_c_kg_ha[i]), max(0., n - before.residue_n_kg_ha[i])),
                            basis='Actual increase in layer residue during harvest')
        ledger.transfer('shoot-return', 'plant', 'surface_residue',
                        CN(max(0., soil.surface_residue_c_kg_ha - before.surface_residue_c_kg_ha),
                           max(0., soil.surface_residue_n_kg_ha - before.surface_residue_n_kg_ha)),
                        basis='Actual increase in surface residue during harvest')
        ledger.transfer('shoot-export', 'plant', 'shoot_export',
                        CN(max(0., flux['harvested_c_kg_ha'] - h['grain_dry_kg_ha'] * CARBON_FRACTION),
                           max(0., flux['harvested_n_kg_ha'] - h['grain_n_kg_ha'])),
                        basis='Supplied aboveground residue removal fraction')
        residual = ledger.accounts['plant']
        reconcile(residual.carbon, 'harvest carbon routing')
        reconcile(residual.nitrogen, 'harvest nitrogen routing')
        h['material_transfers'] = ledger.transactions
        h['material_routing_error'] = {'carbon': residual.carbon, 'nitrogen': residual.nitrogen}
        h['evidence_class'] = 'computed_physical_stock_and_flux_not_observation'
        return h
