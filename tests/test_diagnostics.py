"""Failure logging remains bounded and explains rejected cycle packets."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import unittest

import test_external_power as external
from test_decoder import decoder, sample


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def coordinator(self):
        return external.ExternalPowerTests().make()[0]

    def test_log_capacity_and_safe_messages(self):
        coordinator = self.coordinator()
        for i in range(105):
            coordinator.record_failure('API status', f'SmartThings HTTP {i}')
        self.assertEqual(len(coordinator.diagnostic_entries), 100)
        self.assertEqual(coordinator.diagnostic_entries[0]['message'], 'SmartThings HTTP 5')
        self.assertIn('+00:00', coordinator.diagnostic_entries[-1]['timestamp'])

    def test_repeated_failures_keep_first_last_count_and_warn_only_once(self):
        coordinator = self.coordinator()
        class Clock:
            value = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
            @classmethod
            def now(cls, zone):
                return cls.value
        logger = Mock()
        with patch.dict(external.scope, datetime=Clock, LOGGER=logger):
            coordinator.record_failure('Parsing', 'first details', 'same-packet')
            first_seen = coordinator.diagnostic_entries[0]['first_seen']
            Clock.value = datetime(2026, 10, 20, 12, 5, tzinfo=timezone.utc)
            coordinator.record_failure('Parsing', 'updated clock details', 'same-packet')
            coordinator.record_failure('Parsing', 'updated clock details', 'same-packet')
        self.assertEqual(len(coordinator.diagnostic_entries), 1)
        row = coordinator.diagnostic_entries[0]
        self.assertEqual(row['count'], 3)
        self.assertEqual(row['first_seen'], first_seen)
        self.assertEqual(row['last_seen'], Clock.value.isoformat())
        self.assertEqual(row['message'], 'updated clock details')
        logger.warning.assert_called_once()

    async def test_request_failures_are_logged_before_update_fails(self):
        class APIError(Exception):
            pass
        class AuthError(APIError):
            pass
        class UpdateError(Exception):
            pass
        external.scope.update(SmartThingsError=APIError, SmartThingsAuthError=AuthError,
                     UpdateFailed=UpdateError, ConfigEntryAuthFailed=UpdateError)
        for error in (APIError('SmartThings HTTP 500'), AuthError('SmartThings access denied')):
            coordinator = self.coordinator()
            coordinator.client = NS(status=AsyncMock(side_effect=error))
            with self.assertRaises(UpdateError):
                await coordinator._async_update_data()
            self.assertEqual(coordinator.diagnostic_entries[-1]['category'], 'API status')
            self.assertEqual(coordinator.diagnostic_entries[-1]['message'], str(error))

    def test_log_sensor_survives_coordinator_outage(self):
        Sensor = external.production_class('sensor.py', 'SamsungEHSLogSensor', {'SamsungEHSSensor': object})
        sensor = Sensor()
        sensor.coordinator = NS(last_update_success=False, diagnostic_entries=[
            {'timestamp': 'now', 'category': 'API status', 'message': 'SmartThings HTTP 500'}])
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 1)
        self.assertEqual(sensor.extra_state_attributes['entries'][0]['message'], 'SmartThings HTTP 500')
        self.assertIn('entries', sensor._unrecorded_attributes)

    def test_packet_errors_and_normal_compressor_off(self):
        now = datetime(2026, 10, 19, 9, 1, tzinfo=timezone.utc)
        data = sample()
        self.assertEqual(decoder.cycle_errors(data, now), [])
        self.assertEqual(decoder.decode(data, now)['compressor_frequency'], 0)
        record = data['components']['main']['samsungce.ehsCycleData']['indoor']['value'][0]
        record['data'] = 'invalid private payload'
        messages = decoder.cycle_errors(data, now)
        self.assertIn('hexadecimal', messages[0])
        self.assertNotIn('private', messages[0])
        record['data'] = '00'
        self.assertIn('length', decoder.cycle_errors(data, now)[0])
        record['data'] = sample()['components']['main']['samsungce.ehsCycleData']['indoor']['value'][0]['data']
        record['timestamp'] = '2020-01-01T00:00:00Z'
        self.assertIn('15 minutes', decoder.cycle_errors(data, now)[0])
        data['components']['main']['samsungce.ehsCycleData']['indoor']['value'] = []
        self.assertIn('missing', decoder.cycle_errors(data, now)[0])


if __name__ == '__main__':
    unittest.main()
