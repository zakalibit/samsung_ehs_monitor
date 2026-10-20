/* Daily ratios are supplied by the backend; this card never averages COP samples. */
class SamsungEHSDailyCOPCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error('Specify the Estimated COP today entity');
    this.config = config;
    this.lastState = Symbol();
    if (!this.shadowRoot) this.attachShadow({mode: 'open'});
  }
  getCardSize() { return 5; }
  set hass(hass) {
    if (!this.config) return;
    const state = hass.states[this.config.entity];
    if (state === this.lastState) return;
    this.lastState = state;
    const root = this.shadowRoot;
    root.replaceChildren();
    const el = (tag, text, parent) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (parent) parent.append(node);
      return node;
    };
    const style = el('style', `
      ha-card {padding:20px;color:var(--primary-text-color);}
      h2 {font-size:20px;font-weight:500;margin:0 0 6px;}
      p {font-size:12px;color:var(--secondary-text-color);line-height:1.5;margin:6px 0 14px;}
      .plot {overflow-x:auto;} svg {display:block;min-width:100%;height:245px;}
      text {fill:var(--secondary-text-color);font:11px sans-serif;}
      .value {fill:var(--primary-text-color);font-weight:600;}
      details {font-size:12px;margin-top:14px;} summary {cursor:pointer;}
      table {border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;}
      td,th {padding:8px 3px;text-align:right;border-bottom:1px solid var(--divider-color);}
      td:first-child,th:first-child {text-align:left;}
    `);
    root.append(style);
    const card = el('ha-card', undefined, root);
    el('h2', this.config.title || 'Daily estimated COP', card);
    if (!state) { el('p', `Entity not found: ${this.config.entity}`, card); return; }
    if (!state.attributes.power_source) {
      el('p', 'Select an external power sensor in Samsung EHS Monitor → Configure to collect daily energy.', card);
      return;
    }
    const count = Math.max(1, Math.min(35, Number(this.config.days_to_show) || 14));
    const history = state.attributes.daily_history;
    const rows = (Array.isArray(history) ? history : [])
      .filter(row => row && typeof row === 'object'
        && /^\d{4}-\d{2}-\d{2}$/.test(row.date)
        && typeof row.date === 'string')
      .slice(-count).map(row => ({
        date: row.date,
        heat_kwh: Number.isFinite(row.heat_kwh) ? row.heat_kwh : null,
        electricity_kwh: Number.isFinite(row.electricity_kwh) && row.electricity_kwh >= 0 ? row.electricity_kwh : null,
        cop: Number.isFinite(row.cop) && row.cop >= 0 ? row.cop : null,
        coverage: Number.isFinite(row.coverage) ? Math.max(0, Math.min(100, row.coverage)) : null,
        today: row.today === true,
        complete: row.complete === true,
      }));
    const fixed = (value, places) => Number.isFinite(value) ? value.toFixed(places) : '—';
    if (!rows.length || !rows.some(r => Number.isFinite(r.cop))) {
      el('p', 'Collecting paired heat and electricity readings. Bars appear after valid energy has accumulated.', card);
    }
    el('p', 'Each bar = heat kWh ÷ electrical kWh for that date. Striped bars are incomplete or still in progress; gaps are not counted as zero.', card);
    const plot = el('div', undefined, card); plot.className = 'plot';
    const ns = 'http://www.w3.org/2000/svg';
    const svgEl = (tag, attrs, parent, text) => {
      const node = document.createElementNS(ns, tag);
      for (const [key, val] of Object.entries(attrs || {})) node.setAttribute(key, String(val));
      if (text !== undefined) node.textContent = text;
      if (parent) parent.append(node);
      return node;
    };
    const width = Math.max(360, rows.length * 45 + 45);
    const svg = svgEl('svg', {viewBox:`0 0 ${width} 245`, width, role:'img', 'aria-label':'Daily estimated COP bar chart'}, plot);
    const defs = svgEl('defs', {}, svg);
    const pattern = svgEl('pattern', {id:'partial', width:6, height:6, patternUnits:'userSpaceOnUse', patternTransform:'rotate(45)'}, defs);
    svgEl('rect', {width:6,height:6,fill:'var(--primary-color)',opacity:0.22}, pattern);
    svgEl('line', {x1:0,y1:0,x2:0,y2:6,stroke:'var(--primary-color)','stroke-width':3}, pattern);
    const maximum = Math.max(1, Math.ceil(Math.max(0, ...rows.map(r => Number.isFinite(r.cop) ? r.cop : 0))));
    for (const value of [0, maximum/2, maximum]) {
      const y = 190 - value / maximum * 150;
      svgEl('line',{x1:35,y1:y,x2:width-8,y2:y,stroke:'var(--divider-color)'},svg);
      svgEl('text',{x:27,y:y+4,'text-anchor':'end'},svg,Number(value.toFixed(1)));
    }
    const step = (width - 45) / Math.max(1, rows.length);
    rows.forEach((r, i) => {
      const x = 40 + i * step;
      const valid = Number.isFinite(r.cop);
      if (valid) {
        const height = r.cop / maximum * 150;
        const bar = svgEl('rect',{x,y:190-height,width:Math.max(12,step-12),height,rx:3,
          fill:r.complete ? 'var(--primary-color)' : 'url(#partial)'},svg);
        svgEl('title',{},bar,`${r.date}: COP ${r.cop.toFixed(2)}; heat ${r.heat_kwh} kWh; electricity ${r.electricity_kwh} kWh; coverage ${r.coverage}%${r.today ? ' of elapsed day' : ''}`);
        svgEl('text',{x:x+(step-12)/2,y:183-height,'text-anchor':'middle',class:'value'},svg,r.cop.toFixed(2));
      } else {
        svgEl('text',{x:x+(step-12)/2,y:181,'text-anchor':'middle'},svg,'—');
      }
      svgEl('text',{x:x+(step-12)/2,y:208,'text-anchor':'middle'},svg,r.date.slice(5));
      svgEl('text',{x:x+(step-12)/2,y:225,'text-anchor':'middle'},svg,r.today ? 'today' : `${fixed(r.coverage, 0)}%`);
    });
    const details = el('details',undefined,card);
    el('summary','Daily energy and coverage',details);
    const table = el('table',undefined,details);
    const header = el('tr',undefined,el('thead',undefined,table));
    for (const heading of ['Date','Heat kWh','Input kWh','COP','Coverage']) el('th',heading,header);
    const body = el('tbody',undefined,table);
    for (const r of [...rows].reverse()) {
      const row = el('tr',undefined,body);
      for (const value of [r.date, fixed(r.heat_kwh, 3), fixed(r.electricity_kwh, 3),
        Number.isFinite(r.cop) ? r.cop.toFixed(2) : '—', `${fixed(r.coverage, 1)}%`]) el('td',value,row);
    }
    el('p', `Local days: ${state.attributes.timezone || 'Home Assistant timezone'}. Source: ${state.attributes.power_source}.`,card);
  }
}
if (!customElements.get('samsung-ehs-daily-cop-card')) customElements.define('samsung-ehs-daily-cop-card', SamsungEHSDailyCOPCard);
window.customCards = window.customCards || [];
window.customCards.push({type:'samsung-ehs-daily-cop-card',name:'Samsung EHS Daily COP',description:'Daily ratios of paired thermal and electrical energy, with coverage.'});

class SamsungEHSFlowReturnRow extends HTMLElement {
  setConfig(config) {
    if (!config.entity || !config.return_entity) throw new Error('Specify flow and return entities');
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({mode: 'open'});
    this.shadowRoot.innerHTML = `
      <style>
        :host {display:flex;align-items:center;min-height:40px;color:var(--primary-text-color);}
        ha-icon {margin-right:16px;color:var(--state-icon-color);flex:none;}
        .name {flex:1;} .values {margin-left:16px;white-space:nowrap;font-variant-numeric:tabular-nums;}
      </style>
      <ha-icon icon="mdi:thermometer-water"></ha-icon>
      <span class="name"></span><span class="values"></span>`;
    this.shadowRoot.querySelector('.name').textContent = config.name || 'Flow/Return';
    this.shadowRoot.querySelector('ha-icon').setAttribute('icon', config.icon || 'mdi:thermometer-water');
  }

  set hass(hass) {
    if (!this.config) return;
    const format = entityId => {
      const state = hass.states[entityId];
      if (!state || ['unknown', 'unavailable', ''].includes(state.state)) return '—';
      const value = Number(state.state);
      if (!Number.isFinite(value)) return '—';
      const temperature = new Intl.NumberFormat(hass.locale?.language || hass.language, {
        maximumFractionDigits: 1,
      }).format(value);
      return `${temperature}${state.attributes.unit_of_measurement || '°C'}`;
    };
    this.shadowRoot.querySelector('.values').textContent =
      `${format(this.config.entity)} / ${format(this.config.return_entity)}`;
  }
}
if (!customElements.get('samsung-ehs-flow-return-row')) {
  customElements.define('samsung-ehs-flow-return-row', SamsungEHSFlowReturnRow);
}
