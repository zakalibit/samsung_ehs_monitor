"""Samsung EHS Monitor: read-only SmartThings status polling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path

from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ai_export import websocket_export_performance
from .auth import async_create_client
from .daily import DailyPerformance
from .snapshots import SnapshotHistory
from .frontend import RESOURCE_PATH, async_register_resources
from .dashboard import async_setup_dashboard
from .decoder import field
from .performance import PowerHistory, LIVE_COP_MAX_SKEW, COP_MIN_POWER_W, RollingPerformance, electrical_watts, net_heat_kw, instantaneous_cop, number, external_watts
from .api import SmartThingsAuthError, SmartThingsClient, SmartThingsError
from .const import DOMAIN, CONF_TOKEN, CONF_DEVICE_ID, CONF_POWER_SENSOR, DEFAULT_SCAN_INTERVAL

LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


class SamsungEHSCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass, LOGGER, name="Samsung EHS Monitor",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.device_id = entry.data[CONF_DEVICE_ID]
        self.power_sensor = entry.options.get(CONF_POWER_SENSOR)
        self.configured_options = dict(entry.options)
        self.presentation_id = None
        self.diagnostic_entries = []
        self.snapshots = SnapshotHistory()
        self.live_power = PowerHistory()
        self.performance = RollingPerformance()
        self.daily = DailyPerformance(hass.config.time_zone, self.power_sensor)
        self.daily_store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_daily")
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_performance")
        token = entry.data.get(CONF_TOKEN)
        self.client = SmartThingsClient(async_get_clientsession(hass), token if isinstance(token, str) else None)

    def saved_daily(self):
        """Save totals, interval ledger, and replay context in one atomic record."""
        return {**self.daily.saved(), "snapshot_history": self.snapshots.saved()}

    @callback
    def record_failure(self, category, message, key=None):
        now = datetime.now(timezone.utc).isoformat()
        key = key or message
        for index, entry in enumerate(self.diagnostic_entries):
            if entry["category"] == category and entry["key"] == key:
                entry.update(message=message, timestamp=now, last_seen=now, count=entry["count"]+1)
                self.diagnostic_entries.append(self.diagnostic_entries.pop(index))
                return
        self.diagnostic_entries.append({
            "timestamp": now, "first_seen": now, "last_seen": now, "count": 1,
            "key": key, "category": category, "message": message,
        })
        self.diagnostic_entries = self.diagnostic_entries[-100:]
        LOGGER.warning("%s: %s", category, message)

    def capture_external_power(self, now):
        state = self.hass.states.get(self.power_sensor)
        watts = external_watts(state.state, state.attributes.get("unit_of_measurement")) if state else None
        reported = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
        try:
            power_time = reported.timestamp() if reported.tzinfo is not None else None
        except AttributeError:
            power_time = None
        if watts is None:
            power_time = now.timestamp()
        self.live_power.record(power_time, watts, now.timestamp())
        return watts

    def refresh_live(self, data, now=None):
        """Refresh the live ratio without polling or changing seven-day accounting."""
        now = now or datetime.now(timezone.utc)
        metrics, (heat_time, outdoor_time) = self.snapshots.live(now.timestamp())
        data["_decoded"] = metrics
        if self.power_sensor:
            state = self.hass.states.get(self.power_sensor)
            watts = self.capture_external_power(now)
            displayed_watts = external_watts(
                state.state, state.attributes.get("unit_of_measurement"), clamp_negative=True) if state else None
            fresh = watts is not None
        else:
            report = field(data, "main", "powerConsumptionReport", "powerConsumption") or {}
            watts = electrical_watts(report.get("power"), self.presentation_id) if isinstance(report, dict) else None
            displayed_watts = electrical_watts(
                report.get("power"), self.presentation_id, clamp_negative=True) if isinstance(report, dict) else None
            attribute = data.get("components", {}).get("main", {}).get("powerConsumptionReport", {}).get("powerConsumption", {})
            try:
                timestamp = datetime.fromisoformat(attribute["timestamp"].replace("Z", "+00:00"))
                fresh = 0 <= (now - timestamp).total_seconds() <= 900
                power_time = timestamp.timestamp()
            except (KeyError, TypeError, ValueError, AttributeError):
                fresh = False
                power_time = None
            self.live_power.record(power_time, watts, now.timestamp())
        paired = self.live_power.closest(heat_time)
        matching_method = "closest" if paired else "unavailable"
        if paired is None and heat_time is not None:
            paired = self.live_power.latest()
            matching_method = "latest_fallback" if paired else "unavailable"
        aligned = (heat_time is not None and outdoor_time is not None
                   and abs(heat_time-outdoor_time) <= LIVE_COP_MAX_SKEW)
        matched_in_time = paired is not None and heat_time is not None and abs(paired[0]-heat_time) <= LIVE_COP_MAX_SKEW
        standby = ((number(watts) and watts <= COP_MIN_POWER_W)
                   or (paired is not None and number(paired[1]) and paired[1] <= COP_MIN_POWER_W))
        cop = instantaneous_cop(metrics.get("thermal_power"), paired[1]) if fresh and aligned and matched_in_time and not standby else None
        data["_cop_match"] = {
            "power_matching_method": matching_method,
            "cop_status": "standby_power" if standby else "power_time_mismatch" if paired and not matched_in_time else "valid" if cop is not None else "unavailable",
            "minimum_cop_power_watts": COP_MIN_POWER_W,
            "thermal_timestamp": datetime.fromtimestamp(heat_time, timezone.utc).isoformat() if heat_time is not None else None,
            "outdoor_timestamp": datetime.fromtimestamp(outdoor_time, timezone.utc).isoformat() if outdoor_time is not None else None,
            "power_timestamp": datetime.fromtimestamp(paired[0], timezone.utc).isoformat() if paired else None,
            "power_watts": paired[1] if paired else None,
            "time_difference_seconds": round(paired[0]-heat_time, 3) if paired else None,
            "max_time_difference_seconds": LIVE_COP_MAX_SKEW,
        }
        data.setdefault("_performance", {}).update(
            electrical_input_power=displayed_watts,
            estimated_cop=cop,
            net_thermal_power=net_heat_kw(metrics),
        )

        # Record power cheaply; the timer or next API batch performs reconciliation.
        self.snapshots.sample(now.timestamp(), watts if self.power_sensor else None,
                              available=self.snapshots.valid_response)

    def update_daily(self, data, now):
        """Reconcile on the heartbeat/API batch, never on every power event."""
        self.daily.replay(now.timestamp(), self.snapshots.intervals(now.timestamp()),
                          minimum_cop_power_watts=COP_MIN_POWER_W)
        history, values = self.daily.summary(now.timestamp())
        data["_daily_history"] = history
        data.setdefault("_performance", {}).update(values)

    @callback
    def daily_tick(self, now):
        if self.data is not None:
            if self.last_update_success:
                self.refresh_live(self.data, now)
            else:
                self.snapshots.sample(now.timestamp(), None, available=False)

            self.update_daily(self.data, now)
            self.daily_store.async_delay_save(self.saved_daily, 10)
            self.async_update_listeners()

    @callback
    def external_power_changed(self, event):
        if self.data is not None:
            if self.last_update_success:
                self.refresh_live(self.data)
            else:
                self.capture_external_power(datetime.now(timezone.utc))
            self.async_update_listeners()

    async def _async_update_data(self) -> dict:
        try:
            data = await self.client.status(self.device_id)
            now = datetime.now(timezone.utc)
            self.snapshots.ingest(data, now.timestamp())
            for key, message in self.snapshots.failures:
                self.record_failure("Parsing", message, key)
            report = field(data, "main", "powerConsumptionReport", "powerConsumption") or {}
            if not isinstance(report, dict):
                report = {}
            energy = report.get("energy")
            counter = energy / 1000 if number(energy) else None
            # A report timestamp is mandatory for the live ratio. It must be recent.
            attribute = ((data.get("components", {}).get("main", {})
                          .get("powerConsumptionReport", {}).get("powerConsumption", {})))
            try:
                timestamp = datetime.fromisoformat(attribute["timestamp"].replace("Z", "+00:00"))
                fresh = 0 <= (now - timestamp).total_seconds() <= 900
            except (KeyError, TypeError, ValueError):
                fresh = False
            if fresh:
                counter_time = timestamp.timestamp()
                self.performance.observe_counter(counter_time, counter, now.timestamp())
            else:
                self.performance.previous = None
            data["_performance"] = self.performance.reconcile(self.snapshots.heat_intervals, now.timestamp())
            self.refresh_live(data, now)
            self.update_daily(data, now)
            self.store.async_delay_save(self.performance.saved, 30)
            return data
        except SmartThingsAuthError as exc:
            self.record_failure("API status", str(exc))
            raise ConfigEntryAuthFailed("SmartThings token no longer authorised") from exc
        except SmartThingsError as exc:
            self.record_failure("API status", str(exc))
            raise UpdateFailed("SmartThings status retrieval failed") from exc


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Serve the bundled daily-ratio card; no HACS/resource entry required."""
    await hass.http.async_register_static_paths([
        StaticPathConfig(RESOURCE_PATH, str(Path(__file__).parent / "frontend" / "daily-cop-card.js"), False)])
    websocket_api.async_register_command(hass, websocket_export_performance)
    await async_register_resources(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SamsungEHSCoordinator(hass, entry)
    try:
        coordinator.client = await async_create_client(hass, entry.data)
    except SmartThingsAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except SmartThingsError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc
    daily_saved = await coordinator.daily_store.async_load()
    coordinator.daily = DailyPerformance(hass.config.time_zone, coordinator.power_sensor, daily_saved)
    if (isinstance(daily_saved, dict) and daily_saved.get("zone") == hass.config.time_zone
            and daily_saved.get("source") == coordinator.power_sensor):
        coordinator.snapshots = SnapshotHistory(daily_saved.get("snapshot_history"))
    saved = await coordinator.store.async_load()
    coordinator.performance = RollingPerformance(saved)
    if not coordinator.power_sensor:
        try:
            details = await coordinator.client.device(coordinator.device_id)
            coordinator.presentation_id = details.get("presentationId")
        except SmartThingsAuthError as exc:
            coordinator.record_failure("API device metadata", str(exc))
            raise ConfigEntryAuthFailed("SmartThings token no longer authorised") from exc
        except SmartThingsError as exc:
            coordinator.record_failure("API device metadata", str(exc))
            LOGGER.warning("Device metadata unavailable; electrical input power and live COP remain unknown until reload")
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    if coordinator.power_sensor:
        entry.async_on_unload(async_track_state_change_event(
            hass, [coordinator.power_sensor], coordinator.external_power_changed))
    entry.async_on_unload(async_track_time_interval(hass, coordinator.daily_tick, timedelta(seconds=60)))
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_dashboard(hass, entry)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Only changed options require a reload; reconfiguration reloads explicitly.
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and dict(entry.options) != coordinator.configured_options:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN][entry.entry_id]
        if coordinator.data is not None:
            coordinator.update_daily(coordinator.data, datetime.now(timezone.utc))
        await coordinator.daily_store.async_save(coordinator.saved_daily())
        await coordinator.store.async_save(coordinator.performance.saved())
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
