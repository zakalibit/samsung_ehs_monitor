"""Read-only Samsung EHS entities from SmartThings API (no commands)."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .decoder import decode, latest


def read(data, component, capability, attr):
    return (((data.get("components") or {}).get(component) or {})
            .get(capability) or {}).get(attr, {}).get("value")


def power(data, field):
    report = read(data, "main", "powerConsumptionReport", "powerConsumption")
    return report.get(field) if isinstance(report, dict) else None


def fsv(data, identifier):
    values = read(data, "main", "samsungce.ehsFsvSettings", "fsvSettings") or []
    return next((row.get("value") for row in values if row.get("id") == identifier), None)


def cycle(data, kind):
    row = latest(data, kind)
    return row.get("data") if row else None


class SamsungEHSSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name, getter, *, unit=None, diagnostic=False,
                 device_class=None, state_class=None):
        super().__init__(coordinator)
        self.key = key
        self.getter = getter
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.device_id)},
            "name": "Samsung EHS Monitor",
            "manufacturer": "Samsung",
        }

    @property
    def extra_state_attributes(self):
        if self.key in {"estimated_cop_today", "estimated_heat_energy_today", "electrical_energy_today", "daily_performance_coverage"}:
            return {
                "power_source": self.coordinator.power_sensor,
                "timezone": self.hass.config.time_zone,
                "calculation": "ratio of energy over valid paired intervals; check coverage",
                "daily_history": self.coordinator.daily.history(datetime.now(timezone.utc).timestamp()),
            }
        if self.key in {"electrical_input_power", "estimated_cop"} and self.coordinator.power_sensor:
            state = self.hass.states.get(self.coordinator.power_sensor)
            return {
                "power_source": self.coordinator.power_sensor,
                "source_unit": state.attributes.get("unit_of_measurement") if state else None,
                "electricity_7d_source": "Samsung accumulated energy counter",
                **(self.coordinator.data.get("_cop_match", {}) if self.key == "estimated_cop" else {}),
            }
        if self.key in {"energy", "deltaEnergy", "power_raw", "electrical_input_power", "estimated_cop"}:
            report = read(self.coordinator.data, "main", "powerConsumptionReport", "powerConsumption")
            report = report if isinstance(report, dict) else {}
            return {
                "power_source": "Samsung SmartThings",
                **(self.coordinator.data.get("_cop_match", {}) if self.key == "estimated_cop" else {}),
                "power_consumption_start": report.get("start"),
                "power_consumption_end": report.get("end"),
                "presentation_id": self.coordinator.presentation_id,
                "power_scaling": "raw kW to W" if "EHS" in (self.coordinator.presentation_id or "") else "raw W (or metadata unavailable)",
            }
        return None

    @property
    def native_value(self):
        try:
            return self.getter(self.coordinator.data)
        except (TypeError, ValueError, AttributeError, KeyError, IndexError):
            return None


class SamsungEHSLogSensor(SamsungEHSSensor):
    """Keep failures visible even when the polling coordinator is unavailable."""

    _unrecorded_attributes = frozenset({"entries"})

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return len(self.coordinator.diagnostic_entries)

    @property
    def extra_state_attributes(self):
        return {"entries": list(reversed(self.coordinator.diagnostic_entries)),
                "capacity": 100, "retention": "Since integration setup or reload"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    definitions = [
        ("room_temperature", "Room temperature", "INDOOR1", "temperatureMeasurement", "temperature", "°C"),
        ("heating_status", "Heating status", "INDOOR1", "switch", "switch", None),
        ("operating_mode", "Operating mode", "INDOOR1", "airConditionerMode", "airConditionerMode", None),
        ("temperature_reference", "Temperature reference", "INDOOR1", "samsungce.ehsTemperatureReference", "temperatureReference", None),
        ("cooling_setpoint", "Cooling setpoint", "INDOOR1", "thermostatCoolingSetpoint", "coolingSetpoint", "°C"),
        ("outing_mode", "Outing mode", "main", "custom.outingMode", "outingMode", None),
    ]
    entities = [SamsungEHSLogSensor(
        coordinator, "failure_log", "Failure log", lambda d: None, diagnostic=True)]
    for key, name, component, capability, attr, unit in definitions:
        entities.append(SamsungEHSSensor(
            coordinator, key, name,
            lambda d, c=component, p=capability, a=attr: read(d, c, p, a),
            unit=unit,
            device_class=SensorDeviceClass.TEMPERATURE if unit == "°C" else None,
        ))

    # Match official SmartThings integration: Wh -> kWh and energy TOTAL_INCREASING.
    for key, name in (("energy", "Electrical energy"), ("deltaEnergy", "Electrical energy difference")):
        entities.append(SamsungEHSSensor(
            coordinator, key, name,
            lambda d, k=key: (x / 1000 if isinstance((x := power(d, k)), (int, float)) else None),
            unit="kWh", device_class=SensorDeviceClass.ENERGY, diagnostic=True,
            state_class=SensorStateClass.TOTAL_INCREASING if key == "energy" else SensorStateClass.TOTAL,
        ))
    entities.append(SamsungEHSSensor(
        coordinator, "power_raw", "Electrical power (raw)",
        lambda d: power(d, "power"), diagnostic=True,
    ))

    settings = read(coordinator.data, "main", "samsungce.ehsFsvSettings", "fsvSettings") or []
    for setting in settings:
        identifier = setting.get("id")
        if not identifier:
            continue
        temp = setting.get("type") == "temperature"
        entities.append(SamsungEHSSensor(
            coordinator, f"fsv_{identifier}", f"FSV {identifier}",
            lambda d, i=identifier: fsv(d, i),
            unit="°C" if temp else None,
            device_class=SensorDeviceClass.TEMPERATURE if temp else None,
            diagnostic=True,
        ))

    for kind in ("indoor", "outdoor"):
        entities.append(SamsungEHSSensor(
            coordinator, f"cycle_{kind}", f"{kind.title()} cycle (raw hex)",
            lambda d, k=kind: cycle(d, k), diagnostic=True,
        ))

    metrics = [
        ("water_return_temperature", "Water return temperature", "°C", SensorDeviceClass.TEMPERATURE),
        ("water_flow_temperature", "Water flow temperature", "°C", SensorDeviceClass.TEMPERATURE),
        ("water_flow_rate", "Water flow rate", "L/min", None),
        ("circulation_pump_speed", "Circulation pump speed", "%", None),
        ("outdoor_temperature", "Outdoor temperature", "°C", SensorDeviceClass.TEMPERATURE),
        ("compressor_frequency", "Compressor frequency", "Hz", SensorDeviceClass.FREQUENCY),
        ("compressor_target_frequency", "Compressor target frequency", "Hz", SensorDeviceClass.FREQUENCY),
        ("discharge_temperature", "Discharge gas temperature", "°C", SensorDeviceClass.TEMPERATURE),
        ("thermal_power", "Estimated thermal output", "kW", SensorDeviceClass.POWER),
    ]
    for key, name, unit, device_class in metrics:
        entities.append(SamsungEHSSensor(
            coordinator, key, name,
            lambda d, k=key: decode(d)[k],
            unit=unit, device_class=device_class, state_class=SensorStateClass.MEASUREMENT,
        ))
    performance = [
        ("estimated_heat_energy_today", "Estimated heat energy today", "kWh", SensorDeviceClass.ENERGY),
        ("electrical_energy_today", "Electrical energy today", "kWh", SensorDeviceClass.ENERGY),
        ("estimated_cop_today", "Estimated COP today", None, None),
        ("daily_performance_coverage", "Daily performance coverage", "%", None),
        ("electrical_input_power", "Electrical input power", "W", SensorDeviceClass.POWER),
        ("estimated_cop", "Estimated COP", None, None),
        ("net_thermal_power", "Net thermal power", "kW", SensorDeviceClass.POWER),
        ("estimated_heat_energy_7d", "Estimated heat energy 7d", "kWh", SensorDeviceClass.ENERGY),
        ("electrical_energy_7d", "Electrical energy 7d", "kWh", SensorDeviceClass.ENERGY),
        ("estimated_cop_7d", "Estimated COP 7d", None, None),
        ("performance_7d_coverage", "Performance 7d coverage", "%", None),
    ]
    for key, name, unit, device_class in performance:
        entities.append(SamsungEHSSensor(
            coordinator, key, name,
            lambda d, k=key: d.get("_performance", {}).get(k),
            unit=unit, device_class=device_class,
            state_class=SensorStateClass.MEASUREMENT if unit != "kWh" and key != "estimated_cop_today" else None,
        ))
    # Estimated COP uses the same model-specific scaling as official SmartThings.
    async_add_entities(entities)
