"""Single public dispatch and normalization layer over preserved simulators."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import date,timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import gzip,json,math

from pydantic import ValidationError
from . import VERSION
from .models import *
from .fixtures import expand,scenario_ids,living_fixture
from .settings import Settings

LIMITATIONS=[
 'Simulated scenarios are not independently validated farm predictions.',
 'Crop-rotation effects underperformed a simple crop-history baseline; no optimal-rotation recommendation is provided.',
 '85 crop profiles and new ecological parameters are largely transferred or hypothetical.',
 'Living effects may be large and are not observed local pest or microbial outcomes.',
 'No precise field accuracy, uncertainty interval, fresh-product conversion or profit is inferred from missing observations.',
]

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return sha256(canonical(value)).hexdigest()
def safe_json(value):return json.loads(json.dumps(value,default=str,allow_nan=False))

@lru_cache(maxsize=1)
def _profiles():
    from ..crops.profiles import profiles
    return profiles()

@lru_cache(maxsize=1)
def _catalog():
    from ..physiology.profiles import resolve,SUBTYPES
    from ..crops.products import mappings
    result=[];maps=mappings()
    for key,old in _profiles().items():
        p=resolve(key,subtype=next(iter(SUBTYPES[key])))if key in SUBTYPES else resolve(key)
        original=old['crop_parameters']is not None;reference=old['implementation_status']=='EXECUTABLE_REFERENCE'
        kernel='preserved_reference'if reference else'legacy_experimental'if original else'special_physiology'
        perennial=p['archetype_parameters'].get('perennial',False)
        product=maps[key]['product_id']if original else p['archetype_parameters']['product_id']
        organ=maps[key]['product_organ']if original else p['archetype_parameters']['product_organ']
        result.append({'crop_id':key,'name':p['common_name'],'scientific_identity':p.get('taxonomy'),
            'subtypes':deepcopy(SUBTYPES.get(key,{})),'requires_subtype':key in SUBTYPES,
            'family':p.get('family'),'process_family':p.get('physiology_family',p['archetype']),
            'lifecycle':'perennial'if perennial else'annual_or_first_cycle','engine':kernel,'engine_version':VERSION,
            'implementation_status':'reference'if reference else'experimental','parameter_evidence':'preserved_reference_parameters'if reference else'transferred_or_hypothetical_C_D',
            'validation_status':'development_evaluated_not_farmer_validated'if reference else'functional_execution_not_independent_field_validation',
            'modes':['standard','experimental_living'],'product_id':product,'product_organ':organ,
            'required_inputs':['location','area','dated weather','soil horizons and initial W/C/N','planting/harvest calendar','management provenance'],
            'special_requirements':(['explicit subtype']if key in SUBTYPES else[])+(['paddy water/N configuration']if key=='rice'else[])+
                (['night VPD and source; induction assumptions']if key=='pineapple'else[])+(['flowering type']if key=='strawberry'else[]),
            'initial_state':'explicit organ/age stocks required for an already established special perennial'if not original and perennial else'explicit soil stocks; new crop established from declared profile propagule',
            'financial_support':{'status':'regional_benchmarks_optional_not_local_truth'if reference else'financial_data_unavailable',
                'requires':['product dry fraction/recovery/grade','dated compatible prices','cost coverage','market access and sale plan'],
                'automatic_price':None},'environmental_suitability':None,'limitations':LIMITATIONS})
    return result

class ServiceError(Exception):
    def __init__(self,code,message,field='request'):
        self.issue=Issue(field=field,code=code,message=message);super().__init__(message)

class AgroSimService:
    def __init__(self,settings=None):self.settings=settings or Settings.from_env()

    def list_crops(self,*,lifecycle=None,status=None,family=None):
        return [deepcopy(c)for c in _catalog()if(lifecycle is None or c['lifecycle']==lifecycle)and(status is None or c['implementation_status']==status)and(family is None or c['family']==family)]

    def get_crop(self,crop_id):
        row=next((c for c in _catalog()if c['crop_id']==crop_id),None)
        if row is None:raise ServiceError('unknown_crop','Unknown canonical crop ID','crop_id')
        return deepcopy(row)

    def data_sources(self,location=None):
        resources=self.settings.resources_dir
        power=resources/'power.json'if resources else None
        available=bool(power and power.is_file());meta={}
        if available:
            try:
                data=json.loads(power.read_text());d=data.get('data',data.get('response',data));coords=d['geometry']['coordinates'];h=d['header']
                meta={'point':{'latitude':coords[1],'longitude':coords[0]},'start':h.get('start'),'end':h.get('end'),
                    'version':h.get('api',{}).get('version'),'sha256':sha256(power.read_bytes()).hexdigest(),
                    'retrieved_at':data.get('retrieved_utc'),'compatible_location':location is None or abs(coords[1]-location.latitude)<=.002 and abs(coords[0]-location.longitude)<=.002}
            except (ValueError,KeyError,TypeError,OSError):available=False
        return [{'source_id':'controlled_reference','name':'Offline controlled scenario','status':'available','origin':'hypothetical',
            'geographic_support':'illustrative environments only; not location-derived','time_support':'explicit fixture calendar',
            'resolution':'daily controlled input','freshness':None,'network_required':False},
            {'source_id':'nasa_power','name':'NASA POWER','status':'available_cached'if available else'unavailable_not_configured',
             'origin':'source_reference','resolution':'coarse parameter-specific grid point sample; not plot observation',
             'geographic_support':'configured cached point only','metadata':meta,'network_required':False,
             'missing_behavior':'refuse incompatible/missing forcing; no live acquisition by the API'},
            {'source_id':'regional_finance','name':'NASS/ERS/farmdoc compatible benchmarks','status':'available_configured'if resources and(resources/'regional_finance.json').is_file()else'unavailable_not_configured',
             'origin':'regional_proxy','geographic_support':'explicit configured country/state/product/year only','missing_behavior':'financial_data_unavailable'},
            *[{'source_id':key,'name':name,'status':'not_integrated_in_release_service','missing_behavior':'unavailable; not silently substituted'}
              for key,name in [('smap','SMAP'),('gpm','GPM/IMERG'),('srtm','SRTM'),('landsat','Landsat'),('soilgrids','SoilGrids')]]]

    def get_capabilities(self,crop_id,location=None):
        return {**self.get_crop(crop_id),'sources':self.data_sources(location)}

    def get_scenario_requirements(self,request:RequirementsRequest):
        crops=[self.get_crop(c)for c in request.crops]
        ids=set(request.crops)
        return {'farm':request.farm.model_dump(mode='json'),'crops':crops,'simulation_ready':False,
            'missing_inputs':['explicit soil/state','dated weather','planting/calendar and management','experimental opt-in where required'],
            'sources':self.data_sources(request.farm.location),
            'reference_scenarios':[r for r in scenario_ids()if r['crop_id']in ids or r['crop_id']is None],
            'field_profile_inferred':False,'financial_status':'financial_data_unavailable','warnings':LIMITATIONS}

    def prepare(self,request):
        if not isinstance(request,SimulationRequest):request=SimulationRequest.model_validate(request)
        try:r=expand(request)
        except (ValueError,KeyError)as exc:raise ServiceError('invalid_reference',str(exc),'reference_scenario_id')
        return r

    def validate_simulation(self,request):
        errors=[];warnings=[];missing=[];engine=None
        def error(field,code,message):errors.append(Issue(field=field,code=code,message=message))
        try:r=self.prepare(request)
        except ServiceError as exc:return Validation(valid=False,errors=[exc.issue],warnings=[])
        except ValidationError:return Validation(valid=False,errors=[Issue(field='reference_scenario_id',code='invalid_reference_overrides',message='Reference overrides violate the request contract')],warnings=[])
        for field in ['start','end','soil','weather','management']:
            if getattr(r,field)is None:missing.append(field);error(field,'required','Explicit '+field+' required or adopt a compatible reference scenario')
        if not r.plantings:error('plantings','required','At least one explicit crop planting required')
        if r.start and r.end:
            if not 0<=(r.end-r.start).days<=1830:error('end','horizon_limit','Horizon must be ordered and at most 1831 days')
        ids=[];profiles=[]
        from ..physiology.profiles import resolve
        for i,p in enumerate(r.plantings):
            field=f'plantings.{i}'
            try:
                cap=self.get_crop(p.crop_id);prof=resolve(p.crop_id,subtype=p.subtype)
                ids.append(p.crop_id);profiles.append(prof)
                if not cap['requires_subtype']and p.subtype:error(field+'.subtype','unsupported_subtype','This crop does not accept a subtype')
                if cap['implementation_status']=='experimental'and not r.allow_experimental_profiles:error('allow_experimental_profiles','experimental_opt_in_required','Experimental crop profiles require explicit adoption')
            except (ValueError,ServiceError):error(field+'.crop_id','unsupported_crop_or_subtype','Unknown crop or missing/unsupported explicit subtype');continue
            if p.planted>p.harvest_deadline:error(field,'invalid_calendar','Harvest deadline precedes establishment')
            if r.start and r.end:
                if p.planted<r.start and (not r.initial_plant or r.initial_plant.crop_id!=p.crop_id):error(field+'.planted','missing_initial_plant','Pre-existing plants require explicit organ/age stocks')
                if p.planted>r.end or p.harvest_deadline<r.start:error(field,'calendar_outside_horizon','Planting interval does not intersect simulation')
                perennial=prof['archetype_parameters'].get('perennial',False)
                if perennial and len(r.plantings)>1:error('plantings','perennial_plan','Persistent perennials must use a continuous production plan, not annual replacement slots')
                if not perennial and p.harvest_deadline>r.end:error(field+'.harvest_deadline','incomplete_annual_calendar','Annual harvest deadline must lie within weather horizon')
            if p.crop_id=='rice'and r.paddy is None:error('paddy','required','Rice requires explicit paddy/nonflooded field and nitrogen configuration')
            opts=r.crop_options.get(p.crop_id)
            if p.crop_id=='pineapple'and(not opts or opts.night_VPD_kPa is None or not opts.night_VPD_source):error('crop_options.pineapple','required_CAM_inputs','Explicit nighttime VPD and source required')
            if p.crop_id=='strawberry'and(not opts or not opts.flowering_type):error('crop_options.strawberry.flowering_type','required','Choose day_neutral or short_day')
            if opts and opts.flowering_type=='short_day'and opts.short_day_chill_hours is None:error('crop_options.'+p.crop_id,'required_chilling','Short-day strawberry needs explicit scenario/cultivar chilling')
            if p.crop_id in ['apple','grape','olive','mango','orange','lemon','avocado','guava','cacao','date','papaya','passion_fruit','coconut','strawberry']and(not opts or not opts.pollination):
                warnings.append(Issue(field='crop_options.'+p.crop_id,code='reproductive_support_missing',message='Pollination is not assumed; flowers may abort and no fruit may be produced'))
        ordered=sorted(r.plantings,key=lambda p:p.planted)
        if r.paddy and r.paddy.initial_pond_mm>r.paddy.bund_height_mm:error('paddy.initial_pond_mm','pond_exceeds_bund','Initial pond water exceeds bund height')
        if any(b.planted<=a.harvest_deadline for a,b in zip(ordered,ordered[1:])):error('plantings','overlapping_calendar','Planting windows must not overlap')
        if r.initial_plant:
            if r.initial_plant.crop_id not in ids:error('initial_plant.crop_id','mismatch','Initial plant must match the production plan')
            if set(r.initial_plant.pools)!=set(['green_leaf','senesced_leaf','structural_shoot','root','grain','labile_N']):error('initial_plant.pools','incomplete_organs','All six authoritative organ pools required')
            if r.initial_plant.pools.get('grain')and(r.initial_plant.pools['grain'].dry or r.initial_plant.pools['grain'].N):error('initial_plant.pools.grain','unsupported_initial_fruit','Initial fruit requires matched cohorts; unsupported by this API')
            if not any('physiology_parameters'in p for p in profiles):error('initial_plant','unsupported_initialization','Explicit organ-age initialization is supported for special physiology only')
        if r.mode=='experimental_living'and not r.living:error('living','explicit_assumptions_required','Living mode needs adoption of the hypothetical network')
        if r.mode=='standard'and r.living:error('living','mode_mismatch','Living configuration is only valid in experimental_living mode')
        if r.weather and r.start and r.end:
            if r.weather.kind=='inline':
                days=r.weather.days or[]
                if len(days)!=(r.end-r.start).days+1 or not days or days[0].date!=r.start or days[-1].date!=r.end or any(b.date-a.date!=timedelta(days=1)for a,b in zip(days,days[1:])):error('weather.days','incomplete_daily_weather','Daily forcing must exactly and contiguously cover the horizon')
            elif r.weather.kind=='cached_nasa_power':
                source=self.data_sources(r.farm.location)[1]
                if source['status']!='available_cached'or not source.get('metadata',{}).get('compatible_location'):error('weather','environment_unavailable','No compatible cached NASA point; use explicit inline forcing or adopted controlled reference')
                if r.weather.source_start_year is None:error('weather.source_start_year','required','Historical replay source year required')
            elif not r.adopt_reference_assumptions:error('weather','reference_adoption_required','Controlled weather requires explicit adoption')
        for i,event in enumerate(r.management or[]):
            if r.start and r.end and not r.start<=event.date<=r.end:error(f'management.{i}.date','outside_horizon','Management event outside horizon')
        for key in r.crop_options:
            if key not in ids:error('crop_options.'+key,'unknown_plan_crop','Options must match a selected crop')
            else:
                for event in r.crop_options[key].pruning:
                    if r.start and r.end and not r.start<=event.date<=r.end:error('crop_options.'+key+'.pruning','outside_horizon','Pruning event outside horizon')
        if r.paddy and 'rice'not in ids:error('paddy','incompatible_field','Paddy configuration is only supported for rice plans')
        if r.finance:
            for message in self._validate_finance(r):error('finance',message[0],message[1])
        if profiles:engine='special_physiology'if any(not _profiles()[i]['crop_parameters']for i in ids)else'preserved_reference'if all(_profiles()[i]['implementation_status']=='EXECUTABLE_REFERENCE'for i in ids)else'legacy_experimental'
        warnings.extend(Issue(field='science',code='scientific_limitation',message=m)for m in LIMITATIONS)
        if r.reference_scenario_id:warnings.append(Issue(field='reference_scenario_id',code='hypothetical_reference',message='Reference inputs are controlled assumptions, not derived from the supplied location'))
        return Validation(valid=not errors,errors=errors,warnings=warnings,engine=engine,required_inputs=missing)

    def _validate_finance(self,r):
        f=r.finance;errors=[]
        if not r.farm.location.country:errors.append(('country_required','Financial location needs explicit country'))
        if f.use_regional_benchmarks:errors.append(('regional_benchmark_selection_unavailable','Automatic benchmark adoption requires a configured matched account; supply prices/costs explicitly in V1.0'))
        ids={p.crop_id for p in r.plantings};specs={p.crop_id:p for p in f.products}
        if set(specs)!=ids or len(specs)!=len(f.products):errors.append(('product_coverage','One correct sale-product specification per selected crop required'))
        for key,p in specs.items():
            try:expected=self.get_crop(key)['product_id']
            except ServiceError:continue
            if p.product_id!=expected:errors.append(('product_mismatch','Sale product must match actual harvested organ identity'))
        for q in f.prices:
            if q.date>f.decision_date:errors.append(('future_price','Price must be available at financial decision date'))
            if q.product_id not in {p.product_id for p in f.products}:errors.append(('price_product_mismatch','Quote product not in supplied sale products'))
            if q.unit=='bu'and q.product_id not in ['corn_grain','soybean_seed','spring_wheat_grain']:errors.append(('unsupported_unit','Bushels are supported only for explicitly mapped grains'))
        for item in f.costs:
            if item.crop_id not in ids:errors.append(('cost_crop_mismatch','Expense must match a selected crop'))
            if item.year!=item.expense_date.year:errors.append(('cost_year_mismatch','Expense year must match declared crop-year account'))
            if r.start and r.end and not r.start<=item.expense_date<=r.end:errors.append(('expense_outside_horizon','Expense outside financial horizon'))
            if item.accounting=='capital_cash'and item.economic:errors.append(('capital_accounting','Capital purchase cannot be a current economic expense'))
        products={p.product_id for p in f.products}
        for item in f.markets:
            if item.product_id not in products:errors.append(('market_product_mismatch','Market product must match a supplied sale product'))
            if item.status=='confirmed'and(not item.buyer_id or not item.buyer_location):errors.append(('market_contract_missing','Confirmed access requires supplied buyer identity and location'))
        quote_ids={q.identifier for q in f.prices};market_ids={m.identifier for m in f.markets}
        if len(quote_ids)!=len(f.prices)or len(market_ids)!=len(f.markets):errors.append(('duplicate_identity','Price and market identifiers must be unique'))
        for sale in f.sales:
            if sale.product_id not in products or sale.price_id not in quote_ids or sale.market_id not in market_ids:errors.append(('sale_reference_missing','Sale plan needs compatible supplied product, quote and market IDs'))
            if f.sale_timing=='explicit'and sale.date is None:errors.append(('sale_date_required','Explicit sale timing requires dates'))
            if sale.date and r.start and r.end and not r.start<=sale.date<=r.end:errors.append(('sale_outside_horizon','Sale outside horizon; choose explicit longer accounting support'))
        for key in {c.crop_id for c in f.costs}:
            for year in {c.year for c in f.costs if c.crop_id==key}:
                covers=[]
                for c in f.costs:
                    if c.crop_id==key and c.year==year:
                        if set(covers)&set(c.covers):errors.append(('overlapping_costs','Expense coverage would double count within the crop-year account'))
                        covers+=c.covers
        if f.storage_fraction and not f.storage_source:errors.append(('storage_source_required','Storage needs provenance, capacity and loss assumption'))
        return errors

    def _weather(self,r):
        from ..processes import WeatherDay as Day
        if r.weather.kind=='inline':return[Day(**d.model_dump())for d in r.weather.days],r.weather.source.model_dump(mode='json')
        if r.weather.kind=='controlled_reference':
            return [Day(r.start+timedelta(days=i),5.,17.,23.,20.,4.)for i in range((r.end-r.start).days+1)],r.weather.source.model_dump(mode='json')
        from ..data.power import read_power
        frame,_,meta=read_power(self.settings.resources_dir/'power.json','configured_cached_point',r.farm.location.latitude,r.farm.location.longitude)
        rows={date.fromisoformat(x['date']):x for x in frame.to_dict('records')};result=[]
        for i in range((r.end-r.start).days+1):
            d=r.start+timedelta(days=i);year=r.weather.source_start_year+d.year-r.start.year
            try:source=date(year,d.month,d.day);row=rows[source]
            except (ValueError,KeyError):raise ServiceError('weather_gap','Cached historical replay does not cover this exact calendar; supply explicit forcing','weather')
            result.append(Day(d,**{k:row[k]for k in ['rain_mm','tmin_C','tmax_C','solar_MJ_m2']}))
        return result,{'origin':'source_reference','source_id':'nasa_power','sha256':meta['sha256'],'header':meta['header'],
            'native_resolution_is_field_scale':False,'interpretation':'Historical month/day replay, not a future forecast','source_start_year':r.weather.source_start_year}

    def run_simulation(self,request,*,run_id='direct',created_at='unassigned',output=None):
        r=self.prepare(request);validation=self.validate_simulation(r)
        if not validation.valid:raise ServiceError('invalid_simulation','Simulation inputs failed validation; use validation endpoint')
        from ..physiology.profiles import resolve
        from ..processes import Simulator,SoilState as State,Planting as P,Management as M
        from ..soil import HorizonLayer
        from ..crops.runtime import run as legacy_run
        from ..physiology.runtime import run as special_run
        from ..finance.units import area_ha
        selected={p.crop_id:resolve(p.crop_id,subtype=p.subtype)for p in r.plantings}
        names={key:p['crop_parameters']['name']for key,p in selected.items()};reverse={value:key for key,value in names.items()}
        physical=Simulator([HorizonLayer(**l.model_dump())for l in r.soil.layers],latitude=r.farm.location.latitude)
        soil=State(**r.soil.initial_state.model_dump());weather,weather_meta=self._weather(r)
        plants=[P(p.planted,names[p.crop_id],p.harvest_deadline,p.residue_removal_fraction,p.harvest_mode)for p in r.plantings]
        events=[M(x.date,x.fertilizer_n_kg_ha,x.irrigation_mm)for x in r.management]
        living=None
        if r.mode=='experimental_living':living=living_fixture();living['enabled'].update(r.living.enabled)
        is_special=validation.engine=='special_physiology';kwargs={'living_config':living}
        if is_special:
            kwargs.update(subtypes={p.crop_id:p.subtype for p in r.plantings if p.subtype},
                crop_options={names[k]:v.model_dump(mode='json',exclude_none=True)for k,v in r.crop_options.items()},
                paddy=r.paddy.model_dump(mode='json')if r.paddy else None)
            if r.initial_plant:
                initial=r.initial_plant.model_dump(mode='json');crop_id=initial.pop('crop_id')
                initial['planting']=next(p for p in plants if p.crop==names[crop_id]);kwargs['initial']=initial
        raw=(special_run if is_special else legacy_run)(physical,soil,weather,plants,events,list(selected),**kwargs)
        products=self._products(raw,selected,plants,r)
        raw['product_harvests']=products
        finance=self._finance(r,raw,plants,names)
        area=area_ha(r.farm.area,r.farm.area_unit)
        daily=raw['daily'];initial=raw['initial_state']['ecosystem']['soil'];final=raw['final_state']['ecosystem'];s=final['soil']
        water={key:float(sum(d.get(key,0.)for d in daily))for key in ['transpiration_mm','evaporation_mm','irrigation_mm','rain_mm','runoff_mm','drainage_mm']}
        outcomes=[ProductOutcome(crop_id=reverse[h['crop']],product_id=h['product_id'],harvested=h['date'],
            product_dry_kg_ha=h['product_dry_kg_ha'],total_product_dry_kg=h['product_dry_kg_ha']*area,
            product_N_kg_ha=h.get('product_N_kg_ha'),cohort_id=h.get('cohort_id'))for h in products]
        if finance.detail:
            # A lot can contain simultaneous source cohorts. Divide only by
            # their actual dry share, never duplicate a converted lot mass.
            for product in outcomes:
                lots=[l for l in finance.detail['inventory']if l['product_id']==product.product_id and l['harvested']==product.harvested]
                source=sum(p.product_dry_kg_ha for p in outcomes if p.product_id==product.product_id and p.harvested==product.harvested)
                product.saleable_mass_kg=sum(l['produced_kg']for l in lots)*product.product_dry_kg_ha/source if source>0 else 0.
        seasonal={}
        for h in outcomes:
            key=(h.harvested[:4],h.crop_id,h.product_id);seasonal[key]=seasonal.get(key,0.)+h.product_dry_kg_ha
        stages=[];last={}
        traces=raw['final_state']['diagnostics'].get('physiology_trace',[])or raw['final_state']['diagnostics'].get('growth_days',[])
        for row in traces:
            crop=row['crop'];stage=row['stage']
            if last.get(crop)!=stage:stages.append({'date':row['date'],'crop_id':reverse.get(crop,crop),'stage':stage,'origin':'simulated'});last[crop]=stage
        active=final['plant'];active_id=reverse.get(active['planting']['crop'])if active else None
        warnings=validation.warnings
        if not products or not any(p.product_dry_kg_ha>0 for p in outcomes):warnings.append(Issue(field='agriculture.products',code='no_positive_harvest',message='Execution completed with no positive mature product; not a server failure or invented yield'))
        ecosystem=None
        if living:
            ecological=['weed_water_consumed_mm','weed_uptake_N_kg_ha','herbivore_consumed_C_kg_ha','predator_consumed_C_kg_ha',
                'pathogen_necrotic_C_kg_ha','residue_consumed_C_kg_ha','respired_c_kg_ha','mineralized_n_kg_ha','immobilized_n_kg_ha']
            ecosystem={'status':'experimental_hypothetical','fluxes':{k:sum(d.get(k,0.)for d in daily)for k in ecological},
                'initialization':'Explicit adopted hypothetical guild stocks reclassified from supplied humus, not measured abundance',
                'guild_state':raw['final_state'].get('guild_state'),'field_population_counts':None,'causal_yield_effect':None,
                'new_crop_host_effects':'disabled unless independently supported; no universal host assumption'}
        numerical={'engine':validation.engine,'mode':r.mode,'products':[p.model_dump()for p in outcomes],
            'initial_soil':initial,'final_soil':s,'water':water,'finance':finance.model_dump(),'ecosystem':ecosystem}
        result=SimulationResult(run_id=run_id,created_at=created_at,engine_version=VERSION,selected_engine=validation.engine,
            scenario_id=r.scenario_id,mode=r.mode,area_ha=area,crops=[{'crop_id':p.crop_id,'subtype':p.subtype,
                'profile_status':self.get_crop(p.crop_id)['implementation_status']}for p in r.plantings],
            units={'product_dry':'kg dry/ha','total_product_dry':'kg dry','water':'mm','soil_N':'kg N/ha','soil_C':'kg C/ha'},
            input_provenance=[r.soil.source.model_dump(mode='json'),weather_meta,*[m.source.model_dump(mode='json')for m in r.management]],
            validation_status='experimental_scenario_not_independent_field_validation',parameter_evidence='preserved_reference'if validation.engine=='preserved_reference'else'Tier_C_D_transfers_and_hypotheses',
            warnings=warnings,agriculture=Agriculture(products=outcomes,
                seasonal_products=[{'year':int(k[0]),'crop_id':k[1],'product_id':k[2],'product_dry_kg_ha':v}for k,v in sorted(seasonal.items())],
                planting_dates=[p.model_dump(mode='json')for p in r.plantings],developmental_stages=stages,
                water=water,initial_soil=initial,final_soil=s,active_crop_at_end=active_id,
                crop_survival_status='living_at_end'if active else'harvested_or_terminated',
                terminal_events=[{'crop_id':reverse.get(h['crop'],h['crop']),'date':h['harvested'],'reason':h.get('reason')}for h in raw['harvests']],
                conservation=raw['conservation']),finance=finance,ecosystem=ecosystem,
            timeseries_url=f'/api/v1/simulations/{run_id}/timeseries',artifacts={'research_archive':f'/api/v1/simulations/{run_id}/artifacts'},
            numerical_sha256=digest(numerical))
        if output:
            output=Path(output);output.mkdir(parents=True,exist_ok=True)
            write_atomic(output/'result.json',result.model_dump(mode='json'))
            write_atomic(output/'resolved_input.json',r.model_dump(mode='json'))
            with (output/'research.json.gz').open('wb')as stream:
                with gzip.GzipFile(filename='',fileobj=stream,mode='wb',mtime=0)as zipped:zipped.write(canonical(safe_json(raw)))
            series=[]
            keys=['transpiration_mm','evaporation_mm','irrigation_mm','rain_mm','drainage_mm','runoff_mm','soil_water_mm','soil_mineral_n_kg_ha',
                'soil_organic_c_kg_ha','residue_c_kg_ha','plant_biomass_dry_kg_ha','lai','water_stress_factor','n_growth_factor',
                'mineralized_n_kg_ha','respired_c_kg_ha']
            for d in daily:series.append({'date':d['date'],'crop_id':reverse.get(d['crop']),**{k:d.get(k)for k in keys}})
            write_atomic(output/'timeseries.json',series)
        return result

    def _products(self,raw,selected,plants,r):
        by_name={p['crop_parameters']['name']:key for key,p in selected.items()};result=[]
        for h in raw['product_harvests']:
            key=by_name[h['crop']];cap=self.get_crop(key)
            result.append({**h,'product_id':cap['product_id']})
        return result

    def _finance(self,r,raw,plants,names):
        if not r.finance:return FinancialOutcome(status='financial_data_unavailable',missing=['price/cost/product conversion/market and sale-plan inputs'])
        from ..finance.schema import Farm as F,Price as Q,CostItem,MarketAccess
        from ..finance.events import allocate_season_costs,evaluate_product_events
        from ..physiology.finance import finance_events
        f=r.finance;lots=finance_events(raw,plants)
        if not lots:return FinancialOutcome(status='financial_data_unavailable',missing=['No executed product export'])
        def source(s):return {**s.model_dump(mode='json'),'available_at':str(f.decision_date)}
        specs=[{'crop':names[p.crop_id],'product_id':p.product_id,'commodity_class':p.commodity_class,'grade':p.grade,
            'marketed_fraction':p.marketed_fraction,'dry_matter_fraction':p.dry_matter_fraction,'source':source(p.source)}for p in f.products]
        by_product={p.product_id:p for p in f.products}
        qs=[Q(q.identifier,q.product_id,by_product[q.product_id].commodity_class,q.amount,f.currency,q.unit,'assumed_sales',
            r.farm.location.country,r.farm.location.state,r.farm.location.county,'Explicit caller-declared market',str(q.date),str(q.date),
            by_product[q.product_id].grade,'Caller-declared conditional sale basis','farmer_supplied',source(q.source))for q in f.prices]
        markets=[MarketAccess(m.identifier,m.product_id,m.status,m.buyer_id,m.buyer_location,by_product[m.product_id].commodity_class,
            by_product[m.product_id].grade,m.max_kg,str(r.start),str(r.end),'farmer_declared_contract'if m.status=='confirmed'else'no_contract',source(m.source))for m in f.markets]
        costs={}
        for c in f.costs:
            key=f'{names[c.crop_id]}:{c.year}'
            costs.setdefault(key,[]).append(CostItem(c.identifier,c.category,c.covers,names[c.crop_id],c.amount,c.unit,f.currency,
                c.accounting,c.cash,'farmer_supplied',source(c.source),expense_date=str(c.expense_date),
                payment_date=str(c.payment_date)if c.payment_date else None,economic=c.economic))
        required={f'{h["crop"]}:{h["harvested"][:4]}'for h in lots}
        missing=[]
        if not f.prices:missing.append('compatible prices')
        if set(costs)!=required:missing.append('explicit cost accounts for every harvested crop/year')
        if not markets:missing.append('market access')
        if not f.sales:missing.append('explicit sale plan')
        if missing:return FinancialOutcome(status='financial_data_unavailable',missing=missing,currency=f.currency)
        annual_water={}
        for d in raw['daily']:
            y=int(d['date'][:4]);annual_water[y]=annual_water.get(y,0.)+d.get('irrigation_mm',0.)*10.*(r.farm.area if r.farm.area_unit=='ha'else r.farm.area*.40468564224)
        accounts,water,allocation=allocate_season_costs(lots,costs,irrigation_by_year=annual_water,
            source={'description':'Explicit API allocation by actual produced dry mass; annual fixed cost charged once'})
        sales=[]
        for s in f.sales:
            if f.sale_timing=='on_harvest':
                for h in lots:
                    if h['product_id']==s.product_id:sales.append({'date':h['harvested'],'lot_id':h['lot_id'],**s.model_dump(exclude={'date'}),'source':{'description':'Caller explicitly chose same-day harvest sales'},'allow_assumed_access':True})
            elif s.date:sales.append({**s.model_dump(mode='json'),'source':{'description':'Caller-declared dated sale plan'},'allow_assumed_access':True})
            else:raise ServiceError('sale_date_required','Explicit sale plan needs dates','finance.sales')
        farm=F(r.farm.location.country,r.farm.location.state,r.farm.location.county,r.farm.area,r.farm.area_unit,f.currency,r.start.year,
            [],any(annual_water.values()),'Explicit supplied soil','Conditional simulation enterprise',{'description':'API farm inputs'})
        detail=evaluate_product_events(harvests=lots,product_specs=specs,costs_by_lot=accounts,irrigation_by_lot=water,allocation=allocation,
            farm=farm,prices=qs,markets=markets,sales=sales,scenario={'id':'supplied','price_multiplier':1.,'cost_multiplier':1.,
            'marketability_multiplier':1.,'probability':None,'source':{'description':'Explicit conditional price/cost scenario'}},
            decision_date=str(f.decision_date),start=str(r.start),end=str(r.end),irrigation_by_year=annual_water,
            discount_rate=f.discount_rate,opening_cash=f.opening_cash,yield_origin='agrosim_living'if r.mode=='experimental_living'else'agrosim_stable',
            storage={'initial_fraction':f.storage_fraction,'capacity_kg':f.storage_capacity_kg,'daily_loss_fraction':f.storage_daily_loss_fraction,
                'source':source(f.storage_source)if f.storage_source else None})
        detail['conditional_ranking_eligible']=False;detail['yield_support']='API preserved/reference or experimental profile; inspect result.selected_engine'
        return FinancialOutcome(status='evaluated_conditional'if not detail['problems']else'partially_evaluated',missing=detail['problems'],currency=f.currency,
            revenue=detail['gross_sales_revenue'],operating_cost=detail['total_operating_costs'],full_economic_cost=detail['full_economic_costs'],
            net_return=detail['return_above_full_economic_costs'],net_cash_flow=detail['net_cash_flow'],detail=detail)

    def validate_comparison(self,request):
        resolved=[self.prepare(r)for r in request.scenarios]
        for r in resolved:
            v=self.validate_simulation(r)
            if not v.valid:raise ServiceError('invalid_comparison_scenario','Every comparison scenario must validate','scenarios')
        first=resolved[0]
        for r in resolved[1:]:
            if r.farm!=first.farm or r.start!=first.start or r.end!=first.end or r.soil!=first.soil or r.weather!=first.weather:
                raise ServiceError('incompatible_comparison','Comparisons require identical farm, horizon, initial soil and weather; supply matched inline inputs','scenarios')
            if (r.finance is None)!=(first.finance is None):raise ServiceError('incompatible_finance','All compared scenarios need compatible finance support or all omit it','scenarios')
            if r.finance and(r.finance.currency!=first.finance.currency or r.finance.discount_rate!=first.finance.discount_rate):raise ServiceError('incompatible_finance','Currency/discount basis must match','scenarios')
        return resolved

    def compare_scenarios(self,request,*,run_id='direct',created_at='unassigned',output=None):
        resolved=self.validate_comparison(request);results=[]
        for i,r in enumerate(resolved):results.append(self.run_simulation(r,run_id=run_id+'_'+str(i),created_at=created_at,
            output=Path(output)/('scenario_'+str(i))if output else None))
        for i,result in enumerate(results):
            result.timeseries_url=f'/api/v1/simulations/{run_id}/timeseries?scenario={i}'
            result.artifacts={'research_archive':f'/api/v1/simulations/{run_id}/artifacts?scenario={i}'}
        ranking=None;reason='No ranking requested; unlike products are kept separate.'
        if request.ranking=='conditional_financial':
            if all(x.finance.status=='evaluated_conditional'and x.finance.net_return is not None for x in results):
                covers=[sorted({c for line in x.finance.detail['cost_breakdown']for c in line.get('covers',[])})for x in results]
                if all(c==covers[0]for c in covers):
                    ranking=sorted([{'scenario_id':x.scenario_id,'conditional_net_return':x.finance.net_return,
                        'currency':x.finance.currency,'evidence':'conditional supplied scenario, not optimal rotation'}for x in results],key=lambda x:x['conditional_net_return'],reverse=True);reason=None
                else:reason='Cost coverage differs; conditional ranking unsupported.'
            else:reason='Complete comparable economic inputs unavailable; no ranking or optimum claimed.'
        value=ComparisonResult(run_id=run_id,scenarios=results,comparison_basis={'matched_environment':True,'product_specific_outputs':True,
            'management':'Explicit scenario-specific definitions; differences not inferred as isolated causal rotation effects',
            'independent_rotation_accuracy':None},conditional_ranking=ranking,ranking_unavailable_reason=reason)
        if output:write_atomic(Path(output)/'result.json',value.model_dump(mode='json'))
        return value

def write_atomic(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp');temporary.write_bytes(canonical(value));temporary.replace(path)
