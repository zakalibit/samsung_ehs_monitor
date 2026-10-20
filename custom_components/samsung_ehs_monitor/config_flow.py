"""Token-only SmartThings authentication then compatible-device picker."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector, entity_registry as er
from homeassistant.core import callback

from .api import SmartThingsAuthError, SmartThingsClient, SmartThingsError, is_ehs_status
from .const import CONF_DEVICE_ID, CONF_TOKEN, CONF_POWER_SENSOR, CONF_CREATE_DASHBOARD, DOMAIN


class SamsungEHSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SamsungEHSOptionsFlow()


    def __init__(self):
        self._token: str | None = None
        self._devices: dict[str, str] = {}

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            client = SmartThingsClient(async_get_clientsession(self.hass), token)
            try:
                devices = await client.devices()
                matches = {}
                for device in devices:
                    device_id = device.get("deviceId")
                    if not device_id:
                        continue
                    # Status capability inspection is more reliable than model name.
                    try:
                        status = await client.status(device_id)
                    except SmartThingsAuthError:
                        continue  # No permission for this individual device.
                    except SmartThingsError:
                        continue
                    if is_ehs_status(status):
                        label = device.get("label") or device.get("name") or "Samsung EHS"
                        matches[device_id] = f"{label} ({device_id[:8]}…)"
                if not matches:
                    errors["base"] = "no_devices"
                else:
                    self._token = token
                    self._devices = matches
                    return await self.async_step_device()
            except SmartThingsAuthError:
                errors["base"] = "invalid_auth"
            except SmartThingsError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )}),
            errors=errors,
        )

    async def async_step_device(self, user_input=None):
        if not self._token or not self._devices:
            return await self.async_step_user()
        errors = {}
        if user_input is not None:
            chosen = user_input[CONF_DEVICE_ID]
            if chosen not in self._devices:
                errors["base"] = "invalid_device"
            else:
                await self.async_set_unique_id(chosen)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._devices[chosen],
                    data={CONF_TOKEN: self._token, CONF_DEVICE_ID: chosen},
                )
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): vol.In(self._devices)}),
            errors=errors,
        )


class SamsungEHSOptionsFlow(config_entries.OptionsFlow):
    """Choose any external HA power sensor, including Shelly clamp power."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            entity_id = user_input.get(CONF_POWER_SENSOR) or None
            if entity_id:
                state = self.hass.states.get(entity_id)
                registered = er.async_get(self.hass).async_get(entity_id)
                if registered is not None and registered.platform == DOMAIN:
                    errors[CONF_POWER_SENSOR] = "own_sensor"
                elif not entity_id.startswith("sensor.") or state is None:
                    errors[CONF_POWER_SENSOR] = "invalid_power_sensor"
                elif state.attributes.get("unit_of_measurement") not in {"W", "kW"}:
                    errors[CONF_POWER_SENSOR] = "invalid_power_unit"
            if not errors:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options, CONF_POWER_SENSOR: entity_id,
                    CONF_CREATE_DASHBOARD: user_input.get(CONF_CREATE_DASHBOARD, False)})
        schema = vol.Schema({
            vol.Optional(CONF_CREATE_DASHBOARD, default=False): bool,
            vol.Optional(CONF_POWER_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")),
        })
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input if user_input is not None else self.config_entry.options),
            errors=errors,
        )
