"""Manual tokens or authentication shared with the built-in SmartThings integration."""
import asyncio

from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartThingsAuthError, SmartThingsClient, SmartThingsError
from .const import CONF_SMARTTHINGS_ENTRY, CONF_TOKEN


async def async_create_client(hass, data):
    """Resolve the source connection anew for every request, including after reloads."""
    source_id = data.get(CONF_SMARTTHINGS_ENTRY)
    if not source_id:
        token = data.get(CONF_TOKEN)
        if not isinstance(token, str) or not token.strip():
            raise SmartThingsAuthError(
                "Choose a manual access token or reuse a SmartThings connection")
        return SmartThingsClient(async_get_clientsession(hass), token.strip())

    async def access_token():
        source = hass.config_entries.async_get_entry(source_id)
        if source is None or source.domain != "smartthings":
            raise SmartThingsAuthError(
                "The selected SmartThings connection was removed; reconfigure Samsung EHS Monitor")
        if source.state != ConfigEntryState.LOADED:
            raise SmartThingsError("The selected SmartThings connection is not loaded; check its setup")
        runtime = getattr(source, "runtime_data", None)
        client = getattr(runtime, "client", None)
        refresh = getattr(client, "refresh_token_function", None)
        if not callable(refresh):
            raise SmartThingsError(
                "The SmartThings connection cannot share authentication; update Home Assistant or use a manual token")
        try:
            # Use the official integration's session, lock and token persistence.
            # Creating another OAuth session could race refresh-token rotation.
            token = await refresh()
            if not isinstance(token, str) or not token:
                raise SmartThingsAuthError("The SmartThings connection has no access token; repair its authentication")
            return token
        except ConfigEntryAuthFailed as exc:
            raise SmartThingsAuthError("Repair authentication on the selected SmartThings connection") from exc
        except ClientResponseError as exc:
            if exc.status in (400, 401, 403):
                raise SmartThingsAuthError("Repair authentication on the selected SmartThings connection") from exc
            raise SmartThingsError("SmartThings token refresh is temporarily unavailable") from exc
        except (ClientError, asyncio.TimeoutError, HomeAssistantError) as exc:
            raise SmartThingsError("SmartThings token refresh is temporarily unavailable") from exc

    return SmartThingsClient(async_get_clientsession(hass), token_provider=access_token)
