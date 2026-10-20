"""Pure calculation helpers; rolling windows never fill missing data with zero."""
from __future__ import annotations

from math import isfinite

WEEK = 7 * 86400
MAX_GAP = 660  # Five-minute polls; do not bridge extended outages.


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def electrical_watts(raw, presentation_id, *, clamp_negative=False):
    """Match HA SmartThings: EHS presentation raw kW -> W; otherwise raw W."""
    if not number(raw) or not isinstance(presentation_id, str) or not presentation_id:
        return None
    if raw < 0:
        return 0.0 if clamp_negative else None
    return raw * 1000 if "EHS" in presentation_id else raw


def external_watts(state, unit, *, clamp_negative=False):
    """Convert an HA power state to W; never reinterpret energy or clamp export."""
    if isinstance(state, bool):
        return None
    try:
        value = float(state)
    except (TypeError, ValueError):
        return None
    if not isfinite(value):
        return None
    if value < 0:
        if not clamp_negative:
            return None
        value = 0.0
    if unit == "W":
        return value
    if unit == "kW":
        return value * 1000
    return None


LIVE_COP_MAX_SKEW = 90


class PowerHistory:
    """A bounded history of actual source reports, separate from heartbeat holds."""

    def __init__(self):
        self.samples = {}

    def record(self, timestamp, watts, now):
        self.samples = {t: value for t, value in self.samples.items() if now-1800 <= t <= now}
        if number(timestamp) and now-1800 <= timestamp <= now:
            self.samples[timestamp] = watts if number(watts) and watts >= 0 else None
        # Bound memory even for power meters publishing many times per second.
        if len(self.samples) > 4096:
            self.samples = dict(sorted(self.samples.items())[-4096:])

    def latest(self):
        """Return the newest report, preserving invalid/zero readings."""
        if not self.samples:
            return None
        when = max(self.samples)
        return when, self.samples[when]

    def closest(self, timestamp):
        if not number(timestamp) or not self.samples:
            return None
        # Prefer the earlier report when the distances are equal. Do not skip
        # invalid or zero samples to cherry-pick an older positive denominator.
        when = min(self.samples, key=lambda t: (abs(t-timestamp), t))
        if abs(when-timestamp) > LIVE_COP_MAX_SKEW:
            return None
        return when, self.samples[when]


def net_heat_kw(metrics):
    """Signed sensible-water heat including defrost; zero only for fresh off data."""
    flow, hot, cold, frequency = (metrics.get(k) for k in (
        "water_flow_rate", "water_flow_temperature", "water_return_temperature",
        "compressor_frequency"))
    if not all(number(x) for x in (flow, hot, cold, frequency)):
        return None
    if flow < 0 or frequency < 0 or abs(hot - cold) > 20:
        return None
    if frequency == 0:
        return 0.0
    if flow < 1:
        return None
    return round(0.0698 * flow * (hot - cold), 3)


def instantaneous_cop(heat, watts):
    if not number(heat) or heat <= 0 or not number(watts) or watts <= 0:
        return None
    return round(heat * 1000 / watts, 2)


class RollingPerformance:
    """Paired heat/counter intervals stored in kWh, clipped to the last 168h.

    Interpolate both boundary energies linearly within each poll interval.
    Negative counter jumps, missing inputs and long gaps invalidate that interval.
    A complete window is required before publishing any seven-day total or COP.
    """

    def __init__(self, saved=None):
        self.rows = (saved or {}).get("rows", [])
        self.previous = None  # Never integrate unobserved time across a restart.

    def update(self, now, heat, counter_kwh, *, heat_intervals=None):
        if self.previous is not None and now <= self.previous[0]:
            return self.values(now)  # Repeated/out-of-order counter report.
        valid = number(heat) and number(counter_kwh) and counter_kwh >= 0
        if valid and self.previous is not None:
            before, old_heat, old_counter = self.previous
            dt = now - before
            delta = counter_kwh - old_counter
            if 0 < dt <= MAX_GAP and delta >= 0:
                # Trapezoid on signed thermal samples; counter difference on electricity.
                if heat_intervals is None:
                    self.rows.append([before, now, (old_heat + heat) / 2 * dt / 3600, delta])
                elif abs(sum(b-a for a, b, q in heat_intervals) - dt) < 0.001:
                    thermal_kwh = sum(q * (b-a) / 3600 for a, b, q in heat_intervals)
                    self.rows.append([before, now, thermal_kwh, delta])
        self.previous = (now, heat, counter_kwh) if valid else None
        self.rows = [r for r in self.rows if r[1] > now - WEEK]
        return self.values(now)

    def values(self, now):
        heat = electricity = seconds = 0.0
        for start, end, q, e in self.rows:
            overlap = max(0, min(end, now) - max(start, now - WEEK))
            fraction = overlap / (end - start)
            heat += q * fraction
            electricity += e * fraction
            seconds += overlap
        complete = seconds >= WEEK - 0.001
        return {
            "performance_7d_coverage": round(min(100, seconds / WEEK * 100), 3),
            "estimated_heat_energy_7d": round(heat, 3) if complete else None,
            "electrical_energy_7d": round(electricity, 3) if complete else None,
            "estimated_cop_7d": round(heat / electricity, 2)
            if complete and electricity > 0 and heat > 0 else None,
        }

    def saved(self):
        return {"rows": self.rows}
