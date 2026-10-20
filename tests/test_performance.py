import importlib.util
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
        self.assertIsNone(v["estimated_cop_7d"])
        self.assertIsNone(v["electrical_energy_7d"])

    def test_reset_invalidates_window(self):
        self.assertIsNone(self.week(reset=True).values(p.WEEK)["estimated_cop_7d"])

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
        self.assertIsNone(v["estimated_cop_7d"])
        self.assertGreater(v["performance_7d_coverage"], 99)

    def test_defrost_reduces_accumulated_heat(self):
        r = p.RollingPerformance()
        r.update(0, -2, 0)
        r.update(300, -2, 1/12)
        self.assertAlmostEqual(r.rows[0][2], -1/6)


if __name__ == "__main__":
    unittest.main()
