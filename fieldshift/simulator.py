"""From-scratch daily soil/crop simulator with explicit water, N and C ledgers.

This is an uncalibrated research model, not an implementation of APSIM or DSSAT.
All yield outputs are simulated dry grain. No rotation outcome file is read.
"""
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from copy import deepcopy
import math


VERSION='0.1.0'
CARBON_FRACTION=.45  # kg C / kg dry biomass, illustrative constant


def bounded(x,low=0.,high=1.):return min(high,max(low,x))


def finite_nonnegative(value,name):
    if not math.isfinite(value) or value<0:raise ValueError(f'{name} must be finite and nonnegative')


@dataclass(frozen=True)
class Layer:
    thickness_m:float
    wilting_point:float
    field_capacity:float
    saturation:float
    drainage_fraction_day:float=.5
    residual_water_content:float=.04

    def validate(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):raise ValueError('Nonfinite layer parameter')
        if self.thickness_m<=0:raise ValueError('Positive layer thickness required')
        if not 0<=self.residual_water_content<self.wilting_point<self.field_capacity<self.saturation<=.8:
            raise ValueError('Require residual < wilting < field capacity < saturation')
        if not 0<=self.drainage_fraction_day<=1:raise ValueError('Drainage fraction must be 0–1')

    def mm(self,theta):return theta*self.thickness_m*1000


@dataclass(frozen=True)
class Processes:
    infiltration_limit_mm_day:float=35.
    snow_melt_mm_per_C_day:float=3.
    residue_decay_per_day_20C:float=.008
    humus_decay_per_day_20C:float=.000025
    humification_fraction:float=.25
    humus_cn:float=12.
    decomposition_q10:float=2.
    mineral_n_mobility:float=.8
    denitrification_fraction_day:float=.02
    mulch_half_cover_dry_kg_ha:float=2000.
    mulch_max_evap_reduction:float=.65
    bare_soil_evap_coefficient:float=.8
    daily_n_deposition_kg_ha:float=0.

    def validate(self):
        for k,v in asdict(self).items():finite_nonnegative(v,k)
        for k in ['humification_fraction','mineral_n_mobility','denitrification_fraction_day','mulch_max_evap_reduction']:
            if getattr(self,k)>1:raise ValueError(f'{k} must be <= 1')
        if min(self.humus_cn,self.decomposition_q10,self.mulch_half_cover_dry_kg_ha)<=0:
            raise ValueError('C:N, Q10 and mulch scale must be positive')


@dataclass(frozen=True)
class Crop:
    name:str
    base_temp_C:float
    optimum_temp_C:float
    maximum_temp_C:float
    maturity_gdd:float
    max_root_depth_m:float
    root_biomass_fraction:float
    rue_g_dry_per_MJ_PAR:float
    max_lai:float
    specific_leaf_area_m2_kg:float
    vegetative_leaf_fraction:float
    extinction_coefficient:float
    kc_mid:float
    depletion_fraction:float
    plant_n_fraction:float
    grain_n_fraction:float
    harvest_index:float
    seed_dry_kg_ha:float
    uptake_max_n_kg_ha_day:float
    kill_minimum_temp_C:float
    fixation_max_n_kg_ha_day:float=0.
    fixation_inhibition_n_kg_ha:float=50.

    def validate(self):
        if not self.name:raise ValueError('Crop name required')
        for k,v in asdict(self).items():
            if k=='name':continue
            if not math.isfinite(v):raise ValueError(f'Nonfinite {k}')
            if k not in ['base_temp_C','optimum_temp_C','maximum_temp_C','kill_minimum_temp_C'] and v<0:
                raise ValueError(f'Negative {k}')
        if not self.base_temp_C<self.optimum_temp_C<self.maximum_temp_C:raise ValueError('Invalid crop temperatures')
        for k in ['root_biomass_fraction','vegetative_leaf_fraction','depletion_fraction','plant_n_fraction','grain_n_fraction','harvest_index']:
            if not 0<getattr(self,k)<1:raise ValueError(f'{k} must be strictly between 0 and 1')
        if min(self.maturity_gdd,self.max_root_depth_m,self.max_lai,self.seed_dry_kg_ha,self.fixation_inhibition_n_kg_ha)<=0:
            raise ValueError('Positive crop growth scales required')


@dataclass(frozen=True)
class WeatherDay:
    date:date
    rain_mm:float
    tmin_C:float
    tmax_C:float
    solar_MJ_m2:float
    et0_mm:float|None=None

    @property
    def tmean_C(self):return (self.tmin_C+self.tmax_C)/2

    def validate(self):
        if not isinstance(self.date,date):raise ValueError('Weather dates must be date objects')
        for k in ['rain_mm','solar_MJ_m2']:finite_nonnegative(getattr(self,k),k)
        if not all(math.isfinite(v) for v in [self.tmin_C,self.tmax_C]) or self.tmin_C>self.tmax_C:
            raise ValueError('Invalid daily temperatures')
        if self.et0_mm is not None:finite_nonnegative(self.et0_mm,'ET0')


@dataclass(frozen=True)
class Planting:
    date:date
    crop:str
    harvest_deadline:date
    aboveground_residue_removal_fraction:float=0.


@dataclass(frozen=True)
class Management:
    date:date
    fertilizer_n_kg_ha:float=0.
    irrigation_mm:float=0.


@dataclass
class SoilState:
    water_mm:list[float]
    mineral_n_kg_ha:list[float]
    humus_c_kg_ha:list[float]
    humus_n_kg_ha:list[float]
    residue_c_kg_ha:list[float]
    residue_n_kg_ha:list[float]
    temperature_C:list[float]
    surface_residue_c_kg_ha:float=0.
    surface_residue_n_kg_ha:float=0.
    snow_mm:float=0.

    def validate(self,layers):
        n=len(layers)
        for key in ['water_mm','mineral_n_kg_ha','humus_c_kg_ha','humus_n_kg_ha','residue_c_kg_ha','residue_n_kg_ha','temperature_C']:
            a=getattr(self,key)
            if len(a)!=n:raise ValueError('State and soil layer counts differ')
            for v in a:
                if key=='temperature_C':
                    if not math.isfinite(v):raise ValueError('Nonfinite soil temperature')
                else:finite_nonnegative(v,key)
        for k in ['surface_residue_c_kg_ha','surface_residue_n_kg_ha','snow_mm']:finite_nonnegative(getattr(self,k),k)
        for w,layer in zip(self.water_mm,layers):
            if w>layer.mm(layer.saturation)+1e-8:raise ValueError('Soil water exceeds saturation')


@dataclass
class PlantState:
    planting:Planting
    biomass_dry_kg_ha:float
    n_kg_ha:float
    gdd:float=0.
    transpiration_mm:float=0.
    potential_transpiration_mm:float=0.
    fixation_n_kg_ha:float=0.
    uptake_n_kg_ha:float=0.
    growing_days:int=0


def reference_et0(day,latitude):
    """FAO-56 Hargreaves fallback, Ra in MJ/m²/day, 0.408 converts to mm."""
    if day.et0_mm is not None:return day.et0_mm
    if not math.isfinite(latitude) or not -90<=latitude<=90:raise ValueError('Invalid latitude')
    j=day.date.timetuple().tm_yday;phi=math.radians(latitude)
    dr=1+.033*math.cos(2*math.pi*j/365)
    delta=.409*math.sin(2*math.pi*j/365-1.39)
    sunset=math.acos(bounded(-math.tan(phi)*math.tan(delta),-1,1))
    ra=(24*60/math.pi)*.0820*dr*(sunset*math.sin(phi)*math.sin(delta)+math.cos(phi)*math.cos(delta)*math.sin(sunset))
    return max(0.,.0023*.408*ra*(day.tmean_C+17.8)*math.sqrt(day.tmax_C-day.tmin_C))


def default_crops():
    """Illustrative cultivar-unspecific parameters. None fitted to rotation outcomes."""
    return {
        'corn':Crop('corn',8,28,40,1500,1.2,.2,3.2,5.5,22,.40,.6,1.2,.5,.018,.014,.50,25,5,-2),
        'soybeans':Crop('soybeans',8,26,38,1250,1.0,.2,2.4,5.,28,.45,.65,1.15,.5,.035,.060,.42,70,4,-2,5.),
        'spring_wheat':Crop('spring_wheat',0,22,35,1800,1.0,.2,2.7,5.,24,.40,.6,1.15,.5,.023,.022,.45,120,4,-5)
    }


class Simulator:
    def __init__(self,layers,crops=None,processes=None,latitude=40.):
        self.layers=tuple(layers);self.crops=crops or default_crops();self.processes=processes or Processes();self.latitude=latitude
        if not self.layers:raise ValueError('At least one soil layer required')
        for x in self.layers:x.validate()
        for key,x in self.crops.items():
            x.validate()
            if key!=x.name:raise ValueError('Crop key/name mismatch')
        self.processes.validate()
        if not math.isfinite(latitude) or not -90<=latitude<=90:raise ValueError('Invalid latitude')

    def root_fractions(self,depth):
        rooted=[];top=0.
        for layer in self.layers:
            rooted.append(bounded((depth-top)/layer.thickness_m));top+=layer.thickness_m
        return rooted

    @staticmethod
    def inventories(s,plant):
        return {'water':sum(s.water_mm)+s.snow_mm,
                'nitrogen':sum(s.mineral_n_kg_ha)+sum(s.humus_n_kg_ha)+sum(s.residue_n_kg_ha)+s.surface_residue_n_kg_ha+(plant.n_kg_ha if plant else 0.),
                'carbon':sum(s.humus_c_kg_ha)+sum(s.residue_c_kg_ha)+s.surface_residue_c_kg_ha+(plant.biomass_dry_kg_ha*CARBON_FRACTION if plant else 0.)}

    def _move_drainage(self,s,i,amount,flux):
        amount=min(amount,s.water_mm[i])
        if amount<=0:return
        transported=s.mineral_n_kg_ha[i]*self.processes.mineral_n_mobility*bounded(amount/s.water_mm[i])
        s.water_mm[i]-=amount;s.mineral_n_kg_ha[i]-=transported
        if i+1<len(self.layers):
            s.water_mm[i+1]+=amount;s.mineral_n_kg_ha[i+1]+=transported
        else:
            flux['drainage_mm']+=amount;flux['leached_n_kg_ha']+=transported

    def _hydrology(self,s,day,irrigation,flux):
        if day.tmean_C<=0:s.snow_mm+=day.rain_mm;rain=0.
        else:rain=day.rain_mm
        melt=min(s.snow_mm,self.processes.snow_melt_mm_per_C_day*max(0,day.tmean_C))
        s.snow_mm-=melt;liquid=rain+melt+irrigation
        infiltration=min(liquid,self.processes.infiltration_limit_mm_day)
        flux['runoff_mm']=liquid-infiltration;flux['snow_melt_mm']=melt
        s.water_mm[0]+=infiltration
        for i,layer in enumerate(self.layers):
            self._move_drainage(s,i,max(0,s.water_mm[i]-layer.mm(layer.saturation)),flux)
            self._move_drainage(s,i,max(0,s.water_mm[i]-layer.mm(layer.field_capacity))*layer.drainage_fraction_day,flux)

    def _decomposition(self,s,day,flux):
        p=self.processes
        for i,layer in enumerate(self.layers):
            s.temperature_C[i]+=(day.tmean_C-s.temperature_C[i])/(3+4*i)
            temp=bounded(p.decomposition_q10**((s.temperature_C[i]-20)/10),0,4) if s.temperature_C[i]>0 else 0.
            wet=bounded((s.water_mm[i]-layer.mm(layer.residual_water_content))/max(1,layer.mm(layer.field_capacity-layer.residual_water_content)))
            oxygen=1-.7*bounded((s.water_mm[i]-layer.mm(layer.field_capacity))/layer.mm(layer.saturation-layer.field_capacity))
            modifier=temp*wet*oxygen
            # Humus respiration liberates its associated nitrogen.
            f=1-math.exp(-p.humus_decay_per_day_20C*modifier)
            c=s.humus_c_kg_ha[i]*f;n=s.humus_n_kg_ha[i]*f
            s.humus_c_kg_ha[i]-=c;s.humus_n_kg_ha[i]-=n;s.mineral_n_kg_ha[i]+=n
            flux['respired_c_kg_ha']+=c;flux['mineralized_n_kg_ha']+=n
            pools=[(s.residue_c_kg_ha[i],s.residue_n_kg_ha[i],False)]
            if i==0:pools.append((s.surface_residue_c_kg_ha,s.surface_residue_n_kg_ha,True))
            for pool_c,pool_n,surface in pools:
                f=1-math.exp(-p.residue_decay_per_day_20C*modifier)
                dc=pool_c*f;dn=pool_n*f;required_n=dc*p.humification_fraction/p.humus_cn
                need=max(0,required_n-dn)
                if need>s.mineral_n_kg_ha[i] and need>0:
                    scale=s.mineral_n_kg_ha[i]/need;dc*=scale;dn*=scale;required_n*=scale
                if surface:
                    s.surface_residue_c_kg_ha-=dc;s.surface_residue_n_kg_ha-=dn
                else:s.residue_c_kg_ha[i]-=dc;s.residue_n_kg_ha[i]-=dn
                net=dn-required_n;s.mineral_n_kg_ha[i]+=net
                s.humus_c_kg_ha[i]+=dc*p.humification_fraction;s.humus_n_kg_ha[i]+=required_n
                flux['respired_c_kg_ha']+=dc*(1-p.humification_fraction)
                flux['mineralized_n_kg_ha']+=max(0,net);flux['immobilized_n_kg_ha']+=max(0,-net)
            saturation=bounded((s.water_mm[i]-layer.mm(layer.field_capacity))/layer.mm(layer.saturation-layer.field_capacity))
            loss=s.mineral_n_kg_ha[i]*(1-math.exp(-p.denitrification_fraction_day*saturation*temp))
            s.mineral_n_kg_ha[i]-=loss;flux['denitrified_n_kg_ha']+=loss

    def _grow(self,s,plant,day,et0,flux):
        crop=self.crops[plant.planting.crop];p=self.processes
        progress=plant.gdd/crop.maturity_gdd
        above=plant.biomass_dry_kg_ha*(1-crop.root_biomass_fraction)
        senescence=1-bounded((progress-.7)/.3)*.85
        lai=min(crop.max_lai,above*crop.vegetative_leaf_fraction*crop.specific_leaf_area_m2_kg/10000)*senescence
        cover=1-math.exp(-crop.extinction_coefficient*lai)
        depth=min(sum(x.thickness_m for x in self.layers),max(.05,crop.max_root_depth_m*bounded(progress/.5)))
        fractions=self.root_fractions(depth)
        taw=sum(layer.mm(layer.field_capacity-layer.wilting_point)*f for layer,f in zip(self.layers,fractions))
        available=[max(0,w-layer.mm(layer.wilting_point))*f for w,layer,f in zip(s.water_mm,self.layers,fractions)]
        ks=bounded(sum(available)/max(1e-12,(1-crop.depletion_fraction)*taw))
        potential=et0*crop.kc_mid*cover
        transp=min(sum(available),potential*ks)
        if sum(available)>0:
            for i,value in enumerate(available):s.water_mm[i]-=transp*value/sum(available)
        plant.transpiration_mm+=transp;plant.potential_transpiration_mm+=potential
        flux['transpiration_mm']=transp;flux['potential_transpiration_mm']=potential
        water_factor=transp/potential if potential>1e-12 else 0.
        t=day.tmean_C
        thermal=bounded((t-crop.base_temp_C)/(crop.optimum_temp_C-crop.base_temp_C)) if t<=crop.optimum_temp_C else bounded((crop.maximum_temp_C-t)/(crop.maximum_temp_C-crop.optimum_temp_C))
        # RUE is net biomass efficiency. No second autotrophic respiration deduction.
        potential_growth=day.solar_MJ_m2*.48*cover*crop.rue_g_dry_per_MJ_PAR*10*thermal*water_factor
        demand=max(0,(plant.biomass_dry_kg_ha+potential_growth)*crop.plant_n_fraction-plant.n_kg_ha)
        accessible=[n*f for n,f in zip(s.mineral_n_kg_ha,fractions)]
        uptake=min(demand,sum(accessible),crop.uptake_max_n_kg_ha_day*water_factor*thermal)
        if sum(accessible)>0:
            for i,value in enumerate(accessible):s.mineral_n_kg_ha[i]-=uptake*value/sum(accessible)
        plant.n_kg_ha+=uptake;plant.uptake_n_kg_ha+=uptake;flux['plant_uptake_n_kg_ha']=uptake
        # Fix only unmet demand. Grain N export can exceed the retained fixed N.
        remaining=max(0,demand-uptake)
        suppression=1/(1+sum(accessible)/crop.fixation_inhibition_n_kg_ha)
        fixed=min(remaining,crop.fixation_max_n_kg_ha_day*cover*thermal*water_factor*suppression)
        plant.n_kg_ha+=fixed;plant.fixation_n_kg_ha+=fixed;flux['fixed_n_kg_ha']=fixed
        n_capacity=max(0,plant.n_kg_ha/crop.plant_n_fraction-plant.biomass_dry_kg_ha)
        growth=min(potential_growth,n_capacity)
        plant.biomass_dry_kg_ha+=growth;flux['net_assimilated_c_kg_ha']=growth*CARBON_FRACTION
        flux['growth_dry_kg_ha']=growth;flux['n_growth_factor']=growth/potential_growth if potential_growth>0 else 1.
        flux['water_stress_factor']=water_factor;flux['lai']=lai;flux['root_depth_m']=depth
        plant.gdd+=max(0,(bounded(day.tmin_C,crop.base_temp_C,crop.maximum_temp_C)+bounded(day.tmax_C,crop.base_temp_C,crop.maximum_temp_C))/2-crop.base_temp_C)
        plant.growing_days+=1
        return cover,depth

    def _harvest(self,s,plant,day,depth,reason,flux):
        crop=self.crops[plant.planting.crop]
        filling=bounded((plant.gdd/crop.maturity_gdd-.45)/.45)
        above=plant.biomass_dry_kg_ha*(1-crop.root_biomass_fraction)
        # Seed reserves are counted in plant mass but cannot become a new grain
        # harvest when no photosynthetic biomass has been produced.
        new_above=max(0,above-crop.seed_dry_kg_ha*(1-crop.root_biomass_fraction))
        grain=min(new_above*crop.harvest_index*filling,plant.n_kg_ha/crop.grain_n_fraction)
        grain_n=grain*crop.grain_n_fraction
        leftover=plant.biomass_dry_kg_ha-grain;left_n=max(0,plant.n_kg_ha-grain_n)
        roots=plant.biomass_dry_kg_ha*crop.root_biomass_fraction
        root_n=left_n*roots/leftover if leftover>0 else 0.
        shoot_residue=max(0,leftover-roots);shoot_n=left_n-root_n
        removal=plant.planting.aboveground_residue_removal_fraction
        removed_c=shoot_residue*CARBON_FRACTION*removal;removed_n=shoot_n*removal
        s.surface_residue_c_kg_ha+=shoot_residue*CARBON_FRACTION*(1-removal)
        s.surface_residue_n_kg_ha+=shoot_n*(1-removal)
        fractions=self.root_fractions(depth)
        weights=[f*l.thickness_m for f,l in zip(fractions,self.layers)];total=sum(weights)
        for i,w in enumerate(weights):
            s.residue_c_kg_ha[i]+=roots*CARBON_FRACTION*w/total;s.residue_n_kg_ha[i]+=root_n*w/total
        flux['harvested_c_kg_ha']=grain*CARBON_FRACTION+removed_c
        flux['harvested_n_kg_ha']=grain_n+removed_n
        flux['grain_dry_kg_ha']=grain
        return {'crop':crop.name,'planted':plant.planting.date.isoformat(),'harvested':day.date.isoformat(),
                'reason':reason,'growing_days':plant.growing_days,'gdd':plant.gdd,'maturity_fraction':plant.gdd/crop.maturity_gdd,
                'biomass_dry_kg_ha':plant.biomass_dry_kg_ha,'grain_dry_kg_ha':grain,'grain_n_kg_ha':grain_n,
                'fixed_n_kg_ha':plant.fixation_n_kg_ha,'soil_uptake_n_kg_ha':plant.uptake_n_kg_ha,
                'transpiration_mm':plant.transpiration_mm,'potential_transpiration_mm':plant.potential_transpiration_mm,
                'residue_removal_fraction':removal,'yield_status':'uncalibrated_simulation_not_observation'}

    def run(self,weather,initial_state,plantings=(),management=(),keep_daily=True):
        weather=list(weather);plantings=list(plantings);management=list(management)
        if not weather:raise ValueError('Weather cannot be empty')
        for i,d in enumerate(weather):
            d.validate()
            if i and d.date!=weather[i-1].date+timedelta(days=1):raise ValueError('Daily weather must be contiguous and ordered, with no missing dates')
        if len({p.date for p in plantings})!=len(plantings):raise ValueError('Multiple plantings on one date')
        scheduled=sorted(plantings,key=lambda x:x.date)
        for i,p in enumerate(scheduled):
            if p.crop not in self.crops:raise ValueError('Unsupported crop')
            if not weather[0].date<=p.date<=p.harvest_deadline<=weather[-1].date:raise ValueError('Crop dates outside weather or harvest before sowing')
            if not math.isfinite(p.aboveground_residue_removal_fraction) or not 0<=p.aboveground_residue_removal_fraction<=1:raise ValueError('Invalid residue removal fraction')
            if i and p.date<=scheduled[i-1].harvest_deadline:raise ValueError('Overlapping planting windows')
        for m in management:
            if not weather[0].date<=m.date<=weather[-1].date:raise ValueError('Management outside weather range')
            finite_nonnegative(m.fertilizer_n_kg_ha,'fertilizer');finite_nonnegative(m.irrigation_mm,'irrigation')
        s=deepcopy(initial_state);s.validate(self.layers);plant=None
        initial=self.inventories(s,plant);daily=[];harvests=[];totals={};max_error={'water':0.,'nitrogen':0.,'carbon':0.}
        by_date={p.date:p for p in scheduled};events={}
        for m in management:events.setdefault(m.date,[]).append(m)
        for day in weather:
            before=self.inventories(s,plant)
            keys=['runoff_mm','drainage_mm','leached_n_kg_ha','respired_c_kg_ha','mineralized_n_kg_ha','immobilized_n_kg_ha',
                  'denitrified_n_kg_ha','fixed_n_kg_ha','net_assimilated_c_kg_ha','harvested_c_kg_ha','harvested_n_kg_ha',
                  'seed_c_kg_ha','seed_n_kg_ha','transpiration_mm','potential_transpiration_mm','growth_dry_kg_ha','grain_dry_kg_ha',
                  'plant_uptake_n_kg_ha','lai','root_depth_m']
            f={k:0. for k in keys};f['rain_mm']=day.rain_mm;f['n_growth_factor']=1.;f['water_stress_factor']=1.
            f['fertilizer_n_kg_ha']=sum(m.fertilizer_n_kg_ha for m in events.get(day.date,[]))
            f['irrigation_mm']=sum(m.irrigation_mm for m in events.get(day.date,[]))
            f['deposited_n_kg_ha']=self.processes.daily_n_deposition_kg_ha
            s.mineral_n_kg_ha[0]+=f['fertilizer_n_kg_ha']+f['deposited_n_kg_ha']
            if day.date in by_date:
                if plant:raise ValueError('New crop overlaps an active crop')
                p=by_date[day.date];c=self.crops[p.crop]
                plant=PlantState(p,c.seed_dry_kg_ha,c.seed_dry_kg_ha*c.plant_n_fraction)
                f['seed_c_kg_ha']=plant.biomass_dry_kg_ha*CARBON_FRACTION;f['seed_n_kg_ha']=plant.n_kg_ha
            self._hydrology(s,day,f['irrigation_mm'],f);self._decomposition(s,day,f)
            et0=reference_et0(day,self.latitude);cover=0.;depth=.05;active_crop=plant.planting.crop if plant else None
            # Frost-killed crops do not photosynthesize on their killing day.
            killed=plant is not None and day.date>plant.planting.date and day.tmin_C<=self.crops[plant.planting.crop].kill_minimum_temp_C
            if plant and not killed:cover,depth=self._grow(s,plant,day,et0,f)
            elif plant:depth=min(sum(x.thickness_m for x in self.layers),max(.05,self.crops[plant.planting.crop].max_root_depth_m*bounded(plant.gdd/self.crops[plant.planting.crop].maturity_gdd/.5)))
            # One ET budget: canopy transpiration plus uncovered soil evaporation.
            mulch=1-math.exp(-(s.surface_residue_c_kg_ha/CARBON_FRACTION)/self.processes.mulch_half_cover_dry_kg_ha)
            wet=bounded((s.water_mm[0]-self.layers[0].mm(self.layers[0].residual_water_content))/max(1,self.layers[0].mm(self.layers[0].field_capacity-self.layers[0].residual_water_content)))
            evaporation_demand=et0*self.processes.bare_soil_evap_coefficient*(1-cover)*(1-self.processes.mulch_max_evap_reduction*mulch)*wet
            evaporation_demand=0. if s.snow_mm>0 or day.tmean_C<=0 else evaporation_demand
            f['evaporation_mm']=min(max(0,s.water_mm[0]-self.layers[0].mm(self.layers[0].residual_water_content)),evaporation_demand)
            s.water_mm[0]-=f['evaporation_mm'];f['et0_mm']=et0
            if plant:
                c=self.crops[plant.planting.crop]
                reason='frost' if killed else 'thermal_maturity' if plant.gdd>=c.maturity_gdd else 'deadline' if day.date==plant.planting.harvest_deadline else None
                if reason:
                    harvests.append(self._harvest(s,plant,day,depth,reason,f));plant=None
            after=self.inventories(s,plant)
            inputs={'water':f['rain_mm']+f['irrigation_mm'],
                    'nitrogen':f['fertilizer_n_kg_ha']+f['deposited_n_kg_ha']+f['seed_n_kg_ha']+f['fixed_n_kg_ha'],
                    'carbon':f['seed_c_kg_ha']+f['net_assimilated_c_kg_ha']}
            outputs={'water':f['runoff_mm']+f['drainage_mm']+f['evaporation_mm']+f['transpiration_mm'],
                     'nitrogen':f['leached_n_kg_ha']+f['denitrified_n_kg_ha']+f['harvested_n_kg_ha'],
                     'carbon':f['respired_c_kg_ha']+f['harvested_c_kg_ha']}
            for name in inputs:
                error=after[name]-before[name]-inputs[name]+outputs[name]
                max_error[name]=max(max_error[name],abs(error));f[f'{name}_balance_error']=error
                if abs(error)>1e-7:raise ArithmeticError(f'{name} balance failed on {day.date}: {error}')
                totals[name+'_inputs']=totals.get(name+'_inputs',0.)+inputs[name]
                totals[name+'_outputs']=totals.get(name+'_outputs',0.)+outputs[name]
            # Reject a physical failure; never silently clip negative state pools.
            for a in [s.water_mm,s.mineral_n_kg_ha,s.humus_c_kg_ha,s.humus_n_kg_ha,s.residue_c_kg_ha,s.residue_n_kg_ha]:
                if any(v< -1e-8 or not math.isfinite(v) for v in a):raise ArithmeticError('Negative or nonfinite pool')
            for k,v in f.items():
                if k not in ['lai','root_depth_m','n_growth_factor','water_stress_factor'] and not k.endswith('balance_error'):
                    totals[k]=totals.get(k,0.)+v
            if keep_daily:
                daily.append({'date':day.date.isoformat(),'crop':active_crop,**f,'soil_water_mm':sum(s.water_mm),
                              'snow_mm':s.snow_mm,'soil_mineral_n_kg_ha':sum(s.mineral_n_kg_ha),
                              'soil_organic_c_kg_ha':sum(s.humus_c_kg_ha),'residue_c_kg_ha':sum(s.residue_c_kg_ha)+s.surface_residue_c_kg_ha,
                              'plant_biomass_dry_kg_ha':plant.biomass_dry_kg_ha if plant else 0.,
                              **{f'water_layer_{i}_mm':v for i,v in enumerate(s.water_mm)}})
        final=self.inventories(s,plant)
        return {'version':VERSION,'status':'uncalibrated_process_simulation','observed_rotation_accuracy':None,
                'rotation_outcomes_used_for_fit':False,'start':weather[0].date.isoformat(),'end':weather[-1].date.isoformat(),
                'initial_inventory':initial,'final_inventory':final,'final_state':asdict(s),'harvests':harvests,'totals':totals,
                'max_daily_balance_error':max_error,
                'whole_run_balance_error':{k:final[k]-initial[k]-totals[k+'_inputs']+totals[k+'_outputs'] for k in initial},
                'daily':daily,'active_crop_at_end':plant.planting.crop if plant else None}
