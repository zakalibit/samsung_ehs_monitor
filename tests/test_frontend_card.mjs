/* Run with: node tests/test_frontend_card.mjs */
const {default: assert} = await import('node:assert/strict');
const fs = await import('node:fs');
const vm = await import('node:vm');

class Element {
  constructor(tag = 'element') { this.tag = tag; this.children = []; this.attributes = {}; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  attachShadow() { this.shadowRoot = new Element('shadow'); }
  setAttribute(name, value) { this.attributes[name] = value; }
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
