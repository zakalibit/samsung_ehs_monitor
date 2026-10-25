"""Authenticated, administrator-only AI report export over Home Assistant WebSocket."""
from datetime import datetime, timedelta, timezone
from functools import partial
from importlib import import_module
from zoneinfo import ZoneInfo
import logging

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.messages import BASE_COMMAND_MESSAGE_SCHEMA
from homeassistant.helpers import entity_registry as er

from .ai_report import CONTEXT, METRICS, build_report, numeric
from .const import DOMAIN

# Use the same validation library as HA's WebSocket base schema (older HA
# uses voluptuous; newer releases use probatio).
vol = import_module(type(BASE_COMMAND_MESSAGE_SCHEMA).__module__.split('.')[0])
LOGGER = logging.getLogger(__name__)


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required('type'): 'samsung_ehs_monitor/export_performance',
    vol.Required('entity_id'): str,
    vol.Required('days'): vol.All(int, vol.Range(min=1, max=35)),
})
@websocket_api.async_response
async def websocket_export_performance(hass, connection, msg):
    """Resolve a sensor to its own integration; never export credentials or raw status."""
    registry = er.async_get(hass)
    entity = registry.async_get(msg['entity_id'])
    coordinator = (hass.data.get(DOMAIN, {}).get(entity.config_entry_id)
                   if entity and entity.platform == DOMAIN else None)
    if coordinator is None:
        connection.send_error(msg['id'], 'not_found', 'Select a loaded Samsung EHS Monitor sensor.')
        return
    now = datetime.now(timezone.utc)
    zone = hass.config.time_zone
    first = now.astimezone(ZoneInfo(zone)).date() - timedelta(days=msg['days'] - 1)
    start = datetime.combine(first, datetime.min.time(), ZoneInfo(zone))
    prefix = f'{coordinator.device_id}_'
    sensors = {item.unique_id[len(prefix):]: item.entity_id
               for item in er.async_entries_for_config_entry(registry, entity.config_entry_id)
               if item.platform == DOMAIN and item.domain == 'sensor'
               and item.unique_id.startswith(prefix) and item.disabled_by is None}
    current = {}
    for key, entity_id in sensors.items():
        if key not in CONTEXT and not (key.startswith('fsv_') and key[4:].isdigit()):
            continue
        state = hass.states.get(entity_id)
        if state is None or state.state in ('unknown', 'unavailable'):
            continue
        # Only selected state values and units; never state attributes wholesale.
        value = numeric(state.state)
        if key.startswith('fsv_') and value is None:
            continue
        current[key] = {'value': value if value is not None else state.state,
                        'unit': state.attributes.get('unit_of_measurement'),
                        'last_updated_utc': state.last_updated.isoformat()}
    saved = coordinator.daily.saved()
    warnings = []
    if not getattr(coordinator, 'last_update_success', True):
        warnings.append('The latest SmartThings refresh failed; current settings may be unavailable or stale.')
    if not any(key.startswith('fsv_') for key in current):
        warnings.append('No numeric FSV settings are available; check sensor availability before seeking settings advice.')
    histories = {}
    selected = {key: sensors[key] for key in METRICS if key in sensors}
    if selected and 'recorder' in hass.config.components:
        try:
            try:
                from homeassistant.helpers.recorder import get_instance
            except ImportError:  # Compatibility with earlier HA Recorder exports.
                from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
            # Run database work on Recorder's executor, never the event loop.
            states = await get_instance(hass).async_add_executor_job(partial(
                get_significant_states, hass, start, now, list(selected.values()),
                significant_changes_only=False, minimal_response=False, no_attributes=False))
            histories = {key: [(state.last_updated.timestamp(), state.state,
                               state.attributes.get('unit_of_measurement'))
                              for state in states.get(entity_id, [])]
                         for key, entity_id in selected.items()}
        except Exception:  # Daily accounting remains useful when Recorder is unavailable.
            LOGGER.exception('Could not read Recorder for AI performance export')
            warnings.append('Recorder history could not be read; hourly sensor metrics are unavailable.')
    else:
        warnings.append('Recorder or recorded sensors unavailable; hourly sensor metrics are unavailable.')
    if not coordinator.power_sensor:
        warnings.append('No external power sensor configured; paired energy and COP are unavailable.')
    missing = [key for key in METRICS if not histories.get(key)]
    if missing:
        warnings.append('No recorded history for: ' + ', '.join(missing))
    report = await hass.async_add_executor_job(
        build_report, saved, current, histories, zone, msg['days'], now.timestamp(), warnings)
    connection.send_result(msg['id'], report)
