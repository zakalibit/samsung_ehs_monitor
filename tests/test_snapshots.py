"""Process overlapping API batches without inventing past electrical readings."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS

import test_external_power as external


def stamp(text):
    return datetime.fromisoformat(text).timestamp()


def batch(points):
    cycles = {'indoor': {'value': []}, 'outdoor': {'value': []}}
    for t, frequency in points:
        indoor = bytearray(31)
        indoor[2], indoor[3], indoor[7] = 85, 89, 100  # 30/34 C, 10 L/min -> 2.792 kW
        outdoor = bytearray(28)
        outdoor[0:2] = frequency.to_bytes(2, 'big')
        timestamp = datetime.fromtimestamp(t, timezone.utc).isoformat()
        for kind, packet in [('indoor', indoor), ('outdoor', outdoor)]:
            cycles[kind]['value'].append({'timestamp': timestamp, 'data': packet.hex()})
    return {'components': {'main': {
        'samsungce.deviceIdentification': {'binaryId': {'value': external.d.BINARY_ID}},
        'samsungce.ehsCycleData': cycles}}}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.t = stamp('2026-10-20T12:00:00+00:00')
        self.history = external.Snapshots()
        self.daily = external.daily.DailyPerformance('UTC', 'sensor.power')

    def advance(self, dt, watts):
        t = self.t + dt
        self.history.sample(t, watts)
        self.daily.replay(t, self.history.intervals(t))

    def test_delayed_start_and_stop_and_repeated_batch(self):
        t = self.t
        self.history.ingest(batch([(t, 0)]), t)
        self.advance(0, 1000)
        self.advance(60, 1000)
        self.advance(120, 1000)
        self.assertEqual(self.daily.history(t+120)[-1]['heat_kwh'], 0)
        data = batch([(t+120, 0), (t, 0), (t+60, 40)])  # Deliberately unsorted.
        self.history.ingest(data, t+120)
        self.daily.replay(t+120, self.history.intervals(t+120))
        row = self.daily.history(t+120)[-1]
        self.assertAlmostEqual(row['heat_kwh'], 2.792/60, places=4)
        self.assertAlmostEqual(row['electricity_kwh'], 1/30, places=4)
        for _ in range(3):
            self.history.ingest(data, t+120)
            self.daily.replay(t+120, self.history.intervals(t+120))
        self.assertEqual(row, self.daily.history(t+120)[-1])
        self.assertEqual(external.d.latest(data, 'outdoor')['timestamp'],
                         datetime.fromtimestamp(t+120, timezone.utc).isoformat())

    def test_no_historical_power_before_start_or_across_outage(self):
        self.history.ingest(batch([(self.t-300, 40), (self.t, 40)]), self.t)
        self.advance(0, 1000)
        self.assertEqual(self.history.intervals(self.t), [])
        self.advance(60, None)
        self.advance(120, 1000)
        self.advance(600, 1000)  # Long downtime excluded.
        self.assertAlmostEqual(self.daily.days['2026-10-20']['seconds'], 60)
        restored = external.daily.DailyPerformance('UTC', 'sensor.power', deepcopy(self.daily.saved()))
        fresh_history = external.Snapshots()
        fresh_history.ingest(batch([(self.t+600, 40)]), self.t+600)
        fresh_history.sample(self.t+600, 1000)
        restored.replay(self.t+600, fresh_history.intervals(self.t+600))
        self.assertEqual(restored.days, self.daily.days)

    def test_midnight_and_window_finalization(self):
        self.t = stamp('2026-10-20T23:59:00+00:00')
        for dt in range(0, 3601, 60):
            self.history.ingest(batch([(self.t+dt, 40)]), self.t+dt)
            self.advance(dt, 1000)
        self.assertAlmostEqual(sum(row['heat'] for row in self.daily.days.values()), 2.792)
        self.assertAlmostEqual(sum(row['seconds'] for row in self.daily.days.values()), 3600)
        self.assertAlmostEqual(self.daily.days['2026-10-20']['seconds'], 60)
        self.assertLessEqual(len(self.history.power), 32)

    def test_invalid_and_future_snapshots_do_not_fill_heat(self):
        t = self.t
        data = batch([(t, 40), (t+60, 40), (t+600, 40)])
        data['components']['main']['samsungce.ehsCycleData']['indoor']['value'][1]['data'] = 'bad'
        self.history.ingest(data, t+120)
        for dt in (0, 60, 120):
            self.advance(dt, 1000)
        self.assertAlmostEqual(self.daily.days['2026-10-20']['seconds'], 60)
        self.assertNotIn(t+600, self.history.packets['indoor'])

    def test_counter_window_uses_all_heat_snapshots_once(self):
        t = self.t
        self.history.ingest(batch([(t, 0), (t+60, 40), (t+120, 0)]), t+120)
        intervals = self.history.heat_intervals(t, t+120)
        performance = external.p.RollingPerformance()
        performance.update(t, 0, 10, heat_intervals=[])
        performance.update(t+120, 0, 10.033, heat_intervals=intervals)
        self.assertAlmostEqual(performance.rows[0][2], 2.792/60)
        performance.update(t+120, 0, 10.033, heat_intervals=intervals)
        self.assertEqual(len(performance.rows), 1)


class SnapshotCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_batches_reconcile_with_observed_power_events(self):
        t = stamp('2026-10-20T12:00:00+00:00')
        class Clock:
            value = t

            @classmethod
            def now(cls, zone):
                return datetime.fromtimestamp(cls.value, timezone.utc)

            fromisoformat = staticmethod(datetime.fromisoformat)

        coordinator, _ = external.ExternalPowerTests().make()
        coordinator.store = Mock()
        coordinator.daily_store = Mock()
        coordinator.client = NS(status=AsyncMock(side_effect=[
            batch([(t, 0)]), batch([(t+120, 0), (t+60, 40), (t, 0)])]))
        with patch.dict(external.scope, datetime=Clock, decode=external.d.decode,
                        cycle_errors=external.d.cycle_errors, number=external.p.number):
            coordinator.data = await coordinator._async_update_data()
            Clock.value = t+60
            coordinator.external_power_changed(None)
            Clock.value = t+120
            coordinator.data = await coordinator._async_update_data()
        row = coordinator.daily.history(t+120)[-1]
        self.assertAlmostEqual(row['heat_kwh'], 2.792/60, places=4)
        self.assertAlmostEqual(row['electricity_kwh'], 1200*120/3600000, places=4)
        self.assertEqual(coordinator.data['_performance']['net_thermal_power'], 0)


if __name__ == '__main__':
    unittest.main()
