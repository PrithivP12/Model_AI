"""Shared opt-in persistent organs, reproductive cohorts and resource fluxes.

Six original organ accounts remain the authoritative inventory. Detailed
cohorts/structural subdivisions reconcile to those accounts, never add to them.
No fixed yield or age-to-yield multiplier is used. New equations/coefficients
are experimental, not an independently validated perennial crop model.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import hashlib,math

from ..crops.kernel import CropKernel
from ..living.kernel import LivingKernel
from ..processes import Simulator,bounded
from ..biological_recovery.plant import validate_organs,POOLS
from ..nutrient_recovery.material import CN,MaterialLedger
from .paddy import PaddyMixin


class SpecialKernel(PaddyMixin,CropKernel):
    def __init__(self,*args,**kwargs):
        self.paddy=None;self._pond_evaporation=0.;self.special_transfers=[];self.physiology_trace=[]
        super().__init__(*args,**kwargs)

    def _profile(self,name):
        p=self.profiles.get(name)
        return p if p and 'physiology_parameters'in p else None

    def configuration_signature(self):
        return {**super().configuration_signature(),'special_physiology_version':'experimental_v1',
            'special_code':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in sorted(Path(__file__).parent.glob('*.py'))}}

    def clone_for_ecosystem(self):
        kwargs={'options':deepcopy(self.options),'profiles':self.profiles}
        if hasattr(self,'living_config'):kwargs.update(decomposers=self.decomposers,living_config=self.living_config,guild_state=self.guild_state)
        clone=type(self)(self.layers,deepcopy(self.crops),self.processes,self.latitude,**kwargs)
        clone.capture_runs=False;clone.paddy=deepcopy(self.paddy)
        return clone

    def run(self,*args,**kwargs):
        result=super().run(*args,**kwargs)
        for key in list(result['totals']):
            if key.startswith('special_stock_'):result['totals'].pop(key)
        return result

    def initialize_organs(self,plant,crop):
        profile=self._profile(crop.name)
        if profile is None:return super().initialize_organs(plant,crop)
        if plant.organ_state:
            validate_organs(plant)
            if 'special'not in plant.organ_state:raise ValueError('Complete special-physiology organ state required; no aggregate-only restart')
            return
        if plant.growing_days:raise ValueError('New special crop needs an explicit initial organ configuration')
        fractions={'root':.2,'structural_shoot':.3,'green_leaf':.3,'labile_N':.2}
        pools={k:{'dry':plant.biomass_dry_kg_ha*fractions.get(k,0.),'N':plant.n_kg_ha*fractions.get(k,0.)}for k in POOLS}
        plant.organ_state={'pools':pools,'special':{'age_days':0.,'season_gdd':0.,'chill_hours':0.,'forcing_gdd':0.,
            'dormant':False,'cool_days':0.,'dry_days':0.,'last_flower':None,'cohorts':[],'harvests':[],
            'next_cohort':0,'next_stem':1,'stage':'establishment','net_assimilated_dry':0.,'runner_to_crown_dry':0.,
            'last_season_shoot_dry':0.,'new_shoot_dry':0.,'malate_C':0.,'corm_dry':pools['structural_shoot']['dry']*.4,
            'corm_N':pools['structural_shoot']['N']*.4,'stems':[{'id':0,'dry':pools['structural_shoot']['dry']*.6,
            'N':pools['structural_shoot']['N']*.6,'gdd':0.,'age_days':0.,'flowered':False}],'generation':0},
            'organ_pool_semantics':{'green_leaf':'seasonal foliage','root':'persistent structural and fine roots',
                'structural_shoot':'wood/crown/palm stem OR explicit basal-stem and pseudostem subdivisions',
                'labile_N':'nonstructural carbohydrate/mobile nitrogen, including separately reconciled CAM malate',
                'grain':'actual flower/fruit cohorts or rice grain, not generic grain for fruit crops',
                'senesced_leaf':'actual crown-stolon daughter material in strawberry; otherwise unused'}}
        if profile['physiology_family']not in ['successor_herb','CAM_rosette']:
            plant.organ_state['special'].update(corm_dry=0.,corm_N=0.,stems=[])
        validate_organs(plant)

    def _transfer(self,soil,plant,source,destination,dry,n,day,basis):
        transaction=self._transact(plant,soil,source,destination,dry,n,basis)
        self.special_transfers.append({'date':str(day.date),**transaction})

    def _litter(self,soil,plant,organ,fraction,day,flux,depth):
        pool=plant.organ_state['pools'][organ];fraction=bounded(fraction);dry=pool['dry']*fraction;n=pool['N']*fraction
        if organ=='green_leaf'and n>0:
            p=self._profile(plant.planting.crop)
            resorbed=n*p['physiology_parameters']['leaf_N_resorption_fraction']
            self._transfer(soil,plant,'green_leaf','labile_N',0.,resorbed,day,'Actual leaf nitrogen resorbed into mobile reserve before litter fall')
            n-=resorbed
        if organ=='root':
            accounts={'root':CN(pool['dry']*.45,pool['N']),**{f'layer{i}':CN(c,n)for i,(c,n)in enumerate(zip(soil.residue_c_kg_ha,soil.residue_n_kg_ha))}}
            ledger=MaterialLedger(accounts);weights=[v*l.thickness_m for v,l in zip(self.root_fractions(depth),self.layers)]
            for i,weight in enumerate(weights):
                ledger.transfer(str(i),'root',f'layer{i}',CN(dry*.45*weight/sum(weights),n*weight/sum(weights)),basis='Actual fine-root material returned to rooted layer')
                soil.residue_c_kg_ha[i]=ledger.accounts[f'layer{i}'].carbon;soil.residue_n_kg_ha[i]=ledger.accounts[f'layer{i}'].nitrogen
            plant.organ_state['pools']['root']={'dry':ledger.accounts['root'].carbon/.45,'N':ledger.accounts['root'].nitrogen}
            plant.biomass_dry_kg_ha-=dry;plant.n_kg_ha-=n
            self.special_transfers.extend({'date':str(day.date),**t}for t in ledger.transactions)
        else:self._transfer(soil,plant,organ,'surface_residue',dry,n,day,'Actual senescence, abortion or pruning; retained structure is a separate donor')
        flux['special_litter_C_kg_ha']=flux.get('special_litter_C_kg_ha',0.)+dry*.45
        flux['special_litter_N_kg_ha']=flux.get('special_litter_N_kg_ha',0.)+n

    def _decomposition(self,soil,day,flux):
        return self._paddy_decomposition(soil,day,flux,lambda:super(SpecialKernel,self)._decomposition(soil,day,flux))

    def _daylength(self,day):
        phi=math.radians(self.latitude);decl=.409*math.sin(2*math.pi*day.date.timetuple().tm_yday/365-1.39)
        return 24/math.pi*math.acos(bounded(-math.tan(phi)*math.tan(decl),-1,1))

    def _season(self,soil,plant,day,p,flux):
        s=plant.organ_state['special'];a=p['physiology_parameters'];family=p['physiology_family'];crop=self.crops[plant.planting.crop]
        delta=max(0.,min(day.tmean_C,crop.maximum_temp_C)-crop.base_temp_C)
        s['age_days']+=1.;s['season_gdd']+=delta;hours=self._daylength(day)
        s['daylength_change']=hours-s.get('last_daylength_h',hours);s['last_daylength_h']=hours
        if crop.name=='strawberry':
            s['berry_chill_hours']=s.get('berry_chill_hours',0.)+sum(a['chill_min_C']<=day.tmean_C+(day.tmax_C-day.tmin_C)/2*math.sin(2*math.pi*(i+.5)/8)<=a['chill_max_C']for i in range(8))*3.
        deciduous=family=='deciduous_wood'
        enter=deciduous and hours<a['dormancy_daylength_h']and day.tmean_C<a['dormancy_temperature_C']
        if enter and not s['dormant']:
            s['dormant']=True;s['forcing_gdd']=0.;s['chill_hours']=0.
            if s['new_shoot_dry']>0:s['last_season_shoot_dry']=s['new_shoot_dry']
            s['new_shoot_dry']=0.;s['season_gdd']=0.
        if s['dormant']:
            # Bounded sinusoidal daily-min/max reconstruction, not observed hourly temperature.
            chill=sum(a['chill_min_C']<=day.tmean_C+(day.tmax_C-day.tmin_C)/2*math.sin(2*math.pi*(i+.5)/8)<=a['chill_max_C']for i in range(8))*3.
            s['chill_hours']+=chill
            if s['chill_hours']>=a['chill_hours_required']and day.tmean_C>a['dormancy_temperature_C']:
                s['forcing_gdd']+=delta
                if s['forcing_gdd']>=a['budbreak_gdd']and hours>=a['dormancy_daylength_h']:
                    s['dormant']=False;s['stage']='budbreak';s['season_gdd']=0.
        s['cool_days']=s['cool_days']+1. if day.tmean_C<a['cool_induction_max_C']else s['cool_days']
        layers=self.layers;available=sum(max(0,w-l.mm(l.wilting_point))for w,l in zip(soil.water_mm,layers))
        capacity=sum(l.mm(l.field_capacity-l.wilting_point)for l in layers)
        if available<.35*capacity:s['dry_days']+=1.
        if s['dormant']:
            self._litter(soil,plant,'green_leaf',.08,day,flux,.5)
            s['stage']='dormancy';return False,delta,hours
        return True,delta,hours

    def _flower_allowed(self,plant,day,p):
        s=plant.organ_state['special'];a=p['physiology_parameters'];ident=plant.planting.crop
        if ident=='rice':return False
        if s['age_days']<a['juvenile_days']or plant.organ_state['pools']['structural_shoot']['dry']<a['minimum_structure_dry']:return False
        if s['last_flower']and (day.date-date.fromisoformat(s['last_flower'])).days<a['flower_interval_days']:return False
        if p['physiology_family']in ['successor_herb','CAM_rosette']:
            stems=s['stems']
            if not stems:return False
            mother=stems[0]
            if mother['flowered']or mother['gdd']<a['stem_flowering_gdd']:return False
            if ident=='pineapple':
                if mother.get('age_days',0.)<a['juvenile_days']:return False
                cfg=self.options.get('crop_options',{}).get(ident,{})
                induced=str(day.date)in cfg.get('flower_induction_dates',[])or s['cool_days']>=a['cool_induction_days']
                return induced
            return True
        if ident in ['mango','orange','lemon','avocado','olive']:
            return s['cool_days']>=a['cool_induction_days']or s['dry_days']>=a['dry_induction_days']
        if ident in ['apple','grape']:return not s['dormant']and s['last_season_shoot_dry']>0 and s['season_gdd']>=a['budbreak_gdd']
        if ident=='strawberry':
            mode=self.options.get('crop_options',{}).get(ident,{}).get('flowering_type')
            if mode not in ['day_neutral','short_day']:raise ValueError('Strawberry needs explicit day_neutral or short_day flowering type')
            return 8<=day.tmean_C<=26 and(mode=='day_neutral'or self._daylength(day)<12 and s['daylength_change']<0)
        return True

    def _pollination(self,ident):
        if ident in ['banana','pineapple']:return True # Explicit seedless crop profiles, no pollinator benefit inferred.
        item=self.options.get('crop_options',{}).get(ident,{}).get('pollination')
        if not item:return False
        if not item.get('source')or item.get('status')not in ['assumed_satisfied','observed_satisfied','unavailable']:
            raise ValueError('Explicit reproductive-support provenance required')
        if ident=='date'and item.get('sex')!='female':return False
        if ident=='papaya'and item.get('sex')not in ['female','hermaphrodite']:return False
        return item['status']in ['assumed_satisfied','observed_satisfied']

    def _water_capacity(self,soil,plant,crop,p,et0,cover):
        root=plant.organ_state['pools']['root']['dry'];a=p['physiology_parameters']
        depth=min(sum(l.thickness_m for l in self.layers),max(.05,crop.max_root_depth_m*(-math.expm1(-root/a['root_half_mass_dry']))))
        roots=self.root_fractions(depth)
        water=[max(0.,w-l.mm(l.wilting_point))*f for w,l,f in zip(soil.water_mm,self.layers,roots)]
        taw=sum(l.mm(l.field_capacity-l.wilting_point)*f for l,f in zip(self.layers,roots))
        stress=bounded(sum(water)/max(1e-12,.5*taw))
        return depth,roots,water,stress

    def _take_water(self,soil,water,quantity):
        if quantity>sum(water)+1e-8:raise ArithmeticError('Root water overconsumption')
        if sum(water)>0:
            for i,v in enumerate(water):soil.water_mm[i]-=quantity*v/sum(water)

    def _resource_growth(self,soil,plant,day,et0,flux,p,weights):
        crop=self.crops[plant.planting.crop];s=plant.organ_state['special'];a=p['physiology_parameters'];pools=plant.organ_state['pools']
        lai=min(crop.max_lai,pools['green_leaf']['dry']*crop.specific_leaf_area_m2_kg/10000);cover=-math.expm1(-crop.extinction_coefficient*lai)
        depth,roots,water,stress=self._water_capacity(soil,plant,crop,p,et0,cover)
        thermal=Simulator.growth_temperature_factor(self,day,crop)
        saturation=bounded((soil.water_mm[0]-self.layers[0].mm(self.layers[0].field_capacity))/self.layers[0].mm(self.layers[0].saturation-self.layers[0].field_capacity))
        oxygen=1-saturation*(1-a['aerenchyma_fraction'])if plant.planting.crop=='rice'else 1-.8*saturation
        potential_transp=et0*crop.kc_mid*cover;transp=min(sum(water),potential_transp*stress*oxygen)
        cam=p['physiology_family']=='CAM_rosette'
        potential=day.solar_MJ_m2*.48*cover*crop.rue_g_dry_per_MJ_PAR*10*thermal
        if cam:
            settings=self.options.get('crop_options',{}).get('pineapple',{});vpd=settings.get('night_VPD_kPa')
            if vpd is None or not math.isfinite(vpd)or vpd<=0:raise ValueError('CAM requires explicitly sourced positive nighttime VPD assumption')
            if not settings.get('night_VPD_source'):raise ValueError('CAM VPD provenance required')
            seconds=(24-self._daylength(day))*3600.;night_temp=(day.tmin_C+day.tmean_C)/2
            night_thermal=bounded((night_temp-8)/12)if night_temp<=20 else bounded((40-night_temp)/20)
            conductance=a['night_conductance_mol_m2_s']*night_thermal
            demand=conductance*vpd/a['pressure_kPa']*seconds*.018*lai
            transp=min(sum(water),demand*stress*oxygen)
            gasC=conductance/1.6*a['CO2_gradient_mol_mol']*seconds*.012*10000*lai
            gasC*=transp/demand if demand>0 else 0.
            freeC=max(0.,pools['labile_N']['dry']*.45-s['malate_C'])
            cap=max(0.,pools['green_leaf']['dry']*a['malate_capacity_C_per_leaf_dry']-s['malate_C'])
            fixed=min(gasC,freeC/3.,cap/4.)
            ledger=MaterialLedger({'free_carbohydrate':CN(freeC,0),'vacuolar_malate':CN(s['malate_C'],0),'atmospheric_CO2_C':CN(fixed,0)})
            ledger.transfer('night_PEP','free_carbohydrate','vacuolar_malate',CN(3*fixed,0),basis='Three existing substrate carbons transferred to four-carbon malate')
            ledger.transfer('night_CO2','atmospheric_CO2_C','vacuolar_malate',CN(fixed,0),basis='One atmospheric carbon per four-carbon malate; boundary input counted once')
            # Three existing carbohydrate carbons + one atmospheric carbon -> four-carbon malate.
            pools['labile_N']['dry']+=fixed/.45;plant.biomass_dry_kg_ha+=fixed/.45;s['malate_C']=ledger.accounts['vacuolar_malate'].carbon
            flux['net_assimilated_c_kg_ha']+=fixed;s['net_assimilated_dry']+=fixed/.45
            flux['CAM_night_external_C_kg_ha']=fixed;flux['CAM_night_PEP_internal_C_kg_ha']=3*fixed
            released=min(s['malate_C'],potential*.45*4.)
            ledger.transfer('day_recycling','vacuolar_malate','free_carbohydrate',CN(released,0),basis='Light-limited malate turnover/reassimilation; carrier carbon recycles, no second atmospheric input')
            s['malate_C']=ledger.accounts['vacuolar_malate'].carbon;flux['CAM_day_decarboxylated_C_kg_ha']=released/4.
            self.special_transfers.extend({'date':str(day.date),**t}for t in ledger.transactions)
            # Retain the three-carbon working substrate for the next night.
            # Otherwise treating all daytime carbohydrate as structural growth
            # would extinguish CAM despite adequate light/water.
            working_C=.75*pools['green_leaf']['dry']*a['malate_capacity_C_per_leaf_dry']
            free=max(0.,pools['labile_N']['dry']-(s['malate_C']+working_C)/.45)
            potential=min(potential,free)
        else:
            potential*=transp/potential_transp if potential_transp>0 else 0.
        self._take_water(soil,water,transp);plant.transpiration_mm+=transp;plant.potential_transpiration_mm+=potential_transp
        flux['transpiration_mm']+=transp;flux['potential_transpiration_mm']+=potential_transp
        targets={'root':a['root_n_fraction'],'structural_shoot':a['structural_n_fraction'],
            'green_leaf':a['leaf_n_fraction'],'grain':a['fruit_n_fraction'],'labile_N':0.,'senesced_leaf':a['root_n_fraction']}
        n_per_dry=sum(weights[k]*targets[k]for k in weights)
        available=[n*f for n,f in zip(soil.mineral_n_kg_ha,roots)]
        uptake=min(potential*n_per_dry,sum(available),crop.uptake_max_n_kg_ha_day*thermal*stress*oxygen)
        transfers=[min(stock,uptake*v/sum(available))if sum(available)>0 else 0. for stock,v in zip(soil.mineral_n_kg_ha,available)]
        uptake=sum(transfers)
        reserveN=pools['labile_N']['N'];growth=min(potential,(uptake+reserveN)/max(n_per_dry,1e-12))
        needed=growth*n_per_dry
        if uptake>needed and uptake>0:
            transfers=[v*needed/uptake for v in transfers];uptake=sum(transfers)
        usedN=min(reserveN,max(0.,needed-uptake))
        for i,v in enumerate(transfers):soil.mineral_n_kg_ha[i]-=v
        pools['labile_N']['N']-=usedN;plant.n_kg_ha+=uptake;plant.uptake_n_kg_ha+=uptake
        flux['plant_uptake_n_kg_ha']+=uptake
        if cam:pools['labile_N']['dry']-=growth
        else:
            plant.biomass_dry_kg_ha+=growth;flux['net_assimilated_c_kg_ha']+=growth*.45;s['net_assimilated_dry']+=growth
        if self.paddy is not None:self._sync_mineral(soil)
        allocatedN=uptake+usedN
        for key,fraction in weights.items():
            pools[key]['dry']+=growth*fraction;pools[key]['N']+=allocatedN*fraction*targets[key]/max(n_per_dry,1e-12)
        flux.update(growth_dry_kg_ha=growth,n_growth_factor=growth/potential if potential>0 else 1.,
            water_stress_factor=stress,lai=lai,root_depth_m=depth)
        return cover,depth,growth,weights['grain']*growth,weights['grain']*growth*targets['grain']

    def _emit_product(self,soil,plant,day,p,cohort,flux):
        dry=cohort['dry'];n=cohort['N']
        self._transfer(soil,plant,'grain','harvest_export',dry,n,day,'Actual mature uniquely identified fruit/grain cohort export')
        flux['harvested_c_kg_ha']+=dry*.45;flux['harvested_n_kg_ha']+=n
        flux['product_dry_kg_ha']=flux.get('product_dry_kg_ha',0.)+dry
        row={'crop':plant.planting.crop,'date':str(day.date),'product_id':p['archetype_parameters']['product_id'],
            'product_organ':p['archetype_parameters']['product_organ'],'product_dry_kg_ha':dry,'product_N_kg_ha':n,
            'cohort_id':cohort['id'],'mode':'mature_special_cohort','created':cohort['created'],
            'maturity_gdd':cohort['gdd'],'age_days':cohort['age_days']}
        self.product_events.append(row);plant.organ_state['special']['harvests'].append(row)

    def _successors(self,soil,plant,day,p,growth,old_stem,delta,flux,depth):
        s=plant.organ_state['special'];a=p['physiology_parameters'];pool=plant.organ_state['pools']['structural_shoot']
        add=pool['dry']-old_stem[0];addedN=pool['N']-old_stem[1]
        stems=s['stems'];basal_fraction=.3 if stems else 1.
        s['corm_dry']+=add*basal_fraction;s['corm_N']+=addedN*basal_fraction
        share=.7/len(stems)if stems else 0.
        for stem in stems:
            stem['dry']+=add*share;stem['N']+=addedN*share;stem['gdd']+=delta;stem['age_days']=stem.get('age_days',0.)+1.
        if len(stems)==1 and stems[0]['gdd']>=a['successor_start_gdd']and s['corm_dry']>=a['successor_seed_dry']:
            reserve=plant.organ_state['pools']['labile_N'];amount=a['successor_seed_dry']
            free=max(0.,reserve['dry']-s['malate_C']/.45)
            if free>=amount:
                n=reserve['N']*amount/reserve['dry']
                self._transfer(soil,plant,'labile_N','structural_shoot',amount,n,day,'Existing reserve to replacement sucker/basal rosette')
                stems.append({'id':s['next_stem'],'dry':amount,'N':n,'gdd':0.,'age_days':0.,'flowered':False});s['next_stem']+=1
        # Only the one-fruited parent senesces; basal structures and successors persist.
        retired={h.get('owner_stem')for h in s.get('retire_stems',[])}
        s['retire_stems']=[]
        for stem in list(stems):
            if stem['id']not in retired:continue
            frac=stem['dry']/max(1e-12,sum(x['dry']for x in stems))
            self._litter(soil,plant,'green_leaf',frac,day,flux,depth)
            self._transfer(soil,plant,'structural_shoot','surface_residue',stem['dry'],stem['N'],day,'Actual harvested parent pseudostem/rosette dies; corm and successor retained')
            stems.remove(stem);s['generation']+=1
        if not stems:
            # No new stem is invented. Remaining basal C/reserves must fund one.
            reserve=plant.organ_state['pools']['labile_N'];amount=min(a['successor_seed_dry'],max(0.,reserve['dry']-s['malate_C']/.45))
            if amount>=a['successor_seed_dry']:
                n=reserve['N']*amount/reserve['dry'];self._transfer(soil,plant,'labile_N','structural_shoot',amount,n,day,'Resource-funded replacement establishment')
                stems.append({'id':s['next_stem'],'dry':amount,'N':n,'gdd':0.,'age_days':0.,'flowered':False});s['next_stem']+=1
        if abs(s['corm_dry']+sum(x['dry']for x in stems)-pool['dry'])>1e-7:
            # _transact replaces account dictionaries; compare authoritative current pool.
            pool=plant.organ_state['pools']['structural_shoot']
            if abs(s['corm_dry']+sum(x['dry']for x in stems)-pool['dry'])>1e-7:raise ArithmeticError('Basal/stem subdivisions do not reconcile')

    def _grow(self,soil,plant,day,et0,flux):
        p=self._profile(plant.planting.crop)
        if p is None:return super()._grow(soil,plant,day,et0,flux)
        crop=self.crops[plant.planting.crop];self.initialize_organs(plant,crop)
        s=plant.organ_state['special'];a=p['physiology_parameters'];pools=plant.organ_state['pools'];family=p['physiology_family']
        for event in self.options.get('crop_options',{}).get(crop.name,{}).get('pruning',[]):
            if event['date']==str(day.date):
                if not event.get('source')or family in ['successor_herb','CAM_rosette']:raise ValueError('Pruning needs provenance and compatible woody/crown structure')
                if not math.isfinite(event['fraction'])or not 0<=event['fraction']<=1:raise ValueError('Invalid pruning fraction')
                self._litter(soil,plant,'structural_shoot',event['fraction'],day,flux,.5)
                s['last_season_shoot_dry']*=1-event['fraction'];s['new_shoot_dry']*=1-event['fraction']
        active,delta,hours=self._season(soil,plant,day,p,flux)
        if not active:
            validate_organs(plant);plant.growing_days+=1
            self.physiology_trace.append({'date':str(day.date),'crop':crop.name,'stage':'dormancy','age_days':s['age_days'],
                'dormant':True,'chill_hours':s['chill_hours'],'fruit_cohorts':len(s['cohorts']),'malate_C':s['malate_C'],
                'generation':s['generation'],'root_depth_m':.5,**{k+'_dry':v['dry']for k,v in pools.items()}})
            flux.update(lai=0.,root_depth_m=.5);return 0.,.5
        reserve=pools['labile_N'];leaf_target=a['leaf_recovery_LAI']*10000/crop.specific_leaf_area_m2_kg
        free=max(0.,reserve['dry']-s['malate_C']/.45)
        if pools['green_leaf']['dry']<leaf_target and free>0:
            amount=min(leaf_target-pools['green_leaf']['dry'],free*(-math.expm1(-a['reserve_mobilization_day'])))
            n=min(reserve['N'],amount*.025)
            # No protein-free foliage from pure carbohydrate: N also constrains recovery.
            amount=min(amount,n/.025)
            if amount>0:self._transfer(soil,plant,'labile_N','green_leaf',amount,n,day,'Conserved reserve remobilization at budbreak/regrowth')
        plant.growing_days+=1;plant.gdd+=delta
        old_stem=(pools['structural_shoot']['dry'],pools['structural_shoot']['N'])
        opening=self._flower_allowed(plant,day,p)
        pending=s['cohorts'];rice=plant.planting.crop=='rice'
        filling=[c for c in pending if c['phase']=='fruit']
        product_fraction=a['fruit_fraction']if filling or rice and plant.gdd>=.5*crop.maturity_gdd else a['bud_fraction']if opening else 0.
        base={'root':a['root_fraction'],'structural_shoot':a['stem_fraction'],'labile_N':a['reserve_fraction'],
              'senesced_leaf':a['runner_fraction'],'grain':product_fraction}
        base['green_leaf']=1-sum(base.values())
        if min(base.values())<0:raise ValueError('New organ allocation exceeds available assimilation')
        cover,depth,growth,fruitdry,fruitN=self._resource_growth(soil,plant,day,et0,flux,p,base)
        s['new_shoot_dry']+=growth*base['structural_shoot']
        if rice:
            if fruitdry>0:
                if not pending:pending.append({'id':'rice-0','phase':'fruit','dry':0.,'N':0.,'created':str(day.date),'gdd':0.,'age_days':0.})
                pending[0]['dry']+=fruitdry;pending[0]['N']+=fruitN
        elif opening and fruitdry>0:
            opening_dry=min(fruitdry,growth*a['bud_fraction']);opening_N=fruitN*opening_dry/fruitdry
            if filling:
                for c in filling:c['dry']+=(fruitdry-opening_dry)/len(filling);c['N']+=(fruitN-opening_N)/len(filling)
            owner=s['stems'][0]['id']if family in ['successor_herb','CAM_rosette']and s['stems']else None
            winter_bud=crop.name=='strawberry'and self.options['crop_options'][crop.name].get('flowering_type')=='short_day'
            if winter_bud and not any(c['phase']=='winter_bud'for c in pending):s['berry_chill_hours']=0.
            pending.append({'id':crop.name+'-'+str(s['next_cohort']),'phase':'winter_bud'if winter_bud else'flower','dry':opening_dry,'N':opening_N,
                'created':str(day.date),'gdd':0.,'age_days':0.,'owner_stem':owner})
            s['next_cohort']+=1;s['last_flower']=str(day.date);s['cool_days']=0.;s['dry_days']=0.
            if owner is not None:s['stems'][0]['flowered']=True
        elif filling and fruitdry>0:
            for c in filling:c['dry']+=fruitdry/len(filling);c['N']+=fruitN/len(filling)
        elif fruitdry>0:
            # A pre-existing flower has no fruit sink yet; its allocated dry/N
            # remains actual floral tissue rather than disappearing.
            if pending:
                pending[-1]['dry']+=fruitdry;pending[-1]['N']+=fruitN
            else:raise ArithmeticError('Product allocation has no reproductive donor cohort')
        kept=[]
        for c in pending:
            if c['phase']=='winter_bud':
                settings=self.options['crop_options'][crop.name];required=settings.get('short_day_chill_hours')
                if required is None or not math.isfinite(required)or required<0:raise ValueError('Short-day strawberry needs explicit cultivar/scenario chilling hours')
                if s.get('berry_chill_hours',0.)>=required and hours>=12 and day.tmean_C>=10:
                    c['phase']='flower';c['gdd']=0.;c['age_days']=0.
                else:
                    kept.append(c);continue
            c['gdd']+=delta;c['age_days']+=1.
            if not rice and c['phase']=='flower'and c['gdd']>=a['flower_gdd']:
                if self._pollination(crop.name):c['phase']='fruit';c['gdd']=0.;c['age_days']=0.
                else:
                    self._transfer(soil,plant,'grain','surface_residue',c['dry'],c['N'],day,'Unfertilized floral tissue aborts; pollination not invented')
                    continue
            mature=not rice and c['phase']=='fruit'and c['age_days']>=a['fruit_days']and c['gdd']>=a['fruit_days']*(crop.optimum_temp_C-crop.base_temp_C)*.5
            if mature and c['dry']>0:
                self._emit_product(soil,plant,day,p,c,flux)
                if c.get('owner_stem')is not None:s.setdefault('retire_stems',[]).append(c)
                plant.gdd=0.
            else:kept.append(c)
        s['cohorts']=kept
        if family in ['successor_herb','CAM_rosette']:self._successors(soil,plant,day,p,growth,old_stem,delta,flux,depth)
        if family=='crown_stolon'and pools['senesced_leaf']['dry']>0:
            pool=pools['senesced_leaf'];dry=pool['dry']*.01;n=pool['N']*.01
            self._transfer(soil,plant,'senesced_leaf','structural_shoot',dry,n,day,'Actual stolon-derived daughter crown structure');s['runner_to_crown_dry']+=dry
        self._litter(soil,plant,'green_leaf',-math.expm1(-a['leaf_turnover_day']),day,flux,depth)
        self._litter(soil,plant,'root',-math.expm1(-a['root_turnover_day']),day,flux,depth)
        s['stage']='fruit_development'if s['cohorts']else'establishment'if s['age_days']<a['juvenile_days']else'postharvest_or_vegetative'
        # Indeterminate perennial host-stage proxy stays separate from real
        # cohort age; no mature-annual canopy shutdown is inferred.
        if not rice:plant.gdd=min(plant.gdd,crop.maturity_gdd*.95)
        self.validate_special(plant)
        row={'date':str(day.date),'crop':crop.name,'stage':s['stage'],'age_days':s['age_days'],'dormant':s['dormant'],
            'chill_hours':s['chill_hours'],'fruit_cohorts':len(s['cohorts']),'malate_C':s['malate_C'],'generation':s['generation'],
            'root_depth_m':depth,**{k+'_dry':v['dry']for k,v in plant.organ_state['pools'].items()}}
        self.physiology_trace.append(row)
        for k,v in row.items():
            if isinstance(v,(int,float)):flux['special_stock_'+k]=v
        return cover,depth

    def validate_special(self,plant):
        validate_organs(plant);s=plant.organ_state['special'];pools=plant.organ_state['pools']
        for field in ['age_days','season_gdd','chill_hours','forcing_gdd','malate_C','corm_dry','corm_N']:
            if not math.isfinite(s[field])or s[field]<-1e-10:raise ValueError('Invalid physiology checkpoint '+field)
        if len({c['id']for c in s['cohorts']})!=len(s['cohorts']):raise ValueError('Duplicate reproductive cohort identity')
        for c in s['cohorts']:
            if c['phase']not in ['flower','fruit','winter_bud']or any(not math.isfinite(c[k])or c[k]<0 for k in ['dry','N','age_days','gdd']):raise ValueError('Invalid reproductive cohort checkpoint')
        profile=self._profile(plant.planting.crop)
        if profile['physiology_family']in ['successor_herb','CAM_rosette']:
            for stem in s['stems']:
                if any(not math.isfinite(stem[k])or stem[k]<0 for k in ['dry','N','gdd','age_days']):raise ValueError('Invalid successor checkpoint')
            if len({stem['id']for stem in s['stems']})!=len(s['stems']):raise ValueError('Duplicate successor identity')
            for field,corm in [('dry','corm_dry'),('N','corm_N')]:
                if abs(s[corm]+sum(x[field]for x in s['stems'])-pools['structural_shoot'][field])>1e-7:raise ArithmeticError('Basal/successor subdivision mismatch')
        if abs(sum(c['dry']for c in s['cohorts'])-pools['grain']['dry'])>1e-7 or abs(sum(c['N']for c in s['cohorts'])-pools['grain']['N'])>1e-7:
            raise ArithmeticError('Reproductive cohort and organ inventory mismatch')
        if not -1e-10<=s['malate_C']<=pools['labile_N']['dry']*.45+1e-8:raise ArithmeticError('CAM malate exceeds actual reserve-carbon stock')
        if len({h['cohort_id']for h in s['harvests']})!=len(s['harvests']):raise ArithmeticError('Fruit cohort harvested twice')

    def harvest_reason(self,plant,day,killed):
        p=self._profile(plant.planting.crop)
        if p is None:return super().harvest_reason(plant,day,killed)
        if killed:return 'lethal_temperature_return'
        if plant.planting.crop=='rice'and (plant.gdd>=self.crops['rice'].maturity_gdd or day.date>=plant.planting.harvest_deadline):return 'rice_maturity'if plant.gdd>=self.crops['rice'].maturity_gdd else'rice_immature_termination'
        return None

    def _harvest(self,soil,plant,day,depth,reason,flux):
        p=self._profile(plant.planting.crop)
        if p is None:return super()._harvest(soil,plant,day,depth,reason,flux)
        self.initialize_organs(plant,self.crops[plant.planting.crop]);s=plant.organ_state['special']
        before=len(self.product_events)
        if reason=='rice_maturity':
            for c in s['cohorts']:self._emit_product(soil,plant,day,p,c,flux)
            s['cohorts']=[]
        for key in POOLS:self._litter(soil,plant,key,1.,day,flux,depth)
        products=self.product_events[before:];grain=sum(h['product_dry_kg_ha']for h in products);n=sum(h['product_N_kg_ha']for h in products)
        flux['grain_dry_kg_ha']=grain if plant.planting.crop=='rice'else 0.
        return {'crop':plant.planting.crop,'planted':str(plant.planting.date),'harvested':str(day.date),'reason':reason,
            'grain_dry_kg_ha':grain if plant.planting.crop=='rice'else 0.,'grain_n_kg_ha':n if plant.planting.crop=='rice'else 0.,
            'product_dry_kg_ha':grain,'gdd':plant.gdd,'yield_status':'experimental_not_field_validated'}


class IntegratedSpecialKernel(LivingKernel,SpecialKernel):
    def clone_for_ecosystem(self):return SpecialKernel.clone_for_ecosystem(self)

    def _decomposition(self,soil,day,flux):
        return self._paddy_decomposition(soil,day,flux,lambda:LivingKernel._decomposition(self,soil,day,flux))

    def _grow(self,soil,plant,day,et0,flux):
        declared=self.options.get('crop_host_assumptions',{}).get(plant.planting.crop,{})
        old={g.identity:list(g.hosts)for g in self.guilds}
        if declared and(declared.get('tier')!='D'or not declared.get('source')or set(declared.get('compatible_guilds',[]))-{g.identity for g in self.guilds}):
            raise ValueError('New guild compatibility requires explicit Tier D provenance and known guild identities')
        try:
            for g in self.guilds:
                if g.identity in declared.get('compatible_guilds',[]):g.hosts=list(g.hosts)+[plant.planting.crop]
            return LivingKernel._grow(self,soil,plant,day,et0,flux)
        finally:
            for g in self.guilds:g.hosts=old[g.identity]
