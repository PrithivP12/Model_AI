/** AgroSim V1 fetch client. No frontend/framework dependency. */
export interface Source { origin: "observed" | "user_supplied" | "source_reference" | "regional_proxy" | "hypothetical"; description: string; source_id?: string; }
export interface Farm { location: { latitude: number; longitude: number; country?: string; state?: string; county?: string }; area: number; area_unit?: "ha" | "acre"; }
export interface Issue { field: string; code: string; message: string; }
export interface Job { run_id: string; kind: "simulation" | "comparison"; status: "queued" | "running" | "completed" | "failed" | "canceled"; created_at: string; updated_at: string; progress: number | null; error: Issue | null; result_url: string | null; }
export interface Crop { crop_id: string; name: string; requires_subtype: boolean; subtypes: Record<string,string>; engine: string; lifecycle: string; implementation_status: "reference" | "experimental"; validation_status: string; product_id: string; limitations: string[]; }
export interface Planting { crop_id: string; subtype?: string; planted: string; harvest_deadline: string; residue_removal_fraction?: number; harvest_mode?: "grain" | "terminate_to_residue"; }
export interface SimulationRequest { farm: Farm; scenario_id?: string; reference_scenario_id?: string; adopt_reference_assumptions?: boolean; allow_experimental_profiles?: boolean; start?: string; end?: string; plantings?: Planting[]; mode?: "standard" | "experimental_living"; living?: { fixture_id: "hypothetical_functional_network_v1"; assumptions: "hypothetical_assumptions_adopted"; enabled?: Record<string,boolean> }; soil?: Record<string,unknown>; weather?: Record<string,unknown>; management?: Record<string,unknown>[]; initial_plant?: Record<string,unknown>; crop_options?: Record<string,unknown>; paddy?: Record<string,unknown>; finance?: Record<string,unknown>; }
export interface Validation { valid: boolean; errors: Issue[]; warnings: Issue[]; engine: string | null; required_inputs: string[]; }
export interface Product { crop_id: string; product_id: string; harvested: string; product_dry_kg_ha: number; total_product_dry_kg: number; product_N_kg_ha: number | null; saleable_mass_kg: number | null; cohort_id: string | null; }
export interface SimulationResult { schema_version: "1.0"; run_id: string; created_at: string; engine_version: string; selected_engine: string; mode: "standard" | "experimental_living"; scenario_id: string; area_ha: number; crops: Record<string,unknown>[]; units: Record<string,string>; input_provenance: Record<string,unknown>[]; validation_status: string; parameter_evidence: string; warnings: Issue[]; agriculture: { products: Product[]; seasonal_products: Record<string,unknown>[]; planting_dates: Record<string,unknown>[]; developmental_stages: Record<string,unknown>[]; water: Record<string,number>; initial_soil: Record<string,unknown>; final_soil: Record<string,unknown>; active_crop_at_end: string | null; crop_survival_status: string; terminal_events: Record<string,unknown>[]; conservation: Record<string,number> }; finance: { status: "evaluated_conditional" | "financial_data_unavailable" | "partially_evaluated"; missing: string[]; currency: string | null; revenue: number | null; operating_cost: number | null; full_economic_cost: number | null; net_return: number | null; net_cash_flow: number | null; detail: Record<string,unknown> | null }; ecosystem: Record<string,unknown> | null; timeseries_url: string; artifacts: Record<string,string>; numerical_sha256: string; independent_accuracy: null; farmer_recommendation: null; }
export interface ComparisonResult { schema_version: "1.0"; run_id: string; scenarios: SimulationResult[]; comparison_basis: Record<string,unknown>; conditional_ranking: Record<string,unknown>[] | null; ranking_unavailable_reason: string | null; farmer_recommendation: null; }
export class AgroSimError extends Error { constructor(public status: number, public issues: Issue[]) { super(issues.map(x=>x.message).join("; ")); } }
export class AgroSimClient {
  constructor(public baseUrl = "http://127.0.0.1:8000", private token?: string) {}
  private async call<T>(path: string, body?: unknown, key?: string): Promise<T> {
    const headers: Record<string,string> = {};
    if(body !== undefined) headers["Content-Type"] = "application/json";
    if(this.token) headers.Authorization = `Bearer ${this.token}`;
    if(key) headers["Idempotency-Key"] = key;
    const r = await fetch(this.baseUrl.replace(/\/$/,"")+path, {method: body === undefined ? "GET" : "POST", headers, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const data = await r.json();
    if(!r.ok) throw new AgroSimError(r.status, data.errors ?? [{field:"server",code:"http_error",message:"Backend request failed"}]);
    return data as T;
  }
  listCrops(): Promise<{count:number;crops:Crop[]}> {return this.call("/api/v1/crops");}
  getCrop(id:string): Promise<Crop> {return this.call(`/api/v1/crops/${encodeURIComponent(id)}`);}
  requirements(farm:Farm,crops:string[]): Promise<Record<string,unknown>> {return this.call("/api/v1/farms/requirements",{farm,crops});}
  validate(request:SimulationRequest): Promise<Validation> {return this.call("/api/v1/simulations/validate",request);}
  submit(request:SimulationRequest,idempotencyKey?:string): Promise<Job> {return this.call("/api/v1/simulations",request,idempotencyKey);}
  status(runId:string): Promise<Job> {return this.call(`/api/v1/simulations/${encodeURIComponent(runId)}`);}
  results(runId:string): Promise<SimulationResult|ComparisonResult> {return this.call(`/api/v1/simulations/${encodeURIComponent(runId)}/results`);}
  cancel(runId:string): Promise<Job> {return this.call(`/api/v1/simulations/${encodeURIComponent(runId)}/cancel`,{});}
  compare(scenarios:SimulationRequest[],ranking:"none"|"conditional_financial"="none",key?:string): Promise<Job> {return this.call("/api/v1/comparisons",{scenarios,ranking},key);}
  timeseries(runId:string,query="aggregation=monthly"):Promise<Record<string,unknown>> {return this.call(`/api/v1/simulations/${encodeURIComponent(runId)}/timeseries?${query}`);}
  async wait(runId:string,timeoutMs=120000):Promise<Job> {
    const end=Date.now()+timeoutMs;
    while(Date.now()<end) {const job=await this.status(runId);if(!["queued","running"].includes(job.status))return job;await new Promise(r=>setTimeout(r,300));}
    throw new Error("Polling timed out; job may still be running. Inspect status or cancel explicitly.");
  }
}
