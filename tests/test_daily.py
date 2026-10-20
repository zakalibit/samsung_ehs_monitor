import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest
import json
from zoneinfo import ZoneInfo

path = Path(__file__).resolve().parents[1] / 'custom_components/samsung_ehs_monitor/daily.py'
spec = importlib.util.spec_from_file_location('daily', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def stamp(text):
    return datetime.fromisoformat(text).timestamp()


class DailyTests(unittest.TestCase):
    def test_ratio_of_energy_not_average_of_ratios(self):
        d = m.DailyPerformance('UTC', 'sensor.shelly')
        t = stamp('2026-10-20T00:00:00+00:00')
        d.replay(t+120, [(t, t+60, 4, 1000), (t+60, t+120, 4, 2000)])
        self.assertAlmostEqual(d.history(t+120)[-1]['cop'], 8/3, places=3)
        self.assertNotEqual(d.history(t+120)[-1]['cop'], 3)

    def test_full_day_and_midnight(self):
        d = m.DailyPerformance('UTC', 'sensor.shelly')
        t = stamp('2026-10-20T00:00:00+00:00')
        for dt in range(60, 86401, 60):
            d.replay(t+dt, [(t+dt-60, t+dt, 4, 1000)])
        row = d.history(t+86400)[0]
        self.assertEqual(row['heat_kwh'], 96)
        self.assertEqual(row['electricity_kwh'], 24)
        self.assertEqual(row['cop'], 4)
        self.assertTrue(row['complete'])
        self.assertEqual(row['coverage'], 100)
        self.assertIsNone(d.values(t+86400)['estimated_cop_today'])

    def test_interval_split_at_midnight(self):
        d = m.DailyPerformance('UTC', 'sensor.shelly')
        t = stamp('2026-10-20T23:59:30+00:00')
        d.replay(t+60, [(t, t+60, 4, 1000)])
        self.assertEqual(len(d.days), 2)
        self.assertEqual(d.days['2026-10-20']['seconds'], 30)
        self.assertEqual(d.days['2026-10-21']['seconds'], 30)

    def test_dst_calendar_days(self):
        d = m.DailyPerformance('Europe/Dublin', 'sensor.shelly')
        for date, hours in [('2026-03-29',23),('2026-10-25',25)]:
            start,end=d.boundary(datetime.fromisoformat(date).date())
            self.assertEqual(end-start,hours*3600)

    def test_negative_and_unknown_break_baseline(self):
        d = m.DailyPerformance('UTC', 'sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.replay(t+240, [(t, t+60, 4, 1000), (t+60, t+120, 4, None),
                         (t+120, t+180, 4, -100), (t+180, t+240, 4, 1000)])
        self.assertEqual(sum(r['seconds'] for r in d.days.values()),120)
        self.assertEqual(d.values(t+240)['daily_performance_coverage'],50)

    def test_thermal_expiry_clips_valid_hold(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.replay(t+60, [(t, t+30, 4, 1000), (t+30, t+60, None, 1000)])
        self.assertEqual(d.history(t+60)[-1]['coverage'],50)

    def test_no_bridge_after_long_gap_or_restart(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.replay(t+60, [(t, t+60, 4, 1000)])
        d.replay(t+600, [])
        restored=m.DailyPerformance('UTC','sensor.shelly',d.saved())
        restored.replay(t+660, [])
        self.assertEqual(restored.days,d.days)
        self.assertEqual(sum(r['seconds'] for r in restored.days.values()),60)

    def test_source_or_timezone_change_starts_new_series(self):
        d=m.DailyPerformance('UTC','sensor.a')
        d.days={'2026-10-20':{'heat':1,'electricity':1,'seconds':60}}
        self.assertEqual(m.DailyPerformance('UTC','sensor.b',d.saved()).days,{})
        self.assertEqual(m.DailyPerformance('Europe/Dublin','sensor.a',d.saved()).days,{})

    def test_no_external_sensor_no_daily_data(self):
        d=m.DailyPerformance('UTC',None)
        t=stamp('2026-10-20T00:00:00+00:00')
        d.replay(t+60, [(t, t+60, 4, 1000)])
        self.assertIsNone(d.values(t+60)['estimated_cop_today'])

    def test_defrost_reduces_energy(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.replay(t+120, [(t, t+60, 4, 1000), (t+60, t+120, -2, 1000)])
        self.assertEqual(d.values(t+120)['estimated_cop_today'],1)

    def test_missing_days_are_explicit_and_history_bounded(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.replay(t+60, [(t, t+60, 4, 1000)])
        d.replay(t+2*86400, [])
        rows=d.history(t+2*86400)
        self.assertEqual(len(rows),3)
        self.assertIsNone(rows[1]['cop'])
        d.replay(t+40*86400, [])
        self.assertLessEqual(len(d.history(t+40*86400)),35)

    def test_old_store_migrates_without_losing_or_double_counting_totals(self):
        t = stamp('2026-10-20T12:00:00+00:00')
        legacy = {'zone': 'UTC', 'source': 'sensor.power', 'days': {
            '2026-10-20': {'heat': 3, 'electricity': 1, 'seconds': 3600}}}
        daily = m.DailyPerformance('UTC', 'sensor.power', legacy)
        daily.replay(t+60, [(t, t+60, 6, 1000)])
        saved = json.loads(json.dumps(daily.saved()))
        restored = m.DailyPerformance('UTC', 'sensor.power', saved)
        restored.replay(t+60, [(t, t+60, 6, 1000)])
        self.assertAlmostEqual(restored.days['2026-10-20']['heat'], 3.1)
        self.assertAlmostEqual(restored.days['2026-10-20']['electricity'], 1+1/60)
        self.assertEqual(restored.baseline_days, legacy['days'])
        self.assertEqual(len(restored.ledger), 1)

    def test_ledger_rebuilds_totals_even_if_cached_days_are_missing(self):
        t = stamp('2026-10-20T23:59:30+00:00')
        daily = m.DailyPerformance('UTC', 'sensor.power')
        daily.replay(t+60, [(t, t+60, 6, 1000)])
        saved = json.loads(json.dumps(daily.saved()))
        saved['days'] = {}  # Totals are derived from the persisted interval ledger.
        restored = m.DailyPerformance('UTC', 'sensor.power', saved)
        self.assertEqual(restored.days, daily.days)
        self.assertEqual(len(restored.ledger), 2)
        self.assertAlmostEqual(restored.days['2026-10-20']['seconds'], 30)
        self.assertAlmostEqual(restored.days['2026-10-21']['seconds'], 30)

    def test_new_store_is_reset_when_source_or_timezone_changes(self):
        t = stamp('2026-10-20T12:00:00+00:00')
        daily = m.DailyPerformance('UTC', 'sensor.power')
        daily.replay(t+60, [(t, t+60, 6, 1000)])
        for zone, source in [('UTC', 'sensor.other'), ('Europe/Dublin', 'sensor.power')]:
            restored = m.DailyPerformance(zone, source, daily.saved())
            self.assertEqual(restored.days, {})
            self.assertEqual(restored.ledger, {})
            self.assertEqual(restored.replayed, [])

    def test_missing_intervals_preserve_totals_but_valid_corrections_replace_them(self):
        t = stamp('2026-10-20T12:00:00+00:00')
        daily = m.DailyPerformance('UTC', 'sensor.power')
        daily.replay(t+120, [(t, t+120, 6, 1000)])
        daily.replay(t+120, [])
        self.assertAlmostEqual(daily.days['2026-10-20']['heat'], 0.2)
        daily.replay(t+120, [(t+60, t+120, 0, 500)])
        self.assertAlmostEqual(daily.days['2026-10-20']['heat'], 0.1)
        self.assertAlmostEqual(daily.days['2026-10-20']['electricity'], 1500/60000)
        self.assertEqual(daily.days['2026-10-20']['seconds'], 120)

    def test_ledger_retention_is_bounded_to_35_local_days(self):
        t = stamp('2026-10-20T12:00:00+00:00')
        daily = m.DailyPerformance('UTC', 'sensor.power')
        daily.replay(t+60, [(t, t+60, 6, 1000)])
        future = t+36*86400
        daily.replay(future+60, [(future, future+60, 6, 1000)])
        self.assertNotIn('2026-10-20', daily.days)
        self.assertEqual(len(daily.ledger), 1)
        restored = m.DailyPerformance('UTC', 'sensor.power', daily.saved())
        self.assertEqual(restored.days, daily.days)

    def test_multiple_changes_in_one_minute_keep_exact_energy_on_restore(self):
        t = stamp('2026-10-20T12:00:00+00:00')
        daily = m.DailyPerformance('UTC', 'sensor.power')
        rows = [(t, t+10, 6, 1000), (t+10, t+30, 3, 500), (t+30, t+60, -1, 200)]
        daily.replay(t+60, rows)
        self.assertEqual(len(daily.ledger), 1)
        restored = m.DailyPerformance('UTC', 'sensor.power', json.loads(json.dumps(daily.saved())))
        self.assertAlmostEqual(restored.days['2026-10-20']['heat'], (60+60-30)/3600)
        self.assertAlmostEqual(restored.days['2026-10-20']['electricity'], (10000+10000+6000)/3600000)
        restored.replay(t+60, rows)
        self.assertEqual(restored.days, daily.days)


if __name__=='__main__': unittest.main()
