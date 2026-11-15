"""Observation support first: replicate products, exact cells, distinct harvests."""
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import numpy as np
import openpyxl
from ..io import sha256,write_json


def audit_lirf(root,years=(2008,2009,2010)):
    rows=[];timing=[];tissue=[]
    for y in years:
        if y not in (2008,2009,2010):raise ValueError('Only original training/development years are authorized')
        path=Path(root)/'crop-response-lirf/raw'/f'maize{y}.xlsx'
        w=openpyxl.load_workbook(path,read_only=True,data_only=True);sheet=w['Seasonal'];digest=sha256(path)
        for cells in sheet.iter_rows(min_row=26,max_row=49):
            v=[c.value for c in cells]
            if not all(isinstance(v[i],(int,float))for i in [0,1,3,5,6,7,8])or not isinstance(v[2],str):continue
            if not 0<v[6]<1:raise ValueError('Invalid measured sample HI')
            if abs(v[8]-v[7]*.845)>1e-6:raise ValueError('Source dry grain conversion changed')
            rows.append({'case_id':f'LIRF:{y}:{int(v[0])}','year':y,'treatment':str(int(v[0])),
                'block':int(v[1]),'plot':v[2],'population_per_ha':v[3],
                'sampled_aboveground_dry_kg_ha':v[5],'sampled_HI':v[6],
                'sampled_grain_dry_kg_ha':v[5]*v[6],
                'plot_grain_dry_kg_ha':v[8],
                'sample_minus_plot_grain':v[5]*v[6]-v[8],
                'source':str(path.relative_to(root)),'source_sha256':digest,
                'source_cells':{k:cells[i].coordinate for k,i in [('biomass',5),('HI',6),('plot_grain',8)]},
                'grain_support':'sampled plant dry grain reconstructed within plot, not product of treatment means; plot yield comes from separate rows/area/date',
                'sample_dry_basis':'source methodology corrects residual grain moisture to zero; includes stover+cob+grain; excludes roots'})
        for cells in w['CropLog'].iter_rows():
            v=[c.value for c in cells]
            if isinstance(v[0],datetime)and any(t in str(v).lower()for t in ['biomass','harvest','r1','r6','frost','hail']):
                timing.append({'year':y,'date':v[0].date().isoformat(),'kind':str(v[1]),'label':str(v[2]),'notes':str(v[3]),
                    'exact_treatment_specific':False,'source_cell':cells[0].coordinate,'source_sha256':digest})
        if 'C&N'in w:
            for cells in w['C&N'].iter_rows(min_row=7,max_row=12):
                v=[c.value for c in cells]
                tissue.append({'case_id':f'LIRF:{y}:{int(v[0])}','year':y,'grain_N_fraction':v[7]/100,
                    'stover_N_fraction':v[1]/100,'cob_N_fraction':v[4]/100,'source_cells':[cells[i].coordinate for i in [1,4,7]],
                    'source_sha256':digest,'stock_conversion_allowed':False,'reason':'Separate organ component masses unavailable; concentrations alone cannot create N stocks.'})
        w.close()
    grouped=defaultdict(list)
    for row in rows:grouped[row['case_id']].append(row)
    treatment=[]
    for cid,g in sorted(grouped.items()):
        treatment.append({'case_id':cid,'year':g[0]['year'],'treatment':g[0]['treatment'],'replicates':len(g),
            **{key:float(np.mean([r[key]for r in g]))for key in ['sampled_aboveground_dry_kg_ha','sampled_HI','sampled_grain_dry_kg_ha','plot_grain_dry_kg_ha','sample_minus_plot_grain']},
            'sample_minus_plot_SD':float(np.std([r['sample_minus_plot_grain']for r in g],ddof=1)),
            'biomass_sample_date':next((t['date']for t in timing if t['year']==g[0]['year']and t['label'].lower()=='biomass'),None),
            'timing_support':'field/block log;2010 biomass date unknown; maturity is not plot harvest'})
    return {'status':'replicate_support_and_moisture_audited','replicates':rows,'treatments':treatment,'timing':timing,'tissue':tissue,
        'no_same_sample_assumption_for_plot_grain':True,'years':list(years)}


def write_audit(root,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);r=audit_lirf(root);write_json(out/'report.json',r);return r
