"""Samsung EHS Monitor: read-only SmartThings status polling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .daily import DailyPerformance
from .snapshots import SnapshotHistory
from .frontend import RESOURCE_PATH, async_register_resources
from .dashboard import async_setup_dashboard
from .decoder import decode, field, latest, cycle_errors
from .performance import RollingPerformance, electrical_watts, net_heat_kw, instantaneous_cop, number, external_watts
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
        self.presentation_id = None
        self.diagnostic_entries = []
        self.snapshots = SnapshotHistory()
        self.performance = RollingPerformance()
        self.daily = DailyPerformance(hass.config.time_zone, self.power_sensor)
        self.daily_store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_daily")
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_performance")
        self.client = SmartThingsClient(async_get_clientsession(hass), entry.data[CONF_TOKEN])

    @callback
    def record_failure(self, category, message):
        self.diagnostic_entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category, "message": message,
        })
        self.diagnostic_entries = self.diagnostic_entries[-100:]
        LOGGER.warning("%s: %s", category, message)

    def refresh_live(self, data, now=None):
        """Refresh the live ratio without polling or changing seven-day accounting."""
        now = now or datetime.now(timezone.utc)
        metrics = decode(data, now)
        if self.power_sensor:
            state = self.hass.states.get(self.power_sensor)
            watts = external_watts(state.state, state.attributes.get("unit_of_measurement")) if state else None
            displayed_watts = external_watts(
                state.state, state.attributes.get("unit_of_measurement"), clamp_negative=True) if state else None
            fresh = True  # HA reports availability; a stable power value may be unchanged.
        else:
            report = field(data, "main", "powerConsumptionReport", "powerConsumption") or {}
            watts = electrical_watts(report.get("power"), self.presentation_id) if isinstance(report, dict) else None
            displayed_watts = electrical_watts(
                report.get("power"), self.presentation_id, clamp_negative=True) if isinstance(report, dict) else None
            attribute = data.get("components", {}).get("main", {}).get("powerConsumptionReport", {}).get("powerConsumption", {})
            try:
                timestamp = datetime.fromisoformat(attribute["timestamp"].replace("Z", "+00:00"))
                fresh = 0 <= (now - timestamp).total_seconds() <= 900
            except (KeyError, TypeError, ValueError):
                fresh = False
        data.setdefault("_performance", {}).update(
            electrical_input_power=displayed_watts,
            estimated_cop=instantaneous_cop(metrics.get("thermal_power"), watts) if fresh else None,
            net_thermal_power=net_heat_kw(metrics),
        )

        # Replay delayed cycle packets only against power actually observed here.
        self.snapshots.sample(now.timestamp(), watts if self.power_sensor else None)
        self.daily.replay(now.timestamp(), self.snapshots.intervals(now.timestamp()))
        data["_performance"].update(self.daily.values(now.timestamp()))

    @callback
    def daily_tick(self, now):
        if self.data is not None:
            if self.last_update_success:
                self.refresh_live(self.data, now)
            else:
                self.snapshots.sample(now.timestamp(), None, available=False)
                self.daily.replay(now.timestamp(), self.snapshots.intervals(now.timestamp()))
                self.data.setdefault("_performance", {}).update(self.daily.values(now.timestamp()))
            self.daily_store.async_delay_save(self.daily.saved, 10)
            self.async_update_listeners()

    @callback
    def external_power_changed(self, event):
        if self.data is not None:
            if self.last_update_success:
                self.refresh_live(self.data)
            self.async_update_listeners()

    async def _async_update_data(self) -> dict:
        try:
            data = await self.client.status(self.device_id)
            now = datetime.now(timezone.utc)
            self.snapshots.ingest(data, now.timestamp())
            for message in cycle_errors(data, now):
                self.record_failure("Parsing", message)
            metrics = decode(data, now)
            report = field(data, "main", "powerConsumptionReport", "powerConsumption") or {}
            if not isinstance(report, dict):
                report = {}
            energy = report.get("energy")
            counter = energy / 1000 if number(energy) else None
            heat = net_heat_kw(metrics)
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
                previous = self.performance.previous
                intervals = self.snapshots.heat_intervals(previous[0], counter_time) if previous and counter_time > previous[0] else []
                self.performance.update(counter_time, self.snapshots.heat_at(counter_time), counter,
                                        heat_intervals=intervals)
            else:
                self.performance.previous = None
            data["_performance"] = self.performance.values(now.timestamp())
            self.refresh_live(data, now)
            self.store.async_delay_save(self.performance.saved, 30)
            return data
        except SmartThingsAuthError as exc:
            self.record_failure("API status", str(exc))
            raise ConfigEntryAuthFailed("SmartThings token no longer authorised") from exc
        except SmartThingsError as exc:
            self.record_failure("API status", str(exc))
            raise UpdateFailed("SmartThings status retrieval failed") from exc
        except (TypeError, ValueError, AttributeError, KeyError, IndexError) as exc:
            self.record_failure("Parsing", f"Unexpected status structure ({type(exc).__name__})")
            raise UpdateFailed("Unable to parse SmartThings status") from exc


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Serve the bundled daily-ratio card; no HACS/resource entry required."""
    await hass.http.async_register_static_paths([
        StaticPathConfig(RESOURCE_PATH, str(Path(__file__).parent / "frontend" / "daily-cop-card.js"), False)])
    await async_register_resources(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SamsungEHSCoordinator(hass, entry)
    daily_saved = await coordinator.daily_store.async_load()
    coordinator.daily = DailyPerformance(hass.config.time_zone, coordinator.power_sensor, daily_saved)
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
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.daily_store.async_save(coordinator.daily.saved())
        await coordinator.store.async_save(coordinator.performance.saved())
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
