"""Register the bundled components with Lovelace's resource loader."""
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection

RESOURCE_PATH = "/samsung_ehs_monitor/daily-cop-card.js"
RESOURCE_URL = f"{RESOURCE_PATH}?v=0.12.28"


async def async_register_resources(hass):
    """Maintain one versioned module, preserving unrelated dashboard resources."""
    # The frontend's module loader survives replacement of YAML resource collections
    # by lovelace.reload_resources. The bundle defers HTMLElement definitions until
    # HA has installed its scoped registry; early definitions would be lost.
    # The same URL is loaded only once by the browser.
    add_extra_js_url(hass, RESOURCE_URL)
    resources = hass.data[LOVELACE_DATA].resources
    # Storage resources are loaded lazily. Inspecting async_items before loading
    # can miss an existing entry and create a duplicate on every restart.
    await resources.async_get_info()
    matches = [item for item in resources.async_items()
               if item["url"].split("?", 1)[0] == RESOURCE_PATH]
    if isinstance(resources, ResourceStorageCollection):
        if matches:
            current, *duplicates = matches
            if current["url"] != RESOURCE_URL or current["type"] != "module":
                await resources.async_update_item(current["id"], {
                    "url": RESOURCE_URL, "res_type": "module"})
            for item in duplicates:
                await resources.async_delete_item(item["id"])
        else:
            await resources.async_create_item({"url": RESOURCE_URL, "res_type": "module"})
    else:
        # YAML resources belong to the user. The frontend module registration above
        # provides our components without injecting a resource that reload discards.
        return
