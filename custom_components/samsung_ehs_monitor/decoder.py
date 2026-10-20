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


def latest(data: dict, kind: str):
    values = field(data, "main", "samsungce.ehsCycleData", kind)
    if not isinstance(values, list) or not values:
        return None
    return values[-1] if isinstance(values[-1], dict) else None


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
    except (TypeError, KeyError, ValueError):
        return None


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
    inside = parse_payload(latest(data, "indoor"), 31, now)
    outside = parse_payload(latest(data, "outdoor"), 28, now)
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
