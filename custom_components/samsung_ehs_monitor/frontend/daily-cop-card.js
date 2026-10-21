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
    this.renderValues(hass, [['entity', '°C'], ['return_entity', '°C']]);
  }

  renderValues(hass, fields) {
    if (!this.config) return;
    const format = (entityId, fallbackUnit, precision = 1) => {
      const state = hass.states[entityId];
      if (!state || typeof state.state !== 'string' || state.state.trim() === ''
        || ['unknown', 'unavailable'].includes(state.state)) return '—';
      const value = Number(state.state);
      if (!Number.isFinite(value)) return '—';
      const reading = new Intl.NumberFormat(hass.locale?.language || hass.language, {
        maximumFractionDigits: precision,
      }).format(value);
      return `${reading}${state.attributes?.unit_of_measurement || fallbackUnit}`;
    };
    this.shadowRoot.querySelector('.values').textContent =
      fields.map(([key, unit, precision]) => format(this.config[key], unit, precision)).join(' / ');
  }
}
if (!customElements.get('samsung-ehs-flow-return-row')) {
  customElements.define('samsung-ehs-flow-return-row', SamsungEHSFlowReturnRow);
}

class SamsungEHSPowerRow extends SamsungEHSFlowReturnRow {
  setConfig(config) {
    super.setConfig({...config, name:config.name || 'Power instant/day total',
      icon:config.icon || 'mdi:flash'});
  }

  set hass(hass) {
    this.renderValues(hass, [['entity', 'W'], ['return_entity', 'kWh', 2]]);
  }
}
if (!customElements.get('samsung-ehs-power-row')) {
  customElements.define('samsung-ehs-power-row', SamsungEHSPowerRow);
}

class SamsungEHSCompressorRow extends SamsungEHSFlowReturnRow {
  setConfig(config) {
    if (!config.discharge_entity || !config.pump_entity) {
      throw new Error('Specify compressor frequency, discharge temperature and pump speed entities');
    }
    super.setConfig({...config, return_entity:config.discharge_entity,
      name:config.name || 'Compressor', icon:config.icon || 'mdi:sine-wave'});
  }

  set hass(hass) {
    this.renderValues(hass, [['entity', 'Hz'], ['discharge_entity', '°C'], ['pump_entity', '%']]);
  }
}
if (!customElements.get('samsung-ehs-compressor-row')) {
  customElements.define('samsung-ehs-compressor-row', SamsungEHSCompressorRow);
}

/* Samsung heating water law: endpoint interpolation, held flat outside the range. */
function samsungEHSWaterLaw(values) {
  const finite = value => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 200;
  const cold = values.outdoor_cold, warm = values.outdoor_warm;
  if (!finite(cold) || !finite(warm)) return {error:'Waiting for outdoor endpoints: FSV 2011 and 2012.'};
  if (cold >= warm) return {error:'Invalid outdoor endpoints: FSV 2011 must be lower than 2012.'};
  const curves = [1, 2].flatMap(id => {
    const high = values[`wl${id}_cold`], low = values[`wl${id}_warm`];
    if (!finite(high) || !finite(low) || high < low) return [];
    return [{id, high, low, at: outdoor => {
      const fraction = Math.max(0, Math.min(1, (outdoor-cold)/(warm-cold)));
      return high + fraction*(low-high);
    }}];
  });
  if (!curves.length) return {error:'Waiting for valid flow endpoints: FSV 2021/2022 (WL1) or 2031/2032 (WL2).'};
  const selected = [1, 2].includes(values.selected) ? values.selected : null;
  return {cold, warm, curves, selected, active:curves.find(curve => curve.id === selected)};
}

class SamsungEHSWeatherCurveCard extends HTMLElement {
  setConfig(config) {
    if (!Array.isArray(config.entities) || config.entities.some(row => !row
      || typeof row.entity !== 'string' || typeof row.role !== 'string')) {
      throw new Error('Specify the water-law sensor entities and roles');
    }
    this.config = config;
    this.lastValues = undefined;
    if (!this.shadowRoot) this.attachShadow({mode:'open'});
  }
  getCardSize() { return 5; }
  set hass(hass) {
    if (!this.config) return;
    const values = {};
    for (const row of this.config.entities) {
      const entity = hass.states[row.entity];
      const state = entity?.state;
      let value = typeof state === 'string' && state.trim() !== '' ? Number(state) : NaN;
      if (row.role !== 'selected' && entity?.attributes?.unit_of_measurement === '°F') {
        value = (value-32)*5/9;
      }
      values[row.role] = Number.isFinite(value) && Math.abs(value) <= 200 ? value : null;
    }
    const signature = JSON.stringify(values);
    if (signature === this.lastValues) return;
    this.lastValues = signature;
    const root = this.shadowRoot;
    root.replaceChildren();
    const el = (tag, text, parent) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (parent) parent.append(node);
      return node;
    };
    el('style', `
      ha-card {display:block;box-sizing:border-box;padding:20px;color:var(--primary-text-color);}
      h2 {font-size:20px;font-weight:500;margin:0 0 8px;}
      p {font-size:13px;line-height:1.5;margin:8px 0;}
      .note {font-size:12px;color:var(--secondary-text-color);}
      svg {display:block;width:100%;height:auto;}
      text {fill:var(--secondary-text-color);font:11px sans-serif;}
      .legend {display:flex;gap:14px;flex-wrap:wrap;font-size:12px;margin-top:8px;}
    `, root);
    const card = el('ha-card', undefined, root);
    el('h2', this.config.title || 'Weather compensation · water law', card);
    const model = samsungEHSWaterLaw(values);
    if (model.error) { el('p', model.error, card); return; }
    const {cold, warm, curves, selected, active} = model;
    const outdoor = values.outdoor, actual = values.actual_flow;
    const hasOutdoor = Number.isFinite(outdoor), hasActual = Number.isFinite(actual);
    const format = value => Number(value.toFixed(1));
    const target = active && hasOutdoor ? active.at(outdoor) : null;
    let summary = selected ? `WL${selected}` : 'Active curve unavailable (FSV 2041)';
    if (selected && !active) summary += ' endpoints unavailable';
    if (hasOutdoor) summary += ` · ${format(outdoor)}°C outdoors`;
    else summary += ' · Outdoor temperature unavailable';
    if (target !== null) summary += ` → ${format(target)}°C base target`;
    if (hasActual) summary += ` · ${format(actual)}°C actual flow`;
    el('p', summary, card);
    const xMin = Math.min(cold-5, hasOutdoor ? outdoor-2 : cold-5);
    const xMax = Math.max(warm+5, hasOutdoor ? outdoor+2 : warm+5);
    const flowValues = curves.flatMap(curve => [curve.high, curve.low]);
    if (hasActual && hasOutdoor) flowValues.push(actual);
    const yMin = Math.floor((Math.min(...flowValues)-5)/5)*5;
    const yMax = Math.ceil((Math.max(...flowValues)+5)/5)*5;
    const x = value => 52+(value-xMin)/(xMax-xMin)*370;
    const y = value => 220-(value-yMin)/(yMax-yMin)*180;
    const ns = 'http://www.w3.org/2000/svg';
    const draw = (tag, attrs, parent, text) => {
      const node = document.createElementNS(ns, tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
      if (text !== undefined) node.textContent = text;
      parent.append(node);
      return node;
    };
    const svg = draw('svg', {viewBox:'0 0 450 275', role:'img', 'aria-label':'Outdoor temperature versus target flow-water temperature'}, card);
    const ticks = (min, max) => {
      const rough = (max-min)/4;
      const scale = 10 ** Math.floor(Math.log10(rough));
      const step = [1,2,5,10].find(value => value*scale >= rough)*scale;
      const result = [];
      for (let value=Math.ceil(min/step)*step; value<=max; value+=step) result.push(value);
      return result;
    };
    for (const flow of ticks(yMin, yMax)) {
      draw('line', {x1:52,y1:y(flow),x2:422,y2:y(flow),stroke:'var(--divider-color)'}, svg);
      draw('text', {x:44,y:y(flow)+4,'text-anchor':'end'}, svg, format(flow));
    }
    for (const ambient of ticks(xMin, xMax)) {
      draw('text', {x:x(ambient),y:239,'text-anchor':'middle'}, svg, format(ambient));
    }
    draw('text', {x:52,y:22}, svg, 'Flow water (°C)');
    draw('text', {x:237,y:265,'text-anchor':'middle'}, svg, 'Outdoor temperature (°C)');
    const colors = {1:'var(--primary-color, #03a9f4)',2:'var(--accent-color, #ff9800)'};
    const legend = el('div', undefined, card);
    legend.className = 'legend';
    for (const curve of curves) {
      const color = colors[curve.id];
      const points = [xMin,cold,warm,xMax].map(value => `${x(value)},${y(curve.at(value))}`).join(' ');
      const line = draw('polyline', {points, fill:'none', stroke:color,
        'stroke-width':curve.id === selected ? 3 : 2,
        'stroke-dasharray':curve.id === selected ? 'none' : '6 4', 'data-curve':curve.id}, svg);
      draw('title', {}, line, `WL${curve.id}: ${cold}°C outdoors → ${curve.high}°C flow; ${warm}°C outdoors → ${curve.low}°C flow`);
      for (const [ambient, flow] of [[cold,curve.high],[warm,curve.low]]) {
        draw('circle', {cx:x(ambient),cy:y(flow),r:3,fill:color}, svg);
      }
      el('span', `WL${curve.id}${curve.id === selected ? ' · active' : ''}`, legend).style.color = color;
    }
    if (hasOutdoor) {
      draw('line', {x1:x(outdoor),y1:40,x2:x(outdoor),y2:220,
        stroke:'var(--secondary-text-color)', 'stroke-dasharray':'3 4'}, svg);
      if (target !== null) {
        const point = draw('circle', {cx:x(outdoor),cy:y(target),r:5,fill:colors[selected],
          'data-marker':'target','data-value':target}, svg);
        draw('title', {}, point, `Base target: ${format(target)}°C at ${format(outdoor)}°C outdoors`);
      }
      if (hasActual) {
        const point = draw('circle', {cx:x(outdoor),cy:y(actual),r:5,
          fill:'var(--primary-text-color)', 'data-marker':'actual','data-value':actual}, svg);
        draw('title', {}, point, `Actual flow: ${format(actual)}°C`);
      }
    }
    el('p', 'Configured heating curve before controller offset. Dark marker: actual flow.', card).className = 'note';
  }
}
if (!customElements.get('samsung-ehs-weather-curve-card')) {
  customElements.define('samsung-ehs-weather-curve-card', SamsungEHSWeatherCurveCard);
}
window.customCards.push({type:'samsung-ehs-weather-curve-card', name:'Samsung EHS Weather Curve',
  description:'Configured heating water law with current outdoor and flow-water temperatures.'});
