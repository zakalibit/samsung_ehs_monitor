import importlib.util
import json
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "custom_components/samsung_ehs_monitor/performance.py"
spec = importlib.util.spec_from_file_location("performance", path)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PerformanceTests(unittest.TestCase):
    def test_official_scaling(self):
        self.assertEqual(p.electrical_watts(1.2, "Samsung_EHS_123"), 1200)
        self.assertEqual(p.electrical_watts(1200, "OTHER"), 1200)
        self.assertIsNone(p.electrical_watts(1.2, None))
        self.assertIsNone(p.electrical_watts(float("nan"), "EHS"))

    def test_live_ratio(self):
        self.assertEqual(p.instantaneous_cop(4.8, 1200), 4)
        self.assertIsNone(p.instantaneous_cop(4.8, 0))
        self.assertIsNone(p.instantaneous_cop(None, 1200))
        self.assertIsNone(p.instantaneous_cop(-2, 1200))

    def test_signed_heat_and_off(self):
        values = dict(water_flow_rate=20, water_flow_temperature=35,
                      water_return_temperature=38, compressor_frequency=40)
        self.assertEqual(p.net_heat_kw(values), -4.188)
        values["compressor_frequency"] = 0
        self.assertEqual(p.net_heat_kw(values), 0)
        values["water_flow_temperature"] = None
        self.assertIsNone(p.net_heat_kw(values))

    def week(self, reset=False, gap=False):
        r = p.RollingPerformance()
        for now in range(0, p.WEEK + 1, 300):
            if gap and now == 3600:
                continue
            energy = now / 3600 if not reset or now < 3600 else (now - 3600) / 3600
            r.update(now, 4, energy)
        return r

    def test_seven_day_ratio_and_rollover(self):
        r = self.week()
        v = r.values(p.WEEK)
        self.assertEqual(v["estimated_heat_energy_7d"], 672)
        self.assertEqual(v["electrical_energy_7d"], 168)
        self.assertEqual(v["estimated_cop_7d"], 4)
        self.assertEqual(v["performance_7d_coverage"], 100)
        # Half-interval clipping at the window boundary.
        v = r.update(p.WEEK + 150, 4, 168 + 150 / 3600)
        self.assertEqual(v["estimated_cop_7d"], 4)
        self.assertEqual(v["electrical_energy_7d"], 168)

    def test_partial_window(self):
        r = p.RollingPerformance()
        r.update(0, 4, 0)
        v = r.update(300, 4, 1/12)
        self.assertEqual(v["estimated_cop_7d"], 4)
        self.assertEqual(v["electrical_energy_7d"], 0.083)
        self.assertEqual(v["estimated_heat_energy_7d"], 0.333)
        self.assertLess(v["performance_7d_coverage"], 100)

    def test_counter_reset_excludes_gap_but_keeps_other_valid_totals(self):
        values = self.week(reset=True).values(p.WEEK)
        self.assertEqual(values["estimated_cop_7d"], 4)
        self.assertLess(values["electrical_energy_7d"], 168)
        self.assertLess(values["performance_7d_coverage"], 100)

    def test_short_poll_gap_is_integrated(self):
        self.assertEqual(self.week(gap=True).values(p.WEEK)["estimated_cop_7d"], 4)

    def test_missing_and_long_gap_not_zero_filled(self):
        r = p.RollingPerformance()
        r.update(0, 4, 0)
        r.update(300, None, 1/12)
        r.update(600, 4, 2/12)
        r.update(1500, 4, 5/12)
        self.assertEqual(r.rows, [])

    def test_restart_preserves_rows_but_never_bridges_downtime(self):
        r = self.week()
        restored = p.RollingPerformance(r.saved())
        v = restored.update(p.WEEK + 300, 4, 168 + 1/12)
        self.assertEqual(v["estimated_cop_7d"], 4)
        self.assertEqual(v["electrical_energy_7d"], 167.917)
        self.assertGreater(v["performance_7d_coverage"], 99)
        self.assertLess(v["performance_7d_coverage"], 100)

    def test_report_lag_does_not_hide_existing_energy(self):
        r = self.week()
        for lag in (1, 60, 300):
            with self.subTest(lag=lag):
                values = r.values(p.WEEK+lag)
                self.assertEqual(values['estimated_cop_7d'], 4)
                self.assertEqual(values['electrical_energy_7d'], round(168-lag/3600, 3))
                self.assertEqual(values['estimated_heat_energy_7d'], round(672-4*lag/3600, 3))

    def test_empty_and_expired_windows_remain_unknown(self):
        r = p.RollingPerformance()
        for now in (0, p.WEEK+300):
            values = r.values(now)
            self.assertEqual(values['performance_7d_coverage'], 0)
            for key in ('estimated_cop_7d', 'electrical_energy_7d', 'estimated_heat_energy_7d'):
                self.assertIsNone(values[key])
        r.update(0, 4, 0)
        r.update(300, 4, 1/12)
        self.assertIsNone(r.values(p.WEEK+300)['electrical_energy_7d'])

    def test_partial_cop_uses_paired_energy_without_scaling_missing_periods(self):
        r = p.RollingPerformance()
        r.rows = [[0, 300, 1/3, 1/12], [600, 900, 1/2, 1/6]]
        values = r.values(900)
        self.assertEqual(values['estimated_heat_energy_7d'], 0.833)
        self.assertEqual(values['electrical_energy_7d'], 0.25)
        self.assertEqual(values['estimated_cop_7d'], 3.33)
        self.assertEqual(values['performance_7d_coverage'], round(600/p.WEEK*100, 3))

    def test_known_zero_energy_is_distinct_from_missing_data(self):
        r = p.RollingPerformance()
        r.rows = [[0, 300, 0, 0]]
        values = r.values(300)
        self.assertEqual(values['estimated_heat_energy_7d'], 0)
        self.assertEqual(values['electrical_energy_7d'], 0)
        self.assertIsNone(values['estimated_cop_7d'])
        self.assertGreater(values['performance_7d_coverage'], 0)

    def test_defrost_reduces_accumulated_heat(self):
        r = p.RollingPerformance()
        r.update(0, -2, 0)
        r.update(300, -2, 1/12)
        self.assertAlmostEqual(r.rows[0][2], -1/6)


class PendingCounterTests(unittest.TestCase):
    def test_standby_counter_interval_cannot_add_positive_heat(self):
        for watts in (0, 5, 10):
            r = p.RollingPerformance()
            r.observe_counter(0, 0, 0)
            r.observe_counter(300, watts*300/3600000, 300)
            r.reconcile(lambda a, b: [(a, b, 4)], 300)
            self.assertEqual(r.rows, [])
            self.assertIsNone(r.values(300)['estimated_cop_7d'])

    def pending(self):
        r = p.RollingPerformance()
        r.observe_counter(0, 10, 0)
        r.observe_counter(300, 10.1, 300)
        r.reconcile(lambda a, b: [], 300)
        self.assertEqual(r.rows, [])
        self.assertAlmostEqual(r.pending[0][2], 0.1)
        return r

    def test_late_heat_recovers_multiple_counter_intervals_once(self):
        r = self.pending()
        r.observe_counter(600, 10.2, 600)
        for _ in range(3):
            r.observe_counter(600, 10.2, 660)
            r.reconcile(lambda a, b: [(a, b, 4)], 660)
        self.assertEqual(len(r.rows), 2)
        self.assertAlmostEqual(sum(row[2] for row in r.rows), 2/3)
        self.assertAlmostEqual(sum(row[3] for row in r.rows), 0.2)
        self.assertEqual(r.values(660)['estimated_cop_7d'], 3.33)

    def test_late_heat_revises_accepted_interval_without_duplicate(self):
        r = self.pending()
        r.reconcile(lambda a, b: [(a, b, 4)], 300)
        r.reconcile(lambda a, b: [(0, 150, 4), (150, 300, -2)], 400)
        self.assertEqual(len(r.rows), 1)
        self.assertAlmostEqual(r.rows[0][2], 1/12)
        saved_row = list(r.rows[0])
        r.reconcile(lambda a, b: [], 450)
        self.assertEqual(r.rows, [saved_row])

    def test_gaps_overlaps_and_nonfinite_heat_do_not_create_coverage(self):
        r = self.pending()
        for intervals in (
            [(0, 100, 4), (101, 300, 4)],
            [(0, 200, 4), (100, 300, 4)],
            [(0, 300, 4), (100, 300, 4)],
            [(0, 300, float('nan'))],
        ):
            r.reconcile(lambda a, b: intervals, 300)
            self.assertEqual(r.rows, [])
        r.reconcile(lambda a, b: [(-60, 150, 4), (150, 360, 4)], 300)
        self.assertAlmostEqual(r.rows[0][2], 1/3)

    def test_counter_resets_invalid_values_and_long_gaps_are_not_bridged(self):
        r = self.pending()
        for timestamp, counter in ((300, 50), (200, 50), (600, 0),
                                   (1500, 0.1), (1600, None), (1700, 0.2)):
            r.observe_counter(timestamp, counter, timestamp)
        self.assertEqual(len(r.pending), 1)
        r.observe_counter(1800, 0.3, 1800)
        self.assertEqual(r.pending[-1][:2], [1700, 1800])

    def test_incomplete_intervals_expire_without_zero_filling(self):
        r = self.pending()
        r.reconcile(lambda a, b: [(a, b, 4)], p.PENDING_WINDOW+1)
        self.assertEqual(r.pending, [])
        self.assertEqual(r.rows, [])
        self.assertEqual(r.values(p.PENDING_WINDOW+1)['performance_7d_coverage'], 0)

    def test_complete_rows_outlive_correction_window(self):
        r = self.pending()
        r.reconcile(lambda a, b: [(a, b, 4)], 300)
        r.reconcile(lambda a, b: [], p.PENDING_WINDOW+1)
        self.assertEqual(r.pending, [])
        self.assertEqual(len(r.rows), 1)

    def test_restart_recovers_pending_without_bridging_downtime(self):
        r = self.pending()
        r = p.RollingPerformance(json.loads(json.dumps(r.saved())))
        for timestamp in (0, 300):
            r.observe_counter(timestamp, 10.1, 400)
        r.reconcile(lambda a, b: [(a, b, 4)], 400)
        self.assertEqual(len(r.rows), 1)
        r.observe_counter(400, 10.15, 400)
        r.observe_counter(600, 10.2, 600)
        r.reconcile(lambda a, b: [(a, b, 4)], 600)
        self.assertEqual([row[:2] for row in r.rows], [[0, 300], [400, 600]])
        r = p.RollingPerformance(json.loads(json.dumps(r.saved())))
        r.reconcile(lambda a, b: [(a, b, 4)], 600)
        self.assertEqual(len(r.rows), 2)

    def test_recovered_interval_completes_seven_day_totals(self):
        end = p.WEEK-300
        # Existing accepted history, with only the final interval incomplete.
        r = p.RollingPerformance()
        r.rows = [[0, end, 4*end/3600, end/3600]]
        r.observe_counter(end, end/3600, end)
        r.observe_counter(p.WEEK, p.WEEK/3600, p.WEEK)
        partial = r.reconcile(lambda a, b: [], p.WEEK)
        self.assertEqual(partial['estimated_cop_7d'], 4)
        self.assertEqual(partial['electrical_energy_7d'], 167.917)
        self.assertLess(partial['performance_7d_coverage'], 100)
        recovered = r.reconcile(lambda a, b: [(a, b, 4)], p.WEEK)
        self.assertEqual(recovered['estimated_heat_energy_7d'], 672)
        self.assertEqual(recovered['electrical_energy_7d'], 168)
        self.assertEqual(recovered['estimated_cop_7d'], 4)


if __name__ == "__main__":
    unittest.main()
