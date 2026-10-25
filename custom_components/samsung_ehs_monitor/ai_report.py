"""Provider-neutral, allowlisted performance reports; no raw API payloads."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

METRICS = {
    'outdoor_temperature': '°C', 'room_temperature': '°C',
    'water_flow_temperature': '°C', 'water_return_temperature': '°C',
    'discharge_temperature': '°C', 'water_flow_rate': 'L/min',
    'compressor_frequency': 'Hz', 'compressor_target_frequency': 'Hz',
    'circulation_pump_speed': '%', 'electrical_input_power': 'W',
    'net_thermal_power': 'kW',
}
CONTEXT = ('operating_mode', 'heating_status', 'temperature_reference', 'cooling_setpoint')


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if isfinite(result) else None


def normalized(value, unit, expected):
    value = numeric(value)
    if value is None:
        return None
    if unit == expected:
        return value
    if expected == '°C' and unit == '°F':
        return (value - 32) * 5 / 9
    if (unit, expected) == ('kW', 'W'):
        return value * 1000
    if (unit, expected) == ('W', 'kW'):
        return value / 1000
    return None


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def build_report(saved, current, histories, zone, days, now, warnings=()):
    """Use local calendar days, actual elapsed seconds, and duration-weighted history.

    Histories contain only (UTC epoch, state, unit) tuples for allowlisted metrics.
    Recorder holds are reported as recorded, not proof of fresh device telemetry.
    """
    tz = ZoneInfo(zone)
    today = datetime.fromtimestamp(now, tz).date()
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, datetime.min.time(), tz).timestamp()
    hours = []
    t = start
    while t < now:
        end = min(t + 3600, now)
        hours.append({'start_utc': iso(t), 'end_utc': iso(end),
                      'local_start': datetime.fromtimestamp(t, tz).isoformat(),
                      'duration_seconds': end - t, 'metrics': {},
                      'paired_heat_kwh': None, 'paired_electricity_kwh': None,
                      'paired_seconds': 0, 'paired_coverage_percent': 0, 'cop': None})
        t = end
    # Minute buckets already contain only the energy and seconds actually observed.
    for row in saved.get('intervals', []):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            continue
        stamp, heat, electricity, seconds = map(numeric, row)
        if None in (stamp, heat, electricity, seconds) or not 0 < seconds <= 60.001 or electricity < 0:
            continue
        if stamp < start or stamp >= now:
            continue
        index = int((stamp - start) // 3600)
        hour = hours[index]
        for key, value in [('paired_heat_kwh', heat), ('paired_electricity_kwh', electricity)]:
            hour[key] = (hour[key] or 0) + value
        hour['paired_seconds'] += seconds
    for key, unit in METRICS.items():
        points = sorted(histories.get(key, []), key=lambda point: point[0])
        totals = {}
        for i, (stamp, state, source_unit) in enumerate(points):
            value = normalized(state, source_unit, unit)
            if value is None:
                continue  # Invalid states still terminate the preceding hold.
            a = max(start, stamp)
            b = min(now, points[i+1][0] if i+1 < len(points) else now)
            while a < b:
                index = int((a - start) // 3600)
                stop = min(b, start + (index + 1) * 3600)
                total = totals.setdefault(index, [0, 0, value, value])
                total[0] += value * (stop - a)
                total[1] += stop - a
                total[2] = min(total[2], value)
                total[3] = max(total[3], value)
                a = stop
        for index, (weighted, seconds, low, high) in totals.items():
            hours[index]['metrics'][key] = {
                'mean': round(weighted / seconds, 4), 'min': round(low, 4),
                'max': round(high, 4), 'unit': unit,
                'recorded_seconds': seconds,
                'recorded_coverage_percent': round(seconds / hours[index]['duration_seconds'] * 100, 2)}
    for hour in hours:
        hour['paired_coverage_percent'] = round(min(100, hour['paired_seconds'] / hour['duration_seconds'] * 100), 2)
        heat, electricity = hour['paired_heat_kwh'], hour['paired_electricity_kwh']
        hour['cop'] = round(heat / electricity, 4) if heat is not None and electricity and heat > 0 else None
        for key in ('paired_heat_kwh', 'paired_electricity_kwh'):
            if hour[key] is not None:
                hour[key] = round(hour[key], 6)
    daily = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        a = datetime.combine(day, datetime.min.time(), tz).timestamp()
        b = min(now, datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).timestamp())
        row = saved.get('days', {}).get(day.isoformat())
        seconds = numeric(row.get('seconds')) if isinstance(row, dict) else None
        heat = numeric(row.get('heat')) if isinstance(row, dict) else None
        electricity = numeric(row.get('electricity')) if isinstance(row, dict) else None
        if not seconds:
            heat = electricity = None
        daily.append({'date': day.isoformat(), 'elapsed_seconds': b-a,
                      'paired_seconds': seconds or 0,
                      'coverage_percent': round(min(100, (seconds or 0) / (b-a) * 100), 2) if b > a else 0,
                      'heat_kwh': heat, 'electricity_kwh': electricity,
                      'cop': heat / electricity if heat is not None and heat > 0 and electricity and electricity > 0 else None})
    valid_days = [row for row in daily if row['heat_kwh'] is not None and row['electricity_kwh'] is not None]
    heat = sum(row['heat_kwh'] for row in valid_days)
    electricity = sum(row['electricity_kwh'] for row in valid_days)
    settings = {key: value for key, value in current.items()
                if key in CONTEXT or key.startswith('fsv_') and key[4:].isdigit()}
    return {
        'schema_version': 1, 'report_type': 'samsung_ehs_performance',
        'generated_at_utc': iso(now), 'timezone': zone,
        'period': {'start_utc': iso(start), 'end_utc': iso(now), 'calendar_days': days, 'includes_partial_today': True},
        'sources': {'electricity': 'configured external power sensor' if saved.get('source') else 'not configured',
                    'heat': 'estimated from water flow and temperature difference',
                    'hourly_metrics': 'Home Assistant Recorder; duration-weighted recorded states'},
        'current_settings': settings,
        'summary': {'heat_kwh': heat if valid_days else None,
                    'electricity_kwh': electricity if valid_days else None,
                    'cop': heat / electricity if valid_days and electricity > 0 and heat > 0 else None,
                    'paired_coverage_percent': round(sum(row['paired_seconds'] for row in daily) / (now-start) * 100, 2) if now > start else 0},
        'daily': daily, 'hourly': hours,
        'limitations': [
            'Heat output is estimated, not a calibrated heat-meter measurement.',
            'COP is paired heat energy divided by paired electrical energy; never average instantaneous COP.',
            'Missing data is unknown, not zero; check coverage before comparing periods.',
            'Current settings are a snapshot at export time, not historical settings.',
            'Recorder retention and exclusions limit hourly metrics; recorded holds do not establish source freshness.',
            'Daily totals may include older baseline data without hourly ledger detail.',
            'Heating, hot-water and defrost energy are not separated; do not infer mode-specific efficiency.',
            'No settings change log, building specification, tariff, or manufacturer operating limits are included.',
        ],
        'warnings': list(warnings),
        'analysis_guidance': 'Assess data quality first. Compare similar weather and comfort conditions. Explain evidence, uncertainty and a reversible observation plan for each suggested change. Request model-specific documentation before recommending installer settings. This report cannot apply settings.',
    }
