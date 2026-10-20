"""Exercise production calculations, coordinator and options with lightweight HA stubs."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest

BASE = Path(__file__).resolve().parents[1] / "custom_components/samsung_ehs_monitor"


def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load("performance")
daily = load("daily")
d = load("decoder")


class CoordinatorStub:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, *args, **kwargs):
        self.hass = hass
        self.data = None
        self.notifications = 0
        self.last_update_success = True

    def async_update_listeners(self):
        self.notifications += 1


def production_class(filename, name, scope):
    tree = ast.parse((BASE / filename).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, filename, "exec"), scope)
    return scope[name]


snapshot_scope = dict(deepcopy=deepcopy, datetime=datetime, timezone=timezone,
                      BINARY_ID=d.BINARY_ID, MAX_AGE_SECONDS=d.MAX_AGE_SECONDS,
                      WINDOW=1800, decode=d.decode, field=d.field, record_time=d.record_time,
                      net_heat_kw=p.net_heat_kw)
Snapshots = production_class("snapshots.py", "SnapshotHistory", snapshot_scope)


scope = dict(DataUpdateCoordinator=CoordinatorStub, callback=lambda f: f,
             datetime=datetime, timedelta=timedelta, timezone=timezone,
             HomeAssistant=object, ConfigEntry=object, LOGGER=Mock(),
             DEFAULT_SCAN_INTERVAL=300, CONF_DEVICE_ID="device_id",
             CONF_TOKEN="token", CONF_POWER_SENSOR="power_sensor", DOMAIN="samsung_ehs_monitor",
             SnapshotHistory=Snapshots, RollingPerformance=p.RollingPerformance, DailyPerformance=daily.DailyPerformance, latest=d.latest,
             Store=lambda *a: None, SmartThingsClient=lambda *a: None,
             async_get_clientsession=lambda h: None,
             decode=lambda data, now: data["metrics"], field=d.field,
             electrical_watts=p.electrical_watts, external_watts=p.external_watts,
             net_heat_kw=p.net_heat_kw, instantaneous_cop=p.instantaneous_cop,
             PowerHistory=p.PowerHistory, LIVE_COP_MAX_SKEW=p.LIVE_COP_MAX_SKEW,
             thermal_timestamps=lambda data, now: (now.timestamp(), now.timestamp())
             if 'metrics' in data else d.thermal_timestamps(data, now))
Coordinator = production_class("__init__.py", "SamsungEHSCoordinator", scope)


class ExternalPowerTests(unittest.TestCase):
    def test_units_and_invalid_values(self):
        self.assertEqual(p.external_watts("1200", "W"), 1200)
        self.assertEqual(p.external_watts("1.2", "kW"), 1200)
        self.assertEqual(p.external_watts("0", "W"), 0)
        for value, unit in [("unknown", "W"), ("unavailable", "W"),
                            ("nan", "W"), ("inf", "W"), ("-100", "W"),
                            ("1200", "Wh"), ("1.2", "kWh"), ("12", None)]:
            self.assertIsNone(p.external_watts(value, unit))

    def make(self, state="1200", unit="W", selected=True):
        states = {"sensor.shelly_power": NS(state=state, attributes={"unit_of_measurement": unit},
                                           last_reported=datetime.now(timezone.utc))}
        hass = NS(states=NS(get=states.get), config=NS(time_zone="UTC"))
        entry = NS(data={"device_id": "test", "token": "unused"}, entry_id="test",
                   options={"power_sensor": "sensor.shelly_power"} if selected else {})
        c = Coordinator(hass, entry)
        c.presentation_id = "DA-AC-EHS-01001"
        c.data = {"metrics": {"thermal_power": 4.8}, "components": {"main": {
            "powerConsumptionReport": {"powerConsumption": {
                "value": {"power": 0}, "timestamp": "2026-10-20T10:00:00Z"}}}}}
        return c, states

    def test_selected_shelly_overrides_samsung_zero(self):
        c, _ = self.make()
        c.refresh_live(c.data)
        self.assertEqual(c.data["_performance"]["electrical_input_power"], 1200)
        self.assertEqual(c.data["_performance"]["estimated_cop"], 4)

    def test_unavailable_does_not_fall_back(self):
        c, _ = self.make(state="unavailable")
        c.data["components"]["main"]["powerConsumptionReport"]["powerConsumption"]["value"]["power"] = 1.2
        c.refresh_live(c.data)
        self.assertIsNone(c.data["_performance"]["electrical_input_power"])
        self.assertIsNone(c.data["_performance"]["estimated_cop"])

    def test_source_removal_and_zero(self):
        c, states = self.make(state="0")
        c.refresh_live(c.data)
        self.assertEqual(c.data["_performance"]["electrical_input_power"], 0)
        self.assertIsNone(c.data["_performance"]["estimated_cop"])
        states.clear()
        c.refresh_live(c.data)
        self.assertIsNone(c.data["_performance"]["electrical_input_power"])

    def test_negative_external_power_displays_zero_without_validating_accounting(self):
        for value, unit in [("-100", "W"), ("-0.1", "kW")]:
            c, _ = self.make(state=value, unit=unit)
            c.refresh_live(c.data)
            self.assertEqual(c.data["_performance"]["electrical_input_power"], 0)
            self.assertIsNone(c.data["_performance"]["estimated_cop"])
            self.assertIsNone(p.external_watts(value, unit))

    def test_negative_samsung_power_displays_zero(self):
        c, _ = self.make(selected=False)
        c.data["components"]["main"]["powerConsumptionReport"]["powerConsumption"]["value"]["power"] = -0.1
        c.refresh_live(c.data)
        self.assertEqual(c.data["_performance"]["electrical_input_power"], 0)
        self.assertIsNone(c.data["_performance"]["estimated_cop"])

    def test_state_event_refreshes_without_touching_window(self):
        c, states = self.make()
        c.data["_performance"] = {"estimated_cop_7d": 3.5}
        states["sensor.shelly_power"].state = "600"
        c.external_power_changed(None)
        self.assertEqual(c.data["_performance"]["estimated_cop"], 8)
        self.assertEqual(c.data["_performance"]["estimated_cop_7d"], 3.5)
        self.assertEqual(c.notifications, 1)
        self.assertEqual(c.performance.rows, [])

    def test_no_selection_preserves_samsung_path(self):
        c, _ = self.make(selected=False)
        attribute = c.data["components"]["main"]["powerConsumptionReport"]["powerConsumption"]
        attribute["value"]["power"] = 1.2
        c.refresh_live(c.data, datetime(2026, 10, 20, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(c.data["_performance"]["estimated_cop"], 4)
        c.refresh_live(c.data, datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc))
        self.assertIsNone(c.data["_performance"]["estimated_cop"])


class FlowStub:
    def async_create_entry(self, **kwargs):
        return kwargs

    def async_show_form(self, **kwargs):
        return kwargs

    def add_suggested_values_to_schema(self, schema, values):
        return schema


flow_scope = dict(config_entries=NS(OptionsFlow=FlowStub), CONF_POWER_SENSOR="power_sensor",
                  DOMAIN="samsung_ehs_monitor", CONF_CREATE_DASHBOARD="create_dashboard", er=NS(async_get=lambda h: h.registry),
                  vol=NS(Schema=lambda v: v, Optional=lambda v, **kw: v),
                  selector=NS(EntitySelector=lambda v: v, EntitySelectorConfig=lambda **kw: kw))
Options = production_class("config_flow.py", "SamsungEHSOptionsFlow", flow_scope)


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    def flow(self, unit="W", platform="shelly"):
        f = Options()
        f.hass = NS(states=NS(get=lambda eid: NS(attributes={"unit_of_measurement": unit})),
                    registry=NS(async_get=lambda eid: NS(platform=platform)))
        f.config_entry = NS(options={"other_option": True})
        return f

    async def test_save_and_clear(self):
        f = self.flow()
        result = await f.async_step_init({"power_sensor": "sensor.shelly_power", "create_dashboard": True})
        self.assertEqual(result["data"]["power_sensor"], "sensor.shelly_power")
        self.assertTrue(result["data"]["other_option"])
        self.assertTrue(result["data"]["create_dashboard"])
        result = await f.async_step_init({})
        self.assertIsNone(result["data"]["power_sensor"])
        self.assertFalse(result["data"]["create_dashboard"])

    async def test_reject_energy_and_self_reference(self):
        result = await self.flow(unit="kWh").async_step_init({"power_sensor": "sensor.energy"})
        self.assertEqual(result["errors"]["power_sensor"], "invalid_power_unit")
        result = await self.flow(platform="samsung_ehs_monitor").async_step_init({"power_sensor": "sensor.self"})
        self.assertEqual(result["errors"]["power_sensor"], "own_sensor")


if __name__ == "__main__":
    unittest.main()
