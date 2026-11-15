"""CSIRO Waite v4 raw observations; unknown grain moisture basis stays unknown."""
from pathlib import Path
import json
import math
import pandas as pd
import xlrd
from ..io import sha256,write_json

CROPS={'W':'wheat','Pe':'peas','B':'barley','O':'oats','P':'pasture','F':'fallow'}


def harmonize_waite(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    provenance=json.loads((root/'download_provenance.json').read_text())
    raw=root/'Waite_Trial_Data.xls'
    if sha256(raw)!=provenance['sha256']:raise ValueError('Waite raw checksum mismatch')
    metadata=json.loads((root/'metadata_v4.json').read_text())
    if metadata['versionNumber']!=4 or 'Attribution 4.0'not in metadata['licence']:
        raise ValueError('Only verified public Waite v4 CC BY release is supported')
    # This reserves a future evaluation before scanning harvest values, not a result.
    protocol_path=out/'waite_validation_reservation.json'
    if not protocol_path.exists():
        write_json(protocol_path,{'source_version':4,'raw_sha256':provenance['sha256'],
            'proposed_fit_years':'1925–1969','proposed_selection_years':'1970–1979',
            'proposed_calibration_years':'1980–1989','reserved_test_years':'1990–1993',
            'model_fit_or_test_scoring_performed':False,
            'limits':'Design, treatment management and grain moisture basis must be resolved before comparing rotation effects. No final test performance claimed.'})
    w=xlrd.open_workbook(raw,on_demand=True);history=w.sheet_by_name('History');grain=w.sheet_by_name('Grain Yield')
    if grain.cell_value(1,0)!='Grain yields (kg/ha)':raise ValueError('Waite grain unit definition changed')
    if history.row_values(3)!=grain.row_values(3):raise ValueError('Waite plot columns differ')
    histories={};rows=[];unknown_codes=set();missing=0;anomalies=[]
    h_by_year={int(history.cell_value(r,0)):r for r in range(4,history.nrows)if isinstance(history.cell_value(r,0),(int,float))}
    for r in range(4,grain.nrows):
        value=grain.cell_value(r,0)
        if not isinstance(value,(int,float)):continue
        year=int(value)
        if year not in h_by_year:raise ValueError('Missing Waite crop-history year')
        for col in range(1,grain.ncols):
            plot=str(int(grain.cell_value(3,col)));code=str(history.cell_value(h_by_year[year],col)).strip()
            crop=CROPS.get(code)
            if code and crop is None:unknown_codes.add(code)
            previous=list(histories.get(plot,[]));histories.setdefault(plot,[]).append({'year':year,'crop':crop,'original_code':code or None})
            y=grain.cell_value(r,col)
            if not isinstance(y,(int,float))or not math.isfinite(y):missing+=1;continue
            if y<0:raise ValueError('Negative Waite yield')
            if crop not in ['wheat','peas','barley','oats']:
                anomalies.append({'plot_id':plot,'year':year,'history_crop_code':code,
                    'reason':'Numeric grain cell conflicts with non-grain crop history; excluded pending curator clarification'})
                continue
            rows.append({'record_id':f'WAITE:{plot}:{year}','site_id':'WAITE','farm_id':'WAITE','field_id':None,
                'plot_id':plot,'treatment_id':None,'latitude':-34.96,'longitude':138.64,'country':'Australia',
                'climate_region':None,'year':year,'season':str(year),'current_crop':crop,'crop_variety':None,
                'previous_crops_json':json.dumps(previous),'history_complete_before_1925':False,
                'planting_date':None,'harvest_date':None,'observed_yield_original':float(y),
                'original_unit':'kg/ha grain; moisture basis not specified','observed_yield_dry_kg_ha':None,
                'yield_conversion':None,'fertilizer_n_kg_ha':None,'irrigation_mm':None,'tillage':None,
                'measurement_uncertainty':None,'experimental_controls':None,'source_doi':metadata['doi'],
                'license':'CC-BY-4.0','raw_asset_sha256':provenance['sha256'],
                'source_version':4,'reserved_final_test':1990<=year<=1993})
    w.release_resources()
    result=pd.DataFrame(rows)
    if result.record_id.duplicated().any():raise ValueError('Duplicate Waite plot-year')
    result.to_parquet(out/'waite_rotation_dataset.parquet',index=False)
    result.to_csv(out/'waite_rotation_dataset.csv',index=False)
    pd.DataFrame(anomalies).to_csv(out/'waite_excluded_cells.csv',index=False)
    report={'source_doi':metadata['doi'],'version':4,'records':len(result),'plots':int(result.plot_id.nunique()),
        'years':[int(result.year.min()),int(result.year.max())],'crop_counts':result.current_crop.value_counts().to_dict(),
        'missing_or_nonnumeric_grain_cells':missing,'unrecognized_history_codes':sorted(unknown_codes),
        'crop_history_grain_conflicts_excluded':len(anomalies),
        'latitude_longitude_source':'Official v4 spatial metadata, rounded from degrees/minutes/seconds',
        'grain_moisture_basis':'unknown; not converted to dry grain and not pooled with CSCAP dry-grain targets',
        'training_and_scoring_performed':False,'source_license':metadata['licence'],'citation':metadata['attributionStatement'],
        'limits':['Historical Australian experiment at one location. No new geographic-generalization accuracy claimed.',
            'Treatment/control design, input rates and unknown history codes need interpretation before rotation-effect estimation.',
            'Workbook also contains climate and selected soil data, not yet harmonized by this adapter.']}
    write_json(out/'waite_quality_report.json',report)
    return report
