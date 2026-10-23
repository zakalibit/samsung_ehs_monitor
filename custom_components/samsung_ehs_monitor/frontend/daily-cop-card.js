/* Daily ratios are supplied by the backend; this card never averages COP samples. */
function samsungEHSRegisterRowsAndDailyCard() {
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
      const step = 25;
      const barWidth = 11;
      const width = Math.max(360, rows.length * step + 45);
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
      rows.forEach((r, i) => {
        const x = 35 + (i + 0.5) * step;
        const valid = Number.isFinite(r.cop);
        if (valid) {
          const height = r.cop / maximum * 150;
          const bar = svgEl('rect',{x:x-barWidth/2,y:190-height,width:barWidth,height,rx:3,
            fill:r.complete ? 'var(--primary-color)' : 'url(#partial)'},svg);
          svgEl('title',{},bar,`${r.date}: COP ${r.cop.toFixed(2)}; heat ${r.heat_kwh} kWh; electricity ${r.electricity_kwh} kWh; coverage ${r.coverage}%${r.today ? ' of elapsed day' : ''}`);
          svgEl('text',{x,y:183-height,'text-anchor':'middle',class:'value'},svg,r.cop.toFixed(2));
        } else {
          svgEl('text',{x,y:181,'text-anchor':'middle'},svg,'—');
        }
        svgEl('text',{x,y:208,'text-anchor':'middle'},svg,r.date.slice(5));
        svgEl('text',{x,y:225,'text-anchor':'middle'},svg,r.today ? 'today' : `${fixed(r.coverage, 0)}%`);
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

  class SamsungEHSPerformanceRow extends SamsungEHSFlowReturnRow {
    setConfig(config) {
      const metric = config.metric || 'power';
      if (!['power', 'heat', 'cop'].includes(metric)) throw new Error('Specify power, heat or cop');
      const defaults = {
        power:['Power instant/day total', 'mdi:flash'],
        heat:['Heat instant/day total', 'mdi:heat-wave'],
        cop:['COP instant/day', 'mdi:heat-pump'],
      };
      super.setConfig({...config, metric, name:config.name || defaults[metric][0],
        icon:config.icon || defaults[metric][1]});
    }

    set hass(hass) {
      if (!this.config) return;
      const metric = this.config.metric;
      const unit = metric === 'power' ? 'W' : metric === 'heat' ? 'kW' : '';
      this.renderValues(hass, [['entity', unit, metric === 'power' ? 1 : 2],
        ['return_entity', metric === 'cop' ? '' : 'kWh', 2]]);
    }
  }
  if (!customElements.get('samsung-ehs-performance-row')) {
    customElements.define('samsung-ehs-performance-row', SamsungEHSPerformanceRow);
  }
  // Preserve compatibility with manual dashboards using the earlier power-only row.
  class SamsungEHSPowerRow extends SamsungEHSPerformanceRow {}
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

function samsungEHSRegisterWeatherCard() {
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
      let summary = selected ? `WL${selected}` : curves.map(curve => 'WL'+curve.id).join(' / ');
      if (selected && !active) summary += ' endpoints unavailable';
      if (hasOutdoor) summary += ` · ${format(outdoor)}°C outdoors`;
      else summary += ' · Outdoor temperature unavailable';
      if (target !== null) summary += ` → ${format(target)}°C base target`;
      else if (!selected && hasOutdoor) summary += ' → '+curves.map(curve =>
        `WL${curve.id}: ${format(curve.at(outdoor))}°C`).join(' / ')+' base targets';
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
          'stroke-dasharray':!selected || curve.id === selected ? 'none' : '6 4', 'data-curve':curve.id}, svg);
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
        if (!selected) {
          for (const curve of curves) {
            const baseTarget = curve.at(outdoor);
            const point = draw('circle', {cx:x(outdoor),cy:y(baseTarget),r:4,fill:colors[curve.id],
              'data-marker':'curve-target','data-wl':curve.id,'data-value':baseTarget}, svg);
            draw('title', {}, point, `WL${curve.id} base target: ${format(baseTarget)}°C at ${format(outdoor)}°C outdoors`);
          }
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

}

/* Decode Recorder states before integrating them into hourly means. */
function samsungEHSRawSamples(rows, start, end) {
  if (!Array.isArray(rows)) return [];
  const samples = rows.flatMap(row => {
    if (!row || typeof row !== 'object') return [];
    const time = typeof row.lu === 'number' ? row.lu*1000
      : typeof row.lc === 'number' ? row.lc*1000 : Date.parse(row.last_updated || row.last_changed);
    if (!Number.isFinite(time) || time > end) return [];
    const state = row.s ?? row.state;
    const value = typeof state === 'string' && state.trim() !== '' ? Number(state) : NaN;
    return [{time, value:Number.isFinite(value) ? value : null}];
  }).sort((a, b) => a.time-b.time);
  const previous = samsungEHSLastSample(samples, start);
  return [...(previous ? [{time:start, value:previous.value}] : []),
    ...samples.filter(point => point.time > start)];
}

function samsungEHSHourlySamples(samples, start, end, origin=Math.floor(start/3600000)*3600000) {
  // Recorder states persist until the next change. Weight each valid state by
  // its duration, excluding unavailable intervals instead of treating them as zero.
  const hour = 3600000;
  const buckets = [];
  for (let time=origin; time<end; time+=hour) {
    buckets.push({time, end:Math.min(time+hour,end), total:0, duration:0});
  }
  for (let index=0; index<samples.length; index++) {
    const point = samples[index];
    if (point.value === null) continue;
    let from = Math.max(start,point.time);
    const to = Math.min(end,samples[index+1]?.time ?? end);
    while (from<to) {
      const bucket = buckets[Math.floor((from-origin)/hour)];
      const until = Math.min(to,bucket.end);
      bucket.total += point.value*(until-from);
      bucket.duration += until-from;
      from = until;
    }
  }
  return buckets.map(bucket => ({time:bucket.time,end:bucket.end,
    value:bucket.duration ? bucket.total/bucket.duration : null}));
}

function samsungEHSDayStart(end, timeZone) {
  // Find the first instant of today's date in HA's timezone. Searching dates
  // handles DST days without assuming they are exactly 24 hours long.
  const calendar = new Intl.DateTimeFormat('en-CA', {
    timeZone, year:'numeric', month:'2-digit', day:'2-digit'});
  const today = calendar.format(new Date(end));
  let left=end-36*3600000, right=end;
  while (left<right) {
    const middle=Math.floor((left+right)/2);
    if (calendar.format(new Date(middle)) === today) right=middle;
    else left=middle+1;
  }
  return left;
}

let samsungEHSChartLoading;
async function samsungEHSEnsureNativeChart() {
  if (customElements.get('ha-chart-base')) return;
  if (!samsungEHSChartLoading) {
    samsungEHSChartLoading = (async () => {
      if (typeof window.loadCardHelpers !== 'function') throw new Error('Home Assistant card helpers unavailable');
      const helpers = await window.loadCardHelpers();
      // Creating this unattached card loads HA's chart module without fetching history.
      helpers.createCardElement({type:'history-graph', entities:['sun.sun']});
      let timer;
      try {
        await Promise.race([
          customElements.whenDefined('ha-chart-base'),
          new Promise((resolve, reject) => { timer=setTimeout(() => reject(new Error('Chart module loading timed out')), 10000); }),
        ]);
      } finally { clearTimeout(timer); }
    })().catch(error => { samsungEHSChartLoading=undefined; throw error; });
  }
  await samsungEHSChartLoading;
}

function samsungEHSLastSample(samples, time) {
  let left=0, right=samples.length;
  while (left<right) {
    const middle=(left+right)>>1;
    if (samples[middle].time<=time) left=middle+1; else right=middle;
  }
  return samples[left-1];
}

function samsungEHSRoundBarCaps(series, hidden) {
  // Match HA Energy: 4 px on the outer positive/negative cap only. All of our
  // series already share hourly buckets, so no zero-filling is needed here.
  const caps = new Set();
  for (let index=series.length-1; index>=0; index--) {
    const dataset = series[index];
    dataset.data = dataset.data.map(point => {
      const value = point.value ?? point;
      const amount = value[1];
      const key = `${dataset.stack ?? dataset.id}:${value[0]}:${amount < 0 ? 'negative' : 'positive'}`;
      let radius = [0,0,0,0];
      if (!hidden.has(dataset.id) && amount !== null && amount !== 0 && !caps.has(key)) {
        radius = amount < 0 ? [0,0,4,4] : [4,4,0,0];
        caps.add(key);
      }
      return {value,itemStyle:{borderRadius:radius,...(amount === 0 ? {borderWidth:0} : {})}};
    });
  }
}

function samsungEHSRegisterHistoryCard() {
  class SamsungEHSHistoryBarCard extends HTMLElement {
    setConfig(config) {
      if (!Array.isArray(config.entities) || !config.entities.length
        || config.entities.some(row => !row || typeof row.entity !== 'string')) {
        throw new Error('Specify history entities');
      }
      const hours = config.hours_to_show === undefined ? 24 : Number(config.hours_to_show);
      if (!Number.isFinite(hours) || hours <= 0 || hours > 168) throw new Error('History range must be between 1 and 168 hours');
      this.config = config;
      this._hours = hours;
      this._today = config.period === 'day' || (config.period === undefined && config.hours_to_show === undefined);
      clearTimeout(this._retryTimer); this._retryTimer = null;
      this._retryDelay = 5000;
      this._generation = (this._generation || 0)+1;
      this._pending = false;
      this._history = null;
      this._chart = null;
      this._hidden = new Set();
      this._lastAttempt = 0;
      if (!this.shadowRoot) this.attachShadow({mode:'open'});
      const root = this.shadowRoot;
      root.replaceChildren();
      const el = (tag, text, parent) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        parent.append(node);
        return node;
      };
      el('style', `
        ha-card {display:block;padding:20px;color:var(--primary-text-color);}
        h2 {font-size:20px;font-weight:500;margin:0 0 12px;}
        ha-chart-base {display:block;}
        p {font-size:12px;line-height:1.5;color:var(--secondary-text-color);margin:8px 0 0;}
      `, root);
      const card = el('ha-card', undefined, root);
      el('h2', config.title || 'Recorded values', card);
      this._plot = el('div', undefined, card);
      this._info = el('p', 'Loading recorded readings…', card);
      if (this._connected && this._hass) this._fetch();
    }
    getCardSize() { return 5; }
    connectedCallback() {
      this._connected = true;
      if (!this._timer) this._timer = setInterval(() => this._fetch(), 60000);
      if (this._hass) this._fetch();
    }
    disconnectedCallback() {
      this._connected = false;
      clearInterval(this._timer); this._timer = null;
      clearTimeout(this._retryTimer); this._retryTimer = null;
      this._generation = (this._generation || 0)+1;
      this._pending = false;
    }
    set hass(hass) {
      const reconnected = this._hass?.connected === false && hass.connected !== false;
      this._hass = hass;
      if (this._chart) this._chart.hass = hass;
      if (reconnected || (!this._history && !this._pending && !this._lastAttempt)) this._fetch();
    }
    async _fetch() {
      if (!this._connected || !this.config || !this._hass || this._pending) return;
      if (this._hass.connected === false) {
        this._info.textContent = 'Waiting for Home Assistant to reconnect…';
        return;
      }
      clearTimeout(this._retryTimer); this._retryTimer = null;
      this._pending = true;
      const generation = this._generation;
      let requestTimer;
      try {
        const end = Date.now();
        const start = this._today ? samsungEHSDayStart(end,this._hass.config?.time_zone) : end-this._hours*3600000;
        this._lastAttempt = end;
        await samsungEHSEnsureNativeChart();
        if (generation !== this._generation || !this._connected) return;
        const history = await Promise.race([
          this._hass.callWS({type:'history/history_during_period',
            start_time:new Date(start).toISOString(), end_time:new Date(end).toISOString(),
            entity_ids:this.config.entities.map(row => row.entity),
            minimal_response:true, no_attributes:true, significant_changes_only:true, include_start_time_state:true}),
          new Promise((resolve,reject) => { requestTimer=setTimeout(() => reject(new Error('History request timed out')),20000); }),
        ]);
        clearTimeout(requestTimer);
        if (generation !== this._generation || !this._connected) return;
        if (!history || Array.isArray(history) || typeof history !== 'object') throw new Error('Invalid history response');
        this._start = start; this._end = end;
        this._history = this.config.entities.map(row => ({...row,
          unit:this._hass.states[row.entity]?.attributes?.unit_of_measurement || '',
          samples:samsungEHSHourlySamples(samsungEHSRawSamples(history[row.entity], start, end), start, end, this._today ? start : undefined)}));
        // Rendering can also fail while HA's chart module is being initialized.
        // Keep it inside the retry boundary, rather than reject an unobserved promise.
        this._draw();
        this._retryDelay = 5000;
        const count = this._history.reduce((sum, series) => sum+series.samples.filter(point => point.value !== null).length, 0);
        this._info.textContent = count ? 'Hourly averages · partial hours use available data' : 'No numeric history available. Check Recorder for these entities.';
      } catch (error) {
        if (generation === this._generation && this._connected) {
          const delay = this._retryDelay || 5000;
          this._info.textContent = `Chart or history could not be loaded. Retrying in ${delay/1000} seconds.`;
          console.warn('Samsung EHS history card failed to load or render',error);
          // Discard only a chart that failed before attachment; retain displayed data.
          if (this._chart && !this._chart.parentNode) this._chart = null;
          this._retryTimer = setTimeout(() => { this._retryTimer=null; this._fetch(); },delay);
          this._retryDelay = Math.min(delay*2,60000);
        }
      } finally {
        clearTimeout(requestTimer);
        if (generation === this._generation) this._pending = false;
      }
    }
    _draw() {
      if (!this._history) return;
      const style = getComputedStyle(this);
      const fallback = ['#0288d1','#f39c12','#8e44ad','#16a085'];
      this._series = this._history.map((series, index) => ({
        id:series.entity, name:series.name || series.entity, type:'bar',
        stack:this.config.stacked === false ? undefined : 'readings',
        color:style.getPropertyValue(`--graph-color-${index+1}`).trim()
          || style.getPropertyValue(`--color-${index+1}`).trim() || fallback[index%fallback.length],
        barMaxWidth:50, cursor:'default',
        // One time-weighted mean per hour, aligned across all series.
        data:series.samples.map(point => [point.time,point.value]),
      }));
      const units = new Set(this._history.map(series => series.unit));
      if (!this._chart) {
        this._chart = document.createElement('ha-chart-base');
        this._chart.height = '280px';
        this._chart.setAttribute('chart-type','bar');
        this._chart.addEventListener('dataset-hidden', event => {
          this._hidden.add(event.detail.id); this._updateBarCaps();
        });
        this._chart.addEventListener('dataset-unhidden', event => {
          this._hidden.delete(event.detail.id); this._updateBarCaps();
        });
      }
      this._chart.hass = this._hass;
      const language = this._hass.locale?.language || this._hass.language;
      const timeZone = this._hass.config?.time_zone;
      const axisClock = new Intl.DateTimeFormat(language, {timeZone,hour:'2-digit',minute:'2-digit'});
      const axisDate = new Intl.DateTimeFormat(language, {timeZone,month:'short',day:'numeric'});
      this._chart.options = {
        animation:false,
        xAxis:{type:'time',min:new Date(this._today ? this._start : Math.floor(this._start/3600000)*3600000),max:new Date(this._end),
          axisLabel:this._today ? {showMinLabel:true,formatter:(value,index) =>
            `${index === 0 ? axisDate.format(new Date(this._start))+'\n' : ''}${axisClock.format(new Date(value))}`} : undefined},
        yAxis:{type:'value',name:units.size === 1 ? this._history[0].unit : '', splitLine:{show:true}},
        grid:{left:1,right:1,top:25,bottom:0,containLabel:true},
        legend:{type:'custom',data:this._series.map(series => ({id:series.id,name:series.name}))},
        tooltip:{trigger:'axis',renderMode:'html',confine:true,axisPointer:{type:'shadow',snap:true},
          formatter:params => this._tooltip(params)},
      };
      this._updateBarCaps();
      if (!this._chart.parentNode) this._plot.append(this._chart);
    }
    _updateBarCaps() {
      samsungEHSRoundBarCaps(this._series,this._hidden);
      this._chart.data = [...this._series];
    }
    _tooltip(params) {
      const items = Array.isArray(params) ? params : [params];
      const time = Number(items[0]?.axisValue ?? items[0]?.value?.[0]);
      if (!Number.isFinite(time)) return null;
      const language = this._hass.locale?.language || this._hass.language;
      const timeZone = this._today || this._hass.locale?.time_zone !== 'local' ? this._hass.config?.time_zone : undefined;
      const clock = new Intl.DateTimeFormat(language, {timeZone,...(this._today ? {} : {month:'short',day:'numeric'}),hour:'2-digit',minute:'2-digit'});
      const number = new Intl.NumberFormat(language, {maximumFractionDigits:4});
      const body = document.createElement('div');
      body.style.maxWidth = '300px';
      const title = document.createElement('div');
      title.textContent = `${clock.format(new Date(time))} – ${clock.format(new Date(Math.min(time+3600000,this._end)))} · average`;
      title.style.fontWeight = 'bold';
      body.append(title);
      let count = 0;
      this._history.forEach((series,index) => {
        if (this._hidden.has(series.entity)) return;
        // Read only this hour’s mean; never borrow a value from an earlier hour.
        const point = samsungEHSLastSample(series.samples,time);
        if (!point || time >= point.end) return;
        const row = document.createElement('div');
        row.style.marginTop = '6px';
        const marker = document.createElement('ha-chart-tooltip-marker');
        marker.color = this._series[index].color;
        row.append(marker);
        const label = document.createElement('span');
        const value = point.value === null ? 'unavailable' : `${number.format(point.value)}${series.unit ? ' '+series.unit : ''}`;
        label.textContent = ` ${series.name || series.entity}: ${value}`;
        row.append(label);
        body.append(row);
        count++;
      });
      return count ? body : null;
    }
  }
  if (!customElements.get('samsung-ehs-history-bar-card')) {
    customElements.define('samsung-ehs-history-bar-card', SamsungEHSHistoryBarCard);
  }
  window.customCards.push({type:'samsung-ehs-history-bar-card', name:'Samsung EHS History Bars',
    description:'Hourly averaged measurements using Home Assistant’s native stacked bar chart and shared tooltip.'});
}

// Extra frontend modules can run before HA installs its scoped-registry
// polyfill. That replaces both customElements and HTMLElement, losing early
// registrations. Re-check the CURRENT registry until HA's own root is defined;
// never wait on a whenDefined() promise belonging to the superseded registry.
// Class definitions must also be deferred so they extend the current HTMLElement.
(function samsungEHSWaitForFrontend() {
  if (!customElements.get('home-assistant')) {
    setTimeout(samsungEHSWaitForFrontend,50);
    return;
  }
  samsungEHSRegisterRowsAndDailyCard();
  samsungEHSRegisterWeatherCard();
  samsungEHSRegisterHistoryCard();
})();
