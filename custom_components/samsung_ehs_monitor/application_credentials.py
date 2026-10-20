"""Standalone SmartThings OAuth application credentials and token exchange."""
from aiohttp import BasicAuth, ClientTimeout

from homeassistant.exceptions import OAuth2TokenRequestConnectionError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import LocalOAuth2Implementation

from .const import DOMAIN, OAUTH_AUTHORIZE_URL, OAUTH_TOKEN_URL


class EHSOAuth2Implementation(LocalOAuth2Implementation):
    """SmartThings requires HTTP Basic client authentication at the token endpoint."""

    @property
    def service_domain(self):
        return DOMAIN

    async def _token_request(self, data):
        body = {**data, "client_id": self.client_id}
        body.pop("client_secret", None)
        session = async_get_clientsession(self.hass)
        async with session.post(
            self.token_url, data=body, auth=BasicAuth(self.client_id, self.client_secret),
            headers={"Accept": "application/json"}, timeout=ClientTimeout(total=30),
            allow_redirects=False,
        ) as response:
            response.raise_for_status()
            try:
                token = await response.json()
                valid = (isinstance(token, dict) and bool(token.get("access_token"))
                         and float(token.get("expires_in", 0)) > 0)
                if body.get("grant_type") == "authorization_code":
                    valid = valid and bool(token.get("refresh_token"))
                if not valid:
                    raise ValueError("Invalid token response")
            except (TypeError, ValueError, AttributeError) as exc:
                raise OAuth2TokenRequestConnectionError(domain=DOMAIN) from exc
        return token


async def async_get_auth_implementation(hass, auth_domain, credential):
    return EHSOAuth2Implementation(
        hass, auth_domain, credential.client_id, credential.client_secret,
        OAUTH_AUTHORIZE_URL, OAUTH_TOKEN_URL)


async def async_get_description_placeholders(hass):
    return {"oauth_consent_url": "https://github.com/zakalibit/samsung_ehs_monitor#oauth-setup"}
