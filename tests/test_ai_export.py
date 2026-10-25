"""Export routing is device-specific and does not leak raw attributes or credentials."""
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from types import ModuleType
from zoneinfo import ZoneInfo
import logging
import unittest

from test_auth import production
from test_ai_report import report


class Schema:
    def Required(self, key):
        return key

    def All(self, *args):
        return args

    def Coerce(self, kind):
        return kind

    def Range(self, **kwargs):
        return kwargs


class AIExportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entities = [NS(entity_id='sensor.daily', unique_id='private-device-123_estimated_cop_today',
                            config_entry_id='entry', platform='samsung_ehs_monitor',
                            domain='sensor', disabled_by=None),
                         NS(entity_id='sensor.fsv', unique_id='private-device-123_fsv_2011',
                            platform='samsung_ehs_monitor', domain='sensor', disabled_by=None),
                         NS(entity_id='sensor.mode', unique_id='private-device-123_operating_mode',
                            platform='samsung_ehs_monitor', domain='sensor', disabled_by=None)]
        self.registry = NS(async_get=lambda entity: self.entities[0] if entity == 'sensor.daily' else None)
        registry_api = NS(async_get=lambda hass: self.registry,
                          async_entries_for_config_entry=lambda registry, entry: self.entities)
        decorators = NS(require_admin=lambda fn: fn,
                        websocket_command=lambda schema: lambda fn: fn,
                        async_response=lambda fn: fn)
        self.scope = production('ai_export.py', {
            'datetime': datetime, 'timedelta': timedelta, 'timezone': timezone,
            'ZoneInfo': ZoneInfo, 'partial': partial, 'logging': logging,
            'import_module': lambda name: Schema(), 'BASE_COMMAND_MESSAGE_SCHEMA': object(),
            'websocket_api': decorators, 'er': registry_api,
            'DOMAIN': 'samsung_ehs_monitor', 'METRICS': report.METRICS,
            'CONTEXT': report.CONTEXT, 'build_report': report.build_report,
            'numeric': report.numeric})
        coordinator = NS(device_id='private-device-123', power_sensor='sensor.secret',
                         daily=NS(saved=lambda: {'source': 'sensor.secret'}))
        self.hass = NS(data={'samsung_ehs_monitor': {'entry': coordinator}},
                       config=NS(time_zone='Europe/Dublin', components=set()),
                       states=NS(get=lambda entity: NS(
                           state='-10' if entity == 'sensor.fsv' else 'heat',
                           attributes={'token': 'private', 'unit_of_measurement': '°C'},
                           last_updated=datetime.now(timezone.utc))))
        async def executor(function, *args):
            return function(*args)
        self.hass.async_add_executor_job = executor
        self.connection = NS(send_error=Mock(), send_result=Mock())

    async def test_missing_sensor_rejected(self):
        await self.scope['websocket_export_performance'](
            self.hass, self.connection, {'id': 1, 'entity_id': 'sensor.other', 'days': 7})
        self.connection.send_error.assert_called_once()
        self.connection.send_result.assert_not_called()

    async def test_no_recorder_still_exports_settings_and_daily_report(self):
        await self.scope['websocket_export_performance'](
            self.hass, self.connection, {'id': 1, 'entity_id': 'sensor.daily', 'days': 7})
        result = self.connection.send_result.call_args.args[1]
        self.assertEqual(result['period']['calendar_days'], 7)
        self.assertEqual(result['current_settings']['fsv_2011']['value'], -10)
        self.assertEqual(result['current_settings']['operating_mode']['value'], 'heat')
        self.assertTrue(result['warnings'])
        self.assertNotIn('private', str(result))
        self.assertNotIn('sensor.secret', str(result))
        self.assertNotIn('private-device-123', str(result))
        self.connection.send_error.assert_not_called()


    async def test_recorder_history_is_restricted_to_selected_device_metrics(self):
        self.entities.append(NS(entity_id='sensor.outside', unique_id='private-device-123_outdoor_temperature',
                                platform='samsung_ehs_monitor', domain='sensor', disabled_by=None))
        self.hass.config.components.add('recorder')
        recorder = ModuleType('homeassistant.components.recorder')
        history = ModuleType('homeassistant.components.recorder.history')
        helper = ModuleType('homeassistant.helpers.recorder')
        recorder.get_instance = helper.get_instance = lambda hass: NS(
            async_add_executor_job=hass.async_add_executor_job)
        def get_history(hass, start, end, entity_ids, **kwargs):
            self.assertEqual(entity_ids, ['sensor.outside'])
            self.assertFalse(kwargs['minimal_response'])
            return {'sensor.outside': [NS(last_updated=start, state='10',
                                        attributes={'unit_of_measurement': '°C', 'token': 'private'})]}
        history.get_significant_states = get_history
        modules = {'homeassistant': ModuleType('homeassistant'),
                   'homeassistant.components': ModuleType('homeassistant.components'),
                   'homeassistant.helpers': ModuleType('homeassistant.helpers'),
                   'homeassistant.components.recorder': recorder,
                   'homeassistant.components.recorder.history': history,
                   'homeassistant.helpers.recorder': helper}
        with patch.dict('sys.modules', modules):
            await self.scope['websocket_export_performance'](
                self.hass, self.connection, {'id': 1, 'entity_id': 'sensor.daily', 'days': 7})
        result = self.connection.send_result.call_args.args[1]
        self.assertEqual(result['hourly'][0]['metrics']['outdoor_temperature']['mean'], 10)
        self.assertNotIn('private', str(result))


if __name__ == '__main__':
    unittest.main()
