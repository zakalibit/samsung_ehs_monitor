"""Read-only Lovelace dashboard backed by the entity registry, not entity names."""
from pathlib import Path
import logging

from homeassistant.components import frontend
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import cached_json_fragment
from homeassistant.util.yaml import load_yaml_dict

from .const import CONF_CREATE_DASHBOARD, CONF_DEVICE_ID, DOMAIN

LOGGER = logging.getLogger(__name__)


def build_dashboard(template, sensors, title):
    """Resolve template keys, omitting missing/disabled rows and empty cards."""
    def resolve(value):
        if isinstance(value, list):
            return [resolved for item in value if (resolved := resolve(item)) is not None]
        if not isinstance(value, dict):
            return value
        entity_fields = {"entity", "return_entity"}
        if any(value[key] not in sensors for key in entity_fields if key in value):
            return None
        result = {key: (sensors[item] if key in entity_fields else resolve(item))
                  for key, item in value.items()}
        if "entities" in result and not result["entities"]:
            return None
        return result

    config = resolve(template)
    config["title"] = title
    return config


class SamsungEHSDashboard(LovelaceConfig):
    """Generate fresh IDs on every load; saves are intentionally unsupported."""

    def __init__(self, hass, entry, template, path):
        super().__init__(hass, path, {
            "title": f"Samsung EHS · {entry.title}", "mode": MODE_YAML,
            "icon": "mdi:heat-pump", "show_in_sidebar": True, "require_admin": False})
        self.entry = entry
        self.template = template

    @property
    def mode(self):
        return MODE_YAML

    async def async_load(self, force):
        prefix = f"{self.entry.data[CONF_DEVICE_ID]}_"
        sensors = {
            entity.unique_id[len(prefix):]: entity.entity_id
            for entity in er.async_entries_for_config_entry(
                er.async_get(self.hass), self.entry.entry_id)
            if entity.platform == DOMAIN and entity.domain == "sensor"
            and entity.unique_id.startswith(prefix) and entity.disabled_by is None
        }
        return build_dashboard(self.template, sensors, self.config["title"])

    async def async_json(self, force):
        return cached_json_fragment(await self.async_load(force))

    async def async_get_info(self):
        config = await self.async_load(False)
        return {"mode": self.mode, "views": len(config["views"])}

    @callback
    def registry_changed(self, event):
        # Includes removals and renames, whose old IDs may no longer be in the registry.
        self._config_updated()


async def async_setup_dashboard(hass, entry):
    """Register an ephemeral panel; never write user dashboards or .storage."""
    if not entry.options.get(CONF_CREATE_DASHBOARD, False):
        return
    path = f"samsung-ehs-{entry.entry_id.lower()}"
    dashboards = hass.data[LOVELACE_DATA].dashboards
    if path in dashboards or frontend.async_panel_exists(hass, path):
        LOGGER.warning("Cannot create Samsung EHS dashboard: path %s is already in use", path)
        return
    template = await hass.async_add_executor_job(
        load_yaml_dict, str(Path(__file__).with_name("dashboard.yaml")))
    dashboard = SamsungEHSDashboard(hass, entry, template, path)
    frontend.async_register_built_in_panel(
        hass, "lovelace", frontend_url_path=path,
        sidebar_title=dashboard.config["title"], sidebar_icon="mdi:heat-pump",
        config={"mode": MODE_YAML}, require_admin=False, show_in_sidebar=True)
    dashboards[path] = dashboard

    @callback
    def remove_dashboard():
        if dashboards.get(path) is dashboard:
            dashboards.pop(path)
            frontend.async_remove_panel(hass, path)

    entry.async_on_unload(remove_dashboard)
    entry.async_on_unload(hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED, dashboard.registry_changed))
