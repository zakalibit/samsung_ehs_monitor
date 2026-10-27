"""Choose a manual token or an existing SmartThings connection."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector, entity_registry as er
from homeassistant.core import callback

from .emoncms import valid_url
from .api import SmartThingsAuthError, SmartThingsError, is_ehs_status
from .auth import async_create_client
from .const import CONF_DEVICE_ID, CONF_TOKEN, CONF_POWER_SENSOR, CONF_CREATE_DASHBOARD, CONF_SMARTTHINGS_ENTRY, DOMAIN, CONF_EMONCMS_ENABLED, CONF_EMONCMS_URL, CONF_EMONCMS_NODE, CONF_EMONCMS_KEY


class SamsungEHSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    DOMAIN = DOMAIN

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SamsungEHSOptionsFlow()

    def __init__(self):
        super().__init__()
        self._auth = {}
        self._devices = {}
        self._entry_to_update = None

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["smartthings", "token"])

    async def async_step_smartthings(self, user_input=None):
        connections = {
            entry.entry_id: entry.title
            for entry in self.hass.config_entries.async_entries("smartthings")
        }
        if not connections:
            return self.async_abort(reason="no_smartthings")
        errors = {}
        if user_input is not None:
            source_id = user_input[CONF_SMARTTHINGS_ENTRY]
            if source_id not in connections:
                errors["base"] = "invalid_connection"
            else:
                self._auth = {CONF_SMARTTHINGS_ENTRY: source_id}
                error = await self._discover()
                if error is None:
                    return await self.async_step_device()
                errors["base"] = error
        return self.async_show_form(
            step_id="smartthings", data_schema=vol.Schema({
                vol.Required(CONF_SMARTTHINGS_ENTRY): vol.In(connections)}), errors=errors)

    async def async_step_token(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._auth = {CONF_TOKEN: user_input[CONF_TOKEN].strip()}
            error = await self._discover()
            if error is None:
                return await self.async_step_device()
            errors["base"] = error
        return self.async_show_form(
            step_id="token", data_schema=vol.Schema({
                vol.Required(CONF_TOKEN): selector.TextSelector(selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD))}), errors=errors)

    async def _discover(self):
        self._devices = {}
        try:
            client = await async_create_client(self.hass, self._auth)
            matches = {}
            for device in await client.devices():
                device_id = device.get("deviceId")
                if not device_id:
                    continue
                if self._entry_to_update and device_id != self._entry_to_update.data[CONF_DEVICE_ID]:
                    continue
                try:
                    status = await client.status(device_id)
                except SmartThingsAuthError:
                    raise
                except SmartThingsError:
                    continue
                if is_ehs_status(status):
                    label = device.get("label") or device.get("name") or "Samsung EHS"
                    matches[device_id] = f"{label} ({device_id[:8]}…)"
            if not matches:
                return "no_devices"
            self._devices = matches
        except SmartThingsAuthError:
            return "invalid_auth"
        except SmartThingsError:
            return "cannot_connect"
        return None

    async def async_step_device(self, user_input=None):
        if not self._auth or not self._devices:
            return await self.async_step_user()
        errors = {}
        if user_input is not None:
            chosen = user_input[CONF_DEVICE_ID]
            if chosen not in self._devices:
                errors["base"] = "invalid_device"
            elif self._entry_to_update:
                # Keep the device identity, options, entities and stored history.
                data = {key: value for key, value in self._entry_to_update.data.items()
                        if key not in {CONF_TOKEN, "auth_implementation", CONF_SMARTTHINGS_ENTRY}}
                data.update(self._auth)
                result = self.async_update_and_abort(self._entry_to_update, data=data)
                self.hass.config_entries.async_schedule_reload(self._entry_to_update.entry_id)
                return result
            else:
                await self.async_set_unique_id(chosen)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._devices[chosen], data={**self._auth, CONF_DEVICE_ID: chosen})
        return self.async_show_form(step_id="device", data_schema=vol.Schema({
            vol.Required(CONF_DEVICE_ID): vol.In(self._devices)}), errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        self._entry_to_update = self._get_reconfigure_entry()
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data):
        self._entry_to_update = self._get_reauth_entry()
        return await self.async_step_user()


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
            publishing = bool(user_input.get(CONF_EMONCMS_ENABLED, False))
            url = user_input.get(CONF_EMONCMS_URL, 'https://emoncms.org').strip().rstrip('/')
            node = user_input.get(CONF_EMONCMS_NODE, 28)
            key = (user_input.get(CONF_EMONCMS_KEY) or self.config_entry.options.get(CONF_EMONCMS_KEY, '')).strip()
            if publishing:
                if not entity_id:
                    errors[CONF_POWER_SENSOR] = 'emoncms_requires_power'
                if not valid_url(url):
                    errors[CONF_EMONCMS_URL] = 'invalid_emoncms_url'
                if isinstance(node, bool) or not isinstance(node, int) or not 1 <= node <= 65535:
                    errors[CONF_EMONCMS_NODE] = 'invalid_emoncms_node'
                if len(key) != 32 or any(char not in '0123456789abcdefABCDEF' for char in key):
                    errors[CONF_EMONCMS_KEY] = 'invalid_emoncms_key'
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if entry.entry_id != self.config_entry.entry_id and entry.options.get(CONF_EMONCMS_ENABLED):
                        if entry.options.get(CONF_EMONCMS_URL, '').rstrip('/') == url and entry.options.get(CONF_EMONCMS_NODE) == node:
                            errors[CONF_EMONCMS_NODE] = 'emoncms_node_in_use'
            if not errors:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options, CONF_POWER_SENSOR: entity_id,
                    CONF_CREATE_DASHBOARD: user_input.get(CONF_CREATE_DASHBOARD, False),
                    CONF_EMONCMS_ENABLED: publishing, CONF_EMONCMS_URL: url,
                    CONF_EMONCMS_NODE: node, CONF_EMONCMS_KEY: key})
        schema = vol.Schema({
            vol.Optional(CONF_EMONCMS_ENABLED, default=False): bool,
            vol.Optional(CONF_EMONCMS_URL, default='https://emoncms.org'): str,
            vol.Optional(CONF_EMONCMS_NODE, default=28): int,
            vol.Optional(CONF_EMONCMS_KEY): selector.TextSelector(selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD)),
            vol.Optional(CONF_CREATE_DASHBOARD, default=False): bool,
            vol.Optional(CONF_POWER_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")),
        })
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {key: value for key, value in (user_input if user_input is not None else self.config_entry.options).items()
                         if key != CONF_EMONCMS_KEY}),
            errors=errors,
        )
