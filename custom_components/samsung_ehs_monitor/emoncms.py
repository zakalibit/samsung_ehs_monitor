"""Publish finalized paired minute records; never send credentials in URLs."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from math import isfinite
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout

FINALIZE_SECONDS = 1800
BATCH_SIZE = 120


def valid_url(value):
    try:
        parts = urlsplit(value)
        _ = parts.port
        if any(char.isspace() for char in value):
            return False
        return (parts.scheme in ('http', 'https') and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and not parts.query and not parts.fragment)
    except (ValueError, TypeError):
        return False


def prepare_batch(ledger, cursor, heat_total, electricity_total, now, node):
    """Cumulative feeds are explicit counters, not reintegrated power samples.

    Publish at minute end, after the entire minute has left the correction window.
    Powers are averages across observed paired seconds only; paired_coverage
    exposes partial minutes. Use the explicit cumulative energy feeds for COP.
    """
    rows = []
    cutoff = now - FINALIZE_SECONDS
    for stamp, values in sorted(ledger.items()):
        if stamp < cursor or stamp + 60 > cutoff:
            continue
        if len(values) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in values):
            continue
        heat, electricity, seconds = values
        if electricity < 0 or not 0 < seconds <= 60.001:
            continue
        heat_total += heat
        electricity_total += electricity
        cursor = int(stamp + 60)
        rows.append([cursor, node, {
            'heatpump_elec': round(electricity * 3600000 / seconds, 6),
            'heatpump_elec_kwh': round(electricity_total, 9),
            'heatpump_heat': round(heat * 3600000 / seconds, 6),
            'heatpump_heat_kwh': round(heat_total, 9),
            'paired_coverage': round(min(100, seconds / 60 * 100), 3),
        }])
        if len(rows) >= BATCH_SIZE:
            break
    return {'rows': rows, 'cursor': cursor, 'heat': heat_total, 'electricity': electricity_total}


class EmoncmsPublisher:
    """One serialized, durable outbox per integration; target streams survive toggles."""

    def __init__(self, session, store, url, node, api_key, source, zone, live_node=27):
        self.session, self.store = session, store
        self.url, self.node, self.api_key = url.rstrip('/'), node, api_key
        self.source, self.zone = source, zone
        self.live_node = live_node
        self.live_status = 'Waiting for live readings.'
        self.target = sha256(f'{self.url}|{node}'.encode()).hexdigest()
        self.state = {'streams': {}}
        self.lock = asyncio.Lock()
        self.status = 'Waiting for finalized paired data (about 30–32 minutes after enabling).'

    async def load(self, now):
        saved = await self.store.async_load()
        self.state = saved if isinstance(saved, dict) and isinstance(saved.get('streams'), dict) else {'streams': {}}
        if self.target not in self.state['streams']:
            self.state['streams'][self.target] = {
                'source': self.source, 'zone': self.zone,
                'cursor': (int(now) // 60 + 1) * 60,
                'heat': 0.0, 'electricity': 0.0, 'pending': None}
            await self.store.async_save(self.state)

    async def publish(self, ledger, now):
        if self.lock.locked():
            return
        async with self.lock:
            stream = self.state['streams'][self.target]
            if stream['source'] != self.source or stream['zone'] != self.zone:
                self.status = 'Power source or timezone changed. Choose a new Emoncms node to avoid resetting existing counters.'
                return
            pending = stream.get('pending')
            if pending is None:
                pending = prepare_batch(ledger, stream['cursor'], stream['heat'], stream['electricity'], now, self.node)
                if not pending['rows']:
                    self.status = 'Waiting for finalized paired data; no new eligible records.'
                    return
                stream['pending'] = pending
            # Persist before sending. A timeout/restart retries identical timestamps
            # and counters. Feed input processing must use Log to feed only.
            await self.store.async_save(self.state)
            error = await self._send_rows(pending['rows'])
            if error:
                self.status = error
                return
            stream.update(cursor=pending['cursor'], heat=pending['heat'],
                          electricity=pending['electricity'], pending=None)
            await self.store.async_save(self.state)
            self.status = 'Publishing finalized paired data (30-minute delay).'

    async def publish_live(self, inputs, now):
        """Live readings have no cumulative processing or historical backfill."""
        values = {key: value for key, value in inputs.items()
                  if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)}
        if not values:
            self.live_status = 'No valid live readings available.'
            return
        error = await self._send_rows([[int(now), self.live_node, values]])
        self.live_status = error or f'Publishing live readings to node {self.live_node}.'

    async def _send_rows(self, rows):
        try:
            async with self.session.post(
                self.url + '/input/bulk.json',
                data={'apikey': self.api_key, 'time': '0',
                      'data': json.dumps(rows, allow_nan=False)},
                timeout=ClientTimeout(total=30), allow_redirects=False,
            ) as response:
                if response.status != 200:
                    return f'Emoncms HTTP {response.status}; upload will be retried.'
                result = (await response.text()).strip()
                if result != 'ok':
                    try:
                        accepted = json.loads(result)
                    except (ValueError, TypeError):
                        accepted = None
                    if accepted != 'ok' and (not isinstance(accepted, dict) or accepted.get('success') is not True):
                        return 'Emoncms did not acknowledge the upload; check URL, write key and input permissions.'
        except (ClientError, asyncio.TimeoutError):
            return 'Cannot reach Emoncms; upload will be retried.'


LIVE_FIELDS = {
    'electrical_input_power': 'W',
    'water_flow_temperature': '°C',
    'water_return_temperature': '°C',
    'outdoor_temperature': '°C',
    'room_temperature': '°C',
}


def live_inputs(entities, states, device_id):
    """Preserve emoncms_history's entity-ID input names, including renamed sensors."""
    prefix = device_id + '_'
    result = {}
    for entity in entities:
        if (entity.platform != 'samsung_ehs_monitor' or entity.domain != 'sensor'
                or entity.disabled_by is not None or not entity.unique_id.startswith(prefix)):
            continue
        key = entity.unique_id[len(prefix):]
        if key not in LIVE_FIELDS:
            continue
        state = states.get(entity.entity_id)
        if state is None:
            continue
        try:
            value = float(state.state)
        except (ValueError, TypeError, OverflowError):
            continue
        if not isfinite(value):
            continue
        unit = state.attributes.get('unit_of_measurement')
        if unit != LIVE_FIELDS[key]:
            if unit == 'kW' and LIVE_FIELDS[key] == 'W':
                value *= 1000
            elif unit == '°F' and LIVE_FIELDS[key] == '°C':
                value = (value - 32) * 5 / 9
            else:
                continue
        result[entity.entity_id] = value
    return result
