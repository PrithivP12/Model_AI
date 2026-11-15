"""Harmonize verified CSCAP observations without fabricating missing management."""
from pathlib import Path
import json
import hashlib
import numpy as np
import pandas as pd
import openpyxl
from ..io import sha256, write_json

DOI='10.15482/USDA.ADC/1411953'
EXPECTED='b9e40bf74554c32849dcb6e6a613d6b7a60b4b8a2f8cbed33073d7c7732f95b6'


def harmonize(root, output):
    root=Path(root);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    raw=root/'raw/Sustainable_Corn_Research_Data_2011-2015_0.xlsx'
    if sha256(raw)!=EXPECTED:raise ValueError('Raw experiment checksum differs from verified source')
    w=openpyxl.load_workbook(raw,read_only=True,data_only=True)
    iterator=w['Plot Identifiers'].iter_rows(values_only=True);header=next(iterator);next(iterator);next(iterator)
    plots={(str(r[0]),str(r[2])):dict(zip(header,r))for r in iterator if r[0]is not None}
    a=w['Agronomic'];m=list(a.iter_rows(min_row=1,max_row=3,values_only=True));idx=m[0].index('AGR17')
    if m[1][idx]!='Corn grain yield at 15.5% MB'or m[2][idx]!='kg ha-1':raise ValueError('Unverified outcome definition')
    ag=a.iter_rows(values_only=True);agheader=next(ag);next(ag);next(ag)
    raw_yields={}
    for r in ag:
        d=dict(zip(agheader,r));v=d['AGR17']
        try:raw_yields[(str(d['uniqueid']),str(d['plotid']),int(d['year']))]=float(v)
        except (ValueError,TypeError):pass
    w.close()
    source_features=root/'plot_features.csv';data=pd.read_csv(source_features,dtype={'plot':str})
    if data.duplicated(['site','plot','year']).any():raise ValueError('Duplicate plot-year observations')
    rows=[]
    for r in data.to_dict('records'):
        key=(r['site'],r['plot'],r['year']);p=plots[(r['site'],r['plot'])]
        if key not in raw_yields or abs(raw_yields[key]-r['y'])>1e-8:raise ValueError('Derived yield does not match original workbook')
        history=[]
        for year in range(2011,r['year']):
            crop=p.get(f'{year}crop')
            history.append({'year':year,'crop':str(crop).strip()if crop else None})
        known_n=0. if r['nitrogen']=='NIT1'else None
        row={'record_id':f"CSCAP:{r['site']}:{r['plot']}:{r['year']}",
            'site_id':r['site'],'farm_id':r['farm'],'field_id':None,'plot_id':r['plot'],
            'treatment_id':':'.join(str(p.get(k))for k in ['rotation','tillage','drainage','nitrogen','landscape']),
            'rotation_code':p['rotation'],'rotation_family':r['family'],'latitude':r['latitude'],'longitude':r['longitude'],
            'country':'USA','climate_region':None,'year':r['year'],'season':str(r['year']),
            'current_crop':'corn','crop_variety':None,'previous_crops_json':json.dumps(history),
            'history_complete_before_2011':False,'planting_date':None,'harvest_date':None,
            'observed_yield_original':r['y'],'original_unit':'kg/ha at 15.5% moisture wet basis',
            'observed_yield_dry_kg_ha':r['y']*.845,'yield_conversion':'dry = wet * (1 - 0.155)',
            'fertilizer_n_kg_ha':known_n,'fertilizer_quality':'explicit zero treatment'if known_n==0 else 'unknown: plot-specific applicability not resolved',
            'irrigation_mm':None,'tillage':r['tillage'],'drainage':r['drainage'],'nitrogen_treatment':r['nitrogen'],
            'replicate':r['rep'],'landscape':r['landscape'],'measurement_uncertainty':None,
            'pest_management':None,'disease_management':None,'source_doi':DOI,'license':'CC0-1.0',
            'raw_asset_sha256':EXPECTED,'source_features_sha256':sha256(source_features),
            'soil_latest_availability':None if pd.isna(r['soil_latest_availability'])else r['soil_latest_availability'],
            'planned_maturity':r['plantmaturity'],'planned_seeding_rate':r['plantrate'],
            'reference_control':'continuous maize, where matched within site/year/block/tillage/drainage/N label',
            'is_holdout_farm':r['is_holdout_farm']}
        row.update({k:v for k,v in r.items()if k.startswith('soil_SOIL')})
        rows.append(row)
    result=pd.DataFrame(rows)
    if result.observed_yield_dry_kg_ha.isna().any()or(result.observed_yield_dry_kg_ha<0).any():raise ValueError('Invalid observed yield')
    result.to_parquet(output/'rotation_dataset.parquet',index=False)
    result.to_csv(output/'rotation_dataset.csv',index=False)
    report={'records':len(result),'sites':int(result.site_id.nunique()),'farms':int(result.farm_id.nunique()),
        'years':sorted(map(int,result.year.unique())),'source_doi':DOI,'raw_asset_sha256':EXPECTED,
        'outcome_checked_against_original_workbook':True,
        'missing_fraction':{k:float(result[k].isna().mean())for k in result},
        'management_correction':'Old per-site/year sums mix applications to different plots. New benchmark excludes all ambiguous numeric doses; only explicit NIT1 zeros are retained.',
        'soil_semantics':'Existing prior-availability depth contexts retained; no stocks inferred from these concentrations.',
        'limitations':['Observed treatment contrasts are cropping-system contrasts, not pure causal rotation effects.',
            'Only maize outcomes meeting existing source-bound eligibility rules. Variant rotations excluded.',
            'Ordered crop history begins in 2011; no earlier history invented.',
            'Measurement uncertainty, many dates and management variables are missing.',
            'Source publication vintage is not a historical operational forecast archive.']}
    write_json(output/'data_quality.json',report)
    (output/'data_quality_report.md').write_text('# Rotation dataset quality\n\n'+
        f"{len(result)} verified maize plot-year outcomes from {report['sites']} experiments / {report['farms']} farms, 2011–2015.\n\n"+
        report['management_correction']+'\n\n'+ '\n'.join('- '+v for v in report['limitations'])+
        '\n\nField missingness and raw checksums: `data_quality.json`.\n')
    (output/'data_sources.md').write_text('# Measured rotation observations\n\n'+
        'Abendroth et al. (2017), Sustainable Corn CAP 2011–2015. DOI: https://doi.org/'+DOI+
        '\n\nPublic Ag Data Commons workbook, CC0. AGR17 is maize grain yield, kg/ha at 15.5% moisture. '+
        'All exported outcomes were matched against the original workbook. Derived pre-decision soil features retain original SOIL identifiers. '+
        'The original workbook and its dictionary remain in rotation-learning-v1/raw.\n')
    pd.DataFrame([{'source_id':'cscap','doi':DOI,'license':'CC0-1.0','raw_path':str(raw),'sha256':EXPECTED,
        'records_used':len(result),'role':'raw measured yield and crop sequence'}]).to_csv(output/'data_inventory.csv',index=False)
    return report


def build_features(root, dataset_path, output):
    root=Path(root);data=pd.read_parquet(dataset_path)
    tables=[];provenance=[]
    for site in data.site_id.unique():
        path=root/'weather'/f'{site}.json'
        if not path.exists():raise FileNotFoundError(f'No NASA prior-period climate cache for {site}')
        d=json.loads(path.read_text());response=d['response'];parameters=response['properties']['parameter'];row={'site_id':site}
        for name,values in parameters.items():
            if name not in ['T2M','PRECTOTCORR','ALLSKY_SFC_SW_DWN']:continue
            if any(not key.startswith(tuple(str(y)for y in range(2001,2011)))for key in values):
                raise ValueError('Climate normal includes response-year or later weather')
            units=response['parameters'][name]['units']
            expected={'T2M':['C'],'PRECTOTCORR':['mm/day'],'ALLSKY_SFC_SW_DWN':['MJ/m^2/day','kW-hr/m^2/day']}
            if units not in expected[name]:raise ValueError(f'Unverified monthly climate unit {name}: {units}')
            for month in range(1,13):
                vs=[v for key,v in values.items()if int(key[-2:])==month and v not in [-999,-9999]]
                if len(vs)!=10 or not np.isfinite(vs).all():raise ValueError('Incomplete ten-year climate normal')
                row[f'climate_{name}_m{month:02d}']=float(np.mean(vs))*(3.6 if units=='kW-hr/m^2/day'else 1)
        tables.append(row);provenance.append({'site_id':site,'source_id':'power','raw_asset_sha256':sha256(path),
            'path':str(path),'time_start':'2001-01-01','time_end':'2010-12-31',
            'feature_use':'pre-study climate context; actual historical publication vintage unknown',
            'coarse_grid_context':True,'uncertainty':None})
    result=data.merge(pd.DataFrame(tables),on='site_id',validate='many_to_one')
    dates=pd.to_datetime(result.soil_latest_availability,errors='coerce')
    cutoff=pd.to_datetime(result.year.astype(str)+'-04-01')
    if (dates.notna()&(dates>=cutoff)).any():raise ValueError('Post-decision soil feature')
    result['previous_crop']=result.previous_crops_json.map(lambda s:json.loads(s)[-1]['crop']if json.loads(s)else 'unknown')
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True);result.to_parquet(out,index=False)
    pd.DataFrame(provenance).to_csv(out.with_suffix('.provenance.csv'),index=False)
    return {'records':len(result),'sites':len(tables),'climate_features':36,'file':str(out),
            'disease_pest_pollinator_microbiome_genomic_features':0}
