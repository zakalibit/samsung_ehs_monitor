"""Local-calendar daily energy ratios from paired heat and external power samples."""
from datetime import datetime, time, timedelta
from math import isfinite
from zoneinfo import ZoneInfo


class DailyPerformance:
    def __init__(self, zone, source, saved=None):
        self.zone = ZoneInfo(zone)
        self.source = source
        saved = saved if isinstance(saved, dict) else {}
        compatible = saved.get("zone") == zone and saved.get("source") == source
        saved = saved if compatible else {}
        # Older stores contain only totals. Preserve these as an immutable baseline.
        baseline = saved.get("baseline_days", {}) if saved.get("version") == 2 else saved.get("days", {})
        self.baseline_days = {day: dict(row) for day, row in baseline.items()}
        self.days = {day: dict(row) for day, row in self.baseline_days.items()}
        self.ledger = {}
        for row in saved.get("intervals", []):
            if not isinstance(row, (list, tuple)) or len(row) != 4 or not all(self._number(v) for v in row):
                continue
            start, heat, electricity, seconds = row
            if electricity < 0 or not 0 < seconds <= 60.001:
                continue
            try:
                day = datetime.fromtimestamp(start, self.zone).date().isoformat()
            except (OverflowError, OSError, ValueError):
                continue
            if start in self.ledger:
                continue
            self.ledger[start] = [heat, electricity, seconds]
            total = self.days.setdefault(day, {"heat": 0.0, "electricity": 0.0, "seconds": 0.0})
            total["heat"] += heat
            total["electricity"] += electricity
            total["seconds"] += seconds
        self._last_prune_day = None
        self.replayed = self._valid_intervals(saved.get("replayed", []))

    @staticmethod
    def _number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)

    def _valid_intervals(self, intervals):
        result = []
        for row in intervals if isinstance(intervals, (list, tuple)) else []:
            if (isinstance(row, (list, tuple)) and len(row) == 4
                    and all(self._number(v) for v in row)):
                a, b, heat, watts = row
                if b > a and watts >= 0:
                    result.append((a, b, heat, watts))
        accepted = []
        for row in sorted(result):
            if not accepted or row[0] >= accepted[-1][1]:
                accepted.append(row)
        return accepted

    def boundary(self, day):
        start = datetime.combine(day, time.min, self.zone)
        end = datetime.combine(day + timedelta(days=1), time.min, self.zone)
        return start.timestamp(), end.timestamp()

    def replay(self, now, intervals, minimum_cop_power_watts=10):
        """Replace covered periods only; missing data never removes accepted energy."""
        intervals = intervals if self.source else []
        cutoff = now - 1800
        old = [(max(a, cutoff), b, heat, watts) for a, b, heat, watts in self.replayed if b > cutoff]
        incoming = [(max(a, cutoff), min(b, now), heat, watts)
                    for a, b, heat, watts in self._valid_intervals(intervals)
                    if b > cutoff and a < now]
        # Incoming valid measurements may revise an accepted hold. A gap or a bad
        # response cannot revise it to zero or erase it from the interval ledger.
        points = sorted({t for row in old + incoming for t in row[:2]})
        merged = []
        old_index = incoming_index = 0
        for a, b in zip(points, points[1:]):
            while old_index < len(old) and old[old_index][1] <= a:
                old_index += 1
            while incoming_index < len(incoming) and incoming[incoming_index][1] <= a:
                incoming_index += 1
            row = incoming[incoming_index] if incoming_index < len(incoming) else None
            if row is None or row[0] > a:
                row = old[old_index] if old_index < len(old) else None
            if row is None or row[0] > a or row[1] < b:
                continue
            heat, watts = row[2:]
            # Explicitly conflicting power can retract an earlier accepted hold.
            # Missing telemetry still preserves it; do not turn uncertainty into zero heat.
            if heat > 0 and watts <= minimum_cop_power_watts:
                continue
            if merged and merged[-1][1] == a and merged[-1][2:] == (heat, watts):
                merged[-1] = (merged[-1][0], b, heat, watts)
            else:
                merged.append((a, b, heat, watts))
        for a, b, heat, watts in old:
            self._apply(a, b, heat, watts, -1)
        for a, b, heat, watts in merged:
            self._apply(a, b, heat, watts, 1)
        self.replayed = merged
        self._prune(now)

    def _apply(self, start, end, heat, watts, sign):
        while start < end:
            day = datetime.fromtimestamp(start, self.zone).date()
            _, midnight = self.boundary(day)
            bucket = int(start // 60) * 60
            stop = min(end, midnight, bucket + 60)
            seconds = (stop - start) * sign
            q, e = heat * seconds / 3600, watts * seconds / 3600000
            row = self.days.setdefault(day.isoformat(), {"heat": 0.0, "electricity": 0.0, "seconds": 0.0})
            row["heat"] += q
            row["electricity"] += e
            row["seconds"] += seconds
            entry = self.ledger.setdefault(bucket, [0.0, 0.0, 0.0])
            entry[0] += q
            entry[1] += e
            entry[2] += seconds
            if abs(entry[2]) < 1e-6:
                self.ledger.pop(bucket)
            if abs(row["seconds"]) < 1e-6:
                row.update(heat=0.0, electricity=0.0, seconds=0.0)
            start = stop

    def _prune(self, now):
        today = datetime.fromtimestamp(now, self.zone).date()
        if today == self._last_prune_day:
            return
        self._last_prune_day = today
        oldest_day = today - timedelta(days=34)
        oldest = oldest_day.isoformat()
        cutoff, _ = self.boundary(oldest_day)
        self.days = {day: row for day, row in self.days.items() if day >= oldest}
        self.baseline_days = {day: row for day, row in self.baseline_days.items() if day >= oldest}
        self.ledger = {t: row for t, row in self.ledger.items() if t >= cutoff}

    def history(self, now):
        today = datetime.fromtimestamp(now, self.zone).date().isoformat()
        result = []
        # Include days with no data, so missing days never look like zero COP.
        first = min(self.days, default=today)
        day = datetime.fromisoformat(first).date()
        last = datetime.fromisoformat(today).date()
        while day <= last:
            key = day.isoformat()
            row = self.days.get(key, {"heat": 0, "electricity": 0, "seconds": 0})
            start, end = self.boundary(day)
            elapsed = max(0, min(now, end) - start)
            coverage = min(100, row["seconds"] / elapsed * 100) if elapsed else 0
            cop = row["heat"] / row["electricity"] if row["electricity"] > 0 and row["heat"] > 0 else None
            result.append({"date": key, "heat_kwh": round(row["heat"], 4),
                           "electricity_kwh": round(row["electricity"], 4),
                           "cop": round(cop, 3) if cop is not None else None,
                           "coverage": round(coverage, 2), "today": key == today,
                           "complete": key != today and row["seconds"] >= end - start - 0.001})
            day += timedelta(days=1)
        return result

    def summary(self, now):
        history = self.history(now)
        row = history[-1]
        return history, {"estimated_heat_energy_today": row["heat_kwh"] if row["electricity_kwh"] > 0 or row["coverage"] > 0 else None,
                "electrical_energy_today": row["electricity_kwh"] if row["coverage"] > 0 else None,
                "estimated_cop_today": row["cop"], "daily_performance_coverage": row["coverage"]}

    def values(self, now):
        return self.summary(now)[1]

    def saved(self):
        return {"version": 2, "zone": self.zone.key, "source": self.source,
                "days": {day: dict(row) for day, row in self.days.items()},
                "baseline_days": {day: dict(row) for day, row in self.baseline_days.items()},
                "intervals": [[t, *row] for t, row in sorted(self.ledger.items())],
                "replayed": [list(row) for row in self.replayed]}
