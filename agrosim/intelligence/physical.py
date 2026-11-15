"""Conservative experimental vertical redistribution and root uptake alternatives.

This is a bounded explicit finite-volume Darcy experiment, not a validated
Richards solver. Boundary influx is uniform within a day; ET remains daily.
"""
from dataclasses import dataclass,asdict,replace
from copy import deepcopy
import math
from ..recovery.water import DiagnosticSimulator
from ..processes import bounded
from ..io import number


@dataclass(frozen=True)
class Retention:
    alpha_per_m:float
    n:float
    residual:float
    saturation:float
    ksat_mm_day:float=100.

    def __post_init__(self):
        number(self.alpha_per_m,'retention alpha',.0001,1000);number(self.n,'retention n',1.01,5)
        number(self.residual,'residual theta',0,.5);number(self.saturation,'saturation',self.residual+.01,.8)
        number(self.ksat_mm_day,'conductivity',.000001,10000)

    def head(self,theta):
        se=bounded((theta-self.residual)/(self.saturation-self.residual),1e-7,1-1e-9);m=1-1/self.n
        return -min(1e5,((se**(-1/m)-1)**(1/self.n))/self.alpha_per_m)

    def conductivity(self,theta):
        se=bounded((theta-self.residual)/(self.saturation-self.residual),1e-9,1.);m=1-1/self.n
        return self.ksat_mm_day*se**.5*(1-(1-se**(1/m))**m)**2


class PhysicalCandidate(DiagnosticSimulator):
    def __init__(self,*args,options=None,**kwargs):
        options=dict(options or {});super().__init__(*args,options=options,**kwargs)
        self.growth_multiplier=1.;self.capture_runs=True
        self.root_beta=number(options.get('root_beta_per_m',0),'root distribution beta',0,10)
        self.root_crop_scope=tuple(options.get('root_crop_scope',['corn']))
        if any(c not in ['corn','soybeans','spring_wheat']for c in self.root_crop_scope):raise ValueError('Unsupported crop-specific root scope')
        self._active_root_crop=None
        self.substeps=int(number(options.get('substeps',24),'hydrology steps',1,192))
        self.k_scale=number(options.get('conductivity_scale',1),'conductivity multiplier',.05,20)
        self.retention=[r if isinstance(r,Retention)else Retention(**r)for r in options.get('retention',[])]
        if options.get('redistribution')=='darcy'and len(self.retention)!=len(self.layers):raise ValueError('One retention curve required per layer')

    def configuration_signature(self):
        return {'class':'physical_candidate_v1','root_beta_per_m':self.root_beta,'root_crop_scope':self.root_crop_scope,'substeps':self.substeps,
                'conductivity_scale':self.k_scale,'retention':[asdict(r)for r in self.retention],
                'options':self.options}

    def clone_for_ecosystem(self):
        if self.options.get('evaporation')=='fao56_dual':raise ValueError('Stateful evaporation depletion has no ecosystem checkpoint adapter')
        clone=type(self)(self.layers,deepcopy(self.crops),self.processes,self.latitude,options=deepcopy(self.options))
        clone.capture_runs=False
        return clone

    def run(self,*args,**kwargs):
        result=super().run(*args,**kwargs)
        if not self.capture_runs:self.captured.clear()
        return result

    def _grow(self,s,plant,day,et0,flux):
        threshold=number(self.options.get('emergence_gdd',0),'emergence thermal time',0,400)
        if plant.gdd<threshold:
            c=self.crops[plant.planting.crop]
            plant.gdd+=max(0,(bounded(day.tmin_C,c.base_temp_C,c.maximum_temp_C)+bounded(day.tmax_C,c.base_temp_C,c.maximum_temp_C))/2-c.base_temp_C)
            plant.growing_days+=1;flux['lai']=0.;flux['root_depth_m']=.05
            return 0.,.05
        key=plant.planting.crop;original=self.crops[key]
        if self.growth_multiplier!=1:self.crops[key]=replace(original,rue_g_dry_per_MJ_PAR=original.rue_g_dry_per_MJ_PAR*self.growth_multiplier)
        previous=self._active_root_crop;self._active_root_crop=key
        try:return super()._grow(s,plant,day,et0,flux)
        finally:self.crops[key]=original;self._active_root_crop=previous

    def extract_root_water(self,s,available,transp,depth):
        if self.root_beta==0 or (self._active_root_crop is not None and self._active_root_crop not in self.root_crop_scope):return super().extract_root_water(s,available,transp,depth)
        remaining=transp;capacity=list(available);transfers=[0.]*len(available);top=0.;density=[]
        for layer in self.layers:
            bottom=min(top+layer.thickness_m,depth)
            density.append((math.exp(-self.root_beta*top)-math.exp(-self.root_beta*bottom))/self.root_beta/max(1e-12,bottom-top)if bottom>top else 0.)
            top+=layer.thickness_m
        # Compensate unmet demand in layers that still have physically accessible water.
        for _ in range(len(capacity)+1):
            weights=[v*r for v,r in zip(capacity,density)];total=sum(weights)
            if remaining<=1e-12 or total<=0:break
            moved=[min(cap,remaining*w/total)for cap,w in zip(capacity,weights)]
            for i,v in enumerate(moved):capacity[i]-=v;transfers[i]+=v
            remaining-=sum(moved)
        if remaining>1e-8:raise ArithmeticError('Root allocation failed to meet feasible water demand')
        for i,v in enumerate(transfers):s.water_mm[i]-=v

    def _transfer(self,s,source,destination,amount):
        amount=min(amount,max(0,s.water_mm[source]-self.layers[source].mm(self.layers[source].residual_water_content)),
                   max(0,self.layers[destination].mm(self.layers[destination].saturation)-s.water_mm[destination]))
        if amount<=0:return 0.
        nitrogen=s.mineral_n_kg_ha[source]*self.processes.mineral_n_mobility*bounded(amount/max(1e-12,s.water_mm[source]))
        s.water_mm[source]-=amount;s.water_mm[destination]+=amount
        s.mineral_n_kg_ha[source]-=nitrogen;s.mineral_n_kg_ha[destination]+=nitrogen
        return amount

    def _hydrology(self,s,day,irrigation,flux):
        if self.options.get('redistribution')!='darcy':return super()._hydrology(s,day,irrigation,flux)
        if day.tmean_C<=0:s.snow_mm+=day.rain_mm;rain=0.
        else:rain=day.rain_mm
        melt=min(s.snow_mm,self.processes.snow_melt_mm_per_C_day*max(0,day.tmean_C));s.snow_mm-=melt
        liquid=rain+melt+irrigation;infiltration=min(liquid,self.processes.infiltration_limit_mm_day)
        flux['runoff_mm']=liquid-infiltration;flux['snow_melt_mm']=melt
        flux['interlayer_down_mm']=0.;flux['interlayer_up_mm']=0.
        for step in range(self.substeps):
            s.water_mm[0]+=infiltration/self.substeps
            # Saturation overflow is an explicit gravity flux, never post-step stock clipping.
            for i,layer in enumerate(self.layers):self._move_drainage(s,i,max(0,s.water_mm[i]-layer.mm(layer.saturation)),flux)
            edges=range(len(self.layers)-1)if step%2==0 else reversed(range(len(self.layers)-1))
            for i in edges:
                a,b=self.layers[i],self.layers[i+1];ra,rb=self.retention[i:i+2];dz=(a.thickness_m+b.thickness_m)/2
                ta=s.water_mm[i]/a.mm(1);tb=s.water_mm[i+1]/b.mm(1)
                ka,kb=ra.conductivity(ta),rb.conductivity(tb);k=2*ka*kb/max(1e-20,ka+kb)*self.k_scale
                gradient=1-(rb.head(tb)-ra.head(ta))/dz;q=k*gradient/self.substeps
                if abs(q)<1e-12:continue
                source,destination=(i,i+1)if q>0 else (i+1,i)
                cap=min(abs(q),max(0,s.water_mm[source]-self.layers[source].mm(self.layers[source].residual_water_content)),
                        max(0,self.layers[destination].mm(self.layers[destination].saturation)-s.water_mm[destination]))
                # Do not cross local hydrostatic equilibrium in one explicit edge update.
                sign=1 if q>0 else -1
                def after_gradient(amount):
                    xa=ta-sign*amount/a.mm(1);xb=tb+sign*amount/b.mm(1)
                    return 1-(rb.head(xb)-ra.head(xa))/dz
                if gradient*after_gradient(cap)<0:
                    lo,hi=0.,cap
                    for _ in range(12):
                        mid=(lo+hi)/2
                        if gradient*after_gradient(mid)>0:lo=mid
                        else:hi=mid
                    cap=lo
                moved=self._transfer(s,source,destination,cap)
                flux['interlayer_down_mm'if q>0 else 'interlayer_up_mm']+=moved
            bottom=len(self.layers)-1;l=self.layers[bottom];r=self.retention[bottom]
            drainage=min(max(0,s.water_mm[bottom]-l.mm(l.residual_water_content)),
                         r.conductivity(s.water_mm[bottom]/l.mm(1))*self.k_scale/self.substeps)
            self._move_drainage(s,bottom,drainage,flux)
