"""Conservative TP1X Samsung EHS SmartThings cycle data decoder.

Reverse-engineered mapping: https://www.dwellsee.com/decoding-guide/
Only exact known binary ID and payload lengths are accepted. Read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone

BINARY_ID = "TP1X_DA_AC_EHS_01001_0000"
MAX_AGE_SECONDS = 900  # SmartThings cycle samples normally update every 5 minutes.


def field(data: dict, component: str, capability: str, attribute: str):
    return (((data.get("components") or {}).get(component) or {}).get(capability) or {}).get(attribute, {}).get("value")


def record_time(record):
    try:
        value = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        return value.timestamp() if value.tzinfo is not None else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def latest(data: dict, kind: str):
    values = field(data, "main", "samsungce.ehsCycleData", kind)
    if not isinstance(values, list):
        return None
    records = [row for row in values if isinstance(row, dict)]
    return max(records, key=lambda row: record_time(row) or float("-inf"), default=None)


def cycle_failures(data, now=None, samples=None, fallback=None):
    """Stable failure keys allow repeated reports to update one diagnostic entry."""
    if field(data, "main", "samsungce.deviceIdentification", "binaryId") != BINARY_ID:
        return [("binary_id", "Unsupported or missing binary ID; cycle decoding skipped")]
    now = now or datetime.now(timezone.utc)
    current = now.timestamp()
    errors = []
    for kind in ("indoor", "outdoor"):
        if samples is None:
            records = field(data, "main", "samsungce.ehsCycleData", kind)
            decoded = [decode_packet(row, kind) for row in records] if isinstance(records, list) else []
        else:
            decoded = samples[kind]
        sample = max(decoded, key=lambda row: row.timestamp if row.timestamp is not None else float("-inf"), default=None)
        if sample is None:
            errors.append((f"{kind}:missing", f"{kind}: missing cycle packet"))
            continue
        timestamp = sample.timestamp
        message, reason = sample.failure, sample.failure
        if not message and timestamp > current:
            reason = "future"
            message = f"packet timestamp is {timestamp-current:.1f} seconds in the future"
        elif not message and current-timestamp > MAX_AGE_SECONDS:
            reason = "stale"
            message = f"packet is {(current-timestamp)/60:.1f} minutes old (limit 15 minutes)"
        if not message:
            continue
        decoded_time = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else "invalid/missing"
        difference = f"{current-timestamp:.3f} seconds" if timestamp is not None else "unknown"
        message += (f"; packet timestamp (UTC): {decoded_time}; system timestamp (UTC): {now.astimezone(timezone.utc).isoformat()}"
                    f"; system minus packet: {difference}")
        has_fallback = fallback[kind] if fallback is not None else any(row.usable(current) for row in decoded)
        message += "; using an older valid snapshot" if has_fallback else "; no recent valid snapshot available"
        errors.append((f"{kind}:{timestamp}:{reason}", f"{kind}: {message}"))
    return errors


def cycle_errors(data: dict, now=None) -> list[str]:
    return [message for key, message in cycle_failures(data, now)]


class CycleSample:
    """One decoded source packet; freshness is evaluated at the consumer's time."""

    def __init__(self, timestamp, readings, failure=None):
        self.timestamp = timestamp
        self.readings = readings
        self.failure = failure
        self.valid = failure is None

    def usable(self, when):
        return self.valid and 0 <= when-self.timestamp <= MAX_AGE_SECONDS


def packet_readings(inside=None, outside=None):
    """Decode the known byte layout, preserving positive heating output semantics."""
    result = {name: None for name in (
        "water_return_temperature", "water_flow_temperature", "water_flow_rate",
        "circulation_pump_speed", "outdoor_temperature", "compressor_frequency",
        "compressor_target_frequency", "discharge_temperature", "thermal_power",
    )}
    if inside is not None:
        result.update(
            water_return_temperature=inside[2] - 55,
            water_flow_temperature=inside[3] - 55,
            water_flow_rate=round(inside[7] / 10, 1),
            circulation_pump_speed=inside[8] if inside[8] <= 100 else None,
        )
    if outside is not None:
        result.update(
            outdoor_temperature=outside[8] - 55,
            compressor_frequency=int.from_bytes(outside[0:2], "big"),
            compressor_target_frequency=int.from_bytes(outside[2:4], "big"),
            discharge_temperature=outside[5] - 55,
        )
    return result


def decode_packet(record, kind):
    """Validate and decode once, independently of the current clock."""
    timestamp = record_time(record)
    if not isinstance(record, dict):
        return CycleSample(timestamp, {}, "missing cycle packet")
    try:
        payload = bytes.fromhex(record["data"])
    except (KeyError, TypeError, ValueError):
        return CycleSample(timestamp, {}, "invalid or missing hexadecimal payload")
    length = 31 if kind == "indoor" else 28
    if len(payload) != length:
        return CycleSample(timestamp, {}, f"incorrect payload length (expected {length} bytes)")
    if timestamp is None:
        return CycleSample(None, {}, "missing or invalid timestamp (timezone required)")
    values = packet_readings(**{"inside" if kind == "indoor" else "outside": payload})
    return CycleSample(timestamp, {key: value for key, value in values.items()
                                   if value is not None and key != "thermal_power"})


def combine_samples(inside=None, outside=None):
    values = packet_readings()
    for sample in (inside, outside):
        if sample is not None:
            values.update(sample.readings)
    flow, hot, cold, freq = (values[key] for key in (
        "water_flow_rate", "water_flow_temperature", "water_return_temperature", "compressor_frequency"))
    if (all(isinstance(x, (int, float)) for x in (flow, hot, cold, freq))
            and freq > 0 and flow >= 1 and 0 < hot-cold <= 20):
        values["thermal_power"] = round(0.0698*flow*(hot-cold), 3)
    return values


def decode(data: dict, now=None) -> dict:
    """Compatibility helper; the integration shares SnapshotHistory's cached samples."""
    if field(data, "main", "samsungce.deviceIdentification", "binaryId") != BINARY_ID:
        return combine_samples()
    when = (now or datetime.now(timezone.utc)).timestamp()
    selected = []
    for kind in ("indoor", "outdoor"):
        records = field(data, "main", "samsungce.ehsCycleData", kind)
        samples = [decode_packet(row, kind) for row in records] if isinstance(records, list) else []
        selected.append(max((sample for sample in samples if sample.usable(when)),
                            key=lambda sample: sample.timestamp, default=None))
    return combine_samples(*selected)
