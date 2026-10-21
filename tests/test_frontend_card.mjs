/* Run with: node tests/test_frontend_card.mjs */
const {default: assert} = await import('node:assert/strict');
const fs = await import('node:fs');
const vm = await import('node:vm');

class Element {
  constructor(tag = 'element') { this.tag = tag; this.children = []; this.attributes = {}; this.style = {}; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  attachShadow() { this.shadowRoot = new Element('shadow'); }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelector(selector) {
    this.selectors ??= new Map();
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element(selector));
    return this.selectors.get(selector);
  }
}
const elements = new Map();
const context = vm.createContext({
  HTMLElement: Element,
  document: {createElement: tag => new Element(tag), createElementNS: (ns, tag) => new Element(tag)},
  customElements: {get: name => elements.get(name), define: (name, value) => elements.set(name, value)},
  window: {}, Intl,
});
vm.runInContext(fs.readFileSync(new URL('../custom_components/samsung_ehs_monitor/frontend/daily-cop-card.js', import.meta.url), 'utf8'), context);
const Card = elements.get('samsung-ehs-daily-cop-card');
const flatten = node => [node, ...node.children.flatMap(flatten)];
const valid = {date:'2026-10-20', heat_kwh:8, electricity_kwh:2, cop:4,
  coverage:100, complete:false, today:true};
let cases = 0;
for (const history of [undefined, null, false, 'bad', {}, [],
  [null, 2, {}, {date:42}, {date:'2026-10-20', cop:'bad', coverage:'bad'}],
  [{...valid, heat_kwh:null, electricity_kwh:'bad', coverage:{}}],
  [{...valid, cop:-3, coverage:Infinity}], [valid]]) {
  const card = new Card();
  card.setConfig({entity:'sensor.daily'});
  assert.doesNotThrow(() => { card.hass = {states:{'sensor.daily':{
    attributes:{power_source:'sensor.meter', daily_history:history}, state:'4'}}}; });
  for (const node of flatten(card.shadowRoot)) {
    for (const value of Object.values(node.attributes)) {
      assert.doesNotMatch(String(value), /NaN|Infinity/);
    }
  }
  cases++;
}
const card = new Card();
card.setConfig({entity:'sensor.missing'});
card.hass = {states:{}};
assert(flatten(card.shadowRoot).some(node => node.textContent === 'Entity not found: sensor.missing'));
card.setConfig({entity:'sensor.daily'});
card.hass = {states:{'sensor.daily':{attributes:{power_source:'sensor.meter', daily_history:[valid]}, state:'4'}}};
assert(flatten(card.shadowRoot).some(node => node.textContent === '4.00'));
console.log(`Frontend card checks passed (${cases} history cases, missing entity and reconfiguration)`);

const waterLaw = context.samsungEHSWaterLaw;
const settings = {outdoor_cold:-10, outdoor_warm:15, wl1_cold:40, wl1_warm:25,
  wl2_cold:50, wl2_warm:35, selected:1};
let model = waterLaw(settings);
for (const [outdoor, expected] of [[-20,40],[-10,40],[2.5,32.5],[15,25],[25,25]]) {
  assert.equal(model.active.at(outdoor), expected);
}
assert.equal(waterLaw({...settings, selected:2}).active.at(5), 41);
assert.equal(waterLaw({...settings, selected:null}).active, undefined);
assert.equal(waterLaw({...settings, selected:2, wl2_cold:null}).active, undefined);
for (const changed of [{outdoor_cold:null}, {outdoor_cold:15}, {outdoor_cold:20},
  {outdoor_cold:-1e308}, {outdoor_warm:NaN}, {wl1_cold:20, wl2_cold:null},
  {wl1_cold:Infinity, wl2_warm:null}]) {
  assert(waterLaw({...settings, ...changed}).error);
}
const Weather = elements.get('samsung-ehs-weather-curve-card');
const weatherConfig = {entities:Object.keys({...settings, outdoor:5, actual_flow:34})
  .map(role => ({entity:`sensor.${role}`, role}))};
function weatherState(changes = {}) {
  return {states:Object.fromEntries(Object.entries({...settings, outdoor:5, actual_flow:34, ...changes})
    .map(([role, value]) => [`sensor.${role}`, {state:String(value)}]))};
}
const weather = new Weather();
weather.setConfig(weatherConfig);
weather.hass = weatherState();
let nodes = flatten(weather.shadowRoot);
assert.equal(nodes.filter(node => node.attributes['data-curve']).length, 2);
assert.equal(nodes.find(node => node.attributes['data-marker'] === 'target').attributes['data-value'], '31');
assert.equal(nodes.find(node => node.attributes['data-marker'] === 'actual').attributes['data-value'], '34');
weather.hass = weatherState({selected:2, outdoor:-20});
nodes = flatten(weather.shadowRoot);
assert.equal(nodes.find(node => node.attributes['data-marker'] === 'target').attributes['data-value'], '50');
weather.hass = weatherState({selected:'unavailable'});
assert(!flatten(weather.shadowRoot).some(node => node.attributes['data-marker'] === 'target'));
for (const changes of [{outdoor:'unknown'}, {actual_flow:'unavailable'}, {outdoor_cold:'bad'},
  {outdoor_cold:'1e308'}, {outdoor_warm:Infinity}, {outdoor_cold:15}, {wl1_warm:null, wl2_cold:null}]) {
  assert.doesNotThrow(() => { weather.hass = weatherState(changes); });
  for (const node of flatten(weather.shadowRoot)) {
    for (const value of Object.values(node.attributes)) assert.doesNotMatch(String(value), /NaN|Infinity/);
  }
}
weather.hass = {states:{}};
assert(flatten(weather.shadowRoot).some(node => node.textContent?.includes('FSV 2011 and 2012')));
console.log('Weather curve checks passed: interpolation, clamping, WL selection, current markers and missing/invalid settings');

const fahrenheit = weatherState();
for (const [role, value] of Object.entries({...settings, outdoor:5, actual_flow:34})) {
  if (role === 'selected') continue;
  fahrenheit.states[`sensor.${role}`] = {state:String(value*9/5+32), attributes:{unit_of_measurement:'°F'}};
}
weather.hass = fahrenheit;
assert(Math.abs(Number(flatten(weather.shadowRoot).find(node => node.attributes['data-marker'] === 'target').attributes['data-value'])-31) < 1e-9);
console.log('Weather curve Fahrenheit-to-Celsius normalization passed');

const Compressor = elements.get('samsung-ehs-compressor-row');
const compressor = new Compressor();
compressor.setConfig({entity:'sensor.frequency', discharge_entity:'sensor.discharge', pump_entity:'sensor.pump'});
const compressorHass = {language:'en', states:{
  'sensor.frequency':{state:'40',attributes:{unit_of_measurement:'Hz'}},
  'sensor.discharge':{state:'37',attributes:{unit_of_measurement:'°C'}},
  'sensor.pump':{state:'65',attributes:{unit_of_measurement:'%'}},
}};
compressor.hass = compressorHass;
assert.equal(compressor.shadowRoot.querySelector('.values').textContent, '40Hz / 37°C / 65%');
assert.equal(compressor.shadowRoot.querySelector('.name').textContent, 'Compressor');
compressorHass.states['sensor.frequency'].state = '0';
compressorHass.states['sensor.discharge'].state = 'unavailable';
compressorHass.states['sensor.pump'].state = '0';
compressor.hass = compressorHass;
assert.equal(compressor.shadowRoot.querySelector('.values').textContent, '0Hz / — / 0%');
delete compressorHass.states['sensor.pump'];
compressorHass.states['sensor.frequency'].state = 'NaN';
compressor.hass = compressorHass;
assert.equal(compressor.shadowRoot.querySelector('.values').textContent, '— / — / —');
const Temperatures = elements.get('samsung-ehs-flow-return-row');
const temperatures = new Temperatures();
temperatures.setConfig({entity:'sensor.flow',return_entity:'sensor.return'});
temperatures.hass = {language:'en',states:{'sensor.flow':{state:'31'},'sensor.return':{state:'27'}}};
assert.equal(temperatures.shadowRoot.querySelector('.values').textContent, '31°C / 27°C');
console.log('Compressor and temperature row formatting checks passed, including zero/missing readings');

const PowerRow = elements.get('samsung-ehs-power-row');
const powerRow = new PowerRow();
powerRow.setConfig({entity:'sensor.power',return_entity:'sensor.day_energy'});
const powerHass = {language:'en',states:{'sensor.power':{state:'1200',attributes:{unit_of_measurement:'W'}},
  'sensor.day_energy':{state:'12.345',attributes:{unit_of_measurement:'kWh'}}}};
powerRow.hass = powerHass;
assert.equal(powerRow.shadowRoot.querySelector('.name').textContent, 'Power instant/day total');
assert.equal(powerRow.shadowRoot.querySelector('.values').textContent, '1,200W / 12.35kWh');
powerHass.states['sensor.power'].state = '0';
powerHass.states['sensor.day_energy'].state = 'unavailable';
powerRow.hass = powerHass;
assert.equal(powerRow.shadowRoot.querySelector('.values').textContent, '0W / —');
console.log('Power instant/day total formatting checks passed');

const PerformanceRow = elements.get('samsung-ehs-performance-row');
for (const [metric, instant, day, expected] of [
  ['power','1200','12.345','1,200W / 12.35kWh'],
  ['heat','4.875','38.567','4.88kW / 38.57kWh'],
  ['heat','-2.75','30.25','-2.75kW / 30.25kWh'],
  ['cop','4.126','3.375','4.13 / 3.38'],
]) {
  const row = new PerformanceRow();
  row.setConfig({metric,entity:'sensor.instant',return_entity:'sensor.today'});
  const hass = {language:'en',states:{'sensor.instant':{state:instant},'sensor.today':{state:day}}};
  row.hass = hass;
  assert.equal(row.shadowRoot.querySelector('.values').textContent, expected);
  hass.states['sensor.today'].state = 'unknown';
  row.hass = hass;
  assert(row.shadowRoot.querySelector('.values').textContent.endsWith(' / —'));
}
console.log('Paired instantaneous/daily power, signed heat and unitless COP checks passed');
