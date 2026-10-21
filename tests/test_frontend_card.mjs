/* Run with: node tests/test_frontend_card.mjs */
const {default: assert} = await import('node:assert/strict');
const fs = await import('node:fs');
const vm = await import('node:vm');

class Element {
  constructor(tag = 'element') { this.tag = tag; this.children = []; this.attributes = {}; this.style = {}; }
  append(child) { this.children.push(child); child.parentNode=this; }
  replaceChildren() { this.children = []; }
  attachShadow() { this.shadowRoot = new Element('shadow'); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.handlers ??= {}; this.handlers[name] = handler; }
  querySelector(selector) {
    this.selectors ??= new Map();
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element(selector));
    return this.selectors.get(selector);
  }
}
const elements = new Map();
const timers = new Map();
let timerId = 0;
const context = vm.createContext({
  HTMLElement: Element,
  document: {createElement: tag => new Element(tag), createElementNS: (ns, tag) => new Element(tag)},
  customElements: {get: name => elements.get(name), define: (name, value) => elements.set(name, value),
    whenDefined: name => elements.has(name) ? Promise.resolve() : Promise.reject(new Error('Not loaded'))},
  window: {}, Intl,
  getComputedStyle: () => ({getPropertyValue: () => ''}),
  setInterval: (callback, period) => { const id=++timerId; timers.set(id,{callback,period}); return id; },
  clearInterval: id => timers.delete(id),
  setTimeout, clearTimeout,
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

const rawSamples = context.samsungEHSRawSamples;
const now = Date.now(), start = now-86400000;
const raw = [
  {s:'1',lu:(now-3000)/1000}, {s:'9',lu:(now-2000)/1000},
  {s:'-2',lu:(now-1000)/1000}, {s:'0',lu:now/1000},
  {s:'unavailable',lu:(now-500)/1000}, {s:'NaN',lu:(now-400)/1000},
  {state:'3',last_updated:new Date(now-2500).toISOString()},
  {s:'99',lu:(now+3600000)/1000}, null,
];
const parsed = rawSamples(raw,start,now);
assert.deepEqual(Array.from(parsed.filter(row=>row.value!==null),row=>row.value),[1,3,9,-2,0]);
assert.equal(parsed.filter(row=>row.value===null).length,2);
const Bars = elements.get('samsung-ehs-history-bar-card');
const bars = new Bars();
bars.setConfig({entities:[{entity:'sensor.power',name:'Power'},{entity:'sensor.heat',name:'Heat'}]});
let requests = 0, request;
bars._hass = {language:'en',states:{'sensor.power':{attributes:{unit_of_measurement:'kW'}},
  'sensor.heat':{attributes:{unit_of_measurement:'kW'}}},callWS:async args => {
    requests++; request=args; return {'sensor.power':raw,'sensor.heat':[{s:'4',lu:(now-3000)/1000}]};
  }};
bars._connected = true;
let helpersLoaded=0, nativeModuleLoads=0;
context.window.loadCardHelpers=async()=>{helpersLoaded++; return {createCardElement(config){
  assert.equal(config.type,'history-graph'); nativeModuleLoads++; elements.set('ha-chart-base',class {});
}};};
await bars._fetch();
assert.equal(helpersLoaded,1);
assert.equal(nativeModuleLoads,1);
assert.equal(request.type,'history/history_during_period');
assert.equal(request.include_start_time_state,true);
assert.equal(request.period,undefined);
assert.equal(request.statistic_ids,undefined);
const hourly=context.samsungEHSHourlySamples;
assert.equal(bars._history[0].samples.length,Math.ceil(bars._end/3600000)-Math.floor(bars._start/3600000));
assert(bars._info.textContent.includes('Hourly averages'));
assert.equal(bars._chart.tag,'ha-chart-base');
assert.equal(bars._chart.options.tooltip.trigger,'axis');
assert.equal(bars._chart.data.length,2);
assert(bars._chart.data.every(series=>series.type === 'bar' && series.stack === 'readings'));
for (let index=0; index<2; index++) {
  assert.deepEqual(Array.from(bars._chart.data[index].data,point=>Array.from(point)),
    Array.from(bars._history[index].samples,point=>[point.time,point.value]));
}
assert(bars._chart.data[0].data.some(point=>point[1]!==null));
assert(bars._chart.data[0].data.some(point=>point[1]===null));
assert.notEqual(bars._chart.data[0].color,bars._chart.data[1].color);
assert.deepEqual(Array.from(bars._chart.data[0].data,point=>point[0]),
  Array.from(bars._chart.data[1].data,point=>point[0]));
assert(bars._chart.data.every(series=>series.sampling === undefined));
bars.hass = bars._hass;
assert.equal(requests,1); // State refreshes do not refetch the full history.
const hover=bars._history[0].samples.find(point=>point.value!==null).time;
const tip=bars._chart.options.tooltip.formatter([{axisValue:hover}]);
assert(flatten(tip).some(node=>node.textContent?.includes('Power: ') && node.textContent.includes('kW')));
assert(flatten(tip).some(node=>node.textContent?.includes('Heat: 4 kW')));
assert(flatten(tip).some(node=>node.tag==='ha-chart-tooltip-marker'));
assert(flatten(tip).some(node=>node.textContent?.includes('hourly average')));
bars._chart.handlers['dataset-hidden']({detail:{id:'sensor.heat'}});
const hiddenTip=bars._tooltip([{axisValue:hover}]);
assert(!flatten(hiddenTip).some(node=>node.textContent?.includes('Heat:')));
bars._chart.handlers['dataset-unhidden']({detail:{id:'sensor.heat'}});
const unavailableTip=bars._tooltip([{axisValue:bars._history[0].samples[0].time}]);
assert(flatten(unavailableTip).some(node=>node.textContent?.includes('Power: unavailable')));
bars.connectedCallback();
assert.equal(timers.size,1);
bars.disconnectedCallback();
assert.equal(timers.size,0);
const failedBars = new Bars();
failedBars.setConfig({entities:[{entity:'sensor.power'}]});
failedBars._connected=true;
let failures=0;
failedBars._hass={states:{},callWS:async()=>{ failures++; throw new Error('denied'); }};
await failedBars._fetch();
failedBars.hass=failedBars._hass;
assert.equal(failures,1);
assert(failedBars._info.textContent.includes('Retrying'));
const staleBars = new Bars();
staleBars.setConfig({entities:[{entity:'sensor.power'}]});
staleBars._connected=true;
let resolveHistory;
staleBars._hass={states:{},callWS:()=>new Promise(resolve=>{resolveHistory=resolve;})};
const pendingHistory=staleBars._fetch();
await Promise.resolve(); // Native loader finishes before the history request starts.
staleBars.disconnectedCallback();
resolveHistory({'sensor.power':raw});
await pendingHistory;
assert.equal(staleBars._history,null);
console.log('Hourly-history bars: native renderer, aligned means, shared tooltip, refresh cleanup and failures passed');

const hour=3600000;
const means=(samples,start,end)=>Array.from(hourly(samples,start,end),p=>p.value);
// Uneven reporting must not bias the mean: 10 for 45 minutes, 2 for 15.
assert.deepEqual(means([{time:0,value:10},{time:hour*.75,value:2}],0,hour),[8]);
// A state crossing hour boundaries contributes to each hour; signed values and zero remain valid.
assert.deepEqual(means([{time:0,value:4},{time:hour*1.5,value:-4}],0,hour*3),[4,0,-4]);
assert.deepEqual(means([{time:0,value:0}],0,hour),[0]);
// Unknown stops the hold. Missing duration is excluded, not counted as zero.
assert.deepEqual(means([{time:0,value:4},{time:hour*.5,value:null},
  {time:hour*2.5,value:8}],0,hour*3),[4,null,8]);
assert.deepEqual(means([],0,hour*2),[null,null]);
// No state is invented before the first reading, and the current partial hour is included.
assert.deepEqual(means([{time:hour*.75,value:6}],0,hour*1.25),[6,6]);
assert.deepEqual(means([{time:hour*.25,value:2},{time:hour*.5,value:6}],hour*.25,hour*.75),[4]);
// Simultaneous updates have no duration; the last update is the state held forward.
assert.deepEqual(means([{time:0,value:100},{time:0,value:2}],0,hour),[2]);
// A Recorder start-state predating the requested window is retained as its baseline.
const baseline=rawSamples([{s:'4',lu:0},{s:'8',lu:hour*1.5/1000}],hour,hour*2);
assert.deepEqual(means(baseline,hour,hour*2),[6]);
assert.equal(baseline[0].time,hour);
console.log('Hourly means: duration weighting, boundaries, partial hours, gaps, zero, signed values and baseline passed');
