"""Read measured water/management only. Reserved yield labels have a separate reader."""
from pathlib import Path
from datetime import datetime,date,timedelta
from collections import defaultdict
import json,math
import numpy as np
import openpyxl
from ..io import sha256,write_json

DEPTHS=[(0,15),(15,45),(45,75),(75,105)]


def trim_contiguous(case):
    days=sorted(case['weather'],key=lambda r:r['date']);kept=[]
    for row in days:
        if kept and (date.fromisoformat(row['date'])-date.fromisoformat(kept[-1]['date'])).days!=1:break
        kept.append(row)
    if not kept or kept[0]['date']!=case['planting_date']:raise ValueError('Daily input coverage does not begin on planting date')
    case['late_discontinuous_days_excluded']=len(days)-len(kept)
    case['weather']=kept
    return case


def valid(v):return isinstance(v,(float,int))and not isinstance(v,bool)and math.isfinite(v)and v not in [-999,-9999]


def valid_temperature(value):return valid(value)and -100<=value<=70


def measured(cell):
    if cell.fill is None:return False
    color=cell.fill.fgColor
    return valid(cell.value)and color.type=='theme'and color.theme==9


def soil_prior(project,include_deep=False):
    w=openpyxl.load_workbook(Path(project)/'crop-response-lirf/raw/soils.xlsx',read_only=True,data_only=True)
    rows=list(w['Moisture Release'].iter_rows(min_row=5,values_only=True));profile=[]
    for lo,hi in (DEPTHS+[(105,135),(135,175),(175,225)]if include_deep else DEPTHS):
        values=[]
        for r in rows:
            if all(valid(r[k])for k in [2,3,28,32]):
                overlap=max(0,min(hi,r[3])-max(lo,r[2]))
                if overlap:values.append((r[28],r[32],overlap))
        if not values:raise ValueError('No prior pressure-plate overlap for soil layer')
        weights=np.array([r[2]for r in values]);fc=float(np.average([r[0]for r in values],weights=weights));wp=float(np.average([r[1]for r in values],weights=weights))
        if not .02<wp<fc<.5:raise ValueError('Invalid measured retention prior')
        profile.append({'top_cm':lo,'bottom_cm':hi,'theta_fc':fc,'theta_wp':wp,
            'source':'2007 laboratory measured pressure-plate mean theta10 and theta1500; field-wide depth-overlap average',
            'field_capacity_from_future_years_used':False})
    w.close();return profile


def old_cases(project,year):
    root=Path(project)/'crop-response-lirf';w=openpyxl.load_workbook(root/'raw'/f'maize{year}.xlsx',read_only=True,data_only=True)
    plantings=[]
    for r in w['CropLog'].iter_rows(values_only=True):
        if isinstance(r[0],datetime)and str(r[1]).lower()=='planting':plantings.append(r[0].date())
    if not plantings:raise ValueError('No actual planting date')
    planting=min(plantings)
    weatherbook=openpyxl.load_workbook(root/'raw'/f'weather{year}.xlsx',read_only=True,data_only=True)
    station={r[0].date():r for r in weatherbook['Daily'].iter_rows(min_row=7,values_only=True)if isinstance(r[0],datetime)};weatherbook.close()
    solar=json.loads((root/'nasa_solar_fallback.json').read_text())
    subset=list((Path(project)/'reports/agrosim/hybrid/raw').glob('*.json'))+list((Path(project)/'research/completion-pass/temperature_fallback').glob('*.json'))
    temperatures={}
    for path in subset:
        source=json.loads(path.read_text())['data']
        for variable in ['T2M_MIN','T2M_MAX']:
            if source['parameters'][variable]['units']!='C':raise ValueError('Temperature fallback unit mismatch')
            temperatures.setdefault(variable,{}).update(source['properties']['parameter'][variable])
    result=[]
    for treatment in range(1,7):
        rows={int(r[0].value):r for r in w[f'Tmnt{treatment}'].iter_rows(min_row=5)if valid(r[0].value)and 1<=r[0].value<=366}
        observations={};events={};weather=[];fallback=[];canopy={};full={}
        preplant=[d for d,r in rows.items()if date(year,1,1)+timedelta(days=d-1)<planting and valid(r[17].value)]
        if len(preplant)!=1:raise ValueError('Missing or ambiguous preplant soil mineral N')
        first_doy=preplant[0];initial_n=rows[first_doy][17].value
        for doy,r in rows.items():
            t=date(year,1,1)+timedelta(days=doy-1)
            if t<planting:continue
            record=station.get(t)
            if record is None:raise ValueError('Weather coverage missing')
            sr=record[8];minimum=record[4];maximum=record[3];eto=record[17]
            if not valid(sr):
                sr=solar['properties']['parameter']['ALLSKY_SFC_SW_DWN'].get(t.strftime('%Y%m%d'))
                fallback.append({'date':str(t),'parameter':'solar','source':'NASA POWER'})
            if not valid_temperature(minimum):
                minimum=temperatures.get('T2M_MIN',{}).get(t.strftime('%Y%m%d'))
                fallback.append({'date':str(t),'parameter':'tmin','source':'NASA POWER'})
            if not valid_temperature(maximum):
                maximum=temperatures.get('T2M_MAX',{}).get(t.strftime('%Y%m%d'))
                fallback.append({'date':str(t),'parameter':'tmax','source':'NASA POWER'})
            if not all(valid(v)for v in [sr,minimum,maximum,eto]):raise ValueError('Weather variables missing')
            if not valid_temperature(minimum)or not valid_temperature(maximum)or minimum>maximum:raise ValueError('Physically invalid weather temperature')
            rain=r[1].value or 0;irrigation=r[2].value or 0
            if not all(valid(v)and v>=0 for v in [rain,irrigation]):raise ValueError('Invalid water event')
            n=r[17].value if doy!=first_doy and valid(r[17].value)else 0
            # Dataset describes 25 mg N/L well water = 0.25 kg N/ha/mm.
            events[str(t)]={'n_kg_ha':float(n)+.25*float(irrigation),'irrigation_mm':float(irrigation)}
            weather.append({'date':str(t),'rain_mm':float(rain),'tmin_C':float(minimum),'tmax_C':float(maximum),'solar_MJ_m2':float(sr),'et0_mm':float(eto)})
            if all(measured(c)for c in r[4:8]):
                observations[str(t)]=[float(c.value)/100 for c in r[4:8]]
            if all(measured(c)for c in r[4:11]):full[str(t)]=[float(c.value)/100 for c in r[4:11]]
            if measured(r[14])and 0<=r[14].value<=1:canopy[str(t)]=float(r[14].value)
        if len(observations)<2:raise ValueError('Insufficient actual measured water profiles')
        result.append({'id':f'LIRF:{year}:{treatment}','year':year,'treatment':str(treatment),'planting_date':str(planting),
            'weather':weather,'events':events,'soil_observations':observations,'initial_n_kg_ha':float(initial_n),
            'measured_depths_cm':DEPTHS,'weather_fallback':fallback,'kind':'measured treatment mean; four replications averaged',
            'canopy_observations':canopy,
            'full_soil_observations':full,
            'raw_sha256':sha256(root/'raw'/f'maize{year}.xlsx')})
    w.close();return [trim_contiguous(c)for c in result]


def new_cases(project):
    root=Path(project)/'research/completion-pass/raw';w=openpyxl.load_workbook(root/'test_2012_2013.xlsx',read_only=True,data_only=True)
    planting={}
    for r in w['Crop Log'].iter_rows(values_only=True):
        if isinstance(r[0],datetime)and str(r[1]).lower()=='planting':planting[r[0].year]=max(planting.get(r[0].year,date(1900,1,1)),r[0].date())
    ws=w['Weather data'];it=ws.iter_rows(values_only=True);header=next(it);hourly=defaultdict(list)
    for row in it:
        if valid(row[0])and valid(row[1]):hourly[(int(row[0]),int(row[1]))].append(dict(zip(header,row)))
    station={};quality=[]
    for (year,doy),records in hourly.items():
        required=['AirTemp_C','HrlySolRad_kJ_m^2_min^1','ETo']
        good=len(records)==24 and all(valid(r.get(k))for r in records for k in required)
        if not good:
            quality.append({'year':year,'doy':doy,'status':'incomplete_hourly_weather','rows':len(records)});continue
        station[(year,doy)]={'tmin_C':min(r['AirTemp_C']for r in records),'tmax_C':max(r['AirTemp_C']for r in records),
            'solar_MJ_m2':sum(r['HrlySolRad_kJ_m^2_min^1']*60/1000 for r in records),'et0_mm':sum(r['ETo']for r in records)}
    it=w['Water Balance ET'].iter_rows();next(it);header=[c.value for c in next(it)];groups=defaultdict(list)
    for cells in it:
        row={k:c for k,c in zip(header,cells)}
        if valid(row['Year'].value)and valid(row['DOY'].value):groups[(int(row['Year'].value),str(row['Trt_code'].value))].append(row)
    camera=defaultdict(list)
    it=w['Canopy Cover by Plot'].iter_rows(values_only=True);next(it);heads=next(it)
    for values in it:
        r=dict(zip(heads,values))
        if valid(r.get('Year'))and isinstance(r.get('Date'),datetime)and valid(r.get('Canopy_Cover')):
            value=float(r['Canopy_Cover'])/100
            if not 0<=value<=1:raise ValueError('Measured photo-table canopy cover is outside percent bounds')
            camera[(int(r['Year']),str(r['Trt_code']),str(r['Date'].date()))].append(value)
    cases=[]
    for (year,treatment),records in groups.items():
        weather=[];events={};observations={};missing=[];canopy={};full={}
        canopy={t:float(np.mean(v))for (y,tr,t),v in camera.items()if y==year and tr==treatment}
        for r in records:
            doy=int(r['DOY'].value);t=date(year,1,1)+timedelta(days=doy-1)
            if t<planting[year]:continue
            s=station.get((year,doy))
            if not s:missing.append(str(t));continue
            rain=r['precip_gross (mm)'].value or 0;irrigation=r['irr_gross (mm)'].value or 0
            if not all(valid(v)and v>=0 for v in [rain,irrigation]):raise ValueError('Invalid test water event')
            weather.append({'date':str(t),'rain_mm':float(rain),**s})
            n=r['Nitrogen_Appl (kg/ha)'].value
            events[str(t)]={'n_kg_ha':float(n)if valid(n)else 0,'irrigation_mm':float(irrigation)}
            values=[r[k]for k in ['SWC_15','SWC_30','SWC_60','SWC_90']]
            if all(valid(c.value)for c in values):observations[str(t)]=[float(c.value)/100 for c in values]
            deep=[r[k]for k in ['SWC_15','SWC_30','SWC_60','SWC_90','SWC_120','SWC_150','SWC_200']]
            if all(valid(c.value)for c in deep):full[str(t)]=[float(c.value)/100 for c in deep]
        if missing:
            quality.append({'year':year,'treatment':treatment,'status':'case_incomplete_weather','dates':missing});continue
        if len(observations)<2:continue
        cases.append({'id':f'LIRF:{year}:{treatment}','year':year,'treatment':treatment,'planting_date':str(planting[year]),
            'weather':weather,'events':events,'soil_observations':observations,'initial_n_kg_ha':150.,
            'initial_n_status':'assumed; 2012–13 preplant mineral-N assay not supplied in the used table',
            'weather_basis':'24 hourly records per source year/DOY; hourly temperature extrema, solar rate integrated over 60 minutes, summed grass ETo',
            'measured_depths_cm':DEPTHS,'kind':'treatment mean; measured TDR/neutron columns only',
            'canopy_observations':canopy,
            'full_soil_observations':full,
            'raw_sha256':sha256(root/'test_2012_2013.xlsx')})
    w.close();return [trim_contiguous(c)for c in cases],quality


def labels(project,year):
    if year<=2011:
        w=openpyxl.load_workbook(Path(project)/'crop-response-lirf/raw'/f'maize{year}.xlsx',read_only=True,data_only=True)
        output={str(int(r[0])):float(r[8])for r in w['Seasonal'].iter_rows(min_row=7,max_row=12,values_only=True)if valid(r[0])and valid(r[8])};w.close();return output
    w=openpyxl.load_workbook(Path(project)/'research/completion-pass/raw/test_2012_2013.xlsx',read_only=True,data_only=True)
    it=w['Annual data by plot'].iter_rows(values_only=True);next(it);header=next(it);groups=defaultdict(list)
    for row in it:
        r=dict(zip(header,row))
        if r['Year']==year and valid(r['Grain Yield_15.5%mc_Kg ha-1']):
            groups[str(r['Trt_code'])].append(float(r['Grain Yield_15.5%mc_Kg ha-1'])*.845)
    w.close();return {k:float(np.mean(v))for k,v in groups.items()}
