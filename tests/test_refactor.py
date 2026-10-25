"""Behavioral regressions for event cost, shared samples and boundary validation."""
from copy import deepcopy
import ast
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import unittest

import test_external_power as external
from test_auth import api, Response
from test_snapshots import batch, stamp


class AccountingSchedulingTests(unittest.TestCase):
    def test_power_events_capture_samples_without_replaying_history(self):
        coordinator, _ = external.ExternalPowerTests().make()
        coordinator.daily_store = Mock()
        coordinator.daily.replay = Mock(wraps=coordinator.daily.replay)
        coordinator.snapshots.intervals = Mock(wraps=coordinator.snapshots.intervals)
        coordinator.daily.history = Mock(wraps=coordinator.daily.history)
        for _ in range(100):
            coordinator.external_power_changed(None)
        coordinator.daily.replay.assert_not_called()
        coordinator.snapshots.intervals.assert_not_called()
        coordinator.daily.history.assert_not_called()
        self.assertEqual(coordinator.data['_performance']['estimated_cop'], 4)
        self.assertTrue(coordinator.snapshots.power)
        coordinator.daily_tick(datetime.now(timezone.utc))
        coordinator.daily.replay.assert_called_once()
        coordinator.snapshots.intervals.assert_called_once()
        coordinator.daily.history.assert_called_once()
        self.assertIn('_daily_history', coordinator.data)

    def test_periodic_accounting_uses_all_intermediate_power_changes(self):
        coordinator, states = external.ExternalPowerTests().make()
        t = stamp('2026-10-20T12:00:00+00:00')
        del coordinator.snapshots.live
        coordinator.snapshots.ingest(batch([(t, 40)]), t)
        coordinator.data = batch([(t, 40)])
        coordinator.daily_store = Mock()
        class Clock:
            value = t
            @classmethod
            def now(cls, zone):
                return datetime.fromtimestamp(cls.value, zone)
            fromtimestamp = staticmethod(datetime.fromtimestamp)
        with patch.dict(external.scope, datetime=Clock):
            for dt, watts in ((0, 1000), (10, 2000), (40, 0)):
                Clock.value = t+dt
                state = states['sensor.shelly_power']
                state.state = str(watts)
                state.last_reported = datetime.fromtimestamp(t+dt, timezone.utc)
                coordinator.external_power_changed(None)
            coordinator.daily_tick(datetime.fromtimestamp(t+60, timezone.utc))
        row = coordinator.daily.history(t+60)[-1]
        self.assertAlmostEqual(row['electricity_kwh'], 70000/3600000, places=4)
        self.assertAlmostEqual(row['heat_kwh'], 2.792*40/3600, places=4)
        self.assertEqual(coordinator.daily.days['2026-10-20']['seconds'], 40)


class SharedSampleTests(unittest.TestCase):
    def test_packets_decoded_once_for_display_logs_history_and_repeated_batches(self):
        history = external.Snapshots()
        t = stamp('2026-10-20T12:00:00+00:00')
        data = batch([(t, 40), (t+60, 0)])
        decode = Mock(wraps=external.d.decode_packet)
        with patch.dict(external.snapshot_scope, decode_packet=decode):
            history.ingest(data, t+60)
            history.sample(t, 1000)
            history.sample(t+60, 1000)
            for _ in range(3):
                history.live(t+60)
                history.intervals(t+60)
                history.heat_intervals(t, t+60)
                history.ingest(deepcopy(data), t+60)
        self.assertEqual(decode.call_count, 4)
        self.assertEqual(history.live(t+60)[0]['compressor_frequency'], 0)
        self.assertEqual(history.heat_at(t+60), 0)

    def test_repeated_rejected_packet_is_cached_and_has_stable_failure_key(self):
        history = external.Snapshots()
        t = stamp('2026-10-20T12:00:00+00:00')
        data = batch([(t, 40)])
        data['components']['main']['samsungce.ehsCycleData']['indoor']['value'][0]['data'] = 'bad'
        decode = Mock(wraps=external.d.decode_packet)
        with patch.dict(external.snapshot_scope, decode_packet=decode):
            history.ingest(data, t)
            key = history.failures[0][0]
            history.ingest(data, t+60)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(history.failures[0][0], key)
        self.assertIn('system timestamp', history.failures[0][1])

    def test_fresh_display_cache_expires_and_negative_heat_stays_signed(self):
        history = external.Snapshots()
        t = stamp('2026-10-20T12:00:00+00:00')
        data = batch([(t, 40)])
        indoor = data['components']['main']['samsungce.ehsCycleData']['indoor']['value'][0]
        packet = bytearray.fromhex(indoor['data'])
        packet[2], packet[3] = packet[3], packet[2]
        indoor['data'] = packet.hex()
        history.ingest(data, t)
        history.ingest(batch([]), t+60)
        self.assertIsNone(history.live(t+60)[0]['thermal_power'])
        self.assertEqual(history.heat_at(t+60), -2.792)
        self.assertTrue(all(value is None for value in history.live(t+901)[0].values()))


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    def client(self, data):
        response = Response()
        response.data = data
        return api['SmartThingsClient'](NS(get=Mock(return_value=response)), 'test')

    async def test_rejects_invalid_nested_status_containers(self):
        for data in ([], {'components': []}, {'components': {'main': []}},
                     {'components': {'main': {'capability': []}}},
                     {'components': {'main': {'capability': {'attribute': []}}}},
                     {'components': {'main': {'capability': {'attribute': {'timestamp': []}}}}},
                     {'components': {'main': {'powerConsumptionReport': {'powerConsumption': {'value': []}}}}},
                     {'components': {'main': {'samsungce.ehsFsvSettings': {'fsvSettings': {'value': [None]}}}}}):
            with self.subTest(data=data), self.assertRaises(api['SmartThingsError']):
                await self.client(data).status('pump')

    async def test_packet_content_failures_reach_decoder_while_shape_is_validated(self):
        data = batch([(stamp('2026-10-20T12:00:00+00:00'), 40)])
        data['components']['main']['samsungce.ehsCycleData']['indoor']['value'][0]['data'] = 'bad'
        self.assertEqual(await self.client(data).status('pump'), data)
        data['components']['main']['samsungce.ehsCycleData']['indoor']['value'] = {}
        with self.assertRaises(api['SmartThingsError']):
            await self.client(data).status('pump')

    async def test_bad_device_lists_metadata_and_pagination_are_explicit_failures(self):
        for data in ({'items': {}}, {'items': [None]}, {'items': [{}]},
                     {'items': [], '_links': []}, {'items': [], '_links': {'next': {'href': 5}}}):
            with self.subTest(data=data), self.assertRaises(api['SmartThingsError']):
                await self.client(data).devices()
        with self.assertRaises(api['SmartThingsError']):
            await self.client({'presentationId': []}).device('pump')

    async def test_programming_errors_are_not_disguised_as_bad_device_data(self):
        coordinator, _ = external.ExternalPowerTests().make()
        coordinator.client = NS(status=AsyncMock(side_effect=TypeError('programming bug')))
        with self.assertRaisesRegex(TypeError, 'programming bug'):
            await coordinator._async_update_data()
        self.assertEqual(coordinator.diagnostic_entries, [])


class SensorSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_decoded_entities_read_shared_coordinator_values(self):
        coordinator, _ = external.ExternalPowerTests().make()
        coordinator.data['_decoded'] = external.d.combine_samples()
        coordinator.data['_decoded'].update(water_flow_temperature=34, thermal_power=4.188)
        Sensor = SensorTests().sensor_class()
        Log = external.production_class('sensor.py', 'SamsungEHSLogSensor', {'SamsungEHSSensor': Sensor})
        scope = dict(HomeAssistant=object, ConfigEntry=object, DOMAIN='samsung_ehs_monitor',
                     SamsungEHSSensor=Sensor, SamsungEHSLogSensor=Log,
                     read=external.d.field, cycle=lambda data, kind: None, power=lambda data, key: None,
                     SensorDeviceClass=NS(TEMPERATURE='temperature', POWER='power', ENERGY='energy', FREQUENCY='frequency'),
                     SensorStateClass=NS(TOTAL_INCREASING='total_increasing', TOTAL='total', MEASUREMENT='measurement'))
        tree = ast.parse((external.BASE / 'sensor.py').read_text())
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'performance_value')
        scope['number'] = external.p.number
        exec(compile(ast.Module(body=[helper], type_ignores=[]), 'sensor.py', 'exec'), scope)
        setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == 'async_setup_entry')
        exec(compile(ast.Module(body=[setup], type_ignores=[]), 'sensor.py', 'exec'), scope)
        entities = []
        await scope['async_setup_entry'](NS(data={'samsung_ehs_monitor': {'test': coordinator}}),
                                         NS(entry_id='test'), entities.extend)
        by_key = {entity.key: entity for entity in entities}
        for key, value in coordinator.data['_decoded'].items():
            self.assertEqual(by_key[key].native_value, value)
        coordinator.data['_decoded']['water_flow_temperature'] = None
        self.assertIsNone(by_key['water_flow_temperature'].native_value)
        watts = by_key['electrical_input_power']
        kilowatts = by_key['electrical_input_power_kw']
        self.assertEqual(kilowatts._attr_native_unit_of_measurement, 'kW')
        self.assertEqual(kilowatts._attr_native_unit_of_measurement,
                         by_key['thermal_power']._attr_native_unit_of_measurement)
        self.assertEqual(kilowatts._attr_state_class, 'measurement')
        for value, expected in ((1200, 1.2), (0, 0), (None, None), (float('nan'), None)):
            coordinator.data['_performance'] = {'electrical_input_power': value}
            self.assertEqual(kilowatts.native_value, expected)
        coordinator.data['_performance'] = {'electrical_input_power': 1200}
        self.assertEqual(watts.native_value, 1200)



class SensorTests(unittest.TestCase):
    def sensor_class(self):
        class CoordinatorEntity:
            def __init__(self, coordinator):
                self.coordinator = coordinator
                self.hass = coordinator.hass
        class SensorEntity:
            pass
        return external.production_class('sensor.py', 'SamsungEHSSensor', {
            'CoordinatorEntity': CoordinatorEntity, 'SensorEntity': SensorEntity,
            'DOMAIN': 'samsung_ehs_monitor', 'EntityCategory': NS(DIAGNOSTIC='diagnostic')})

    def test_only_daily_cop_exposes_cached_history_and_recorder_excludes_it(self):
        coordinator, _ = external.ExternalPowerTests().make()
        history = [{'date': '2026-10-20', 'cop': 4}]
        coordinator.data['_daily_history'] = history
        coordinator.daily.history = Mock(side_effect=AssertionError('should use cached history'))
        Sensor = self.sensor_class()
        for key in ('estimated_cop_today', 'estimated_heat_energy_today',
                    'electrical_energy_today', 'daily_performance_coverage'):
            sensor = Sensor(coordinator, key, key, lambda data: None)
            attributes = sensor.extra_state_attributes
            self.assertEqual('daily_history' in attributes, key == 'estimated_cop_today')
            if key == 'estimated_cop_today':
                self.assertIs(attributes['daily_history'], history)
                self.assertIn('daily_history', sensor._unrecorded_attributes)
        coordinator.daily.history.assert_not_called()

    def test_seven_day_sensors_explain_partial_coverage(self):
        coordinator, _ = external.ExternalPowerTests().make()
        coordinator.data['_performance'] = {'performance_7d_coverage': 12.5}
        Sensor = self.sensor_class()
        for key in ('estimated_heat_energy_7d', 'electrical_energy_7d', 'estimated_cop_7d'):
            sensor = Sensor(coordinator, key, key, lambda data: None)
            attributes = sensor.extra_state_attributes
            self.assertEqual(attributes['coverage_percent'], 12.5)
            self.assertEqual(attributes['window_hours'], 168)
            self.assertIn('missing periods excluded', attributes['calculation'])

    def test_sensor_programming_errors_are_not_returned_as_unknown(self):
        coordinator, _ = external.ExternalPowerTests().make()
        sensor = self.sensor_class()(coordinator, 'test', 'Test', Mock(side_effect=KeyError('bug')))
        with self.assertRaises(KeyError):
            _ = sensor.native_value


if __name__ == '__main__':
    unittest.main()
