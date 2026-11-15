"""Optional conservative organ accounting around the existing crop/soil kernel.

Organ pools partition aggregate plant dry mass/N; they are NEVER added again to
the system inventory. Allocation and remobilization are scenario assumptions,
not inferred measurements. The legacy kernel remains the default elsewhere.
"""
from copy import deepcopy
import math
from ..processes import bounded,CARBON_FRACTION
from ..state_intelligence.trajectories import TraceKernel

POOLS=('green_leaf','senesced_leaf','structural_shoot','root','grain','labile_N')


def validate_organs(plant):
    if not plant.organ_state:return
    pools=plant.organ_state['pools']
    if set(pools)!=set(POOLS):raise ValueError('Invalid organ checkpoint pools')
    for pool in pools.values():
        if any(not math.isfinite(v)or v< -1e-10 for v in pool.values()):raise ArithmeticError('Nonphysical organ pool')
    for field,total in [('dry',plant.biomass_dry_kg_ha),('N',plant.n_kg_ha)]:
        if abs(sum(p[field]for p in pools.values())-total)>1e-7:raise ArithmeticError('Organ and aggregate inventories disagree')


class BiologicalKernel(TraceKernel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.stage_snapshots={};self.growth_budgets={}

    def stage_state(self,plant,day):
        crop=self.crops[plant.planting.crop]
        if plant.organ_state:
            p=plant.organ_state['pools'];grain=p['grain']['dry'];gn=p['grain']['N'];root=p['root']['dry'];rn=p['root']['N']
            leaves=p['green_leaf']['dry']+p['senesced_leaf']['dry'];leaf_n=p['green_leaf']['N']+p['senesced_leaf']['N']
        else:
            root=plant.biomass_dry_kg_ha*crop.root_biomass_fraction
            grain=min(max(0,plant.biomass_dry_kg_ha-root-crop.seed_dry_kg_ha*(1-crop.root_biomass_fraction))*crop.harvest_index,plant.n_kg_ha/crop.grain_n_fraction)
            gn=grain*crop.grain_n_fraction;rn=(plant.n_kg_ha-gn)*root/max(1e-12,plant.biomass_dry_kg_ha-grain)
            leaves=None;leaf_n=None
        return {'date':str(day.date),'crop':crop.name,'grain':grain,'grain_N':gn,'root':root,'root_N':rn,
            'shoot_dry':plant.biomass_dry_kg_ha-grain-root,'shoot_N':plant.n_kg_ha-gn-rn,
            'shoot_C':(plant.biomass_dry_kg_ha-grain-root)*CARBON_FRACTION,'leaf_dry':leaves,'leaf_N':leaf_n,
            'state':'simulated_physiological_maturity','organ_state':deepcopy(plant.organ_state)}
    def growth_temperature_factor(self,day,crop):
        if crop.name!='corn'or not self.options.get('temperature_plateau'):return super().growth_temperature_factor(day,crop)
        # Literature-shaped APSIM maize sensitivity: FT cardinals 8/15/35/50 C.
        # Daily mean is used here; APSIM's 0.75 Tmax weighting is NOT reproduced.
        t=day.tmean_C
        return bounded((t-8)/7)if t<15 else 1. if t<=35 else bounded((50-t)/15)

    def initialize_organs(self,plant,crop):
        if plant.organ_state:return validate_organs(plant)
        if plant.growing_days:raise ValueError('Aggregate-only active crop needs an explicit organ initialization; no silent midseason reconstruction')
        leaf=(1-crop.root_biomass_fraction)*crop.vegetative_leaf_fraction
        fractions={'root':crop.root_biomass_fraction,'green_leaf':leaf,'structural_shoot':1-crop.root_biomass_fraction-leaf}
        plant.organ_state={'pools':{k:{'dry':plant.biomass_dry_kg_ha*fractions.get(k,0.),'N':plant.n_kg_ha*fractions.get(k,0.)}for k in POOLS},
            'cumulative_leaf_dry':plant.biomass_dry_kg_ha*leaf,'senesced_dry':0.,'leaf_reallocated_dry':0.,'stem_reallocated_dry':0.,
            'flowering_leaf_dry':None,'flowering_stem_dry':None,'maturity_date':None,'death_date':None,
            'dropped_leaf_dry':0.,'dropped_leaf_N':0.,'initialization':'Scenario seed partition, not measured organ biomass'}
        plant.organ_state['assimilated_dry']=0.
        validate_organs(plant)

    def canopy_lai(self,plant,crop):
        if not self.options.get('organ_model'):return super().canopy_lai(plant,crop)
        self.initialize_organs(plant,crop)
        return min(crop.max_lai,plant.organ_state['pools']['green_leaf']['dry']*crop.specific_leaf_area_m2_kg/10000)

    def _grow(self,s,plant,day,et0,flux):
        crop=self.crops[plant.planting.crop]
        if not self.options.get('organ_model'):
            before=plant.gdd;result=self._diagnostic_growth(s,plant,day,et0,flux)
            if before<crop.maturity_gdd<=plant.gdd:self.stage_snapshots[str(plant.planting.date)]=self.stage_state(plant,day)
            return result
        self.initialize_organs(plant,crop);before=plant.biomass_dry_kg_ha;n_before=plant.n_kg_ha;gdd_before=plant.gdd
        cover,depth=self._diagnostic_growth(s,plant,day,et0,flux)
        growth=plant.biomass_dry_kg_ha-before;added_n=plant.n_kg_ha-n_before
        organs=plant.organ_state;p=organs['pools'];progress=plant.gdd/crop.maturity_gdd
        organs['assimilated_dry']+=growth
        p['labile_N']['N']+=added_n
        root=growth*crop.root_biomass_fraction;above=growth-root
        leaf=above*crop.vegetative_leaf_fraction*(1-bounded((progress-.45)/.3))
        stem=above-leaf;p['root']['dry']+=root;p['green_leaf']['dry']+=leaf;p['structural_shoot']['dry']+=stem
        organs['cumulative_leaf_dry']+=leaf
        # N supplied by uptake/fixation is allocated; this cannot create new N.
        for key,target in [('green_leaf',.035),('root',.012),('structural_shoot',.01)]:
            need=max(0,p[key]['dry']*target-p[key]['N']);move=min(need,p['labile_N']['N'])
            p[key]['N']+=move;p['labile_N']['N']-=move
        if progress>=.5 and organs['flowering_leaf_dry']is None:
            organs['flowering_leaf_dry']=p['green_leaf']['dry'];organs['flowering_stem_dry']=p['structural_shoot']['dry']
        # Phenology-driven leaf senescence transfers material, unlike a LAI-only factor.
        target=organs['cumulative_leaf_dry']*.85*bounded((progress-.7)/.3)
        amount=min(p['green_leaf']['dry'],max(0,target-organs['senesced_dry']))
        if amount:
            dn=p['green_leaf']['N']*amount/max(1e-12,p['green_leaf']['dry'])
            p['green_leaf']['dry']-=amount;p['green_leaf']['N']-=dn
            p['senesced_leaf']['dry']+=amount;p['senesced_leaf']['N']+=dn*.4
            p['labile_N']['N']+=dn*.6;organs['senesced_dry']+=amount
        # Demand-based grain sink: constrained by actual C donors and available N.
        filling=bounded((progress-.45)/.45)
        above_total=sum(v['dry']for k,v in p.items()if k!='root')
        new_above=min(max(0,above_total-crop.seed_dry_kg_ha*(1-crop.root_biomass_fraction)),organs['assimilated_dry']*(1-crop.root_biomass_fraction))
        demand=self.grain_demand(plant,crop,day,flux,progress,filling,new_above)
        leaf_limit=max(0,.15*(organs['flowering_leaf_dry']or 0)-organs['leaf_reallocated_dry'])
        stem_limit=max(0,.20*(organs['flowering_stem_dry']or 0)-organs['stem_reallocated_dry'])
        donors={'structural_shoot':min(p['structural_shoot']['dry'],stem+stem_limit),'green_leaf':min(p['green_leaf']['dry'],leaf_limit)}
        floors={'green_leaf':.01,'structural_shoot':.003,'senesced_leaf':.003}
        n_supply=p['labile_N']['N']+sum(max(0,p[k]['N']-p[k]['dry']*minimum)for k,minimum in floors.items())
        grain=min(demand,sum(donors.values()),n_supply/crop.grain_n_fraction)
        remaining=grain
        for key,available in donors.items():
            move=min(remaining,available);p[key]['dry']-=move;p['grain']['dry']+=move;remaining-=move
            if key=='green_leaf':organs['leaf_reallocated_dry']+=move
            else:organs['stem_reallocated_dry']+=max(0,move-stem)
        nitrogen=grain*crop.grain_n_fraction
        for key in ['labile_N',*floors]:
            available=p[key]['N']if key=='labile_N'else max(0,p[key]['N']-p[key]['dry']*floors[key])
            move=min(nitrogen,available);p[key]['N']-=move;p['grain']['N']+=move;nitrogen-=move
        if nitrogen>1e-8:raise ArithmeticError('Grain N source accounting failed')
        if gdd_before<crop.maturity_gdd<=plant.gdd:organs['maturity_date']=str(day.date)
        # Leaf detachment is an explicit optional scenario, disabled by default.
        drop=self.options.get('maturity_leaf_drop_fraction',0.)
        if drop and gdd_before<crop.maturity_gdd<=plant.gdd:
            if not 0<=drop<=1:raise ValueError('Invalid declared leaf-drop fraction')
            dry=p['senesced_leaf']['dry']*drop;dn=p['senesced_leaf']['N']*drop
            p['senesced_leaf']['dry']-=dry;p['senesced_leaf']['N']-=dn
            plant.biomass_dry_kg_ha-=dry;plant.n_kg_ha-=dn
            s.surface_residue_c_kg_ha+=dry*CARBON_FRACTION;s.surface_residue_n_kg_ha+=dn
            organs['dropped_leaf_dry']+=dry;organs['dropped_leaf_N']+=dn
            flux['leaf_litter_C_kg_ha']=dry*CARBON_FRACTION;flux['leaf_litter_N_kg_ha']=dn
        validate_organs(plant)
        if gdd_before<crop.maturity_gdd<=plant.gdd:self.stage_snapshots[str(plant.planting.date)]=self.stage_state(plant,day)
        return cover,depth

    def grain_demand(self,plant,crop,day,flux,progress,filling,new_above):
        """Legacy demand hook; optional candidates may change sinks, never stocks."""
        return max(0,new_above*crop.harvest_index*filling-plant.organ_state['pools']['grain']['dry'])

    def _diagnostic_growth(self,s,plant,day,et0,flux):
        crop=self.crops[plant.planting.crop];progress=plant.gdd/crop.maturity_gdd
        lai=self.canopy_lai(plant,crop);thermal=self.growth_temperature_factor(day,crop)
        cover=1-math.exp(-crop.extinction_coefficient*lai)
        potential=day.solar_MJ_m2*.48*cover*crop.rue_g_dry_per_MJ_PAR*10
        result=super()._grow(s,plant,day,et0,flux)
        active=not plant.growth_ended and progress<1
        flux['radiation_potential_dry_kg_ha']=potential if active else 0.
        flux['intercepted_PAR_MJ_m2']=day.solar_MJ_m2*.48*cover if active else 0.
        flux['available_PAR_MJ_m2']=day.solar_MJ_m2*.48 if active else 0.
        flux['temperature_loss_dry_kg_ha']=potential*(1-thermal)if active else 0.
        flux['water_loss_dry_kg_ha']=potential*thermal*(1-flux['water_stress_factor'])if active else 0.
        flux['N_loss_dry_kg_ha']=max(0,potential*thermal*flux['water_stress_factor']-flux['growth_dry_kg_ha'])if active else 0.
        budget=self.growth_budgets.setdefault(str(plant.planting.date),{})
        for key in ['radiation_potential_dry_kg_ha','available_PAR_MJ_m2','intercepted_PAR_MJ_m2','temperature_loss_dry_kg_ha','water_loss_dry_kg_ha','N_loss_dry_kg_ha','growth_dry_kg_ha']:
            budget[key]=budget.get(key,0.)+flux[key]
        return result

    def harvest_reason(self,plant,day,killed):
        if self.options.get('organ_model'):
            self.initialize_organs(plant,self.crops[plant.planting.crop])
            if killed and plant.organ_state['death_date']is None:plant.organ_state['death_date']=str(day.date)
        reason=super().harvest_reason(plant,day,killed)
        return 'planned_harvest_deadline'if reason=='observed_harvest_date'and self.options.get('harvest_calendar_role')=='planned'else reason

    def _harvest(self,s,plant,day,depth,reason,flux):
        if not self.options.get('organ_model'):return super()._harvest(s,plant,day,depth,reason,flux)
        self.initialize_organs(plant,self.crops[plant.planting.crop]);validate_organs(plant);p=plant.organ_state['pools']
        terminate=plant.planting.harvest_mode=='terminate_to_residue';grain=0. if terminate else p['grain']['dry'];gn=0. if terminate else p['grain']['N']
        root=p['root']['dry'];rn=p['root']['N'];shoot=plant.biomass_dry_kg_ha-root-grain;sn=plant.n_kg_ha-rn-gn
        remove=plant.planting.aboveground_residue_removal_fraction
        s.surface_residue_c_kg_ha+=shoot*CARBON_FRACTION*(1-remove);s.surface_residue_n_kg_ha+=sn*(1-remove)
        weights=[f*l.thickness_m for f,l in zip(self.root_fractions(depth),self.layers)];total=sum(weights)
        for i,w in enumerate(weights):s.residue_c_kg_ha[i]+=root*CARBON_FRACTION*w/total;s.residue_n_kg_ha[i]+=rn*w/total
        flux['harvested_c_kg_ha']=(grain+shoot*remove)*CARBON_FRACTION;flux['harvested_n_kg_ha']=gn+sn*remove;flux['grain_dry_kg_ha']=grain
        stats=self.growth_stats.get(str(plant.planting.date),{'peak_LAI':0.,'water_sum':0.,'N_sum':0.,'days':0})
        return {'crop':plant.planting.crop,'planted':str(plant.planting.date),'harvested':str(day.date),'reason':reason,
            'growing_days':plant.growing_days,'gdd':plant.gdd,'maturity_fraction':plant.gdd/self.crops[plant.planting.crop].maturity_gdd,
            'biomass_dry_kg_ha':plant.biomass_dry_kg_ha,'grain_dry_kg_ha':grain,'grain_n_kg_ha':gn,
            'fixed_n_kg_ha':plant.fixation_n_kg_ha,'soil_uptake_n_kg_ha':plant.uptake_n_kg_ha,'transpiration_mm':plant.transpiration_mm,
            'potential_transpiration_mm':plant.potential_transpiration_mm,'residue_removal_fraction':remove,
            'root_dry':root,'root_residue_C':root*CARBON_FRACTION,'root_residue_N':rn,'plant_total_N':plant.n_kg_ha,
            'shoot_non_grain_dry':shoot,'shoot_residue_c_added_kg_ha':shoot*CARBON_FRACTION*(1-remove),'shoot_residue_n_added_kg_ha':sn*(1-remove),
            'organ_state':deepcopy(plant.organ_state),'peak_LAI':stats['peak_LAI'],
            'mean_water_stress':stats['water_sum']/max(1,stats['days']),'mean_N_growth_factor':stats['N_sum']/max(1,stats['days']),
            'yield_status':'research_scenario_not_observation'}
