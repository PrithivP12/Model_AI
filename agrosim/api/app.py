"""Versioned REST interface, no frontend or arbitrary resource loading."""
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
import hmac,json

from fastapi import FastAPI,Request,Query,Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,FileResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import VERSION
from .models import *
from .service import AgroSimService,ServiceError
from .settings import Settings
from .jobs import JobStore

def create_app(settings=None):
    settings=settings or Settings.from_env();service=AgroSimService(settings)
    @asynccontextmanager
    async def lifespan(app):
        store=JobStore(settings);store.start();app.state.store=store
        try:yield
        finally:store.stop()
    app=FastAPI(title='AgroSim Global',version=VERSION,description='Experimental scenario backend. No independent farmer-ready accuracy claim.',lifespan=lifespan)
    app.state.service=service
    app.add_middleware(CORSMiddleware,allow_origins=list(settings.origins),allow_methods=['GET','POST'],
        allow_headers=['Content-Type','Authorization','Idempotency-Key'],allow_credentials=False)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(settings.hosts))

    @app.middleware('http')
    async def boundary(request,call_next):
        if settings.token and request.url.path not in ['/api/v1/health']:
            header=request.headers.get('authorization','')
            if not hmac.compare_digest(header,'Bearer '+settings.token):return JSONResponse({'errors':[{'field':'authorization','code':'unauthorized','message':'Bearer authorization required'}]},status_code=401)
        if request.method in ['POST','PUT','PATCH']:
            if request.headers.get('content-type','').split(';')[0]!='application/json':return JSONResponse({'errors':[{'field':'body','code':'json_required','message':'Content-Type application/json required'}]},status_code=415)
            try:
                length=request.headers.get('content-length')
                if length and(int(length)<0 or int(length)>settings.max_body_bytes):raise ValueError()
                body=bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body)>settings.max_body_bytes:raise ValueError()
                request._body=bytes(body)
            except ValueError:return JSONResponse({'errors':[{'field':'body','code':'request_too_large','message':'Request exceeds configured size limit'}]},status_code=413)
        response=await call_next(request);response.headers['X-Content-Type-Options']='nosniff';response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request,exc):
        status=404 if exc.issue.code=='run_not_found'or exc.issue.code=='unknown_crop'else 409 if exc.issue.code in ['result_unavailable','idempotency_conflict']else 429 if exc.issue.code in ['queue_full','retention_limit']else 422
        return JSONResponse({'errors':[exc.issue.model_dump()]},status_code=status)
    @app.exception_handler(RequestValidationError)
    async def input_error(request,exc):
        return JSONResponse({'errors':[{'field':'.'.join(map(str,e['loc'][1:])),'code':e['type'],'message':e['msg']}for e in exc.errors()]},status_code=422)
    @app.exception_handler(Exception)
    async def unexpected(request,exc):return JSONResponse({'errors':[{'field':'server','code':'internal_error','message':'Service could not complete the request'}]},status_code=500)

    @app.get('/api/v1/health')
    def health():return {'status':'ok','version':VERSION,'service':'agrosim','authentication':'shared_bearer'if settings.token else'local_demo_no_auth'}
    @app.get('/api/v1/ready')
    def ready(request:Request):
        from .fixtures import living_fixture
        try:
            count=len(service.list_crops());living_fixture()
            with request.app.state.store.connection()as con:con.execute('SELECT 1').fetchone()
            ok=count==88 and request.app.state.store.thread.is_alive()
        except Exception:ok=False;count=None
        return JSONResponse({'ready':ok,'catalog_count':count,'offline_fixtures':'available'if ok else'unavailable',
            'sources':service.data_sources(),'public_deployment_ready':False},status_code=200 if ok else 503)
    @app.get('/api/v1/crops')
    def crops(lifecycle:str|None=None,status:str|None=None,family:str|None=None):
        rows=service.list_crops(lifecycle=lifecycle,status=status,family=family);return {'count':len(rows),'crops':rows}
    @app.get('/api/v1/crops/{crop_id}')
    def crop(crop_id:str):return service.get_crop(crop_id)
    @app.post('/api/v1/farms/requirements')
    def requirements(request:RequirementsRequest):return service.get_scenario_requirements(request)
    @app.post('/api/v1/simulations/validate',response_model=Validation)
    def validate(request:SimulationRequest):return service.validate_simulation(request)
    @app.post('/api/v1/simulations',response_model=Job,status_code=202)
    def submit(value:SimulationRequest,request:Request,idempotency_key:str|None=Header(default=None)):
        validation=service.validate_simulation(value)
        if not validation.valid:return JSONResponse({'errors':[e.model_dump()for e in validation.errors],'warnings':[w.model_dump()for w in validation.warnings]},status_code=422)
        return request.app.state.store.submit('simulation',value.model_dump(mode='json'),idempotency_key)
    @app.get('/api/v1/simulations/{run_id}',response_model=Job)
    def status(run_id:str,request:Request):return request.app.state.store.get(run_id)
    @app.post('/api/v1/simulations/{run_id}/cancel',response_model=Job)
    def cancel(run_id:str,request:Request):return request.app.state.store.cancel(run_id)
    @app.get('/api/v1/simulations/{run_id}/results',response_model=SimulationResult|ComparisonResult)
    def result(run_id:str,request:Request):return request.app.state.store.get_results(run_id)
    @app.post('/api/v1/comparisons',response_model=Job,status_code=202)
    def comparison(value:ComparisonRequest,request:Request,idempotency_key:str|None=Header(default=None)):
        service.validate_comparison(value)
        return request.app.state.store.submit('comparison',value.model_dump(mode='json'),idempotency_key)
    @app.get('/api/v1/simulations/{run_id}/timeseries')
    def timeseries(run_id:str,request:Request,aggregation:Literal['daily','monthly']='monthly',start:date|None=None,end:date|None=None,
                   offset:int=Query(default=0,ge=0,le=1831),limit:int=Query(default=120,ge=1,le=366),scenario:int|None=Query(default=None,ge=0,le=3)):
        store=request.app.state.store;store.get_results(run_id);directory=store.directory(run_id)
        if scenario is not None:directory=directory/('scenario_'+str(scenario))
        path=directory/'timeseries.json'
        if not path.is_file():raise ServiceError('series_unavailable','Choose a valid comparison scenario index','scenario')
        rows=json.loads(path.read_text());rows=[r for r in rows if(not start or r['date']>=str(start))and(not end or r['date']<=str(end))]
        if aggregation=='monthly':
            grouped={}
            for row in rows:grouped.setdefault(row['date'][:7],[]).append(row)
            aggregate=[];flux={'transpiration_mm','evaporation_mm','irrigation_mm','rain_mm','drainage_mm','runoff_mm','mineralized_n_kg_ha','respired_c_kg_ha'}
            for month,group in sorted(grouped.items()):
                item={'date':month,'crop_id':None,'days':len(group)}
                for key in group[0]:
                    if key in ['date','crop_id']:continue
                    values=[g[key]for g in group if g[key]is not None]
                    item[key]=sum(values)if key in flux else sum(values)/len(values)if values else None
                aggregate.append(item)
            rows=aggregate
        return {'aggregation':aggregation,'stock_aggregation':'mean','flux_aggregation':'sum','total_rows':len(rows),
                'offset':offset,'rows':rows[offset:offset+limit],'next_offset':offset+limit if offset+limit<len(rows)else None}
    @app.get('/api/v1/simulations/{run_id}/artifacts')
    def artifacts(run_id:str,request:Request,scenario:int|None=Query(default=None,ge=0,le=3)):
        store=request.app.state.store;store.get_results(run_id);directory=store.directory(run_id)
        if scenario is not None:directory=directory/('scenario_'+str(scenario))
        path=directory/'research.json.gz'
        if not path.is_file():raise ServiceError('artifact_unavailable','Choose a valid simulation or comparison scenario','scenario')
        return FileResponse(path,media_type='application/gzip',filename='agrosim-research.json.gz')
    @app.get('/api/v1/metadata')
    def metadata():
        from .fixtures import scenario_ids
        return {'version':VERSION,'modes':['standard','experimental_living'],'units':{'area':['ha','acre'],'water':'mm',
            'dry_product':'kg dry/ha','soil_N':'kg N/ha','soil_C':'kg C/ha'},'evidence_tiers':{'A':'direct measurement','B':'compatible literature','C':'transfer','D':'hypothesis'},
            'reference_scenarios':scenario_ids(),'sources':service.data_sources(),'job_concurrency':1,'max_pending':settings.max_pending,
            'retention':'No automatic deletion; bounded stored-job count. Operator archives/removes terminal runs.',
            'public_deployment':'Requires authentication, tenant authorization, rate limiting, TLS and quotas; CORS is not authorization'}
    return app
