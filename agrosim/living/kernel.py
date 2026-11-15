"""Resource-coupled experimental kernel; stable V1 is never routed here.

Daily operator split: hydrology -> background/microbial cycling -> guild
mortality/predation/detritivory/weed resources -> leaf injury -> crop physiology
-> soil evaporation -> harvest. No organism-to-yield multiplier is used.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import hashlib
import math

from ..ecology_v2.soil_biology import SoilBiologyKernel, Decomposers, Parameters
from ..evaluator.audit import EvaluationKernel, reconcile
from ..biological_recovery.plant import validate_organs
from ..nutrient_recovery.material import CN, MaterialLedger
from ..processes import bounded, reference_et0
from .registry import validate_config


def thermal(t, p):
    """Bounded cardinal triangle; exploratory daily-mean response, 0 outside bounds."""
    return bounded((t-p['temp_min'])/(p['temp_opt']-p['temp_min'])) if t <= p['temp_opt'] else bounded((p['temp_max']-t)/(p['temp_max']-p['temp_opt']))


def consumption(food, consumer, max_feed, half_food, modifier, dt=1.):
    """Biomass analogue of a type-II response, capped by actual resource.

    max_feed units kg food C/kg consumer C/day; half_food kg food C/ha.
    Exponential donor cap ensures positivity for finite timesteps.
    """
    if food <= 0 or consumer <= 0 or modifier <= 0: return 0.
    rate = consumer*max_feed*modifier/(half_food+food)
    return food * -math.expm1(-rate*dt)


class LivingKernel(SoilBiologyKernel):
    def __init__(self, *args, living_config=None, guild_state=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.living_config = deepcopy(living_config)
        self.guilds = validate_config(self.living_config)
        if self.living_config['mode'] != 'experimental' or not self.options.get('organ_model'):
            raise ValueError('Living kernel requires explicit experimental mode and existing organ accounting')
        self.guild_state = deepcopy(guild_state) if guild_state is not None else {g.identity: deepcopy(g.initial) for g in self.guilds}
        self.records = []; self.day_records = []; self.response = {}
        self.weed_cover = 0.; self.weed_flux = {}; self._day = None
        self.validate_guild_state()

    def validate_guild_state(self):
        if set(self.guild_state) != {g.identity for g in self.guilds}: raise ValueError('Checkpoint guild identity mismatch')
        for g in self.guilds:
            test = deepcopy(g); test.initial = self.guild_state[g.identity]; test.validate()

    def configuration_signature(self):
        return {**super().configuration_signature(), 'living_config': self.living_config,
                'split': 'daily-resource-conservative-v1', 'implementation': 'agrosim.living',
                'coupling_code': {str(p.relative_to(Path(__file__).parents[1])):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [Path(__file__),Path(__file__).with_name('registry.py'),
                              Path(__file__).parents[1]/'ecology_v2/soil_biology.py',
                              Path(__file__).parents[1]/'biological_recovery/plant.py']}}

    def clone_for_ecosystem(self):
        clone = type(self)(self.layers, deepcopy(self.crops), self.processes, self.latitude,
            options=deepcopy(self.options), decomposers=self.decomposers,
            living_config=self.living_config, guild_state=self.guild_state)
        clone.capture_runs = False
        return clone

    def inventories(self, soil, plant):
        value = super().inventories(soil, plant)
        for s in self.guild_state.values():
            value['carbon'] += s.get('C', 0.); value['nitrogen'] += s.get('N', 0.)
        return value

    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        for k in list(result['totals']):
            if k.startswith(('guild_stock_', 'pathogen_pressure_', 'response_')) or k == 'weed_canopy_cover': result['totals'].pop(k)
        return result

    def moisture(self, soil, i=0):
        layer = self.layers[i]; w = soil.water_mm[i]
        dry = bounded((w-layer.mm(layer.residual_water_content))/max(1e-12, layer.mm(layer.field_capacity-layer.residual_water_content)))
        # For aerobic soil guilds, no benefit above FC: shutdown at saturation.
        oxygen = 1-bounded((w-layer.mm(layer.field_capacity))/max(1e-12, layer.mm(layer.saturation-layer.field_capacity)))
        return dry*oxygen

    def decomposition_environment(self, soil, i, day):
        # New mode only: bounded Q10 with high-heat inhibition, normalized at20C.
        t = soil.temperature_C[i]
        base = bounded(self.processes.decomposition_q10**((t-20)/10), 0, 4) if 0 < t < 50 else 0.
        heat = 1. if t <= 35 else bounded((50-t)/15)
        return base*heat*self.moisture(soil, i), base*heat

    def _book(self, accounts, movements, process):
        ledger = MaterialLedger(accounts)
        before = ledger.total()
        for source, dest, material in movements:
            ledger.transfer(str(len(ledger.transactions)), source, dest, material,
                            basis=f'{process}; declared experimental equations, not measured effect')
        after = ledger.total()
        reconcile(after.carbon-before.carbon, 'guild transaction C')
        reconcile(after.nitrogen-before.nitrogen, 'guild transaction N')
        for t in ledger.transactions:
            row = {**t, 'date': str(self._day.date), 'process': process, 'step_days': 1.}
            self.records.append(row); self.day_records.append(row)
        return ledger.accounts

    def _surface(self, soil):
        return CN(soil.surface_residue_c_kg_ha, soil.surface_residue_n_kg_ha)

    def _set_surface(self, soil, material):
        soil.surface_residue_c_kg_ha = material.carbon; soil.surface_residue_n_kg_ha = material.nitrogen

    def _biomass(self, identity):
        s = self.guild_state[identity]; return CN(s['C'], s['N'])

    def _set_biomass(self, identity, material):
        self.guild_state[identity] = {'C': material.carbon, 'N': material.nitrogen}

    def _mortality(self, g, soil, fraction, process):
        source = self._biomass(g.identity)
        accounts={g.identity:source,'surface_residue':self._surface(soil)}
        root_fraction=g.parameters['root_fraction'] if g.role=='weed' else 0.
        movements=[(g.identity,'surface_residue',source.scale(fraction*(1-root_fraction)))]
        if root_fraction:
            weights=[f*l.thickness_m for f,l in zip(self.root_fractions(g.parameters['root_depth']),self.layers)]
            for i,weight in enumerate(weights):
                key=f'soil_residue_layer_{i}'
                accounts[key]=CN(soil.residue_c_kg_ha[i],soil.residue_n_kg_ha[i])
                movements.append((g.identity,key,source.scale(fraction*root_fraction*weight/sum(weights))))
        a = self._book(accounts,movements,process)
        self._set_biomass(g.identity, a[g.identity]); self._set_surface(soil, a['surface_residue'])
        if root_fraction:
            for i in range(len(self.layers)):
                soil.residue_c_kg_ha[i]=a[f'soil_residue_layer_{i}'].carbon
                soil.residue_n_kg_ha[i]=a[f'soil_residue_layer_{i}'].nitrogen

    def _eat(self, g, source_name, source, soil, flux, modifier):
        p = g.parameters; consumer = self._biomass(g.identity)
        dc = consumption(source.carbon, consumer.carbon, p['max_feed'], p['half_food'], modifier)
        if dc <= 0: return source
        dn = source.nitrogen*dc/source.carbon
        growth_c = min(dc*p['assimilation'], dn*p['cn']); growth_n = growth_c/p['cn']
        resp = (dc-growth_c)*p['respiration']
        waste = CN(dc-growth_c-resp, max(0., dn-growth_n))
        a = self._book({source_name: source, g.identity: consumer, 'surface_waste': self._surface(soil), 'atmosphere_CO2': CN(0,0)},
            [(source_name, g.identity, CN(growth_c,growth_n)), (source_name, 'atmosphere_CO2', CN(resp,0)),
             (source_name, 'surface_waste', waste)], g.role+'_feeding')
        self._set_biomass(g.identity, a[g.identity]); self._set_surface(soil, a['surface_waste'])
        flux['respired_c_kg_ha'] += resp
        flux[g.role+'_consumed_C_kg_ha'] = flux.get(g.role+'_consumed_C_kg_ha',0.) + dc
        flux[g.role+'_growth_C_kg_ha'] = flux.get(g.role+'_growth_C_kg_ha',0.) + growth_c
        flux[g.role+'_respired_C_kg_ha'] = flux.get(g.role+'_respired_C_kg_ha',0.) + resp
        return a[source_name]

    def _detritivore(self, g, soil, day, flux):
        # Treat surface material as a donor distinct from the waste destination.
        source = self._surface(soil); self._set_surface(soil, CN(0,0))
        left = self._eat(g, 'coarse_surface_donor', source, soil, flux, self.response[g.identity]['modifier'])
        waste = self._surface(soil)
        # Actual waste is fragmented into layer0; unconsumed coarse matter stays surface.
        a = self._book({'fragmented_waste': waste, 'soil_residue_layer_0': CN(soil.residue_c_kg_ha[0],soil.residue_n_kg_ha[0])},
                      [('fragmented_waste','soil_residue_layer_0',waste)], 'detritivore_fragmentation')
        soil.residue_c_kg_ha[0] = a['soil_residue_layer_0'].carbon; soil.residue_n_kg_ha[0] = a['soil_residue_layer_0'].nitrogen
        self._set_surface(soil,left)

    def _predator(self, g, soil, day, flux):
        # Shared total daily capacity across prey; resource-order explicit.
        capacity = self._biomass(g.identity).carbon*g.parameters['max_feed']*self.response[g.identity]['modifier']
        for prey in g.resources:
            before = self._biomass(prey)
            if capacity <= 0 or before.carbon <= 0: continue
            # Bound each prey response and shared capacity; no alternative prey invented.
            potential = consumption(before.carbon,self._biomass(g.identity).carbon,g.parameters['max_feed'],g.parameters['half_food'],self.response[g.identity]['modifier'])
            if potential > capacity:
                # Equivalent response modifier that consumes exactly capacity.
                modifier = -math.log1p(-capacity/before.carbon)*(g.parameters['half_food']+before.carbon)/(self._biomass(g.identity).carbon*g.parameters['max_feed'])
            else: modifier = self.response[g.identity]['modifier']
            left = self._eat(g,prey,before,soil,flux,modifier)
            capacity -= before.carbon-left.carbon; self._set_biomass(prey,left)

    def _weed(self, g, soil, day, flux):
        p = g.parameters; c = self._biomass(g.identity)
        if day.date.timetuple().tm_yday < p['emergence_doy']: return
        lai = c.carbon/.45*(1-p['root_fraction'])*p['sla']/10000
        # Sequential Beer-Lambert partition; never allocate incoming light twice.
        own_cover = (1-self.weed_cover)*(1-math.exp(-p['extinction']*lai))
        self.weed_cover += own_cover
        modifier = thermal(day.tmean_C,p)
        potential_c = day.solar_MJ_m2*.48*own_cover*p['rue']*10*.45*modifier
        water_demand = min(reference_et0(day,self.latitude)*p['kc']*own_cover, potential_c*p['water_per_C'])
        roots = self.root_fractions(p['root_depth'])
        water = [max(0,w-l.mm(l.wilting_point))*f for w,l,f in zip(soil.water_mm,self.layers,roots)]
        available_n = [n*f for n,f in zip(soil.mineral_n_kg_ha,roots)]
        growth_c = min(potential_c, sum(available_n)*p['cn'], potential_c*bounded(sum(water)/max(1e-12,water_demand)))
        dn = growth_c/p['cn']
        transfers = [min(stock,dn*accessible/sum(available_n)) if sum(available_n)>0 else 0.
                     for stock,accessible in zip(soil.mineral_n_kg_ha,available_n)]
        # Use actual capped layer transfers, including floating-point endpoint
        # rounding, for growth and the material ledger. Never leave negative N.
        dn = sum(transfers); growth_c = min(growth_c,dn*p['cn'])
        transp = water_demand*growth_c/potential_c if potential_c else 0.
        for i in range(len(roots)):
            if sum(water)>0: soil.water_mm[i] -= transp*water[i]/sum(water)
        # Ledger makes photosynthetic C input and soil N transfer explicit.
        nstock = sum(soil.mineral_n_kg_ha)
        a = self._book({'atmospheric_C_input':CN(growth_c,0), 'soil_mineral_N':CN(0,nstock), g.identity:c},
            [('atmospheric_C_input',g.identity,CN(growth_c,0)),('soil_mineral_N',g.identity,CN(0,dn))], 'weed_growth')
        for i,value in enumerate(transfers): soil.mineral_n_kg_ha[i] -= value
        self._set_biomass(g.identity,a[g.identity])
        for k,v in [('transpiration_mm',transp),('net_assimilated_c_kg_ha',growth_c),('plant_uptake_n_kg_ha',dn)]:
            self.weed_flux[k] = self.weed_flux.get(k,0.)+v
        flux['weed_water_consumed_mm'] = flux.get('weed_water_consumed_mm',0.)+transp
        flux['weed_uptake_N_kg_ha'] = flux.get('weed_uptake_N_kg_ha',0.)+dn
        flux['weed_assimilated_C_kg_ha'] = flux.get('weed_assimilated_C_kg_ha',0.)+growth_c

    def _decomposition(self, soil, day, flux):
        self._day = day; self.day_records = []; self.response = {}; self.weed_cover=0.; self.weed_flux={}
        if self.living_config['enabled']['microbes']:
            super()._decomposition(soil,day,flux)
        else:
            EvaluationKernel._decomposition(self,soil,day,flux)
        for g in self.guilds:
            soil_guild = g.role == 'detritivore'
            t = soil.temperature_C[0] if soil_guild else day.tmean_C
            wet = self.moisture(soil) if soil_guild else 1.
            self.response[g.identity] = {'temperature_C':t,'thermal':thermal(t,g.parameters),'moisture':wet,
                                         'modifier':thermal(t,g.parameters)*wet}
            if g.role != 'pathogen':
                # Mortality continues for process-off controls; initial material is never deleted.
                f = -math.expm1(-g.parameters['mortality'])
                self._mortality(g,soil,f,g.role+'_mortality')
                if g.role == 'weed':
                    for event in self.living_config.get('weed_removal',[]):
                        if event['date'] == str(day.date): self._mortality(g,soil,event['fraction'],'weed_management_termination')
            else:
                self.guild_state[g.identity]['pressure'] *= math.exp(-g.parameters['mortality'])
        # Ordered roles, guild identities/compatible resources are data-driven.
        for role, handler in [('detritivore',self._detritivore),('predator',self._predator),('weed',self._weed)]:
            if self.living_config['enabled'][role]:
                for g in self.guilds:
                    if g.role == role: handler(g,soil,day,flux)

    def _leaf(self, plant):
        p = plant.organ_state['pools']['green_leaf']; return CN(p['dry']*.45,p['N'])

    def _set_leaf(self, plant, value):
        old = self._leaf(plant); p = plant.organ_state['pools']['green_leaf']
        p['dry'] = value.carbon/.45; p['N'] = value.nitrogen
        plant.biomass_dry_kg_ha += (value.carbon-old.carbon)/.45; plant.n_kg_ha += value.nitrogen-old.nitrogen
        validate_organs(plant)

    def _grow(self, soil, plant, day, et0, flux):
        crop=self.crops[plant.planting.crop]; self.initialize_organs(plant,crop)
        progress=plant.gdd/crop.maturity_gdd
        for g in self.guilds:
            if g.role not in ['herbivore','pathogen'] or not self.living_config['enabled'][g.role]: continue
            host = crop.name in g.hosts and not plant.growth_ended and progress < 1
            p = g.parameters
            stage = math.exp(-.5*((progress-p['stage_opt'])/p['stage_width'])**2) if host else 0.
            if g.role == 'herbivore':
                leaf=self._leaf(plant)
                left=self._eat(g,'crop_green_leaf',leaf,soil,flux,self.response[g.identity]['modifier']*stage)
                self._set_leaf(plant,left)
            else:
                s = self.guild_state[g.identity]; lai = self.canopy_lai(plant,crop)
                # Moisture proxy is explicitly soil wetness, NOT observed leaf wetness.
                wet = bounded(soil.water_mm[0]/self.layers[0].mm(self.layers[0].field_capacity))
                r=p['infection']*self.response[g.identity]['thermal']*wet*stage*lai/(p['half_lai']+lai)
                z=s['pressure']
                if 0 < z < 1: s['pressure']=z/(z+(1-z)*math.exp(-r))
                leaf=self._leaf(plant); fraction=-math.expm1(-p['necrosis']*s['pressure']*self.response[g.identity]['thermal']*stage)
                a=self._book({'crop_green_leaf':leaf,'surface_residue':self._surface(soil)},
                    [('crop_green_leaf','surface_residue',leaf.scale(fraction))],'pathogen_necrosis')
                self._set_leaf(plant,a['crop_green_leaf']); self._set_surface(soil,a['surface_residue'])
                flux['pathogen_necrotic_C_kg_ha']=flux.get('pathogen_necrotic_C_kg_ha',0.)+leaf.carbon*fraction
        # Sequential canopy model: weeds intercept first; crop receives remainder.
        # Existing organ physiology propagates actual green-leaf loss to LAI/growth.
        shaded=replace(day,solar_MJ_m2=day.solar_MJ_m2*(1-self.weed_cover))
        result = super()._grow(soil,plant,shaded,et0*(1-self.weed_cover),flux)
        # The existing organ solver permits roundoff-sized negative endpoints.
        # Strict CN transaction accounts need canonical nonnegative donors.
        # Cancel only sub-tolerance negatives against a positive organ of the
        # same element; aggregate plant C/N stays unchanged. This is numerical
        # reconciliation, never a biological source or an accuracy correction.
        pools = plant.organ_state['pools']
        for field in ['dry','N']:
            for pool in pools.values():
                if pool[field] < 0:
                    if pool[field] < -1e-10: raise ArithmeticError('Negative organ material exceeds roundoff tolerance')
                    deficit = -pool[field]
                    donor = max(pools.values(),key=lambda x:x[field])
                    if donor[field] < deficit: raise ArithmeticError('No material to reconcile organ roundoff')
                    donor[field] -= deficit; pool[field] = 0.
                    key='organ_'+field+'_roundoff_reconciled_kg_ha'
                    flux[key] = flux.get(key,0.)+deficit
        validate_organs(plant)
        return result

    def soil_evaporation_demand(self, soil, day, et0, cover, plant, flux):
        return super().soil_evaporation_demand(soil,day,et0*(1-self.weed_cover),cover,plant,flux)

    def after_soil_evaporation(self, flux):
        super().after_soil_evaporation(flux)
        for k,v in self.weed_flux.items(): flux[k] = flux.get(k,0.)+v
        flux['weed_canopy_cover'] = self.weed_cover
        self.validate_guild_state()
        flux['functional_decomposer_C_kg_ha'] = sum(self.decomposers.carbon_kg_ha)
        flux['functional_decomposer_N_kg_ha'] = sum(self.decomposers.nitrogen_kg_ha)
        for g in self.guilds:
            state=self.guild_state[g.identity]
            for key,value in state.items():
                prefix='pathogen_pressure' if key=='pressure' else 'guild_stock'
                flux[f'{prefix}_{g.identity}_{key}']=value
            if g.role=='weed':
                for key in ['C','N']:
                    flux[f'guild_stock_{g.identity}_root_{key}']=state[key]*g.parameters['root_fraction']
                    flux[f'guild_stock_{g.identity}_shoot_{key}']=state[key]*(1-g.parameters['root_fraction'])
            for name,value in self.response[g.identity].items(): flux[f'response_{g.identity}_{name}'] = value
