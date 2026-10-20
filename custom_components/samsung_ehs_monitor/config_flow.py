"""Standalone SmartThings OAuth sign-in with a manual-token fallback."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler
from homeassistant.core import callback

from .api import SmartThingsAuthError, SmartThingsClient, SmartThingsError, is_ehs_status
from .const import CONF_DEVICE_ID, CONF_TOKEN, CONF_POWER_SENSOR, CONF_CREATE_DASHBOARD, OAUTH_SCOPES, DOMAIN


class SamsungEHSConfigFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
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

    @property
    def logger(self):
        return logging.getLogger(__name__)

    @property
    def extra_authorize_data(self):
        return {"scope": " ".join(OAUTH_SCOPES)}

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["oauth", "token"])

    async def async_step_oauth(self, user_input=None):
        return await self.async_step_pick_implementation(user_input)

    async def async_oauth_create_entry(self, data):
        token = data.get(CONF_TOKEN, {})
        if not token.get("access_token") or not token.get("refresh_token"):
            return self.async_abort(reason="missing_refresh_token")
        scopes = token.get("scope", "")
        scopes = scopes.split() if isinstance(scopes, str) else scopes
        if scopes and not any(scope.startswith("r:devices:") for scope in scopes):
            return self.async_abort(reason="missing_scopes")
        self._auth = data
        error = await self._discover()
        if error is not None:
            return self.async_abort(reason=error)
        return await self.async_step_device()

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
        token = self._auth.get(CONF_TOKEN)
        access_token = token.get("access_token") if isinstance(token, dict) else token
        client = SmartThingsClient(async_get_clientsession(self.hass), access_token)
        try:
            matches = {}
            for device in await client.devices():
                device_id = device.get("deviceId")
                if not device_id:
                    continue
                if self._entry_to_update and device_id != self._entry_to_update.data[CONF_DEVICE_ID]:
                    continue
                try:
                    status = await client.status(device_id)
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
                        if key not in {CONF_TOKEN, "auth_implementation", "smartthings_entry"}}
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
