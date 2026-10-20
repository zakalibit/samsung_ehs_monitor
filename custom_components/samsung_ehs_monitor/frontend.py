"""Register the bundled components with Lovelace's resource loader."""
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection

RESOURCE_PATH = "/samsung_ehs_monitor/daily-cop-card.js"
RESOURCE_URL = f"{RESOURCE_PATH}?v=0.8.2"


async def async_register_resources(hass):
    """Maintain one versioned module, preserving unrelated dashboard resources."""
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
        # YAML resources are runtime-only; do not rewrite the user's YAML.
        resources.data = [item for item in resources.async_items() if item not in matches]
        resources.data.append({"url": RESOURCE_URL, "type": "module"})
