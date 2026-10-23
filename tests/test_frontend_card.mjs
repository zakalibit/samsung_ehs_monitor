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
const elements = new Map([['home-assistant',class extends Element {}]]);
const timers = new Map();
const timeouts = new Map();
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
  setTimeout: (callback,delay) => { const id=++timerId; timeouts.set(id,{callback,delay}); return id; },
  clearTimeout: id => timeouts.delete(id),
});
const frontendSource=fs.readFileSync(new URL('../custom_components/samsung_ehs_monitor/frontend/daily-cop-card.js', import.meta.url), 'utf8');
vm.runInContext(frontendSource, context);
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

// Color thresholds must be stable at every half-COP boundary, including partial days.
const colorCard = new Card();
colorCard.setConfig({entity:'sensor.daily'});
const colorValues = [0, 2.999, 3, 3.499, 3.5, 4, 4.5, 5, 5.5, 6, 6.5, 20];
colorCard.hass = {states:{'sensor.daily':{attributes:{power_source:'sensor.meter',
  daily_history:colorValues.map((cop, i) => ({...valid, date:`2026-10-${String(i + 1).padStart(2, '0')}`,
    cop, complete:i % 2 === 0}))}}}};
const coloredBars = flatten(colorCard.shadowRoot).filter(node => node.tag === 'rect');
assert.deepEqual(coloredBars.map(node => node.attributes.fill), [
  '#fb8c00', '#fb8c00', '#c8e6c9', '#c8e6c9', '#a5d6a7', '#81c784',
  '#66bb6a', '#4caf50', '#43a047', '#2e9d46', '#168c39', '#168c39']);
for (const [i, bar] of coloredBars.entries()) {
  assert.equal(Number(bar.attributes.width), 15);
  if (i) assert.equal(Number(bar.attributes.x) - Number(coloredBars[i-1].attributes.x)
    - Number(coloredBars[i-1].attributes.width), 20);
}
console.log('Daily COP bars: solid colors, half-COP thresholds, wider bars and 20-pixel gaps passed');

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

// Values and missing FSV 2041 reproduce the supplied SmartThings sample.
const unselected = new Weather();
unselected.setConfig({entities:weatherConfig.entities.filter(row=>row.role!=='selected')});
unselected.hass=weatherState({wl2_cold:45,wl2_warm:30});
const unselectedNodes=flatten(unselected.shadowRoot);
assert.equal(unselectedNodes.filter(node=>node.attributes['data-curve']).length,2);
assert(unselectedNodes.filter(node=>node.attributes['data-curve']).every(node=>node.attributes['stroke-dasharray']==='none'));
const candidates=unselectedNodes.filter(node=>node.attributes['data-marker']==='curve-target');
assert.deepEqual(candidates.map(node=>[node.attributes['data-wl'],node.attributes['data-value']]),[['1','31'],['2','36']]);
assert(!unselectedNodes.some(node=>/active curve.*(unavailable|not reported)/i.test(node.textContent || '')));
assert(unselectedNodes.some(node=>node.textContent?.includes('WL1: 31°C / WL2: 36°C base targets')));
assert(!unselectedNodes.some(node=>node.attributes['data-marker']==='target'));
unselected.hass=weatherState({wl2_cold:45,wl2_warm:30,outdoor:'unknown'});
assert(!flatten(unselected.shadowRoot).some(node=>node.attributes['data-marker']==='curve-target'));
console.log('Sample water-law settings: both curves and target markers render without FSV 2041');

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
assert.equal(Date.parse(request.start_time),context.samsungEHSDayStart(Date.parse(request.end_time)));
assert.equal(bars._chart.options.xAxis.min.getTime(),Date.parse(request.start_time));
assert.equal(request.period,undefined);
assert.equal(request.statistic_ids,undefined);
const hourly=context.samsungEHSHourlySamples;
assert.equal(bars._history[0].samples.length,Math.ceil((bars._end-bars._start)/3600000));
assert(bars._info.textContent.includes('Hourly averages'));
assert.equal(bars._chart.tag,'ha-chart-base');
assert.equal(bars._chart.options.tooltip.trigger,'axis');
assert.equal(bars._chart.data.length,2);
assert(bars._chart.data.every(series=>series.type === 'bar' && series.stack === 'readings'));
for (let index=0; index<2; index++) {
  assert.deepEqual(Array.from(bars._chart.data[index].data,point=>Array.from(point.value)),
    Array.from(bars._history[index].samples,point=>[point.time,point.value]));
}
assert(bars._chart.data[0].data.some(point=>point.value[1]!==null));
assert(bars._chart.data[0].data.some(point=>point.value[1]===null));
assert.notEqual(bars._chart.data[0].color,bars._chart.data[1].color);
assert.deepEqual(Array.from(bars._chart.data[0].data,point=>point.value[0]),
  Array.from(bars._chart.data[1].data,point=>point.value[0]));
assert(bars._chart.data.every(series=>series.sampling === undefined));
bars.hass = bars._hass;
assert.equal(requests,1); // State refreshes do not refetch the full history.
const hover=bars._history[0].samples.find(point=>point.value!==null).time;
const tip=bars._chart.options.tooltip.formatter([{axisValue:hover}]);
assert(flatten(tip).some(node=>node.textContent?.includes('Power: ') && node.textContent.includes('kW')));
assert(flatten(tip).some(node=>node.textContent?.includes('Heat: 4 kW')));
assert(flatten(tip).some(node=>node.tag==='ha-chart-tooltip-marker'));
const tipHeading=tip.children[0].textContent;
assert(tipHeading.endsWith(' · average'));
const tooltipClock=new Intl.DateTimeFormat('en',{hour:'2-digit',minute:'2-digit'});
assert.equal(tipHeading,`${tooltipClock.format(new Date(hover))} – ${tooltipClock.format(new Date(Math.min(hover+3600000,bars._end)))} · average`);
const firstTick=bars._chart.options.xAxis.axisLabel.formatter(bars._start,0);
const laterTick=bars._chart.options.xAxis.axisLabel.formatter(bars._start+3600000,1);
assert(firstTick.includes('\n'));
assert(!laterTick.includes('\n'));
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
assert(failedBars._info.textContent.includes('Retrying in 5 seconds'));
assert.equal(timeouts.get(failedBars._retryTimer).delay,5000);
await failedBars._fetch();
assert.equal(timeouts.get(failedBars._retryTimer).delay,10000);
failedBars.disconnectedCallback();
assert.equal(failedBars._retryTimer,null);
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

const dayStart=context.samsungEHSDayStart;
for (const [now,zone,expected] of [
  ['2026-10-21T14:30:00Z','Europe/Dublin','2026-10-20T23:00:00Z'],
  ['2026-10-25T14:30:00Z','Europe/Dublin','2026-10-24T23:00:00Z'],
  ['2026-03-29T14:30:00Z','Europe/Dublin','2026-03-29T00:00:00Z'],
  ['2026-10-21T01:30:00Z','America/New_York','2026-10-20T04:00:00Z'],
  ['2026-10-21T14:30:00Z','Asia/Kolkata','2026-10-20T18:30:00Z'],
  ['2026-10-21T00:00:00Z','UTC','2026-10-21T00:00:00Z'],
]) assert.equal(dayStart(Date.parse(now),zone),Date.parse(expected));
const rolling=new Bars();
rolling.setConfig({entities:[{entity:'sensor.power'}],hours_to_show:12});
assert.equal(rolling._today,false);
rolling.setConfig({entities:[{entity:'sensor.power'}],period:'day',hours_to_show:12});
assert.equal(rolling._today,true);
console.log('Today default: HA timezone midnight, DST transitions, date boundaries and explicit rolling range passed');

const offsetMidnight=Date.parse('2026-10-20T18:30:00Z');
const offsetHours=hourly([{time:offsetMidnight,value:3}],offsetMidnight,offsetMidnight+hour*2,offsetMidnight);
assert.equal(offsetHours[0].time,offsetMidnight);
assert.equal(offsetHours[1].time,offsetMidnight+hour);

const rounded = [
  {id:'power',stack:'readings',data:[[0,2],[1,-2],[2,3],[3,0]]},
  {id:'heat',stack:'readings',data:[[0,4],[1,-4],[2,null],[3,0]]},
];
const roundCaps=context.samsungEHSRoundBarCaps;
const radius=(series,index)=>Array.from(series.data[index].itemStyle.borderRadius);
roundCaps(rounded,new Set());
assert.deepEqual(radius(rounded[0],0),[0,0,0,0]);
assert.deepEqual(radius(rounded[1],0),[4,4,0,0]);
assert.deepEqual(radius(rounded[0],1),[0,0,0,0]);
assert.deepEqual(radius(rounded[1],1),[0,0,4,4]);
assert.deepEqual(radius(rounded[0],2),[4,4,0,0]);
assert.deepEqual(radius(rounded[1],2),[0,0,0,0]);
assert.equal(rounded[0].data[3].itemStyle.borderWidth,0);
roundCaps(rounded,new Set(['heat']));
assert.deepEqual(radius(rounded[0],0),[4,4,0,0]);
assert.deepEqual(radius(rounded[0],1),[0,0,4,4]);
roundCaps(rounded,new Set());
assert.deepEqual(radius(rounded[0],0),[0,0,0,0]);
assert.deepEqual(radius(rounded[1],0),[4,4,0,0]);
const separate=[{id:'one',data:[[0,1]]},{id:'two',data:[[0,-2]]}];
roundCaps(separate,new Set());
assert.deepEqual(radius(separate[0],0),[4,4,0,0]);
assert.deepEqual(radius(separate[1],0),[0,0,4,4]);
assert(bars._chart.data.every(series=>series.barMaxWidth===50));
console.log('Energy-style caps: positive/negative stack ends, gaps, zero and legend changes passed');

const recovering = new Bars();
recovering.setConfig({entities:[{entity:'sensor.power'}]});
recovering._connected=true;
recovering._hass={states:{},callWS:async()=>({'sensor.power':raw})};
const draw=recovering._draw.bind(recovering);
recovering._draw=()=>{throw new Error('Chart initialization race');};
await recovering._fetch(); // A render failure must not escape as an unhandled rejection.
assert(recovering._info.textContent.includes('Retrying in 5 seconds'));
assert.equal(recovering._pending,false);
recovering._draw=draw;
const scheduled=timeouts.get(recovering._retryTimer);
timeouts.delete(recovering._retryTimer);
scheduled.callback();
for (let i=0;i<10;i++) await Promise.resolve();
assert(recovering._chart);
assert.equal(recovering._retryTimer,null);
assert.equal(recovering._retryDelay,5000);
assert(!recovering._info.textContent.includes('Retrying'));
recovering.disconnectedCallback();
const offline=new Bars();
offline.setConfig({entities:[{entity:'sensor.power'}]});
offline._connected=true;
let offlineRequests=0;
const connectedHass={states:{},connected:true,callWS:async()=>{offlineRequests++;return {};}};
offline.hass={...connectedHass,connected:false};
assert.equal(offlineRequests,0);
assert(offline._info.textContent.includes('reconnect'));
offline.hass=connectedHass;
for (let i=0;i<10;i++) await Promise.resolve();
assert.equal(offlineRequests,1);
offline.disconnectedCallback();
const hung=new Bars();
hung.setConfig({entities:[{entity:'sensor.power'}]});
hung._connected=true;
hung._hass={states:{},callWS:()=>new Promise(()=>{})};
const hungRequest=hung._fetch();
await Promise.resolve();
const deadline=[...timeouts.values()].find(timer=>timer.delay===20000);
assert(deadline);
deadline.callback();
await hungRequest;
assert.equal(hung._pending,false);
assert(hung._info.textContent.includes('Retrying'));
hung.disconnectedCallback();
assert.equal(timeouts.size,0);
console.log('History recovery: draw errors, scheduled retry, backoff, reconnect, request timeout and timer cleanup passed');

// Reproduce HA's startup race: an extra module runs first, then HA replaces
// BOTH the registry and HTMLElement. A native whenDefined promise would hang.
class BeforeHA {}
class AfterHA {}
const earlyElements=new Map();
const readyElements=new Map();
const startupTimers=[];
const startup=vm.createContext({
  HTMLElement:BeforeHA,window:{},
  customElements:{get:name=>earlyElements.get(name),define:(name,ctor)=>earlyElements.set(name,ctor),
    whenDefined:()=>{throw new Error('Must not wait on the obsolete registry');}},
  setTimeout:(callback,delay)=>{assert.equal(delay,50);startupTimers.push(callback);},
});
vm.runInContext(frontendSource,startup);
assert.equal(earlyElements.size,0); // No card definitions before the HA root exists.
assert.equal(startupTimers.length,1);
startupTimers.shift()(); // A slow startup keeps waiting without registering early.
assert.equal(earlyElements.size,0);
assert.equal(startupTimers.length,1);
startup.HTMLElement=AfterHA;
startup.customElements={get:name=>readyElements.get(name),define:(name,ctor)=>readyElements.set(name,ctor)};
readyElements.set('home-assistant',class extends AfterHA {});
startupTimers.shift()();
const expectedCards=['samsung-ehs-daily-cop-card','samsung-ehs-flow-return-row',
  'samsung-ehs-performance-row','samsung-ehs-power-row','samsung-ehs-compressor-row',
  'samsung-ehs-weather-curve-card','samsung-ehs-history-bar-card'];
for (const name of expectedCards) {
  const ctor=readyElements.get(name);
  assert(ctor,`${name} is registered in HA's replacement registry`);
  assert(new ctor() instanceof AfterHA,`${name} uses the replacement HTMLElement`);
  assert(!(new ctor() instanceof BeforeHA));
}
assert.equal(earlyElements.size,0);
assert.equal(startupTimers.length,0);
assert.equal(startup.window.customCards.length,3);
assert.equal(timeouts.size,0); // A dashboard loaded after HA is ready needs no poll.
console.log('Startup registry replacement: every card waits for HA and uses the new registry/HTMLElement');
