"""Validated existing plant observations; never infer absent measurements as zeros."""
from pathlib import Path
import json
import pandas as pd
from ..io import write_json,sha256
from .audit import read_tables,numeric

FIELDS=['AGR04','AGR05','AGR09','AGR10','AGR11','AGR12','AGR17','AGR19','AGR23','AGR24','AGR25','AGR26','AGR27','AGR28','AGR32','AGR33','AGR34']


def extract_observations(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'observed_tissue_and_grain.json').exists():raise RuntimeError('Preserve completed observed-tissue extraction')
    tables,units,source=read_tables(root);records=json.loads((root/'reports/rotation-recovery/input-audit/records.json').read_text());plots={(v['site_id'],v['plot_id'])for v in records.values()};raw={};issues=[]
    for field in FIELDS:
        if units['Agronomic'][field]!='kg ha-1':raise ValueError('Unverified plant mass units: '+field)
    for row in tables['Agronomic']:
        if(str(row['uniqueid']),str(row['plotid']))not in plots:continue
        key=f"{row['uniqueid']}:{row['plotid']}:{int(row['year'])}";values={}
        for field in FIELDS:
            value=numeric(row.get(field))
            if value is not None and value<0:
                issues.append({'source_row':row['_row'],'field':field,'issue':'negative plant mass excluded'});value=None
            values[field]=value
        raw[key]={**values,'source_row':row['_row'],'year':int(row['year'])}
    report={'source_sha256':sha256(source),'units':{f:units['Agronomic'][f]for f in FIELDS},'rows':raw,'issues':issues,
        'moisture_conversions':{'AGR17':.845,'AGR19':.87},'stage':'R6 corn / R8 soybean plant sample; exact measurement timestamp unavailable',
        'not_verified_residue_return':True,'available_at_historic_decision':None}
    write_json(out/'observed_tissue_and_grain.json',report);return report
