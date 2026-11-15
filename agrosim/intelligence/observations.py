"""Existing LIRF pressure plates and training-only measured crop traits."""
from pathlib import Path
from datetime import datetime,date
import numpy as np
import openpyxl
from scipy.optimize import least_squares
from ..io import sha256
from .physical import Retention


def fit_retention(root,prior,saturation=.55):
    source=Path(root)/'crop-response-lirf/raw/soils.xlsx';w=openpyxl.load_workbook(source,read_only=True,data_only=True)
    rows=list(w['Moisture Release'].iter_rows(min_row=5,values_only=True));w.close()
    pressures=np.array([3,10,33,50,100,1500],float)*1000/9806.65;curves=[];audits=[]
    for h in prior:
        points=[]
        for row in rows:
            if not all(isinstance(row[k],(int,float))for k in [2,3]):continue
            overlap=max(0,min(h['bottom_cm'],row[3])-max(h['top_cm'],row[2]))
            if overlap<=0:continue
            for head,value in zip(pressures,row[27:33]):
                if isinstance(value,(int,float))and 0<value<saturation:points.append((head,float(value),overlap))
        if not points:raise ValueError('No existing pressure-plate support for layer')
        heads,theta,weights=np.array(points).T;weights=weights/weights.sum();residual=.025
        def predicted(x):
            alpha,n=np.exp(x[0]),x[1];return residual+(saturation-residual)/(1+(alpha*heads)**n)**(1-1/n)
        fit=least_squares(lambda x:(predicted(x)-theta)*np.sqrt(weights),[np.log(4),1.4],
                          bounds=([np.log(.01),1.05],[np.log(100),4]))
        curve=Retention(float(np.exp(fit.x[0])),float(fit.x[1]),residual,saturation,100.)
        curves.append(curve)
        audits.append({'top_cm':h['top_cm'],'bottom_cm':h['bottom_cm'],'pressure_theta_pairs':len(points),
            'weighted_retention_rmse':float(np.sqrt(np.average((predicted(fit.x)-theta)**2,weights=weights))),
            'alpha_per_m':curve.alpha_per_m,'n':curve.n,'theta_residual':residual,'theta_saturation':saturation,
            'ksat_mm_day':100.,'conductivity_status':'assumed anchor; conductivity itself was not measured by this table',
            'scope':'2007 laboratory retention samples; saturation and residual fixed, not measured fitted endpoints'})
    return curves,{'source':str(source),'sha256':sha256(source),'pressure_unit':'kPa, converted to metres water head by 1000/9806.65','layers':audits}


def crop_observations(root,years):
    result=[];stages=[]
    for year in years:
        path=Path(root)/'crop-response-lirf/raw'/f'maize{year}.xlsx';w=openpyxl.load_workbook(path,read_only=True,data_only=True)
        concentrations={str(int(row[0])):row for row in w['C&N'].iter_rows(min_row=7,max_row=12,values_only=True)}if 'C&N'in w else {}
        for row in w['Seasonal'].iter_rows(min_row=7,max_row=12,values_only=True):
            if not isinstance(row[0],(int,float)):continue
            key=str(int(row[0]));cn=concentrations.get(key)
            result.append({'case_id':f'LIRF:{year}:{key}','year':year,'treatment':key,
                'aboveground_biomass_dry_kg_ha':row[5]if isinstance(row[5],(int,float))else None,
                'biomass_sample_harvest_index':row[6]if isinstance(row[6],(int,float))else None,
                'maximum_lai':row[4]if isinstance(row[4],(int,float))else None,
                'grain_n_fraction':float(cn[7])/100 if cn else None,
                'stover_cn_ratio':float(cn[2])/float(cn[1])if cn else None,
                'source_sha256':sha256(path),'observation_support':'Biomass/HI measured on sampled plants; grain plot yield is separately sampled. Roots excluded. No N stock inferred from concentration alone.'})
        for row in w['CropLog'].iter_rows(values_only=True):
            if isinstance(row[0],datetime)and str(row[1]).lower()=='growth stage'and str(row[2]).lower()in ['emergence','vt']:
                stages.append({'year':year,'date':row[0].date().isoformat(),'stage':str(row[2]).lower(),'notes':str(row[3]),'source_sha256':sha256(path)})
        w.close()
    return result,stages


def emergence_requirement(cases,stages,base=8,maximum=40):
    values=[]
    for record in stages:
        if record['stage']!='emergence':continue
        case=next(c for c in cases if c['year']==record['year'])
        gdd=sum(max(0,(min(max(d['tmin_C'],base),maximum)+min(max(d['tmax_C'],base),maximum))/2-base)
            for d in case['weather']if d['date']<record['date'])
        values.append({'year':record['year'],'observed_emergence':record['date'],'sowing_to_emergence_gdd':gdd,'estimated_in_log':'estimated'in record['notes'].lower()})
    return float(np.median([r['sowing_to_emergence_gdd']for r in values])),values
