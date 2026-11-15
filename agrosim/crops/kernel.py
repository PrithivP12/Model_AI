"""Additive organ/lifecycle extension of the shared solver, not85 simulators.

The exact original3-crop path is untouched. New profiles use the existing
Simulator._grow resource/assimilation fluxes with explicit organ allocation,
resource-limited reserve remobilization, crop maturity and product-cohort removal.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import hashlib,json,math
from pathlib import Path

from ..processes import Simulator,bounded,CARBON_FRACTION
from ..evaluator.audit import EvaluationKernel
from ..biological_recovery.plant import validate_organs
from ..nutrient_recovery.material import CN,MaterialLedger
from ..living.kernel import LivingKernel
from .profiles import CropRegistry88


class CropKernel(EvaluationKernel):
    def __init__(self,*args,profiles=None,**kwargs):
        self.profiles=deepcopy(profiles or {});self.product_events=[];self.growth_days=[];self._oxygen_factor=1.
        super().__init__(*args,**kwargs)
        for key,p in self.profiles.items():
            if p['crop_parameters']['name']!=key or key not in self.crops:raise ValueError('Exact executable crop/profile mapping required')
    def clone_for_ecosystem(self):
        clone=type(self)(self.layers,deepcopy(self.crops),self.processes,self.latitude,options=deepcopy(self.options),profiles=self.profiles)
        clone.capture_runs=False;return clone
    def run(self,*args,**kwargs):
        result=super().run(*args,**kwargs)
        for name in ['development_fraction','product_pool_dry_kg_ha','product_ready_dry_kg_ha','root_biomass_dry_kg_ha',
                     'canopy_leaf_dry_kg_ha','chill_days','day_length_hours','waterlogging_growth_factor']:
            result['totals'].pop(name,None)
        for name in list(result['totals']):
            if name.startswith('crop_organ_stock_'):result['totals'].pop(name)
        return result
    def configuration_signature(self):
        signature=super().configuration_signature()
        return {**signature,'experimental_crop_profiles':self.profiles,
                'crop_extension_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in Path(__file__).parent.glob('*.py')}}
    def _extension(self,crop):
        profile=self.profiles.get(crop)
        return profile if profile and profile['implementation_status']=='EXECUTABLE_EXPERIMENTAL'else None
    def growth_temperature_factor(self,day,crop):
        return super().growth_temperature_factor(day,crop)*self._oxygen_factor
    def _seed(self,plant,crop):
        self.initialize_organs(plant,crop)
        o=plant.organ_state
        if 'extension'not in o:
            if plant.growing_days:raise ValueError('New crop needs explicit complete organ checkpoint, not midseason initialization')
            o['extension']={'chill_days':0.,'ready_dry':0.,'ready_N':0.,'product_cohorts':[],
                            'last_partial_harvest':None,'cycle_gdd':0.,'stage':'establishment',
                            'harvests':[],'phenological_maturity_date':None,'assimilated_dry':0.,'pegging_progress_gdd':0.}
            o['organ_pool_semantics']={'grain':self.profiles[crop.name]['archetype_parameters']['product_organ'],
                'labile_N':'nonstructural reserve dry matter and unallocated N; legacy compartment name retained for validated ledger compatibility'}
        return o['extension']
    def _transact(self,plant,soil,source,destination,dry,n,basis,flux=None):
        pools=plant.organ_state['pools'];material=CN(dry*.45,n)
        accounts={source:CN(pools[source]['dry']*.45,pools[source]['N']),destination:CN(0,0)}
        if destination in pools:accounts[destination]=CN(pools[destination]['dry']*.45,pools[destination]['N'])
        elif destination=='surface_residue':accounts[destination]=CN(soil.surface_residue_c_kg_ha,soil.surface_residue_n_kg_ha)
        ledger=MaterialLedger(accounts);ledger.transfer('transfer',source,destination,material,basis=basis)
        pools[source]={'dry':ledger.accounts[source].carbon/.45,'N':ledger.accounts[source].nitrogen}
        if destination in pools:pools[destination]={'dry':ledger.accounts[destination].carbon/.45,'N':ledger.accounts[destination].nitrogen}
        else:
            plant.biomass_dry_kg_ha-=dry;plant.n_kg_ha-=n
            if destination=='surface_residue':soil.surface_residue_c_kg_ha=ledger.accounts[destination].carbon;soil.surface_residue_n_kg_ha=ledger.accounts[destination].nitrogen
        if flux is not None:
            flux.setdefault('extension_transferred_C_kg_ha',0.);flux['extension_transferred_C_kg_ha']+=material.carbon
        return ledger.transactions[0]
    def _partial_product(self,soil,plant,day,flux,profile):
        e=plant.organ_state['extension'];pools=plant.organ_state['pools'];a=profile['archetype_parameters'];arch=profile['archetype']
        if arch in ['forage','sugar_stem']:
            organ='structural_shoot'if arch=='sugar_stem'else'green_leaf'
            pools_to_cut=[organ]if arch=='sugar_stem'else['green_leaf','structural_shoot']
            if e['cycle_gdd']<self.crops[plant.planting.crop].maturity_gdd:return
        else:
            pools_to_cut=['grain']
            if e['ready_dry']<=1e-12:return
        if e['last_partial_harvest']and (day.date-date.fromisoformat(e['last_partial_harvest'])).days<a['harvest_interval_days']:return
        total_dry=total_n=0.;transfers=[]
        harvest_capacity=max(0.,e['assimilated_dry']-sum(h['product_dry_kg_ha']for h in e['harvests']))
        for organ in pools_to_cut:
            if organ=='grain':
                dry=min(pools[organ]['dry'],e['ready_dry'])*a['harvest_fraction'];n=min(pools[organ]['N'],e['ready_N'])*a['harvest_fraction']
            else:dry=pools[organ]['dry']*a['harvest_fraction'];n=pools[organ]['N']*a['harvest_fraction']
            factor=min(1.,harvest_capacity/dry)if dry>0 else 0.;dry*=factor;n*=factor;harvest_capacity-=dry
            if organ=='grain':
                e['ready_dry']-=dry;e['ready_N']-=n
            transfers.append(self._transact(plant,soil,organ,'harvest_export',dry,n,'Executed partial product/cutting removal; retained tissue and reserves persist'))
            total_dry+=dry;total_n+=n
        if total_dry<=0:return
        flux['harvested_c_kg_ha']+=total_dry*.45;flux['harvested_n_kg_ha']+=total_n
        flux['product_dry_kg_ha']=flux.get('product_dry_kg_ha',0.)+total_dry
        e['last_partial_harvest']=str(day.date);e['cycle_gdd']=0. if arch in ['forage','sugar_stem']else e['cycle_gdd']
        record={'crop':plant.planting.crop,'date':str(day.date),'product_id':a['product_id'],'product_organ':a['product_organ'],
                'product_dry_kg_ha':total_dry,'product_N_kg_ha':total_n,'mode':'partial_retained_living_tissue','material_transfers':transfers}
        e['harvests'].append(record);self.product_events.append(record)
    def _grow(self,soil,plant,day,et0,flux):
        profile=self._extension(plant.planting.crop)
        if profile is None:return super()._grow(soil,plant,day,et0,flux)
        crop=self.crops[plant.planting.crop];a=profile['archetype_parameters'];e=self._seed(plant,crop);pools=plant.organ_state['pools']
        delta=max(0.,(bounded(day.tmin_C,crop.base_temp_C,crop.maximum_temp_C)+bounded(day.tmax_C,crop.base_temp_C,crop.maximum_temp_C))/2-crop.base_temp_C)
        e['chill_days']+=1. if 0<=day.tmean_C<=10 else 0.
        progress=plant.gdd/crop.maturity_gdd
        dormant=a['perennial']and day.tmean_C<=a['dormancy_temp_C']
        if plant.gdd<a['establishment_gdd']or dormant:
            plant.gdd+=delta;plant.growing_days+=1;e['stage']='dormant'if dormant else'establishment'
            flux.update(root_depth_m=.05,lai=0.,development_fraction=plant.gdd/crop.maturity_gdd)
            return 0.,.05
        # Optional daylength/chill gate acts on storage allocation, not a yield bonus.
        doy=day.date.timetuple().tm_yday;phi=math.radians(self.latitude);decl=.409*math.sin(2*math.pi*doy/365-1.39)
        day_hours=24/math.pi*math.acos(bounded(-math.tan(phi)*math.tan(decl),-1,1))
        reproductive=progress>=a['product_start_fraction']and day_hours>=a['photoperiod_min_hours']and e['chill_days']>=a['chilling_required_days']
        if a['requires_pegging']:
            moist=bounded((soil.water_mm[0]-self.layers[0].mm(self.layers[0].wilting_point))/self.layers[0].mm(self.layers[0].field_capacity-self.layers[0].wilting_point))
            if reproductive:e['pegging_progress_gdd']+=delta*moist*getattr(self.layers[0],'root_penetrability',1.)
            reproductive=reproductive and e['pegging_progress_gdd']>=a['pegging_gdd']
        # Mobilization only redistributes existing reserves; it is not a C input.
        reserve=pools['labile_N'];leaf_target=.2*10000/crop.specific_leaf_area_m2_kg
        if a['perennial']and pools['green_leaf']['dry']<leaf_target and reserve['dry']>0:
            dry=min(leaf_target-pools['green_leaf']['dry'],reserve['dry']*(-math.expm1(-a['reserve_mobilization_day'])))
            n=reserve['N']*dry/reserve['dry'];self._transact(plant,soil,'labile_N','green_leaf',dry,n,'Conserved nonstructural reserve to regrowing leaf')
        before=plant.biomass_dry_kg_ha;oldn=plant.n_kg_ha
        saturation=bounded((soil.water_mm[0]-self.layers[0].mm(self.layers[0].field_capacity))/self.layers[0].mm(self.layers[0].saturation-self.layers[0].field_capacity))
        self._oxygen_factor=1-a['waterlogging_suppression_at_saturation']*saturation
        # Original water/N/net-RUE solver, existing root penetrability; no new
        # hydrology, invented nutrient supply, field yield or fixed yield multiplier.
        previous=self._active_root_crop;self._active_root_crop=crop.name
        try:cover,depth=Simulator._grow(self,soil,plant,day,et0,flux)
        finally:self._active_root_crop=previous;self._oxygen_factor=1.
        growth=plant.biomass_dry_kg_ha-before;added_n=plant.n_kg_ha-oldn
        e['assimilated_dry']+=growth
        root=crop.root_biomass_fraction;reserve_frac=a['reserve_fraction'];product_fraction=a['product_allocation_fraction']if reproductive else 0.
        if profile['archetype']in ['leaf','forage','cover','sugar_stem']:product_fraction=0.
        if crop.name=='hemp':product_fraction=0. # raw bast-fiber-bearing stem, not reproductive seed
        if profile['archetype']=='sweet_grain'and progress>a['harvest_ready_fraction']:product_fraction=0.
        available=1-root-reserve_frac;prod=available*product_fraction
        leaf=(available-prod)*crop.vegetative_leaf_fraction;stem=available-prod-leaf
        weights={'root':root,'labile_N':reserve_frac,'grain':prod,'green_leaf':leaf,'structural_shoot':stem}
        for organ,fraction in weights.items():pools[organ]['dry']+=growth*fraction;pools[organ]['N']+=added_n*fraction
        # Seed N/unused uptake remains accounted even when no new dry matter grows.
        if prod and growth>0:
            e['product_cohorts'].append({'dry':growth*prod,'N':added_n*prod,'ready_gdd':plant.gdd+a['product_maturation_gdd']})
        pending=[]
        for cohort in e['product_cohorts']:
            if plant.gdd>=cohort['ready_gdd']:e['ready_dry']+=cohort['dry'];e['ready_N']+=cohort['N']
            else:pending.append(cohort)
        e['product_cohorts']=pending;e['cycle_gdd']+=delta
        # Real senescence/root turnover returns donor C/N; no imposed residue C:N.
        senescence=a['senescence_day']*(1+2*bounded((progress-.7)/.3))if not a['perennial']else a['senescence_day']
        for organ,rate in [('green_leaf',senescence),('root',a['root_turnover_day'])]:
            pool=pools[organ];fraction=-math.expm1(-rate);dry=pool['dry']*fraction;n=pool['N']*fraction
            if organ=='root':
                ledger=MaterialLedger({'root':CN(pool['dry']*.45,pool['N']),**{f'layer{i}':CN(c,n)for i,(c,n)in enumerate(zip(soil.residue_c_kg_ha,soil.residue_n_kg_ha))}})
                fractions=self.root_fractions(depth);weights_layer=[f*l.thickness_m for f,l in zip(fractions,self.layers)];total=sum(weights_layer)
                for i,w in enumerate(weights_layer):
                    ledger.transfer('root-'+str(i),'root',f'layer{i}',CN(dry*.45*w/total,n*w/total),basis='Actual fine-root turnover to rooted soil layers')
                    soil.residue_c_kg_ha[i]=ledger.accounts[f'layer{i}'].carbon;soil.residue_n_kg_ha[i]=ledger.accounts[f'layer{i}'].nitrogen
                pools['root']={'dry':ledger.accounts['root'].carbon/.45,'N':ledger.accounts['root'].nitrogen}
                plant.biomass_dry_kg_ha-=dry;plant.n_kg_ha-=n
            else:self._transact(plant,soil,organ,'surface_residue',dry,n,'Actual leaf senescence return',flux)
            flux['crop_litter_C_kg_ha']=flux.get('crop_litter_C_kg_ha',0.)+dry*.45
            flux['crop_litter_N_kg_ha']=flux.get('crop_litter_N_kg_ha',0.)+n
        e['stage']='reproductive_or_storage'if reproductive else'vegetative'
        if plant.gdd>=crop.maturity_gdd:
            e['stage']='harvest_stage'
            if e['phenological_maturity_date']is None:e['phenological_maturity_date']=str(day.date)
        if a['repeated_harvest']:self._partial_product(soil,plant,day,flux,profile)
        flux.update(development_fraction=plant.gdd/crop.maturity_gdd,product_pool_dry_kg_ha=pools['grain']['dry'],
                    product_ready_dry_kg_ha=e['ready_dry'],root_biomass_dry_kg_ha=pools['root']['dry'],
                    canopy_leaf_dry_kg_ha=pools['green_leaf']['dry'],chill_days=e['chill_days'],day_length_hours=day_hours,
                    waterlogging_growth_factor=1-a['waterlogging_suppression_at_saturation']*saturation)
        validate_organs(plant)
        for name,pool in pools.items():
            flux['crop_organ_stock_'+name+'_dry_kg_ha']=pool['dry']
            flux['crop_organ_stock_'+name+'_N_kg_ha']=pool['N']
        self.growth_days.append({'date':str(day.date),'crop':crop.name,'stage':e['stage'],'gdd':plant.gdd})
        return cover,depth
    def harvest_reason(self,plant,day,killed):
        profile=self._extension(plant.planting.crop)
        if profile is None:return super().harvest_reason(plant,day,killed)
        a=profile['archetype_parameters']
        if a['perennial']:return None
        if killed:return 'crop_killed_return_remaining_material'
        if day.date>=plant.planting.harvest_deadline:return 'explicit_termination_deadline'
        if not a['repeated_harvest']and plant.gdd>=self.crops[plant.planting.crop].maturity_gdd:return 'simulated_thermal_harvest_stage'
        return None
    def _harvest(self,soil,plant,day,depth,reason,flux):
        profile=self._extension(plant.planting.crop)
        if profile is None:return super()._harvest(soil,plant,day,depth,reason,flux)
        crop=self.crops[plant.planting.crop];e=self._seed(plant,crop);a=profile['archetype_parameters'];pools=plant.organ_state['pools']
        eligible=plant.gdd>=crop.maturity_gdd*a['harvest_ready_fraction']and reason!='crop_killed_return_remaining_material'
        if profile['archetype']in ['leaf','cover']or crop.name=='hemp':
            product_organs=['structural_shoot']if crop.name=='hemp'else['green_leaf','structural_shoot']if crop.name=='leek'or profile['archetype']=='cover'else['green_leaf']
            dry=sum(pools[k]['dry']for k in product_organs)if eligible else 0.
            pn=sum(pools[k]['N']for k in product_organs)if eligible else 0.
        else:product_organs=['grain'];dry=min(e['ready_dry'],pools['grain']['dry'])if eligible else 0.;pn=min(e['ready_N'],pools['grain']['N'])if eligible else 0.
        # Bound exported production by actual cumulative net assimilation, so
        # seed/propagule reserves cannot become a fictitious no-growth harvest.
        capacity=max(0.,e['assimilated_dry']-sum(h['product_dry_kg_ha']for h in e['harvests']))
        product_scale=min(1.,capacity/dry)if dry>0 else 0.;dry*=product_scale;pn*=product_scale
        accounts={k:CN(v['dry']*.45,v['N'])for k,v in pools.items()}
        accounts.update(product_export=CN(0,0),shoot_export=CN(0,0),surface_residue=CN(soil.surface_residue_c_kg_ha,soil.surface_residue_n_kg_ha))
        accounts.update({f'layer{i}':CN(c,n)for i,(c,n)in enumerate(zip(soil.residue_c_kg_ha,soil.residue_n_kg_ha))})
        ledger=MaterialLedger(accounts)
        for organ in product_organs:
            amount=CN(pools[organ]['dry']*.45*product_scale,pools[organ]['N']*product_scale)if eligible and organ!='grain'else CN(dry*.45,pn)if organ=='grain'else CN(0,0)
            ledger.transfer('product-'+organ,organ,'product_export',amount,basis='Actual stage-eligible modeled product organ, dry matter basis')
        fractions=self.root_fractions(depth);weights=[f*l.thickness_m for f,l in zip(fractions,self.layers)];total=sum(weights);roots=ledger.accounts['root']
        for i,w in enumerate(weights):
            amount=roots.scale(w/total)if i<len(weights)-1 else ledger.accounts['root']
            ledger.transfer('root-'+str(i),'root',f'layer{i}',amount,basis='Actual terminal root C/N return to rooted layers')
        removal=plant.planting.aboveground_residue_removal_fraction
        for organ in [k for k in pools if k!='root']:
            remaining=ledger.accounts[organ]
            ledger.transfer('removed-'+organ,organ,'shoot_export',remaining.scale(removal),basis='Declared shoot residue removal')
            ledger.transfer('returned-'+organ,organ,'surface_residue',ledger.accounts[organ],basis='Unharvested tissue/immature product returns, never phantom yield')
        for i in range(len(self.layers)):
            soil.residue_c_kg_ha[i]=ledger.accounts[f'layer{i}'].carbon;soil.residue_n_kg_ha[i]=ledger.accounts[f'layer{i}'].nitrogen
        soil.surface_residue_c_kg_ha=ledger.accounts['surface_residue'].carbon;soil.surface_residue_n_kg_ha=ledger.accounts['surface_residue'].nitrogen
        export=ledger.accounts['product_export'];removed=ledger.accounts['shoot_export']
        flux['harvested_c_kg_ha']+=export.carbon+removed.carbon;flux['harvested_n_kg_ha']+=export.nitrogen+removed.nitrogen
        flux['product_dry_kg_ha']=flux.get('product_dry_kg_ha',0.)+dry
        # Legacy engine requires grain key for history. It is explicitly ZERO for
        # new non-grain products; finance uses product_dry_kg_ha and product_id.
        grain=dry if profile['archetype']in ['c3_grain','c4_grain','pseudo_grain','legume_grain','oilseed']else 0.
        flux['grain_dry_kg_ha']=grain
        event={'crop':crop.name,'date':str(day.date),'product_id':a['product_id'],'product_organ':a['product_organ'],
               'product_dry_kg_ha':dry,'product_N_kg_ha':pn,'mode':'terminal','material_transfers':ledger.transactions}
        self.product_events.append(event)
        return {**event,'planted':str(plant.planting.date),'harvested':str(day.date),'reason':reason,
                'biomass_dry_kg_ha':plant.biomass_dry_kg_ha,'grain_dry_kg_ha':grain,'grain_n_kg_ha':pn if grain else 0.,
                'gdd':plant.gdd,'maturity_fraction':plant.gdd/crop.maturity_gdd,'growing_days':plant.growing_days,
                'fixed_n_kg_ha':plant.fixation_n_kg_ha,'soil_uptake_n_kg_ha':plant.uptake_n_kg_ha,
                'transpiration_mm':plant.transpiration_mm,'potential_transpiration_mm':plant.potential_transpiration_mm,
                'yield_status':'experimental_transferred_parameters_not_validated','organ_state':deepcopy(plant.organ_state)}


class IntegratedCropKernel(LivingKernel,CropKernel):
    """Reuse the completed guild solver via MRO; old living implementation unchanged."""
    def clone_for_ecosystem(self):
        clone=type(self)(self.layers,deepcopy(self.crops),self.processes,self.latitude,options=deepcopy(self.options),
              profiles=self.profiles,decomposers=self.decomposers,living_config=self.living_config,guild_state=self.guild_state)
        clone.capture_runs=False;return clone
    def _grow(self,soil,plant,day,et0,flux):
        # New host connections require explicit experimental scenario declarations;
        # absence means no feeding/infection compatibility, never universal pests.
        declarations=self.options.get('crop_host_assumptions',{})
        hosts=declarations.get(plant.planting.crop,{});old={g.identity:list(g.hosts)for g in self.guilds}
        if hosts and hosts.get('tier')!='D':raise ValueError('New guild-host assumptions require explicit TierD scenario evidence')
        if hosts and(not hosts.get('source')or set(hosts.get('compatible_guilds',[]))-{g.identity for g in self.guilds}):
            raise ValueError('Explicit host-assumption provenance and declared guild identities required')
        try:
            for g in self.guilds:
                if g.identity in hosts.get('compatible_guilds',[]):g.hosts=list(g.hosts)+[plant.planting.crop]
            return LivingKernel._grow(self,soil,plant,day,et0,flux)
        finally:
            for g in self.guilds:g.hosts=old[g.identity]
