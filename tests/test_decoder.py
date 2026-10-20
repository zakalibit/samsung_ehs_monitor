import importlib.util
from pathlib import Path
from datetime import datetime, timezone
import json
import unittest

p = Path(__file__).resolve().parents[1] / 'custom_components/samsung_ehs_monitor/decoder.py'
spec = importlib.util.spec_from_file_location('decoder', p)
decoder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decoder)

SAMPLE = Path(__file__).resolve().parents[2] / 'Pasted text.txt'
# Embedded self-contained sample ensures reproducible tests without personal data.
def sample():
    return {'components': {
        'main': {
            'samsungce.deviceIdentification': {'binaryId': {'value': decoder.BINARY_ID}},
            'samsungce.ehsCycleData': {
                'indoor': {'value': [{'timestamp': '2026-10-19T09:00:49Z', 'data': '3D054A4B0505000000000000000000000000000000000D5B0000000F05054B'}]},
                'outdoor': {'value': [{'timestamp': '2026-10-19T09:00:49Z', 'data': '00000000465239443A0207D000000000FFFF00260024324800000000'}]},
            }
        }
    }}

class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,10,19,9,4,tzinfo=timezone.utc)

    def test_known_snapshot(self):
        values = decoder.decode(sample(), self.now)
        self.assertEqual(values['water_return_temperature'], 19)
        self.assertEqual(values['water_flow_temperature'], 20)
        self.assertEqual(values['water_flow_rate'], 0)
        self.assertEqual(values['circulation_pump_speed'], 0)
        self.assertEqual(values['outdoor_temperature'], 3)
        self.assertEqual(values['compressor_frequency'], 0)
        self.assertIsNone(values['thermal_power'])

    def test_stale(self):
        values = decoder.decode(sample(), datetime(2026,10,19,9,30,tzinfo=timezone.utc))
        self.assertTrue(all(x is None for x in values.values()))

    def test_falls_back_to_recent_valid_packets(self):
        for invalid in (None, '', 'bad', '00'):
            data = sample()
            cycles = data['components']['main']['samsungce.ehsCycleData']
            for kind in ('indoor', 'outdoor'):
                cycles[kind]['value'].append({'timestamp': '2026-10-19T09:03:00Z', 'data': invalid})
            self.assertEqual(decoder.decode(data, self.now), decoder.decode(sample(), self.now))
            self.assertTrue(decoder.cycle_errors(data, self.now))

    def test_fallback_does_not_reuse_expired_values(self):
        data = sample()
        cycles = data['components']['main']['samsungce.ehsCycleData']
        for kind in ('indoor', 'outdoor'):
            cycles[kind]['value'].append({'timestamp': '2026-10-19T09:29:00Z', 'data': None})
        values = decoder.decode(data, datetime(2026, 10, 19, 9, 30, tzinfo=timezone.utc))
        self.assertTrue(all(value is None for value in values.values()))

    def test_newer_valid_zero_is_not_replaced_with_older_positive_value(self):
        data = sample()
        records = data['components']['main']['samsungce.ehsCycleData']['outdoor']['value']
        older = dict(records[0])
        packet = bytearray.fromhex(older['data'])
        packet[0:2] = (40).to_bytes(2, 'big')
        older.update(timestamp='2026-10-19T09:00:00Z', data=packet.hex())
        records.append(older)  # Older record deliberately appears last.
        self.assertEqual(decoder.decode(data, self.now)['compressor_frequency'], 0)

    def test_unknown_firmware(self):
        data = sample()
        data['components']['main']['samsungce.deviceIdentification']['binaryId']['value'] = 'UNKNOWN'
        self.assertTrue(all(x is None for x in decoder.decode(data, self.now).values()))

    def test_running_heat(self):
        data = sample()
        indoor = bytearray.fromhex(data['components']['main']['samsungce.ehsCycleData']['indoor']['value'][0]['data'])
        indoor[2], indoor[3], indoor[7], indoor[8] = 90, 93, 200, 75  # 35, 38 C, 20 LPM
        data['components']['main']['samsungce.ehsCycleData']['indoor']['value'][0]['data'] = indoor.hex()
        outdoor = bytearray.fromhex(data['components']['main']['samsungce.ehsCycleData']['outdoor']['value'][0]['data'])
        outdoor[0:2] = (35).to_bytes(2,'big')
        data['components']['main']['samsungce.ehsCycleData']['outdoor']['value'][0]['data'] = outdoor.hex()
        values = decoder.decode(data, self.now)
        self.assertEqual(values['water_flow_rate'], 20)
        self.assertAlmostEqual(values['thermal_power'], 4.188)

if __name__ == '__main__':
    unittest.main()
