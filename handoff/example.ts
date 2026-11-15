import {AgroSimClient,SimulationRequest} from "./client.js";
const client = new AgroSimClient(process.env.AGROSIM_URL ?? "http://127.0.0.1:8000",process.env.AGROSIM_API_TOKEN);
const request:SimulationRequest = {
  farm:{location:{latitude:40,longitude:-88,country:"US",state:"IL"},area:1,area_unit:"ha"},
  reference_scenario_id:"controlled_corn_v1",adopt_reference_assumptions:true,
  scenario_id:"offline_corn",mode:"standard",
};
const catalog=await client.listCrops();if(catalog.count!==88)throw new Error("Incomplete crop catalog");
const requirements=await client.requirements(request.farm,["corn"]);
const validation=await client.validate(request);if(!validation.valid)throw new Error(JSON.stringify(validation.errors));
const job=await client.submit(request);
const done=await client.wait(job.run_id);if(done.status!=="completed")throw new Error(JSON.stringify(done.error));
const result=await client.results(job.run_id);
console.log(JSON.stringify({catalog:catalog.count,requirements_reported:Boolean(requirements.missing_inputs),run_id:job.run_id,result},null,2));
