"""Regression tests for Lovelace resource registration and version migration."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

BASE = Path(__file__).resolve().parents[1] / 'custom_components/samsung_ehs_monitor'


class StorageResources:
    def __init__(self, items):
        self.saved = items
        self.items = []
        self.loaded = False
        self.writes = 0

    async def async_get_info(self):
        if not self.loaded:
            self.items = list(self.saved)
            self.loaded = True

    def async_items(self):
        return self.items

    async def async_create_item(self, data):
        self.writes += 1
        self.items.append({'id': 'new', 'url': data['url'], 'type': data['res_type']})

    async def async_update_item(self, item_id, data):
        self.writes += 1
        item = next(item for item in self.items if item['id'] == item_id)
        item.update(url=data['url'], type=data['res_type'])

    async def async_delete_item(self, item_id):
        self.writes += 1
        self.items = [item for item in self.items if item['id'] != item_id]


class YAMLResources:
    def __init__(self, data):
        self.data = data

    async def async_get_info(self):
        return {'resources': len(self.data)}

    def async_items(self):
        return self.data


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((BASE / 'frontend.py').read_text())
        nodes = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        self.scope = {'LOVELACE_DATA': 'lovelace', 'ResourceStorageCollection': StorageResources}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'frontend.py', 'exec'), self.scope)
        self.path = self.scope['RESOURCE_PATH']
        self.url = self.scope['RESOURCE_URL']

    async def register(self, resources):
        await self.scope['async_register_resources'](NS(data={'lovelace': NS(resources=resources)}))

    async def test_new_install_and_repeated_setup(self):
        other = {'id': 'other', 'url': '/other.js', 'type': 'module'}
        resources = StorageResources([other])
        await self.register(resources)
        await self.register(resources)
        self.assertEqual(resources.writes, 1)
        self.assertEqual(resources.items, [other, {'id': 'new', 'url': self.url, 'type': 'module'}])

    async def test_upgrade_loads_saved_entries_and_removes_only_own_duplicates(self):
        other = {'id': 'other', 'url': '/other.js', 'type': 'module'}
        resources = StorageResources([
            {'id': 'old', 'url': self.path + '?v=0.8.1', 'type': 'js'},
            {'id': 'duplicate', 'url': self.path, 'type': 'module'}, other])
        await self.register(resources)
        self.assertEqual(resources.items, [
            {'id': 'old', 'url': self.url, 'type': 'module'}, other])
        writes = resources.writes
        await self.register(resources)
        self.assertEqual(resources.writes, writes)

    async def test_yaml_resources_keep_unrelated_items(self):
        other = {'url': '/other.js', 'type': 'module'}
        resources = YAMLResources([other, {'url': self.path + '?v=old', 'type': 'js'}])
        await self.register(resources)
        await self.register(resources)
        self.assertEqual(resources.data, [other, {'url': self.url, 'type': 'module'}])


if __name__ == '__main__':
    unittest.main()
