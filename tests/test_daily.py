import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest
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
        d.advance(t, 4, 1000, t+900)
        d.advance(t+60, 4, 2000, t+900)
        d.advance(t+120, 4, 2000, t+900)
        self.assertAlmostEqual(d.history(t+120)[-1]['cop'], 8/3, places=3)
        self.assertNotEqual(d.history(t+120)[-1]['cop'], 3)

    def test_full_day_and_midnight(self):
        d = m.DailyPerformance('UTC', 'sensor.shelly')
        t = stamp('2026-10-20T00:00:00+00:00')
        for dt in range(0, 86401, 60):
            d.advance(t+dt, 4, 1000, t+dt+900)
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
        d.advance(t, 4, 1000, t+900)
        d.advance(t+60, 4, 1000, t+900)
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
        d.advance(t,4,1000,t+900)
        d.advance(t+60,4,None,t+900)
        d.advance(t+120,4,-100,t+900)
        d.advance(t+180,4,1000,t+900)
        d.advance(t+240,4,1000,t+900)
        self.assertEqual(sum(r['seconds'] for r in d.days.values()),120)
        self.assertEqual(d.values(t+240)['daily_performance_coverage'],50)

    def test_thermal_expiry_clips_valid_hold(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.advance(t,4,1000,t+30)
        d.advance(t+60,None,1000,None)
        self.assertEqual(d.history(t+60)[-1]['coverage'],50)

    def test_no_bridge_after_long_gap_or_restart(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.advance(t,4,1000,t+900)
        d.advance(t+60,4,1000,t+900)
        d.advance(t+600,4,1000,t+900)
        restored=m.DailyPerformance('UTC','sensor.shelly',d.saved())
        restored.advance(t+660,4,1000,t+900)
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
        d.advance(t,4,1000,t+900)
        d.advance(t+60,4,1000,t+900)
        self.assertIsNone(d.values(t+60)['estimated_cop_today'])

    def test_defrost_reduces_energy(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.advance(t,4,1000,t+900)
        d.advance(t+60,-2,1000,t+900)
        d.advance(t+120,4,1000,t+900)
        self.assertEqual(d.values(t+120)['estimated_cop_today'],1)

    def test_missing_days_are_explicit_and_history_bounded(self):
        d=m.DailyPerformance('UTC','sensor.shelly')
        t=stamp('2026-10-20T00:00:00+00:00')
        d.advance(t,4,1000,t+900)
        d.advance(t+60,4,1000,t+900)
        d.advance(t+2*86400,4,1000,t+2*86400+900)
        rows=d.history(t+2*86400)
        self.assertEqual(len(rows),3)
        self.assertIsNone(rows[1]['cop'])
        d.advance(t+40*86400,4,1000,t+40*86400+900)
        self.assertLessEqual(len(d.history(t+40*86400)),35)


if __name__=='__main__': unittest.main()
