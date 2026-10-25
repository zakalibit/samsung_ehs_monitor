"""AI report accounting, missing history, unit handling and privacy boundaries."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1] / 'custom_components/samsung_ehs_monitor'
spec = importlib.util.spec_from_file_location('ai_report', BASE / 'ai_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


class AIReportTests(unittest.TestCase):
    def build(self, saved=None, current=None, histories=None, zone='UTC', days=1,
              now='2026-10-25T02:00:00+00:00'):
        return report.build_report(saved or {}, current or {}, histories or {},
                                   zone, days, stamp(now))

    def test_paired_energy_uses_ratio_of_totals_and_keeps_partial_minutes(self):
        start = stamp('2026-10-25T00:00:00+00:00')
        saved = {'source': 'secret_entity', 'days': {'2026-10-25': {'heat': 10, 'electricity': 3, 'seconds': 90}},
                 'intervals': [[start, 4, 1, 60], [start + 60, 6, 2, 30]]}
        result = self.build(saved=saved)
        self.assertAlmostEqual(result['summary']['cop'], 10/3)
        self.assertAlmostEqual(result['hourly'][0]['cop'], round(10/3, 4))
        self.assertEqual(result['hourly'][0]['paired_seconds'], 90)
        self.assertIsNone(result['hourly'][1]['cop'])
        self.assertNotIn('secret_entity', json.dumps(result))

    def test_weighted_temperatures_unavailable_breaks_and_units(self):
        a = stamp('2026-10-25T00:00:00+00:00')
        result = self.build(histories={'room_temperature': [
            (a, '68', '°F'), (a+900, '30', '°C'), (a+1800, 'unavailable', '°C')],
            'electrical_input_power': [(a, '2', 'kW')],
            'outdoor_temperature': [(a, 'NaN', '°C')]})
        indoor = result['hourly'][0]['metrics']['room_temperature']
        self.assertEqual(indoor['mean'], 25)
        self.assertEqual(indoor['recorded_coverage_percent'], 50)
        self.assertEqual(result['hourly'][0]['metrics']['electrical_input_power']['mean'], 2000)
        self.assertNotIn('outdoor_temperature', result['hourly'][0]['metrics'])
        self.assertNotIn('room_temperature', result['hourly'][1]['metrics'])

    def test_missing_days_are_unknown_and_current_settings_allowlisted(self):
        result = self.build(current={'fsv_2011': {'value': -10}, 'access_token': 'secret',
                                     'device_id': 'private', 'fsv_bad': 'private'}, days=2)
        self.assertEqual(len(result['daily']), 2)
        self.assertIsNone(result['daily'][0]['heat_kwh'])
        self.assertIsNone(result['summary']['cop'])
        self.assertEqual(result['current_settings'], {'fsv_2011': {'value': -10}})
        self.assertNotIn('private', json.dumps(result))
        json.dumps(result, allow_nan=False)

    def test_dst_days_have_23_and_25_hours(self):
        for now, seconds, hours in [('2026-03-30T00:00:00+01:00', 82800, 23),
                                     ('2026-10-26T00:00:00+00:00', 90000, 25)]:
            result = self.build(zone='Europe/Dublin', now=now, days=2)
            self.assertEqual(result['daily'][0]['elapsed_seconds'], seconds)
            self.assertEqual(len(result['hourly']), hours)
            self.assertEqual(len({h['start_utc'] for h in result['hourly']}), hours)

    def test_zero_electricity_and_negative_heat_cannot_produce_positive_cop(self):
        for heat, electricity in [(10, 0), (-1, 2)]:
            result = self.build(saved={'days': {'2026-10-25': {
                'heat': heat, 'electricity': electricity, 'seconds': 60}}})
            self.assertIsNone(result['summary']['cop'])
            self.assertIsNone(result['daily'][0]['cop'])


if __name__ == '__main__':
    unittest.main()
