"""Opt-in bunded water store and explicit mineral-N partition.

Redox is a bounded wetness-history proxy, never Eh. NH4/NO3 partition the
existing mineral-N inventory, so they are not counted again. Rate coefficients
are declared experimental assumptions. Methane, P and NH3 volatilization are
unavailable. Denitrification consumes explicit residue carbon at the idealized
5 CH2O + 4 NO3 stoichiometry (60/56 kg C per kg N).
"""
from copy import deepcopy
from dataclasses import replace
import math

from ..processes import bounded,reference_et0
from ..nutrient_recovery.material import CN,MaterialLedger


class PaddyMixin:
    def initialize_paddy(self,soil):
        config=self.options.get('paddy')
        self.paddy=None;self._pond_evaporation=0.
        if not config:return
        required=['provenance','initial_pond_mm','bund_height_mm','infiltration_limit_mm_day','percolation_limit_mm_day',
                  'initial_ammonium_fraction','fertilizer_form','nitrification_day','denitrification_day','redox_relaxation_day']
        if any(k not in config for k in required)or not config['provenance']:raise ValueError('Explicit paddy physical/N assumptions required')
        if config['fertilizer_form']!='ammonium_equivalent':raise ValueError('Only explicitly supplied ammonium-equivalent fertilizer supported; urea hydrolysis unavailable')
        if not 0<=config['initial_ammonium_fraction']<=1:raise ValueError('Invalid initial mineral-N partition')
        for k in required[1:]:
            if k=='fertilizer_form':continue
            if not math.isfinite(config[k])or config[k]<0:raise ValueError('Nonphysical paddy parameter '+k)
        for k,default in [('NH4_mobility',.1),('NO3_mobility',1.),('oxic_microsite_fraction',.05)]:
            if not math.isfinite(config.get(k,default))or not 0<=config.get(k,default)<=1:raise ValueError('Invalid paddy fraction '+k)
        if config['initial_pond_mm']>config['bund_height_mm']:raise ValueError('Initial pond exceeds declared bund')
        f=config['initial_ammonium_fraction']
        self.paddy={'pond_mm':float(config['initial_pond_mm']),'NH4':[n*f for n in soil.mineral_n_kg_ha],
            'NO3':[n*(1-f)for n in soil.mineral_n_kg_ha],'reduced_fraction':[0. for _ in self.layers],
            'origin':config['provenance']}

    def validate_paddy(self,soil):
        if self.paddy is None:
            if self.options.get('paddy'):raise ValueError('Missing declared paddy state')
            return
        p=self.paddy
        if not self.options.get('paddy'):raise ValueError('Paddy state supplied without compatible field configuration')
        if not math.isfinite(p['pond_mm'])or not 0<=p['pond_mm']<=self.options['paddy']['bund_height_mm']+1e-8:raise ValueError('Invalid pond-water checkpoint')
        for field in ['NH4','NO3','reduced_fraction']:
            if len(p[field])!=len(self.layers)or any(not math.isfinite(v)or v<0 for v in p[field]):raise ValueError('Invalid paddy layer checkpoint')
        if any(v>1 for v in p['reduced_fraction']):raise ValueError('Redox proxy exceeds physical bounds')
        if any(abs(a+b-n)>1e-7 for a,b,n in zip(p['NH4'],p['NO3'],soil.mineral_n_kg_ha)):raise ValueError('NH4/NO3 partition disagrees with mineral-N inventory')

    def _sync_mineral(self,soil):
        if self.paddy is None:return
        for i,total in enumerate(soil.mineral_n_kg_ha):
            old=self.paddy['NH4'][i]+self.paddy['NO3'][i];difference=total-old
            if difference>=0:self.paddy['NH4'][i]+=difference
            elif old>0:
                factor=max(0.,total/old)
                self.paddy['NH4'][i]*=factor;self.paddy['NO3'][i]*=factor

    def inventories(self,soil,plant):
        result=super().inventories(soil,plant)
        if getattr(self,'paddy',None)is not None:result['water']+=self.paddy['pond_mm']
        return result

    def _move_drainage(self,soil,i,amount,flux):
        if getattr(self,'paddy',None)is None:return super()._move_drainage(soil,i,amount,flux)
        self._sync_mineral(soil)
        amount=min(max(0.,amount),soil.water_mm[i])
        if amount<=0:return
        ratio=bounded(amount/max(1e-12,soil.water_mm[i]));moved={}
        for form,mobility in [('NH4',self.options['paddy'].get('NH4_mobility',.1)),('NO3',self.options['paddy'].get('NO3_mobility',1.))]:
            value=self.paddy[form][i]*mobility*ratio;self.paddy[form][i]-=value;moved[form]=value
        nitrogen=sum(moved.values());soil.water_mm[i]-=amount;soil.mineral_n_kg_ha[i]-=nitrogen
        if i+1<len(self.layers):
            soil.water_mm[i+1]+=amount;soil.mineral_n_kg_ha[i+1]+=nitrogen
            for form,n in moved.items():self.paddy[form][i+1]+=n
        else:
            flux['drainage_mm']+=amount;flux['leached_n_kg_ha']+=nitrogen
            for form,n in moved.items():flux['paddy_'+form+'_leached_N_kg_ha']=flux.get('paddy_'+form+'_leached_N_kg_ha',0.)+n

    def _hydrology(self,soil,day,irrigation,flux):
        if getattr(self,'paddy',None)is None:return super()._hydrology(soil,day,irrigation,flux)
        self._sync_mineral(soil);cfg=self.options['paddy'];s=self.paddy
        if day.tmean_C<=0:soil.snow_mm+=day.rain_mm;rain=0.
        else:rain=day.rain_mm
        melt=min(soil.snow_mm,self.processes.snow_melt_mm_per_C_day*max(0.,day.tmean_C));soil.snow_mm-=melt
        flux['snow_melt_mm']=melt;s['pond_mm']+=rain+melt+irrigation
        if str(day.date)in cfg.get('drain_dates',[]):
            amount=s['pond_mm'];s['pond_mm']=0.;flux['runoff_mm']+=amount;flux['paddy_managed_drain_mm']=amount
        deficit=sum(max(0,l.mm(l.saturation)-w)for l,w in zip(self.layers,soil.water_mm))
        infiltration=min(s['pond_mm'],cfg['infiltration_limit_mm_day'],deficit+cfg['percolation_limit_mm_day'])
        s['pond_mm']-=infiltration;soil.water_mm[0]+=infiltration;flux['paddy_infiltration_mm']=infiltration
        for i,layer in enumerate(self.layers):
            self._move_drainage(soil,i,max(0.,soil.water_mm[i]-layer.mm(layer.saturation)),flux)
            self._move_drainage(soil,i,min(cfg['percolation_limit_mm_day'],max(0.,soil.water_mm[i]-layer.mm(layer.field_capacity))),flux)
        overflow=max(0.,s['pond_mm']-cfg['bund_height_mm']);s['pond_mm']-=overflow;flux['runoff_mm']+=overflow
        flux['paddy_overflow_mm']=overflow
        for i,layer in enumerate(self.layers):
            target=bounded((soil.water_mm[i]-layer.mm(layer.field_capacity))/layer.mm(layer.saturation-layer.field_capacity))
            s['reduced_fraction'][i]+=(target-s['reduced_fraction'][i])*(-math.expm1(-cfg['redox_relaxation_day']))

    def _paddy_decomposition(self,soil,day,flux,background):
        if getattr(self,'paddy',None)is None:return background()
        original=self.processes
        self.processes=replace(original,denitrification_fraction_day=0.)
        try:background()
        finally:self.processes=original
        self._sync_mineral(soil);cfg=self.options['paddy'];s=self.paddy
        for i in range(len(self.layers)):
            reduced=s['reduced_fraction'][i];thermal=bounded(2**((soil.temperature_C[i]-20)/10),0,4)
            # Small oxic microsite fraction is a declared aerenchyma proxy.
            oxic=bounded(1-reduced+cfg.get('oxic_microsite_fraction',.05)*reduced)
            nitrified=s['NH4'][i]*(-math.expm1(-cfg['nitrification_day']*oxic*thermal))
            ledger=MaterialLedger({'NH4':CN(0,s['NH4'][i]),'NO3':CN(0,s['NO3'][i]),
                'residue':CN(soil.residue_c_kg_ha[i],soil.residue_n_kg_ha[i]),'CO2':CN(0,0),'N2_gas':CN(0,0)})
            ledger.transfer('nitrification','NH4','NO3',CN(0,nitrified),basis='Aerobic NH4 to NO3; experimental oxygen/redox proxy')
            nitrate=ledger.accounts['NO3'].nitrogen
            loss=min(nitrate*(-math.expm1(-cfg['denitrification_day']*reduced*thermal)),soil.residue_c_kg_ha[i]/(60/56))
            dc=loss*(60/56);dn=soil.residue_n_kg_ha[i]*dc/soil.residue_c_kg_ha[i]if soil.residue_c_kg_ha[i]>0 else 0.
            ledger.transfer('denitrification_N','NO3','N2_gas',CN(0,loss),basis='Actual nitrate donor to gaseous-N boundary')
            ledger.transfer('denitrification_C','residue','CO2',CN(dc,0),basis='Explicit residue electron donor, idealized 5CH2O:4NO3 stoichiometry')
            ledger.transfer('donor_organic_N','residue','NH4',CN(0,dn),basis='Associated residue N mineralized, not lost with respired C')
            for form in ['NH4','NO3']:s[form][i]=ledger.accounts[form].nitrogen
            soil.mineral_n_kg_ha[i]=s['NH4'][i]+s['NO3'][i]
            soil.residue_c_kg_ha[i]=ledger.accounts['residue'].carbon;soil.residue_n_kg_ha[i]=ledger.accounts['residue'].nitrogen
            flux['denitrified_n_kg_ha']+=loss;flux['respired_c_kg_ha']+=dc;flux['mineralized_n_kg_ha']+=dn
            flux['paddy_nitrified_N_kg_ha']=flux.get('paddy_nitrified_N_kg_ha',0.)+nitrified
            flux['paddy_denitrification_C_kg_ha']=flux.get('paddy_denitrification_C_kg_ha',0.)+dc
            self.special_transfers.extend({'date':str(day.date),'soil_layer':i,**t}for t in ledger.transactions)

    def soil_evaporation_demand(self,soil,day,et0,cover,plant,flux):
        self._pond_evaporation=0.
        if getattr(self,'paddy',None)is not None and self.paddy['pond_mm']>0 and day.tmean_C>0:
            self._pond_evaporation=min(self.paddy['pond_mm'],et0*max(0.,1-cover));return 0.
        return super().soil_evaporation_demand(soil,day,et0,cover,plant,flux)

    def after_soil_evaporation(self,flux):
        super().after_soil_evaporation(flux)
        if getattr(self,'paddy',None)is not None:
            self.paddy['pond_mm']-=self._pond_evaporation;flux['evaporation_mm']+=self._pond_evaporation
            flux['paddy_pond_evaporation_mm']=self._pond_evaporation;flux['special_stock_pond_mm']=self.paddy['pond_mm']
            for form in ['NH4','NO3']:flux['special_stock_'+form+'_N_kg_ha']=sum(self.paddy[form])
