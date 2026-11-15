"""Actual HTTP submission/worker/result checks over unchanged numerical engines."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import json,time,sqlite3

import pytest
from fastapi.testclient import TestClient

from agrosim.api.app import create_app
from agrosim.api.models import SimulationRequest
from agrosim.api.settings import Settings
from agrosim.api.service import AgroSimService
from agrosim.api.jobs import JobStore

FARM={'location':{'latitude':40.,'longitude':-88.,'country':'US','state':'IL'},'area':1.,'area_unit':'ha'}
SOURCE={'origin':'hypothetical','description':'Invented API accounting fixture; not a measured price or cost'}

def sample(crop='corn',**changes):
    return {'farm':deepcopy(FARM),'reference_scenario_id':f'controlled_{crop}_v1','adopt_reference_assumptions':True,
            'allow_experimental_profiles':True,**changes}

@pytest.fixture(scope='module')
def client(tmp_path_factory):
    with TestClient(create_app(Settings(tmp_path_factory.mktemp('api-release')/'jobs')))as c:yield c

def completed(c,value):
    reply=c.post('/api/v1/simulations',json=value)
    assert reply.status_code==202,reply.text
    identity=reply.json()['run_id']
    for _ in range(600):
        state=c.get('/api/v1/simulations/'+identity).json()
        if state['status']not in ['queued','running']:break
        time.sleep(.05)
    assert state['status']=='completed',state
    result=c.get('/api/v1/simulations/'+identity+'/results');assert result.status_code==200,result.text
    return identity,result.json()

def finance_for(crop,product,year=2020):
    return {'decision_date':'2020-01-01','currency':'USD','discount_rate':0.,'opening_cash':0.,
        'products':[{'crop_id':crop,'product_id':product,'commodity_class':'ALL CLASSES','grade':'unspecified',
            'marketed_fraction':1.,'dry_matter_fraction':1.,'source':SOURCE}],
        'prices':[{'identifier':'quote','product_id':product,'amount':2.,'unit':'kg','date':'2019-11-01','source':SOURCE}],
        'costs':[{'identifier':'seed','crop_id':crop,'year':y,'category':'seed','covers':['seed'],'amount':100.,
            'unit':'per_ha','accounting':'variable','cash':'cash','expense_date':f'{y}-04-01','payment_date':f'{y}-04-01','source':SOURCE}
            for y in range(2020,year+1)],
        'markets':[{'identifier':'market','product_id':product,'status':'regional_proxy','source':SOURCE}],
        'sales':[{'product_id':product,'price_id':'quote','market_id':'market','fraction':1.}], 'sale_timing':'on_harvest'}

def test_health_readiness_catalog_requirements_openapi(client):
    assert client.get('/api/v1/health').json()['version']=='1.0.0'
    assert client.get('/api/v1/ready').json()['ready']
    crops=client.get('/api/v1/crops').json()['crops'];assert len(crops)==len({c['crop_id']for c in crops})==88
    assert client.get('/api/v1/crops/scallion').json()['requires_subtype']
    req=client.post('/api/v1/farms/requirements',json={'farm':FARM,'crops':['corn','apple']}).json()
    assert not req['simulation_ready']and not req['field_profile_inferred']and req['missing_inputs']
    schema=client.get('/openapi.json').json()
    assert '/api/v1/comparisons'in schema['paths']and 'SimulationResult'in schema['components']['schemas']

@pytest.mark.parametrize('crop,engine',[('corn','preserved_reference'),('barley','legacy_experimental'),('apple','special_physiology'),
    ('rice','special_physiology'),('pineapple','special_physiology'),('banana','special_physiology')])
def test_http_real_engine_dispatch_and_products(client,crop,engine):
    identity,r=completed(client,sample(crop))
    assert r['selected_engine']==engine and r['agriculture']['products']
    assert max(map(abs,r['agriculture']['conservation'].values()))<1e-6
    assert r['independent_accuracy']is None and r['farmer_recommendation']is None
    series=client.get(f'/api/v1/simulations/{identity}/timeseries?aggregation=monthly&limit=2').json()
    assert len(series['rows'])==2 and series['flux_aggregation']=='sum'
    assert client.get(f'/api/v1/simulations/{identity}/artifacts').status_code==200

def test_reference_corn_exact_numerical_signature_reproduction(client):
    _,a=completed(client,sample());_,b=completed(client,sample())
    assert a['numerical_sha256']==b['numerical_sha256']
    assert a['agriculture']==b['agriculture']
    assert a['agriculture']['products'][0]['product_dry_kg_ha']==pytest.approx(4218.638930668569,abs=1e-8)

def test_annual_rotation_carryover_and_living_optional(client):
    _,rotation=completed(client,sample('rotation'))
    assert len(rotation['agriculture']['products'])==3
    assert rotation['agriculture']['initial_soil']!=rotation['agriculture']['final_soil']
    _,living=completed(client,sample('corn',mode='experimental_living',living={'fixture_id':'hypothetical_functional_network_v1','assumptions':'hypothetical_assumptions_adopted'}))
    assert living['ecosystem']['status']=='experimental_hypothetical'
    assert living['mode']=='experimental_living'

@pytest.mark.parametrize('crop,product,year',[('corn','corn_grain',2020),('guava','guava_whole_fruit_dry',2022)])
def test_http_actual_annual_and_repeated_product_finance(client,crop,product,year):
    _,r=completed(client,sample(crop,finance=finance_for(crop,product,year)))
    assert r['finance']['status']=='evaluated_conditional',r['finance']
    dry=sum(h['total_product_dry_kg']for h in r['agriculture']['products'])
    assert r['finance']['revenue']==pytest.approx(dry*2.)
    assert r['finance']['full_economic_cost']==pytest.approx(100.*(year-2019))
    assert r['finance']['net_return']==pytest.approx(dry*2.-100.*(year-2019))
    assert sum(h['saleable_mass_kg']for h in r['agriculture']['products'])==pytest.approx(dry)
    if crop=='guava':assert len(r['finance']['detail']['inventory'])>1

def test_subtype_errors_invalid_fields_and_missing_inputs(client):
    service=AgroSimService();resolved=service.prepare(SimulationRequest.model_validate(sample('scallion_fistulosum'))).model_dump(mode='json')
    resolved['reference_scenario_id']=None;resolved['plantings'][0]['subtype']=None
    assert not client.post('/api/v1/simulations/validate',json=resolved).json()['valid']
    resolved['plantings'][0]['subtype']='invented_hybrid'
    assert client.post('/api/v1/simulations',json=resolved).status_code==422
    assert client.post('/api/v1/simulations',json={'farm':FARM}).status_code==422
    for altered in [sample('../secret'),sample('corn',soil={'path':'/etc/passwd'}),sample('corn',command='id')]:
        assert client.post('/api/v1/simulations',json=altered).status_code==422
    for farm in [{**FARM,'area':-1.},{**FARM,'location':{'latitude':100.,'longitude':0.}},{**FARM,'area':True}]:
        assert client.post('/api/v1/simulations',json=sample('corn',farm=farm)).status_code==422
    assert client.get('/api/v1/simulations/../../etc/passwd').status_code in [404,422]
    assert client.get('/api/v1/simulations/'+'0'*32).status_code==404

def test_size_auth_cors_and_no_error_body_echo(tmp_path):
    settings=Settings(tmp_path/'auth',token='x'*40,max_body_bytes=4096)
    with TestClient(create_app(settings))as c:
        assert c.get('/api/v1/crops').status_code==401
        headers={'Authorization':'Bearer '+'x'*40}
        assert c.get('/api/v1/crops',headers=headers).status_code==200
        headers['Content-Type']='application/json'
        assert c.post('/api/v1/simulations',content=' '*5000,headers=headers).status_code==413
        bad=c.post('/api/v1/simulations',json={'private_secret':'do_not_echo_me'},headers=headers)
        assert 'do_not_echo_me'not in bad.text and '/Users/'not in bad.text
        reply=c.options('/api/v1/crops',headers={**headers,'Origin':'https://evil.invalid','Access-Control-Request-Method':'GET'})
        assert reply.status_code==400 and 'access-control-allow-origin'not in reply.headers

def test_idempotency_and_queue_cancellation_recovery(tmp_path):
    s=Settings(tmp_path/'store',max_pending=2);store=JobStore(s)
    a=store.submit('simulation',sample(),'same');b=store.submit('simulation',sample(),'same')
    assert a.run_id==b.run_id
    with pytest.raises(Exception):store.submit('simulation',sample('barley'),'same')
    assert store.cancel(a.run_id).status=='canceled'
    interrupted=store.submit('simulation',sample())
    with store.connection()as con:con.execute("UPDATE jobs SET status='running' WHERE id=?",(interrupted.run_id,))
    store.start();assert store.get(interrupted.run_id).status=='failed';store.stop()
    store2=JobStore(s);assert store2.get(a.run_id).status=='canceled'

def test_queue_bound_and_failed_worker_never_publish_results(tmp_path):
    from agrosim.api.service import ServiceError
    settings=Settings(tmp_path/'failure',max_pending=1);store=JobStore(settings)
    a=store.submit('simulation',{'farm':'invalid internal fixture'})
    with pytest.raises(ServiceError,match='queue'):store.submit('simulation',sample())
    store.start()
    for _ in range(300):
        state=store.get(a.run_id)
        if state.status not in ['queued','running']:break
        time.sleep(.05)
    assert state.status=='failed'and state.error and '/Users/'not in state.error.message
    with pytest.raises(ServiceError):store.get_results(a.run_id)
    store.stop()

def test_api_calendars_market_and_conversion_fail_with_structured_issues(client):
    svc=AgroSimService();base=svc.prepare(SimulationRequest.model_validate(sample())).model_dump(mode='json')
    base['reference_scenario_id']=None
    bad=deepcopy(base);bad['plantings'][0]['harvest_deadline']='2019-01-01'
    r=client.post('/api/v1/simulations/validate',json=bad)
    assert not r.json()['valid']and any(x['code']=='invalid_calendar'for x in r.json()['errors'])
    bad=deepcopy(base);bad['soil']=None
    assert any(x['field']=='soil'for x in client.post('/api/v1/simulations/validate',json=bad).json()['errors'])
    bad=sample('corn',finance=finance_for('corn','corn_grain'))
    bad['finance']['products'][0]['product_id']='cacao_beans'
    assert client.post('/api/v1/simulations',json=bad).status_code==422
    bad=sample('corn',finance=finance_for('corn','corn_grain'))
    bad['finance']['markets'][0]['status']='confirmed'
    assert client.post('/api/v1/simulations',json=bad).status_code==422
    bad['finance']['products'][0]['dry_matter_fraction']=0.
    assert client.post('/api/v1/simulations',json=bad).status_code==422

def test_cached_environment_unavailable_does_not_fabricate_weather(client):
    svc=AgroSimService();r=svc.prepare(SimulationRequest.model_validate(sample())).model_dump(mode='json')
    r['reference_scenario_id']=None;r['weather']={'kind':'cached_nasa_power','source':{'origin':'source_reference','description':'Explicit missing cache'},'source_start_year':2020}
    response=client.post('/api/v1/simulations/validate',json=r).json()
    assert not response['valid']and any(x['code']=='environment_unavailable'for x in response['errors'])

def test_comparison_http_matched_environment_and_no_unsupported_ranking(client):
    service=AgroSimService();a=service.prepare(SimulationRequest.model_validate(sample())).model_dump(mode='json');b=deepcopy(a)
    a['reference_scenario_id']=None;b['reference_scenario_id']=None
    a['scenario_id']='corn';b['scenario_id']='soybean';b['plantings'][0]['crop_id']='soybean'
    reply=client.post('/api/v1/comparisons',json={'scenarios':[a,b],'ranking':'conditional_financial'})
    assert reply.status_code==202,reply.text
    identity=reply.json()['run_id']
    for _ in range(600):
        job=client.get('/api/v1/simulations/'+identity).json()
        if job['status']not in ['running','queued']:break
        time.sleep(.05)
    assert job['status']=='completed',job
    r=client.get('/api/v1/simulations/'+identity+'/results').json()
    assert r['conditional_ranking']is None and r['ranking_unavailable_reason']
    b['farm']['area']=2.
    assert client.post('/api/v1/comparisons',json={'scenarios':[a,b]}).status_code==422
