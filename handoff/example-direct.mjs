import {runAgroSim} from './direct.mjs';
const python = process.env.AGROSIM_PYTHON || 'python3';
const crops = await runAgroSim(null, {command: 'crops', python});
if (crops.count !== 88) throw new Error('Incomplete catalog');
const result = await runAgroSim({
  farm: {location: {latitude: 40, longitude: -88, country: 'US', state: 'IL'}, area: 1, area_unit: 'ha'},
  reference_scenario_id: 'controlled_corn_v1', adopt_reference_assumptions: true,
  scenario_id: 'offline_corn', mode: 'standard',
}, {python});
console.log(JSON.stringify({catalog: crops.count, result}, null, 2));
