"""Replay a bounded thermal history against power observed during this session."""
from copy import deepcopy
from datetime import datetime, timezone

from .decoder import BINARY_ID, MAX_AGE_SECONDS, decode, field, record_time
from .performance import net_heat_kw

WINDOW = 1800


class SnapshotHistory:
    def __init__(self):
        self.started = None
        self.packets = {'indoor': {}, 'outdoor': {}}
        self.power = {}
        self.observed = []

    def ingest(self, data, now):
        """Deduplicate by source timestamp, independently of response ordering."""
        if field(data, 'main', 'samsungce.deviceIdentification', 'binaryId') != BINARY_ID:
            self.packets = {'indoor': {}, 'outdoor': {}}
            return
        for kind in self.packets:
            records = field(data, 'main', 'samsungce.ehsCycleData', kind)
            if not isinstance(records, list):
                continue
            for record in records:
                timestamp = record_time(record)
                if timestamp is not None and now - WINDOW <= timestamp <= now:
                    self.packets[kind][timestamp] = deepcopy(record)

    def sample(self, now, watts, available=True):
        if self.started is None:
            self.started = now
        self.power[now] = watts if available else None
        self.observed.append(now)

    def intervals(self, now):
        """Hold only past readings; expire heat and exclude unobserved downtime."""
        if self.started is None:
            return []
        start = max(self.started, now - WINDOW)
        boundaries = {start, now}
        for records in self.packets.values():
            boundaries.update(t for t in records if start < t < now)
            boundaries.update(t + MAX_AGE_SECONDS for t in records
                              if start < t + MAX_AGE_SECONDS < now)
        boundaries.update(t for t in self.power if start < t < now)
        boundaries.update(t for t in self.observed if start < t < now)
        points = sorted(boundaries)
        result = []
        # A gap longer than the normal heartbeat is never retrospectively filled.
        observed = [(a, b) for a, b in zip(self.observed, self.observed[1:]) if 0 < b-a <= 180]
        for a, b in zip(points, points[1:]):
            if not any(left <= a and b <= right for left, right in observed):
                continue
            powers = [t for t in self.power if t <= a]
            watts = self.power[max(powers)] if powers else None
            if watts is None or watts < 0:
                continue
            heat = self.heat_at(a + min(0.001, (b-a)/2))
            if heat is not None:
                result.append((a, b, heat, watts))
        self._prune(start)
        return result

    def heat_intervals(self, start, end):
        """Thermal intervals for the Samsung cumulative-counter window."""
        points = {start, end}
        for records in self.packets.values():
            points.update(t for t in records if start < t < end)
            points.update(t + MAX_AGE_SECONDS for t in records if start < t + MAX_AGE_SECONDS < end)
        result = []
        times = sorted(points)
        for a, b in zip(times, times[1:]):
            heat = self.heat_at(a + min(0.001, (b-a)/2))
            if heat is not None:
                result.append((a, b, heat))
        return result

    def heat_at(self, when):
        cycles = {}
        for kind, records in self.packets.items():
            times = [t for t in records if t <= when]
            cycles[kind] = {'value': [records[max(times)]] if times else []}
        data = {'components': {'main': {
            'samsungce.deviceIdentification': {'binaryId': {'value': BINARY_ID}},
            'samsungce.ehsCycleData': cycles,
        }}}
        return net_heat_kw(decode(data, datetime.fromtimestamp(when, timezone.utc)))

    def _prune(self, start):
        for kind, records in self.packets.items():
            past = [t for t in records if t <= start]
            baseline = max(past) if past else None
            self.packets[kind] = {t: r for t, r in records.items() if t >= start or t == baseline}
        past = [t for t in self.power if t <= start]
        baseline = max(past) if past else None
        self.power = {t: w for t, w in self.power.items() if t >= start or t == baseline}
        past = [t for t in self.observed if t <= start]
        baseline = max(past) if past else None
        self.observed = [t for t in self.observed if t >= start or t == baseline]
