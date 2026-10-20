"""OAuth sessions owned by this integration, with automatic token refresh."""
import asyncio

from aiohttp import ClientError, ClientResponseError
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session, UnknownImplementationError, async_get_config_entry_implementation,
)

from .api import SmartThingsAuthError, SmartThingsClient, SmartThingsError
from .const import CONF_TOKEN


async def async_create_client(hass, entry):
    """Use our own saved OAuth credentials or an existing manual token."""
    if not isinstance(entry.data.get(CONF_TOKEN), dict):
        return SmartThingsClient(async_get_clientsession(hass), entry.data.get(CONF_TOKEN))
    try:
        implementation = await async_get_config_entry_implementation(hass, entry)
    except UnknownImplementationError as exc:
        raise SmartThingsAuthError("OAuth application credentials were removed; sign in again") from exc
    except HomeAssistantError as exc:
        raise SmartThingsError("OAuth application credentials are temporarily unavailable") from exc
    oauth = OAuth2Session(hass, entry, implementation)

    async def access_token():
        try:
            await oauth.async_ensure_token_valid()
            token = oauth.token.get("access_token")
            if not token:
                raise SmartThingsAuthError("OAuth access token is missing; sign in again")
            return token
        except ConfigEntryAuthFailed as exc:
            raise SmartThingsAuthError("SmartThings authorization expired; sign in again") from exc
        except ClientResponseError as exc:
            if exc.status in (400, 401, 403):
                raise SmartThingsAuthError("SmartThings authorization expired; sign in again") from exc
            raise SmartThingsError("SmartThings token refresh is temporarily unavailable") from exc
        except (ClientError, asyncio.TimeoutError, HomeAssistantError) as exc:
            raise SmartThingsError("SmartThings token refresh is temporarily unavailable") from exc

    return SmartThingsClient(async_get_clientsession(hass), token_provider=access_token)
