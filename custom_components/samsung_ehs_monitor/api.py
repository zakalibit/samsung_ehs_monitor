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
                return await response.json()
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
            items.extend(data.get("items", []))
            next_link = (data.get("_links") or {}).get("next") or {}
            href = next_link.get("href") if isinstance(next_link, dict) else None
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
        return await self._get(f"/devices/{device_id}")

    async def status(self, device_id: str) -> dict:
        data = await self._get(f"/devices/{device_id}/status")
        if not isinstance(data, dict) or not isinstance(data.get("components"), dict):
            raise SmartThingsError("SmartThings returned an invalid status structure")
        return data


def is_ehs_status(data: dict) -> bool:
    """Capability-based detection verified against user's Samsung EHS response."""
    main = (data.get("components") or {}).get("main") or {}
    return any(key in main for key in (
        "samsungce.ehsCycleData", "samsungce.ehsFsvSettings", "samsungce.ehsTemperatureReference"
    )) or any(
        "samsungce.ehsThermostat" in (component or {})
        for component in (data.get("components") or {}).values()
    )
