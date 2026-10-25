"""Pure calculation helpers; rolling windows never fill missing data with zero."""
from __future__ import annotations

from math import isfinite

WEEK = 7 * 86400
MAX_GAP = 660  # Five-minute polls; do not bridge extended outages.
PENDING_WINDOW = 1800  # Matches the retained thermal snapshot window.


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
COP_MIN_POWER_W = 10  # Standby readings cannot support a meaningful heat-pump COP.


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
    if not number(heat) or heat <= 0 or not number(watts) or watts <= COP_MIN_POWER_W:
        return None
    return round(heat * 1000 / watts, 2)


class RollingPerformance:
    """Paired heat/counter intervals stored in kWh, clipped to the last 168h.

    Interpolate both boundary energies linearly within each poll interval.
    Negative counter jumps and long gaps invalidate that interval. Missing heat
    can be recovered from late snapshots within the retained 30-minute window.
    Publish totals over available valid paired intervals, with window coverage.
    """

    def __init__(self, saved=None):
        saved = saved if isinstance(saved, dict) else {}
        self.rows = [list(row) for row in saved.get("rows", [])]
        self.pending = [list(row) for row in saved.get("pending", [])
                        if isinstance(row, (list, tuple)) and len(row) == 3
                        and all(number(v) for v in row) and 0 < row[1]-row[0] <= MAX_GAP and row[2] >= 0]
        self.counter_watermark = max([row[1] for row in self.rows + self.pending], default=None)
        saved_time = saved.get("counter_watermark")
        if number(saved_time):
            self.counter_watermark = max(self.counter_watermark or saved_time, saved_time)
        self.previous = None  # Never integrate unobserved time across a restart.

    def observe_counter(self, timestamp, counter_kwh, now):
        """Retain the measured electrical interval even when heat is not ready."""
        self._prune(now)
        if not number(timestamp) or not number(counter_kwh) or counter_kwh < 0:
            self.previous = None
            return
        if self.previous is None:
            # Do not create an overlapping interval from a stale report on reload.
            if self.counter_watermark is not None and timestamp <= self.counter_watermark:
                return
        else:
            before, _, old_counter = self.previous
            if timestamp <= before:
                return
            delta = counter_kwh - old_counter
            if 0 < timestamp-before <= MAX_GAP and delta >= 0:
                self.pending.append([before, timestamp, delta])
        self.previous = (timestamp, None, counter_kwh)
        self.counter_watermark = timestamp

    def reconcile(self, heat_intervals, now):
        """Retry and revise recent counter intervals using all currently known heat."""
        self._prune(now)
        rows = {(row[0], row[1]): row for row in self.rows}
        for start, end, electricity in self.pending:
            intervals = heat_intervals(start, end)
            valid = [row for row in intervals
                     if isinstance(row, (list, tuple)) and len(row) == 3
                     and all(number(v) for v in row) and row[1] > row[0]]
            cursor, heat, complete = start, 0.0, True
            for a, b, q in sorted(valid):
                a, b = max(a, start), min(b, end)
                if b <= a:
                    continue
                if abs(a-cursor) > 0.001:
                    complete = False
                    break  # A gap or overlap is not complete paired coverage.
                heat += q * (b-a) / 3600
                cursor = b
            if complete and abs(cursor-end) <= 0.001:
                if heat > 0 and electricity * 3600000 / (end-start) <= COP_MIN_POWER_W:
                    rows.pop((start, end), None)
                    continue  # Counter interval contradicts positive cloud heat.
                rows[(start, end)] = [start, end, heat, electricity]
            # Incomplete retries never remove an already accepted interval.
        self.rows = [rows[key] for key in sorted(rows)]
        return self.values(now)

    def _prune(self, now):
        self.rows = [row for row in self.rows if row[1] > now-WEEK]
        self.pending = [row for row in self.pending if row[0] >= now-PENDING_WINDOW]

    def update(self, now, heat, counter_kwh, *, heat_intervals=None):
        if heat_intervals is not None:
            self.observe_counter(now, counter_kwh, now)
            return self.reconcile(lambda start, end: heat_intervals, now)
        if self.previous is not None and now <= self.previous[0]:
            return self.values(now)  # Repeated/out-of-order counter report.
        valid = number(heat) and number(counter_kwh) and counter_kwh >= 0
        if valid and self.previous is not None:
            before, old_heat, old_counter = self.previous
            dt = now - before
            delta = counter_kwh - old_counter
            if 0 < dt <= MAX_GAP and delta >= 0:
                # Trapezoid on signed thermal samples; counter difference on electricity.
                self.rows.append([before, now, (old_heat + heat) / 2 * dt / 3600, delta])
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
        has_data = seconds > 0
        return {
            "performance_7d_coverage": round(min(100, seconds / WEEK * 100), 3),
            "estimated_heat_energy_7d": round(heat, 3) if has_data else None,
            "electrical_energy_7d": round(electricity, 3) if has_data else None,
            "estimated_cop_7d": round(heat / electricity, 2)
            if has_data and electricity > 0 and heat > 0 else None,
        }

    def saved(self):
        return {"rows": [list(row) for row in self.rows],
                "pending": [list(row) for row in self.pending],
                "counter_watermark": self.counter_watermark}
