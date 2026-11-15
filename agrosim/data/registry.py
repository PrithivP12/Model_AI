"""Evidence-based coverage. Legacy example extraction is not model consumption."""
from pathlib import Path
import json
import math
import pandas as pd
from ..io import resolve, sha256, write_json


def coverage(config, base):
    output=resolve(base,config['coverage_output']);output.parent.mkdir(parents=True,exist_ok=True)
    rows=[]
    for record in config['sources']:
        local=[];samples=0;passed=0;statuses=[]
        for item in record.get('legacy_evidence',[]):
            p=resolve(base,item)
            if not p.exists():continue
            d=json.loads(p.read_text());obs=d.get('observations',[])
            usable=[o for o in obs if isinstance(o.get('value'),(int,float))and math.isfinite(o['value'])]
            samples+=len(usable);passed+=sum(o.get('quality')=='passed'for o in usable)
            statuses.append(d.get('status'))
            local.append({'path':item,'sha256':sha256(p),'observations':len(usable),
                          'legacy_qa_passed':sum(o.get('quality')=='passed'for o in usable)})
        state='sample_tested'if samples else 'adapter_implemented'if record.get('adapter_path')else 'planned'
        availability='found_local'if samples else record.get('availability','not_checked')
        if 'needs_credentials'in statuses:availability='needs_credentials'
        evidence=record.get('current_evidence',[])
        if evidence and all(resolve(base,p).exists()for p in evidence):
            state=record.get('current_implementation',state)
            availability=record.get('current_availability',availability)
            local.extend({'path':p,'sha256':sha256(resolve(base,p))}for p in evidence)
        rows.append({'source_id':record['source_id'],'resource':record['resource'],'product_id':record.get('product_id'),
            'availability':availability,'implementation':state,'legacy_numeric_samples':samples,'legacy_qa_passed':passed,
            'legacy_statuses':','.join(str(v)for v in statuses),'role':record['role'],
            'blocker':record.get('blocker'),'new_workflow_consumed':state in ['consumed_by_model','evaluated'],
            'local_evidence':json.dumps(local),'documentation_url':record['documentation_url']})
    pd.DataFrame(rows).to_csv(output,index=False)
    summary={'registry_records':len(rows),'resource_families':len({r['resource']for r in rows}),
             'new_workflow_consumed':[r['source_id']for r in rows if r['new_workflow_consumed']],
             'all_resources_fully_integrated':False,'report':str(output)}
    write_json(output.with_suffix('.json'),summary)
    lines=['# Source coverage','',
           'Every challenge family is tracked. Legacy numeric samples are evidence of extraction, not proof of current model use or product QC. Unknown product metadata remains unknown.',
           '', '| Source | Availability | Implementation | Legacy values / QA passed | Blocker |',
           '|---|---|---|---|---|']
    lines.extend(f"| {r['source_id']} | {r['availability']} | {r['implementation']} | {r['legacy_numeric_samples']} / {r['legacy_qa_passed']} | {r['blocker'] or ''} |"for r in rows)
    output.with_suffix('.md').write_text('\n'.join(lines)+'\n')
    return summary
