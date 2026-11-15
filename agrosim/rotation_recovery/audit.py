"""Conservative field-season evidence extraction without inventing missing states."""
from pathlib import Path
from datetime import datetime,date
from collections import Counter
import json,re
import numpy as np
import pandas as pd
import openpyxl
from ..data.management import scope,text,numeric
from ..io import write_json,sha256

MAP={'corn':'corn','maize':'corn','soybean':'soybeans','soybeans':'soybeans'}


def stamp(value):
    if value is None:return None
    try:return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:return None


def depth(value):
    m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*',str(value))
    if not m:return None
    a,b=map(float,m.groups());return(a,b)if b>a else None


def read_tables(root):
    path=Path(root)/'rotation-learning-v1/raw/Sustainable_Corn_Research_Data_2011-2015_0.xlsx';w=openpyxl.load_workbook(path,read_only=True,data_only=True);tables={};units={}
    for name in ['Field Operations','Soil','Agronomic','Plot Identifiers','Site Metadata']:
        it=w[name].iter_rows(values_only=True);header=next(it);next(it);unit=next(it);units[name]=dict(zip(header,unit));rows=[]
        for i,row in enumerate(it,start=4):
            record={**dict(zip(header,row)),'_row':i}
            year=numeric(record.get('year',record.get('cropyear')))
            if year is not None and year>2014:continue
            # Explicitly keep previously excluded final/reserved farms out of new work.
            if str(record.get('uniqueid'))in ['KELLOGG','ORR','LAN']:continue
            for key,value in record.items():
                if isinstance(value,(date,datetime)):record[key]=str(value)[:10]
            rows.append(record)
        tables[name]=rows
    w.close();return tables,units,path


def operation_scope(op,plot,crop):
    if MAP.get(text(op.get('cashcrop')).lower())!=crop:return 'excluded','different or unknown cash crop'
    # Reuse the existing spatial/treatment parser only after verifying actual crop.
    copy=dict(op);copy['cashcrop']='corn'
    fertilizer_text=text(op.get('fertilizercrop')).lower()
    fertilizer_crop=MAP.get(fertilizer_text)
    if fertilizer_text and fertilizer_crop is None:return 'ambiguous','unrecognized fertilizer crop scope'
    if fertilizer_crop and fertilizer_crop!=crop:return 'excluded','different fertilizer crop'
    copy['fertilizercrop']='corn'if fertilizer_crop else ''
    return scope(copy,plot)


def season(operations,plot,crop,year):
    planting=[];harvest=[];fertilizer=[];unknown=[]
    for op in operations:
        if text(op.get('uniqueid'))!=plot['site_id']or numeric(op.get('cropyear'))!=year:continue
        operation=text(op.get('operation'));kind='plant'if operation==('plant_corn'if crop=='corn'else 'plant_soy')else 'harvest'if operation==('harvest_corn'if crop=='corn'else 'harvest_soy')else 'fertilizer'if operation in ['fertilizer_synthetic','fertilizer_manure']else None
        if kind is None:continue
        status,reason=operation_scope(op,plot,crop)
        if status=='excluded':continue
        t=stamp(op.get('date'))
        if status!='applies'or not t:
            unknown.append({'row':op['_row'],'operation':operation,'reason':reason if status!='applies'else 'missing/invalid date'});continue
        evidence={'date':t,'source_sheet':'Field Operations','source_row':op['_row'],'scope_reason':reason,'comment':text(op.get('comments')),'crop':crop}
        if kind=='plant':planting.append({**evidence,'variety':text(op.get('planthybrid'))or None,'relative_maturity':numeric(op.get('plantmaturity'))})
        elif kind=='harvest':harvest.append(evidence)
        else:
            n=numeric(op.get('nitrogenelem'))
            if n is None or n<0:unknown.append({'row':op['_row'],'operation':operation,'reason':'N rate missing/invalid'});continue
            # Zero-N treatment must not inherit general MRTN/site rates.
            if plot['nitrogen_treatment']=='NIT1'and n>0 and reason=='site/year/crop scope with no contradictory qualifier'and 'all plots'not in text(op.get('comments')).lower():continue
            fertilizer.append({**evidence,'n_kg_ha':n})
    sow_dates=sorted({r['date']for r in planting});harvest_dates=sorted({r['date']for r in harvest})
    # Multiple operations may mean replanting or partial harvest; preserve ambiguity.
    sow=sow_dates[0]if len(sow_dates)==1 else None;end=harvest_dates[0]if len(harvest_dates)==1 else None
    if sow and end and end<sow:end=None
    uncertain_n=any(r['operation'].startswith('fertilizer')for r in unknown)
    total=sum(r['n_kg_ha']for r in fertilizer)if fertilizer and not uncertain_n else None
    if plot['nitrogen_treatment']=='NIT1'and not uncertain_n and not fertilizer:total=0.
    return {'year':year,'crop':crop,'planting_date':sow,'harvest_date':end,'planting_records':planting,'harvest_records':harvest,
        'fertilizer_events':fertilizer,'fertilizer_total_if_recorded_complete':total,
        'fertilizer_record_status':'ambiguous'if uncertain_n else 'recorded_events_only'if fertilizer else 'explicit_zero_treatment'if total==0 else 'unknown_no_record',
        'irrigation_mm':None,'irrigation_status':'not provided by this operations schema; do not equate absence with zero',
        'unresolved_operations':unknown,'management_is_archived_actual_not_forecast_plan':True}


def run_audit(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed field-input audit')
    fixed=pd.read_csv(root/'reports/rotation-recovery/fixed_cohort.csv');ids=set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode())
    frame=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet');frame=frame[frame.record_id.isin(ids)].copy()
    tables,units,path=read_tables(root)
    expected={'nitrogenelem':'kg per hectare'}
    for key,unit in expected.items():
        if units['Field Operations'][key]!=unit:raise ValueError('Unverified operation units')
    for key,unit in [('SOIL01','g cm-3'),('SOIL15','mg per kg soil'),('SOIL22','mg per kg soil')]:
        if units['Soil'][key]!=unit:raise ValueError('Unverified soil units')
    all_seasons={};records={};soils={};agronomy={};summary=[]
    for r in frame.to_dict('records'):
        plot={'site_id':r['site_id'],'plot_id':str(r['plot_id']),'rotation_family':r['rotation_family'],'nitrogen_treatment':r['nitrogen_treatment']}
        history=json.loads(r['previous_crops_json'])+[{'year':int(r['year']),'crop':'corn'}];timeline=[]
        for h in history:
            crop=MAP.get(str(h['crop']).lower());y=int(h['year']);key=f"{r['site_id']}:{r['plot_id']}:{y}"
            if crop is None:raise ValueError('Fixed cohort crop history changed')
            if key not in all_seasons:all_seasons[key]=season(tables['Field Operations'],plot,crop,y)
            timeline.append({'season_key':key,**all_seasons[key]})
        key=(r['site_id'],str(r['plot_id']))
        soilrows=[]
        for row in tables['Soil']:
            if(text(row['uniqueid']),text(row['plotid']))!=key:continue
            lohi=depth(row['depth']);t=stamp(row['sampledate'])
            soilrows.append({'source_row':row['_row'],'sample_date':t,'year':row['year'],'depth_cm':lohi,'depth_raw':str(row['depth']),
                'values':{k:numeric(row.get(k))for k in ['SOIL01','SOIL02','SOIL13','SOIL14','SOIL15','SOIL22','SOIL30','SOIL31','SOIL32']}})
        soils[':'.join(key)]=soilrows
        agrows=[{k:row.get(k)for k in ['year','_row','AGR04','AGR05','AGR09','AGR10','AGR11','AGR12','AGR17','AGR19','AGR24','AGR26','AGR32','AGR33','AGR34']}for row in tables['Agronomic']if(text(row['uniqueid']),text(row['plotid']))==key]
        agronomy[':'.join(key)]=agrows
        before_start=[row for row in soilrows if row['sample_date']and row['sample_date']<'2011-01-01']
        target=timeline[-1];previous=timeline[-2]
        records[r['record_id']]={'site_id':r['site_id'],'farm_id':r['farm_id'],'plot_id':str(r['plot_id']),'year':int(r['year']),
            'latitude':float(r['latitude']),'longitude':float(r['longitude']),'rotation_family':r['rotation_family'],'nitrogen_treatment':r['nitrogen_treatment'],
            'tillage':r['tillage'],'drainage':r['drainage'],'timeline':timeline,
            'initial_soil_water':{'value':None,'status':'unknown; no field moisture measurements in these Soil retention columns'},
            'initial_mineral_n':{'value':None,'status':'No complete dated pre-start profile established','pre_start_soil_rows':len(before_start)},
            'source_workbook_sha256':sha256(path)}
        prior_bio=next((a for a in agrows if numeric(a['year'])==int(r['year'])-1),{})
        ckey,nkey=('AGR11','AGR12')if previous['crop']=='soybeans'else ('AGR09','AGR10')
        cn_known=all(numeric(prior_bio.get(k))is not None for k in [ckey,nkey])
        summary.append({'record_id':r['record_id'],'site_id':r['site_id'],'farm_id':r['farm_id'],'year':int(r['year']),
            'all_sowing_known':all(s['planting_date']for s in timeline),'all_harvest_known':all(s['harvest_date']for s in timeline),
            'target_harvest_known':bool(target['harvest_date']),'previous_sowing_known':bool(previous['planting_date']),
            'all_N_recorded_or_explicit_zero':all(s['fertilizer_total_if_recorded_complete']is not None for s in timeline),
            'prior_tissue_CN_measured':cn_known,'prior_soil_N_samples':sum(row['sample_date']is not None and row['sample_date']<target['planting_date'] and row['values']['SOIL15']is not None for row in soilrows)if target['planting_date']else 0,
            'soil_moisture_observed':False,'initial_N_verified':False})
    coverage=pd.DataFrame(summary);coverage.to_csv(out/'coverage.csv',index=False)
    write_json(out/'records.json',records);write_json(out/'soil_observations.json',soils);write_json(out/'agronomic_observations.json',agronomy);write_json(out/'seasons.json',all_seasons)
    rates={c:float(coverage[c].mean())for c in coverage if coverage[c].dtype==bool}
    report={'status':'inputs_audited_before_equation_changes','fixed_pairs':len(fixed),'target_records':len(records),'field_seasons':len(all_seasons),'coverage':rates,
        'current_model_information_gaps':['Historical daily meteorology replaced by monthly normals and invented within-month rain/temperature timing.',
            'Actual preceding crop sowing/harvest and N events existed in the workbook but were not joined to prior seasons.',
            'Initial water and mineral N profiles are not established; current values are scenarios, not observations.',
            'Soil saturation/retention is not actual moisture, concentration is not stock without depth/bulk-density support.',
            'Measured vegetative C/N production is not identical to residue returned, root inputs, or subsequent N mineralization.',
            'Current physical maturity automatically ends crop and deposits residue; an observed later harvest date may not be honored as an actual harvest boundary.'],
        'water_error_evaluable':False,'independent_final_data_available':False,'new_equations_changed':False,'recommendation':None}
    write_json(out/'report.json',report);return report
