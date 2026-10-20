"""Live COP uses source-time power reports, independent of current power display."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import test_external_power as external
from test_snapshots import batch


class PowerHistoryTests(unittest.TestCase):
    def test_closest_ties_and_no_cherry_picking(self):
        history = external.p.PowerHistory()
        history.record(100, 1000, 200)
        history.record(120, 2000, 200)
        self.assertEqual(history.closest(110), (100, 1000))
        self.assertEqual(history.closest(118), (120, 2000))
        history.record(119, 0, 200)
        self.assertEqual(history.closest(119), (119, 0))
        history.record(119, None, 200)
        self.assertEqual(history.closest(119), (119, None))
        self.assertIsNone(history.closest(300))

    def test_source_timestamp_is_not_refreshed_by_rereading(self):
        history = external.p.PowerHistory()
        for now in range(200, 500, 60):
            history.record(100, 1000, now)
        self.assertEqual(len(history.samples), 1)
        self.assertIsNone(history.closest(400))
        history.record(100, 1000, 2000)
        self.assertEqual(history.samples, {})
        history.record(2001, 1000, 2000)
        self.assertEqual(history.samples, {})

    def test_memory_bound(self):
        history = external.p.PowerHistory()
        for i in range(4100):
            history.record(1000+i/10, 1000, 1500)
        self.assertEqual(len(history.samples), 4096)


class LiveCOPTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        self.when = self.now.timestamp()
        self.coordinator, self.states = external.ExternalPowerTests().make(state='100')
        self.states['sensor.shelly_power'].last_reported = self.now
        self.data = batch([(self.when-120, 40)])
        self.coordinator.live_power.record(self.when-120, 1000, self.when)

    def refresh(self, data=None):
        with patch.dict(external.scope, decode=external.d.decode,
                        thermal_timestamps=external.d.thermal_timestamps):
            self.coordinator.refresh_live(data if data is not None else self.data, self.now)

    def test_start_stop_power_does_not_skew_old_heat(self):
        self.refresh()
        self.assertEqual(self.data['_performance']['electrical_input_power'], 100)
        self.assertEqual(self.data['_performance']['estimated_cop'], 2.79)
        match = self.data['_cop_match']
        self.assertEqual(match['power_watts'], 1000)
        self.assertEqual(match['time_difference_seconds'], 0)
        self.assertEqual(self.coordinator.snapshots.power[self.when], 100)
        self.assertEqual(match['thermal_timestamp'], match['power_timestamp'])

    def test_no_close_match_uses_latest_power_after_restart(self):
        self.coordinator.live_power = external.p.PowerHistory()
        self.refresh()
        self.assertEqual(self.data['_performance']['estimated_cop'], 27.92)
        self.assertEqual(self.data['_cop_match']['power_matching_method'], 'latest_fallback')
        self.assertEqual(self.data['_cop_match']['power_watts'], 100)
        self.assertEqual(self.data['_cop_match']['time_difference_seconds'], 120)

    def test_zero_latest_fallback_keeps_cop_unknown(self):
        self.coordinator.live_power = external.p.PowerHistory()
        self.states['sensor.shelly_power'].state = '0'
        self.refresh()
        self.assertIsNone(self.data['_performance']['estimated_cop'])
        self.assertEqual(self.data['_cop_match']['power_matching_method'], 'latest_fallback')
        self.assertEqual(self.data['_cop_match']['power_watts'], 0)

    def test_newer_malformed_binary_uses_timestamp_of_valid_packet(self):
        cycles = self.data['components']['main']['samsungce.ehsCycleData']
        for kind in ('indoor', 'outdoor'):
            cycles[kind]['value'].append({'timestamp': self.now.isoformat(), 'data': None})
        self.refresh()
        self.assertEqual(self.data['_performance']['estimated_cop'], 2.79)
        self.assertEqual(self.data['_cop_match']['time_difference_seconds'], 0)

    def test_thermal_packets_too_far_apart(self):
        outdoor = self.data['components']['main']['samsungce.ehsCycleData']['outdoor']['value'][0]
        outdoor['timestamp'] = datetime.fromtimestamp(self.when-300, timezone.utc).isoformat()
        self.refresh()
        self.assertIsNone(self.data['_performance']['estimated_cop'])

    def test_zero_and_unavailable_power_not_replaced_by_positive_sample(self):
        self.coordinator.live_power.record(self.when-120, 0, self.when)
        self.refresh()
        self.assertIsNone(self.data['_performance']['estimated_cop'])
        self.coordinator.live_power.record(self.when-120, 1000, self.when)
        self.states['sensor.shelly_power'].state = 'unavailable'
        self.refresh()
        self.assertIsNone(self.data['_performance']['estimated_cop'])

    def test_power_events_are_retained_during_samsung_api_failure(self):
        class Clock:
            @staticmethod
            def now(zone):
                return self.now
        self.coordinator.last_update_success = False
        with patch.dict(external.scope, datetime=Clock):
            self.coordinator.external_power_changed(None)
        self.assertEqual(self.coordinator.live_power.samples[self.when], 100)
        self.assertEqual(self.coordinator.notifications, 1)

    def test_samsung_power_history_uses_report_timestamps(self):
        c = self.coordinator
        c.power_sensor = None
        self.data['components']['main']['powerConsumptionReport'] = {'powerConsumption': {
            'timestamp': self.now.isoformat(), 'value': {'power': 0.1}}}
        self.refresh()
        self.assertEqual(self.data['_performance']['electrical_input_power'], 100)
        self.assertEqual(self.data['_performance']['estimated_cop'], 2.79)
