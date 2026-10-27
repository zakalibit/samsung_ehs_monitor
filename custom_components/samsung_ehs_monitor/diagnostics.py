"""Download a fresh SmartThings status response from the integration page."""
from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data

from .auth import async_create_client
from .const import CONF_DEVICE_ID

# Export only the status response, never entry data, headers or authentication.
TO_REDACT = {
    "token", "access_token", "refresh_token", "authorization", "Authorization",
    "password", "client_secret", "apikey", "api_key", "emoncms_api_key", "deviceId", "locationId", "ownerId",
    "serialNumber", "serial_number", "latitude", "longitude",
}


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    """Request new JSON on every download, independently of cached sensor data.

    Let API errors fail the download instead of exporting stale data as fresh.
    Bypass sensor structure validation so unsupported responses can be inspected.
    """
    client = await async_create_client(hass, entry.data)
    response = await client.raw_status(entry.data[CONF_DEVICE_ID])
    return async_redact_data(response, TO_REDACT)
