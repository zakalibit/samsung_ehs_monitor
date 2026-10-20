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


def parse_payload(record, length: int, now=None):
    """Return validated bytes only for recent timestamped expected-size data."""
    if not isinstance(record, dict):
        return None
    try:
        packet = bytes.fromhex(record["data"])
        if len(packet) != length:
            return None
        timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        now = now or datetime.now(timezone.utc)
        age = (now - timestamp).total_seconds()
        if not 0 <= age <= MAX_AGE_SECONDS:
            return None
        return packet
    except (TypeError, KeyError, ValueError, AttributeError):
        return None


def latest_valid_record(data, kind, length, now=None):
    """Return the freshest usable packet together with its source timestamp."""
    records = field(data, "main", "samsungce.ehsCycleData", kind)
    if not isinstance(records, list):
        return None
    records = sorted((row for row in records if isinstance(row, dict)),
                     key=lambda row: record_time(row) or float("-inf"), reverse=True)
    return next((row for row in records if parse_payload(row, length, now) is not None), None)


def latest_valid_payload(data, kind, length, now=None):
    record = latest_valid_record(data, kind, length, now)
    return parse_payload(record, length, now) if record is not None else None


def thermal_timestamps(data, now=None):
    """Actual timestamps of the packets selected for the live heat estimate."""
    inside = latest_valid_record(data, "indoor", 31, now)
    outside = latest_valid_record(data, "outdoor", 28, now)
    return record_time(inside), record_time(outside)


def cycle_errors(data: dict, now=None) -> list[str]:
    """Explain rejected packets without exposing their contents or device IDs."""
    if field(data, "main", "samsungce.deviceIdentification", "binaryId") != BINARY_ID:
        return ["Unsupported or missing binary ID; cycle decoding skipped"]
    now = now or datetime.now(timezone.utc)
    errors = []
    for kind, length in (("indoor", 31), ("outdoor", 28)):
        record = latest(data, kind)
        if record is None:
            errors.append(f"{kind}: missing cycle packet")
            continue
        message = None
        try:
            payload = bytes.fromhex(record["data"])
        except (KeyError, TypeError, ValueError):
            message = "invalid or missing hexadecimal payload"
        else:
            if len(payload) != length:
                message = f"incorrect payload length (expected {length} bytes)"
            else:
                timestamp = record_time(record)
                current = (now or datetime.now(timezone.utc)).timestamp()
                if timestamp is None:
                    message = "missing or invalid timestamp (timezone required)"
                elif timestamp > current:
                    message = f"packet timestamp is {timestamp-current:.1f} seconds in the future"
                elif current-timestamp > MAX_AGE_SECONDS:
                    message = f"packet is {(current-timestamp)/60:.1f} minutes old (limit 15 minutes)"
        if message:
            packet_time = record_time(record)
            decoded = datetime.fromtimestamp(packet_time, timezone.utc).isoformat() if packet_time is not None else "invalid/missing"
            system = now.astimezone(timezone.utc).isoformat()
            difference = f"{now.timestamp()-packet_time:.3f} seconds" if packet_time is not None else "unknown"
            message += f"; packet timestamp (UTC): {decoded}; system timestamp (UTC): {system}; system minus packet: {difference}"
            fallback = latest_valid_record(data, kind, length, now)
            suffix = "; using an older valid snapshot" if fallback is not None else "; no recent valid snapshot available"
            errors.append(f"{kind}: {message}{suffix}")
    return errors


def decode(data: dict, now=None) -> dict:
    """Return confirmed TP1X readings or None. Never treat old data as live."""
    binary_id = field(data, "main", "samsungce.deviceIdentification", "binaryId")
    result = {name: None for name in (
        "water_return_temperature", "water_flow_temperature", "water_flow_rate",
        "circulation_pump_speed", "outdoor_temperature", "compressor_frequency",
        "compressor_target_frequency", "discharge_temperature", "thermal_power",
    )}
    if binary_id != BINARY_ID:
        return result
    inside = latest_valid_payload(data, "indoor", 31, now)
    outside = latest_valid_payload(data, "outdoor", 28, now)
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
    # Thermal output is sensible water heat; exclude negative/reverse heat in defrost.
    # Only calculate when compressor actually running and recent flow & return data exist.
    flow = result["water_flow_rate"]
    hot = result["water_flow_temperature"]
    cold = result["water_return_temperature"]
    freq = result["compressor_frequency"]
    if (all(isinstance(x, (int, float)) for x in (flow, hot, cold, freq))
            and freq > 0 and flow >= 1 and 0 < hot - cold <= 20):
        result["thermal_power"] = round(0.0698 * flow * (hot - cold), 3)
    return result
