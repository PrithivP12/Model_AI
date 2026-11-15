"""Fresh-install local API smoke using only packaged controlled fixtures."""
from pathlib import Path
import argparse,json,time
import requests

def smoke(base):
    assert requests.get(base+'/api/v1/ready',timeout=10).json()['ready']
    catalog=requests.get(base+'/api/v1/crops',timeout=10).json();assert catalog['count']==88
    body={'farm':{'location':{'latitude':40.,'longitude':-88.,'country':'US','state':'IL'},'area':1.},
        'scenario_id':'offline_corn','reference_scenario_id':'controlled_corn_v1','adopt_reference_assumptions':True}
    valid=requests.post(base+'/api/v1/simulations/validate',json=body,timeout=10).json();assert valid['valid'],valid
    response=requests.post(base+'/api/v1/simulations',json=body,timeout=10);response.raise_for_status();identity=response.json()['run_id']
    end=time.monotonic()+90
    while time.monotonic()<end:
        state=requests.get(base+'/api/v1/simulations/'+identity,timeout=10).json()
        if state['status']not in ['running','queued']:break
        time.sleep(.2)
    assert state['status']=='completed',state
    response=requests.get(base+'/api/v1/simulations/'+identity+'/results',timeout=10);response.raise_for_status();result=response.json()
    assert result['selected_engine']=='preserved_reference'
    assert abs(result['agriculture']['products'][0]['product_dry_kg_ha']-4218.638930668569)<1e-8
    assert max(map(abs,result['agriculture']['conservation'].values()))<1e-6
    return {'ready':True,'catalog':88,'completed':True,'result':result}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8000');p.add_argument('--output',required=True);a=p.parse_args()
    report=smoke(a.base_url)
    with Path(a.output).open('x')as stream:json.dump(report,stream,indent=2)
    print(json.dumps({'catalog':88,'completed':True,'numerical_sha256':report['result']['numerical_sha256']}))
