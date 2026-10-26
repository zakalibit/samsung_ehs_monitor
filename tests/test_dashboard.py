"""Test registry resolution and managed panel lifecycle without a HA installation."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

BASE = Path(__file__).resolve().parents[1] / 'custom_components/samsung_ehs_monitor'


class Config:
    def __init__(self, hass, path, config):
        self.hass = hass
        self.config = {**config, 'url_path': path}
        self._config_updated = Mock()


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((BASE / 'dashboard.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.frontend = NS(async_panel_exists=Mock(return_value=False),
                           async_register_built_in_panel=Mock(), async_remove_panel=Mock())
        self.registry = NS(async_entries_for_config_entry=lambda registry, entry_id: registry[entry_id],
                           async_get=lambda hass: hass.registry,
                           EVENT_ENTITY_REGISTRY_UPDATED='registry_updated')
        self.scope = dict(LovelaceConfig=Config, MODE_YAML='yaml', LOVELACE_DATA='lovelace',
                          DOMAIN='samsung_ehs_monitor', CONF_DEVICE_ID='device_id',
                          CONF_CREATE_DASHBOARD='create_dashboard', er=self.registry,
                          callback=lambda f: f, frontend=self.frontend, Path=Path,
                          __file__=str(BASE / 'dashboard.py'), LOGGER=Mock(),
                          load_yaml_dict=lambda path: self.template, cached_json_fragment=lambda v: v)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'dashboard.py', 'exec'), self.scope)
        self.template = {'title': 'EHS', 'views': [{'cards': [
            {'type': 'markdown', 'content': 'Help'},
            {'type': 'entities', 'entities': [{'entity': 'thermal_power', 'name': 'Heat'},
                                              {'entity': 'energy'}]},
            {'type': 'custom:samsung-ehs-daily-cop-card', 'entity': 'estimated_cop_today'},
            {'type': 'history-graph', 'entities': [{'entity': 'missing'}]},
        ]}]}
        self.cleanups = []
        self.entry = NS(entry_id='entry1', data={'device_id': 'pump1'}, title='Heat pump',
                        options={'create_dashboard': True}, async_on_unload=self.cleanups.append)
        self.entities = [self.entity('thermal_power', 'sensor.kitchen_heat'),
                         self.entity('energy', 'sensor.disabled', disabled='user'),
                         self.entity('estimated_cop_today', 'sensor.daily'),
                         self.entity('fsv_2011', 'sensor.custom_fsv')]
        self.unsubscribe = Mock()
        async def executor(func, *args):
            return func(*args)
        self.hass = NS(data={'lovelace': NS(dashboards={})}, registry={'entry1': self.entities},
                       async_add_executor_job=executor,
                       bus=NS(async_listen=Mock(return_value=self.unsubscribe)))

    def test_week_entity_resolves_by_sensor_key(self):
        template = {'views': [{'cards': [{'type': 'custom:samsung-ehs-performance-row',
            'entity': 'estimated_cop', 'return_entity': 'estimated_cop_today',
            'week_entity': 'estimated_cop_7d'}]}]}
        result = self.scope['build_dashboard'](template, {
            'estimated_cop': 'sensor.instant', 'estimated_cop_today': 'sensor.today',
            'estimated_cop_7d': 'sensor.renamed_week'}, 'EHS')
        self.assertEqual(result['views'][0]['cards'][0]['week_entity'], 'sensor.renamed_week')

    def entity(self, key, eid, disabled=None, device='pump1', platform='samsung_ehs_monitor'):
        return NS(unique_id=f'{device}_{key}', entity_id=eid, domain='sensor',
                  platform=platform, disabled_by=disabled)

    async def test_resolution_rename_filtering(self):
        self.entities.extend([self.entity('energy', 'sensor.other_pump', device='pump2'),
                              self.entity('energy', 'sensor.other_integration', platform='other')])
        dashboard = self.scope['SamsungEHSDashboard'](self.hass, self.entry, self.template, 'ehs-test')
        config = await dashboard.async_load(False)
        cards = config['views'][0]['cards']
        self.assertEqual(len(cards), 3)  # Help, heat, daily COP; empty history removed.
        self.assertEqual(cards[1]['entities'], [{'entity': 'sensor.kitchen_heat', 'name': 'Heat'}])
        self.entities[0].entity_id = 'sensor.renamed'
        dashboard.registry_changed(NS(data={'entity_id': 'sensor.renamed', 'action': 'update',
                                                'changes': {'entity_id': 'sensor.kitchen_heat'}}))
        dashboard._config_updated.assert_called_once()
        config = await dashboard.async_json(False)
        self.assertEqual(config['views'][0]['cards'][1]['entities'][0]['entity'], 'sensor.renamed')
        self.assertEqual(self.template['views'][0]['cards'][1]['entities'][0]['entity'], 'thermal_power')
        self.entities.clear()
        self.assertEqual((await dashboard.async_load(False))['views'][0]['cards'],
                         [{'type': 'markdown', 'content': 'Help'}])

    def test_combined_temperatures_resolve_both_registered_ids(self):
        template = {'views': [{'cards': [{'type': 'entities', 'entities': [{
            'type': 'custom:samsung-ehs-flow-return-row',
            'entity': 'water_flow_temperature', 'return_entity': 'water_return_temperature',
        }]}]}]}
        sensors = {'water_flow_temperature': 'sensor.renamed_flow',
                   'water_return_temperature': 'sensor.kitchen_return'}
        config = self.scope['build_dashboard'](template, sensors, 'EHS')
        row = config['views'][0]['cards'][0]['entities'][0]
        self.assertEqual(row['entity'], 'sensor.renamed_flow')
        self.assertEqual(row['return_entity'], 'sensor.kitchen_return')
        del sensors['water_return_temperature']
        config = self.scope['build_dashboard'](template, sensors, 'EHS')
        self.assertEqual(config['views'][0]['cards'], [])

    def test_compressor_row_resolves_all_three_ids_and_omits_disabled_sources(self):
        template = {'views': [{'cards': [{'type': 'entities', 'entities': [{
            'type': 'custom:samsung-ehs-compressor-row', 'entity': 'compressor_frequency',
            'discharge_entity': 'discharge_temperature', 'pump_entity': 'circulation_pump_speed',
        }]}]}]}
        sensors = {'compressor_frequency': 'sensor.kitchen_compressor',
                   'discharge_temperature': 'sensor.renamed_discharge',
                   'circulation_pump_speed': 'sensor.renamed_pump'}
        config = self.scope['build_dashboard'](template, sensors, 'EHS')
        row = config['views'][0]['cards'][0]['entities'][0]
        self.assertEqual(row['entity'], 'sensor.kitchen_compressor')
        self.assertEqual(row['discharge_entity'], 'sensor.renamed_discharge')
        self.assertEqual(row['pump_entity'], 'sensor.renamed_pump')
        for missing in sensors:
            available = {key: value for key, value in sensors.items() if key != missing}
            self.assertEqual(self.scope['build_dashboard'](template, available, 'EHS')['views'][0]['cards'], [])

    def test_log_template_uses_registered_sensor_after_rename(self):
        template = {'views': [{'cards': [{'type': 'markdown',
                    'content': "{{ state_attr('__FAILURE_LOG_ENTITY__', 'entries') }}"}]}]}
        config = self.scope['build_dashboard'](template, {'failure_log': 'sensor.renamed_log'}, 'EHS')
        content = config['views'][0]['cards'][0]['content']
        self.assertIn('sensor.renamed_log', content)
        self.assertNotIn('__FAILURE_LOG_ENTITY__', content)

    async def test_registry_updates_ignore_unrelated_entities_and_irrelevant_attributes(self):
        dashboard = self.scope['SamsungEHSDashboard'](self.hass, self.entry, self.template, 'ehs-test')
        await dashboard.async_load(False)
        for data in ({'entity_id': 'sensor.unrelated', 'action': 'create'},
                     {'entity_id': 'sensor.unrelated', 'action': 'remove'},
                     {'entity_id': 'sensor.kitchen_heat', 'action': 'update', 'changes': {'icon': 'old'}}):
            dashboard.registry_changed(NS(data=data))
        dashboard._config_updated.assert_not_called()
        self.entities[0].disabled_by = 'user'
        dashboard.registry_changed(NS(data={'entity_id': 'sensor.kitchen_heat', 'action': 'update',
                                            'changes': {'disabled_by': None}}))
        dashboard._config_updated.assert_called_once()
        dashboard._config_updated.reset_mock()
        self.entities.pop(0)
        dashboard.registry_changed(NS(data={'entity_id': 'sensor.kitchen_heat', 'action': 'remove'}))
        dashboard._config_updated.assert_called_once()

    def test_example_is_generated_from_current_template_and_sensor_names(self):
        import subprocess
        import sys
        subprocess.run([sys.executable, 'scripts/generate_dashboard_example.py', '--check'],
                       cwd=BASE.parents[1], check=True, capture_output=True)

    def test_weather_curve_resolves_available_roles_after_renames(self):
        template = {'views': [{'cards': [{'type': 'custom:samsung-ehs-weather-curve-card',
            'entities': [{'entity': 'fsv_2011', 'role': 'outdoor_cold'},
                         {'entity': 'fsv_2041', 'role': 'selected'},
                         {'entity': 'outdoor_temperature', 'role': 'outdoor'}]}]}]}
        config = self.scope['build_dashboard'](template, {
            'fsv_2011': 'sensor.renamed_cold_endpoint',
            'outdoor_temperature': 'sensor.kitchen_outdoor'}, 'EHS')
        card = config['views'][0]['cards'][0]
        self.assertEqual(card['entities'], [
            {'entity': 'sensor.renamed_cold_endpoint', 'role': 'outdoor_cold'},
            {'entity': 'sensor.kitchen_outdoor', 'role': 'outdoor'}])

    async def test_setup_unload_reload_and_opt_out(self):
        setup = self.scope['async_setup_dashboard']
        await setup(self.hass, self.entry)
        dashboards = self.hass.data['lovelace'].dashboards
        self.assertEqual(list(dashboards), ['samsung-ehs-entry1'])
        self.frontend.async_register_built_in_panel.assert_called_once()
        for cleanup in self.cleanups:
            cleanup()
        self.assertEqual(dashboards, {})
        self.unsubscribe.assert_called_once()
        await setup(self.hass, self.entry)
        self.assertEqual(len(dashboards), 1)
        self.entry.options = {}
        self.entry.entry_id = 'entry2'
        await setup(self.hass, self.entry)
        self.assertEqual(len(dashboards), 1)

    async def test_collision_preserves_existing_dashboard(self):
        existing = object()
        self.hass.data['lovelace'].dashboards['samsung-ehs-entry1'] = existing
        await self.scope['async_setup_dashboard'](self.hass, self.entry)
        self.assertIs(self.hass.data['lovelace'].dashboards['samsung-ehs-entry1'], existing)
        self.frontend.async_register_built_in_panel.assert_not_called()
        self.assertEqual(self.cleanups, [])

    async def test_two_entries_have_separate_panels_and_sensors(self):
        await self.scope['async_setup_dashboard'](self.hass, self.entry)
        other = NS(entry_id='entry2', title='Second pump', data={'device_id': 'pump2'},
                   options={'create_dashboard': True}, async_on_unload=lambda f: None)
        self.hass.registry['entry2'] = [self.entity('thermal_power', 'sensor.second', device='pump2')]
        await self.scope['async_setup_dashboard'](self.hass, other)
        dashboards = self.hass.data['lovelace'].dashboards
        self.assertEqual(len(dashboards), 2)
        config = await dashboards['samsung-ehs-entry2'].async_load(False)
        self.assertEqual(config['views'][0]['cards'][1]['entities'][0]['entity'], 'sensor.second')

    def test_bundled_template_uses_known_unique_id_keys(self):
        import re
        sensor_source = (BASE / 'sensor.py').read_text()
        keys = re.findall(r'entity: (\w+)', (BASE / 'dashboard.yaml').read_text())
        self.assertGreater(len(keys), 25)
        for key in keys:
            self.assertTrue(f'"{key}"' in sensor_source or key.startswith('fsv_') or key in {'cycle_indoor', 'cycle_outdoor'}, key)
        self.assertNotIn('kitchen_', (BASE / 'dashboard.yaml').read_text())


if __name__ == '__main__':
    unittest.main()
