"""Replay a bounded thermal history against power observed during this session."""
from copy import deepcopy
from datetime import datetime, timezone

from .decoder import (BINARY_ID, MAX_AGE_SECONDS, field, record_time, decode_packet,
                      combine_samples, cycle_failures)
from .performance import net_heat_kw, number

WINDOW = 1800


class SnapshotHistory:
    def __init__(self, saved=None):
        self.started = None
        self.packets = {'indoor': {}, 'outdoor': {}}
        self.samples = {"indoor": {}, "outdoor": {}}
        self.failures = []
        self._packet_cache = {}
        self.power = {}
        self.observed = []
        self.breaks = set()
        self.valid_response = True
        self._resume_pending = False
        if isinstance(saved, dict):
            self._restore(saved)

    def ingest(self, data, now):
        """Deduplicate by source timestamp, independently of response ordering."""
        try:
            self.valid_response = field(data, 'main', 'samsungce.deviceIdentification', 'binaryId') == BINARY_ID
        except (TypeError, AttributeError):
            self.valid_response = False
        if not self.valid_response:
            self.failures = cycle_failures(data, datetime.fromtimestamp(now, timezone.utc))
            return  # An unusable response must not delete accepted measurements.
        decoded = {"indoor": [], "outdoor": []}
        for kind in self.packets:
            records = field(data, 'main', 'samsungce.ehsCycleData', kind)
            if not isinstance(records, list):
                continue
            for record in records:
                sample = self._decode(record, kind)
                timestamp = sample.timestamp
                decoded[kind].append(sample)
                if timestamp is not None and now-WINDOW <= timestamp <= now:
                    # Invalid duplicates cannot replace an accepted valid sample.
                    if sample.valid or timestamp not in self.packets[kind]:
                        self.packets[kind][timestamp] = deepcopy(record)
                        self.samples[kind][timestamp] = sample
        fallback = {kind: any(sample.usable(now) for sample in records.values())
                    for kind, records in self.samples.items()}
        self.failures = cycle_failures(data, datetime.fromtimestamp(now, timezone.utc), decoded, fallback)

    def _decode(self, record, kind):
        raw_time = record.get("timestamp") if isinstance(record, dict) else None
        raw_data = record.get("data") if isinstance(record, dict) else None
        key = (kind, isinstance(record, dict), raw_time if isinstance(raw_time, str) else None,
               raw_data if isinstance(raw_data, str) else None)
        if key not in self._packet_cache:
            self._packet_cache[key] = decode_packet(record, kind)
            if len(self._packet_cache) > 4096:
                self._packet_cache.pop(next(iter(self._packet_cache)))
        return self._packet_cache[key]

    def selected(self, when):
        """Newest usable packets for display; history also respects invalid hold breaks."""
        return tuple(max((sample for sample in records.values() if sample.usable(when)),
                         key=lambda sample: sample.timestamp, default=None)
                     for records in self.samples.values())

    def live(self, when):
        inside, outside = self.selected(when)
        return combine_samples(inside, outside), (inside.timestamp if inside else None,
                                                 outside.timestamp if outside else None)

    def sample(self, now, watts, available=True):
        if self.started is None:
            self.started = now
        self.power[now] = watts if available else None
        if self._resume_pending and self.observed:
            self.breaks.add((self.observed[-1], now))
        self._resume_pending = False
        if not self.observed or self.observed[-1] != now:
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
        observed = [(a, b) for a, b in zip(self.observed, self.observed[1:]) if 0 < b-a <= 180 and (a, b) not in self.breaks]
        power_times = sorted(self.power)
        observed_index = power_index = 0
        for a, b in zip(points, points[1:]):
            while observed_index < len(observed) and observed[observed_index][1] <= a:
                observed_index += 1
            if (observed_index == len(observed) or observed[observed_index][0] > a
                    or observed[observed_index][1] < b):
                continue
            while power_index < len(power_times) and power_times[power_index] <= a:
                power_index += 1
            watts = self.power[power_times[power_index-1]] if power_index else None
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
        selected = []
        for records in self.samples.values():
            past = [t for t in records if t <= when]
            sample = records[max(past)] if past else None
            if sample is None or not sample.usable(when):
                return None
            selected.append(sample)
        return net_heat_kw(combine_samples(*selected))

    def _prune(self, start):
        for kind, records in self.packets.items():
            past = [t for t in records if t <= start]
            baseline = max(past) if past else None
            self.packets[kind] = {t: r for t, r in records.items() if t >= start or t == baseline}
            self.samples[kind] = {t: sample for t, sample in self.samples[kind].items()
                                  if t in self.packets[kind]}
        past = [t for t in self.power if t <= start]
        baseline = max(past) if past else None
        self.power = {t: w for t, w in self.power.items() if t >= start or t == baseline}
        past = [t for t in self.observed if t <= start]
        baseline = max(past) if past else None
        self.observed = [t for t in self.observed if t >= start or t == baseline]
        self.breaks = {(a, b) for a, b in self.breaks if b >= start}

    def saved(self):
        return {"started": self.started,
                "packets": {kind: list(records.values()) for kind, records in self.packets.items()},
                "power": [[t, watts] for t, watts in sorted(self.power.items())],
                "observed": list(self.observed), "breaks": [list(pair) for pair in sorted(self.breaks)]}

    def _restore(self, saved):
        # Restore only the context necessary to revise already observed intervals.
        # The first new sample explicitly breaks continuity across restart/reload.
        started = saved.get("started")
        if not number(started):
            return
        self.started = started
        packets = saved.get("packets", {})
        if isinstance(packets, dict):
            for kind in self.packets:
                records = packets.get(kind, [])
                for record in records if isinstance(records, list) else []:
                    timestamp = record_time(record)
                    if timestamp is not None:
                        self.packets[kind][timestamp] = deepcopy(record)
                        self.samples[kind][timestamp] = self._decode(record, kind)
        power = saved.get("power", [])
        for row in power if isinstance(power, list) else []:
            if isinstance(row, (list, tuple)) and len(row) == 2 and number(row[0]):
                self.power[row[0]] = row[1] if number(row[1]) and row[1] >= 0 else None
        observed = saved.get("observed", [])
        self.observed = sorted({t for t in observed if number(t)}) if isinstance(observed, list) else []
        breaks = saved.get("breaks", [])
        self.breaks = {tuple(pair) for pair in breaks
                       if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(number(v) for v in pair)
                       and pair[1] >= pair[0]} if isinstance(breaks, list) else set()
        self._resume_pending = True
