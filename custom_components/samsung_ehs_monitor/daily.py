"""Local-calendar daily energy ratios from paired heat and external power samples."""
from datetime import datetime, time, timedelta
from math import isfinite
from zoneinfo import ZoneInfo


class DailyPerformance:
    def __init__(self, zone, source, saved=None):
        self.zone = ZoneInfo(zone)
        self.source = source
        saved = saved or {}
        self.days = saved.get("days", {}) if saved.get("zone") == zone and saved.get("source") == source else {}
        self.previous = None  # Restart time is not measured time.

    def boundary(self, day):
        start = datetime.combine(day, time.min, self.zone)
        end = datetime.combine(day + timedelta(days=1), time.min, self.zone)
        return start.timestamp(), end.timestamp()

    def advance(self, now, heat, watts, valid_until):
        """Left-hold each reading until its next update, clipped at thermal expiry.

        A minute timer limits event gaps; longer than 180s is unobserved downtime.
        Invalid new samples break the baseline after finishing the preceding valid hold.
        """
        if self.previous:
            before, q, w, expiry = self.previous
            end = min(now, expiry)
            if 0 < now - before <= 180:
                cursor = before
                while cursor < end:
                    day = datetime.fromtimestamp(cursor, self.zone).date()
                    _, midnight = self.boundary(day)
                    stop = min(end, midnight)
                    seconds = stop - cursor
                    record = self.days.setdefault(day.isoformat(), {"heat": 0.0, "electricity": 0.0, "seconds": 0.0})
                    record["heat"] += q * seconds / 3600
                    record["electricity"] += w * seconds / 3600000
                    record["seconds"] += seconds
                    cursor = stop
        valid = all(isinstance(v, (float, int)) and not isinstance(v, bool) and isfinite(v)
                    for v in (heat, watts, valid_until))
        self.previous = (now, heat, watts, valid_until) if valid and watts >= 0 and valid_until > now and self.source else None
        cutoff = (datetime.fromtimestamp(now, self.zone).date() - timedelta(days=34)).isoformat()
        self.days = {day: value for day, value in self.days.items() if day >= cutoff}

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

    def values(self, now):
        row = self.history(now)[-1]
        return {"estimated_heat_energy_today": row["heat_kwh"] if row["electricity_kwh"] > 0 or row["coverage"] > 0 else None,
                "electrical_energy_today": row["electricity_kwh"] if row["coverage"] > 0 else None,
                "estimated_cop_today": row["cop"], "daily_performance_coverage": row["coverage"]}

    def saved(self):
        return {"zone": self.zone.key, "source": self.source, "days": self.days}
