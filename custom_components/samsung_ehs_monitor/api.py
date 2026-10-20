"""Read-only SmartThings client. This module sends GET requests exclusively."""
from __future__ import annotations

import asyncio
from aiohttp import ClientError, ClientResponseError, ClientTimeout

from .const import API_BASE


class SmartThingsError(Exception):
    """SmartThings service or network error."""


class SmartThingsAuthError(SmartThingsError):
    """Invalid or expired token."""


class SmartThingsClient:
    def __init__(self, session, token: str | None = None, *, token_provider=None):
        self._session = session
        self._token = token
        self._token_provider = token_provider

    async def _get(self, path: str) -> dict:
        try:
            token = await self._token_provider() if self._token_provider else self._token
            if not token:
                raise SmartThingsAuthError("SmartThings authentication is not configured")
            async with self._session.get(
                f"{API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                data = await response.json()
                if not isinstance(data, dict):
                    raise SmartThingsError("SmartThings returned an invalid response structure")
                return data
        except ClientResponseError as exc:
            if exc.status in (401, 403):
                raise SmartThingsAuthError("SmartThings access denied") from exc
            raise SmartThingsError(f"SmartThings HTTP {exc.status}") from exc
        except ValueError as exc:
            raise SmartThingsError("SmartThings returned invalid JSON") from exc
        except (ClientError, asyncio.TimeoutError) as exc:
            raise SmartThingsError("Unable to connect to SmartThings") from exc

    async def devices(self) -> list[dict]:
        """List accessible devices; SmartThings paginates with _links.next.href."""
        items: list[dict] = []
        path = "/devices"
        for _ in range(15):
            data = await self._get(path)
            page = data.get("items")
            if (not isinstance(page, list) or any(not isinstance(row, dict)
                    or not isinstance(row.get("deviceId"), str) for row in page)):
                raise SmartThingsError("SmartThings returned an invalid device list")
            items.extend(page)
            links = data.get("_links", {})
            if not isinstance(links, dict):
                raise SmartThingsError("SmartThings returned invalid pagination")
            next_link = links.get("next", {})
            if not isinstance(next_link, dict):
                raise SmartThingsError("SmartThings returned invalid pagination")
            href = next_link.get("href")
            if href is not None and not isinstance(href, str):
                raise SmartThingsError("SmartThings returned invalid pagination")
            if not href:
                break
            # Never follow off-site pagination links with the access token.
            if href.startswith(API_BASE):
                path = href[len(API_BASE):]
            elif href.startswith("/devices"):
                path = href
            else:
                break
        return items

    async def device(self, device_id: str) -> dict:
        data = await self._get(f"/devices/{device_id}")
        if data.get("presentationId") is not None and not isinstance(data["presentationId"], str):
            raise SmartThingsError("SmartThings returned invalid device metadata")
        return data

    async def status(self, device_id: str) -> dict:
        data = await self._get(f"/devices/{device_id}/status")
        validate_status(data)
        return data


def validate_status(data):
    """Validate containers at the API boundary; packet contents belong to the decoder."""
    if (not isinstance(data, dict) or not isinstance(data.get("components"), dict)
            or not isinstance(data["components"].get("main"), dict)):
        raise SmartThingsError("SmartThings returned an invalid status structure")
    for component in data["components"].values():
        if not isinstance(component, dict):
            raise SmartThingsError("SmartThings returned an invalid component structure")
        for capability, attributes in component.items():
            if not isinstance(attributes, dict):
                raise SmartThingsError("SmartThings returned an invalid capability structure")
            for name, attribute in attributes.items():
                if not isinstance(attribute, dict):
                    raise SmartThingsError("SmartThings returned an invalid attribute structure")
                timestamp = attribute.get("timestamp")
                if timestamp is not None and not isinstance(timestamp, str):
                    raise SmartThingsError("SmartThings returned an invalid attribute timestamp")
                value = attribute.get("value")
                if value is None:
                    continue
                if capability in {"samsungce.ehsCycleData", "samsungce.ehsFsvSettings"}:
                    if not isinstance(value, list):
                        raise SmartThingsError("SmartThings returned an invalid cycle/settings list")
                    if capability == "samsungce.ehsFsvSettings":
                        for row in value:
                            if (not isinstance(row, dict) or not isinstance(row.get("id"), (str, int))
                                    or not isinstance(row.get("value"), (str, int, float, bool, type(None)))):
                                raise SmartThingsError("SmartThings returned an invalid settings entry")
                elif capability == "powerConsumptionReport" and name == "powerConsumption":
                    if not isinstance(value, dict):
                        raise SmartThingsError("SmartThings returned an invalid power report")
                elif (capability, name) in {
                    ("temperatureMeasurement", "temperature"), ("switch", "switch"),
                    ("airConditionerMode", "airConditionerMode"),
                    ("samsungce.ehsTemperatureReference", "temperatureReference"),
                    ("thermostatCoolingSetpoint", "coolingSetpoint"),
                    ("custom.outingMode", "outingMode"), ("samsungce.deviceIdentification", "binaryId"),
                }:
                    if not isinstance(value, (str, int, float, bool)):
                        raise SmartThingsError("SmartThings returned an invalid sensor value")


def is_ehs_status(data: dict) -> bool:
    """Capability-based detection verified against user's Samsung EHS response."""
    main = (data.get("components") or {}).get("main") or {}
    return any(key in main for key in (
        "samsungce.ehsCycleData", "samsungce.ehsFsvSettings", "samsungce.ehsTemperatureReference"
    )) or any(
        "samsungce.ehsThermostat" in (component or {})
        for component in (data.get("components") or {}).values()
    )
