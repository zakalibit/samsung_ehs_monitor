"""Manual authentication, shared refresh callbacks and safe reconfiguration."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

BASE = Path(__file__).resolve().parents[1] / 'custom_components/samsung_ehs_monitor'


class ClientError(Exception):
    pass


class HTTPError(ClientError):
    def __init__(self, status):
        self.status = status


class HAError(Exception):
    pass


class AuthFailed(HAError):
    pass


def production(filename, scope):
    tree = ast.parse((BASE / filename).read_text())
    nodes = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))
             or isinstance(n, ast.ImportFrom) and n.module == '__future__']
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), scope)
    return scope


api = production('api.py', dict(asyncio=asyncio, ClientError=ClientError,
                               ClientResponseError=HTTPError, ClientTimeout=lambda **kw: kw,
                               API_BASE='https://api.smartthings.com/v1'))


class Response:
    def __init__(self, data=None):
        self.data = data or {'items': [], 'components': {'main': {}}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return self.data


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.refresh = AsyncMock(return_value='first')
        self.source = NS(entry_id='source', domain='smartthings', state='loaded',
                         runtime_data=NS(client=NS(refresh_token_function=self.refresh)))
        self.http = NS(get=Mock(side_effect=lambda *a, **kw: Response()))
        self.entries = NS(async_get_entry=Mock(return_value=self.source))
        self.hass = NS(config_entries=self.entries)
        self.scope = production('auth.py', dict(
            asyncio=asyncio, ClientError=ClientError, ClientResponseError=HTTPError,
            HomeAssistantError=HAError, ConfigEntryAuthFailed=AuthFailed,
            ConfigEntryState=NS(LOADED='loaded'), CONF_SMARTTHINGS_ENTRY='smartthings_entry',
            async_get_clientsession=lambda h: self.http, CONF_TOKEN='token',
            SmartThingsAuthError=api['SmartThingsAuthError'], SmartThingsError=api['SmartThingsError'],
            SmartThingsClient=api['SmartThingsClient']))

    async def client(self, data=None):
        return await self.scope['async_create_client'](
            self.hass, data if data is not None else {'smartthings_entry': 'source'})

    async def test_shared_callback_refreshes_before_every_request(self):
        client = await self.client()
        await client.devices()
        self.refresh.return_value = 'rotated'
        await client.status('pump')
        self.assertEqual(self.refresh.await_count, 2)
        self.assertEqual([call.kwargs['headers']['Authorization'] for call in self.http.get.call_args_list],
                         ['Bearer first', 'Bearer rotated'])
        self.entries.async_get_entry.assert_called_with('source')

    async def test_source_reload_resolves_new_runtime(self):
        client = await self.client()
        await client.devices()
        replacement = AsyncMock(return_value='new-runtime')
        self.source.runtime_data = NS(client=NS(refresh_token_function=replacement))
        await client.devices()
        replacement.assert_awaited_once()
        self.assertEqual(self.refresh.await_count, 1)
        self.assertEqual(self.http.get.call_args.kwargs['headers']['Authorization'], 'Bearer new-runtime')

    async def test_unloaded_source_is_retryable_and_resumes(self):
        client = await self.client()
        self.source.state = 'not_loaded'
        with self.assertRaises(api['SmartThingsError']) as caught:
            await client.devices()
        self.assertNotIsInstance(caught.exception, api['SmartThingsAuthError'])
        self.refresh.assert_not_awaited()
        self.http.get.assert_not_called()
        self.source.state = 'loaded'
        await client.devices()
        self.refresh.assert_awaited_once()

    async def test_removed_or_wrong_domain_requires_reconfiguration(self):
        client = await self.client()
        for source in (None, NS(domain='other')):
            self.entries.async_get_entry.return_value = source
            with self.assertRaises(api['SmartThingsAuthError']):
                await client.devices()
        self.http.get.assert_not_called()

    async def test_missing_runtime_or_callback_is_actionable(self):
        client = await self.client()
        for runtime in (None, NS(client=NS())):
            self.source.runtime_data = runtime
            with self.assertRaisesRegex(api['SmartThingsError'], 'update Home Assistant'):
                await client.devices()
        self.http.get.assert_not_called()

    async def test_manual_token_without_smartthings(self):
        self.hass = NS()  # No source integration or config entry manager required.
        client = await self.client({'token': ' manual '})
        await client.devices()
        self.assertEqual(self.http.get.call_args.kwargs['headers']['Authorization'], 'Bearer manual')
        self.refresh.assert_not_awaited()

    async def test_legacy_oauth_and_empty_token_require_reauthentication(self):
        for token in ({'access_token': 'old', 'refresh_token': 'old-refresh'}, None, '', '  '):
            with self.assertRaises(api['SmartThingsAuthError']):
                await self.client({'token': token})
        self.http.get.assert_not_called()

    async def test_refresh_auth_failures_are_sanitized(self):
        client = await self.client()
        for error in (AuthFailed('secret'), HTTPError(400), HTTPError(401), HTTPError(403)):
            self.refresh.side_effect = error
            with self.assertRaisesRegex(api['SmartThingsAuthError'], 'Repair authentication') as caught:
                await client.devices()
            self.assertNotIn('secret', str(caught.exception))
        self.http.get.assert_not_called()

    async def test_refresh_temporary_failure_is_retryable(self):
        client = await self.client()
        for error in (ClientError('secret'), HTTPError(503), asyncio.TimeoutError(), HAError('secret')):
            self.refresh.side_effect = error
            with self.assertRaises(api['SmartThingsError']) as caught:
                await client.devices()
            self.assertNotIsInstance(caught.exception, api['SmartThingsAuthError'])
            self.assertNotIn('secret', str(caught.exception))
        self.http.get.assert_not_called()

    async def test_invalid_callback_value_is_rejected(self):
        client = await self.client()
        for value in (None, '', {}):
            self.refresh.return_value = value
            with self.assertRaises(api['SmartThingsAuthError']):
                await client.devices()
        self.http.get.assert_not_called()


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return kwargs

    def async_show_menu(self, **kwargs):
        return kwargs

    def async_abort(self, **kwargs):
        return kwargs

    def async_create_entry(self, **kwargs):
        return kwargs

    def async_update_and_abort(self, entry, **kwargs):
        return {'entry': entry, **kwargs}

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = NS(devices=AsyncMock(return_value=[{'deviceId': 'pump', 'label': 'EHS'},
                                                        {'deviceId': 'other', 'label': 'Other'}]),
                         status=AsyncMock(return_value={'ehs': True}))
        self.factory = AsyncMock(return_value=self.client)
        self.scope = production('config_flow.py', dict(
            vol=NS(Schema=lambda v: v, Required=lambda v: v, Optional=lambda v, **kw: v, In=lambda v: v),
            config_entries=NS(ConfigFlow=FlowBase, OptionsFlow=FlowBase), callback=lambda f: f,
            DOMAIN='samsung_ehs_monitor', CONF_TOKEN='token', CONF_DEVICE_ID='device_id',
            CONF_POWER_SENSOR='power_sensor', CONF_CREATE_DASHBOARD='create_dashboard',
            CONF_SMARTTHINGS_ENTRY='smartthings_entry', async_create_client=self.factory,
            SmartThingsAuthError=api['SmartThingsAuthError'], SmartThingsError=api['SmartThingsError'],
            is_ehs_status=lambda data: data.get('ehs'),
            selector=NS(TextSelector=lambda v: v, TextSelectorConfig=lambda **kw: kw,
                        TextSelectorType=NS(PASSWORD='password'))))
        self.flow = self.scope['SamsungEHSConfigFlow']()
        self.entries = NS(async_schedule_reload=Mock(), async_entries=Mock(return_value=[
            NS(entry_id='source', title='House'), NS(entry_id='another', title='Office')]))
        self.flow.hass = NS(config_entries=self.entries)

    async def test_exactly_two_options_and_source_selection(self):
        self.assertEqual((await self.flow.async_step_user())['menu_options'], ['smartthings', 'token'])
        result = await self.flow.async_step_smartthings()
        self.assertEqual(result['data_schema']['smartthings_entry'], {'source': 'House', 'another': 'Office'})
        self.entries.async_entries.assert_called_with('smartthings')
        self.assertEqual((await self.flow.async_step_smartthings({'smartthings_entry': 'source'}))['step_id'], 'device')
        result = await self.flow.async_step_device({'device_id': 'pump'})
        self.assertEqual(result['data'], {'smartthings_entry': 'source', 'device_id': 'pump'})
        self.factory.assert_awaited_once_with(self.flow.hass, {'smartthings_entry': 'source'})

    async def test_manual_path(self):
        await self.flow.async_step_token({'token': ' manual '})
        result = await self.flow.async_step_device({'device_id': 'pump'})
        self.assertEqual(result['data'], {'token': 'manual', 'device_id': 'pump'})

    async def test_missing_and_invalid_source(self):
        result = await self.flow.async_step_smartthings({'smartthings_entry': 'unknown'})
        self.assertEqual(result['errors']['base'], 'invalid_connection')
        self.factory.assert_not_awaited()
        self.entries.async_entries.return_value = []
        self.assertEqual((await self.flow.async_step_smartthings())['reason'], 'no_smartthings')
        self.assertEqual((await self.flow.async_step_user())['menu_options'], ['smartthings', 'token'])

    async def test_reconfiguration_preserves_identity_and_clears_old_credentials(self):
        for old in ({'token': {'access_token': 'old'}, 'auth_implementation': 'old-app'},
                    {'token': 'manual'}, {'smartthings_entry': 'another'}):
            entry = NS(entry_id='ours', data={'device_id': 'pump', 'other': 1, **old},
                       options={'power_sensor': 'sensor.power'})
            self.flow._get_reconfigure_entry = lambda: entry
            await self.flow.async_step_reconfigure()
            await self.flow.async_step_smartthings({'smartthings_entry': 'source'})
            self.assertEqual(list(self.flow._devices), ['pump'])
            result = await self.flow.async_step_device({'device_id': 'pump'})
            self.assertEqual(result['data'], {'device_id': 'pump', 'other': 1, 'smartthings_entry': 'source'})
            self.assertEqual(entry.options, {'power_sensor': 'sensor.power'})
        self.assertEqual(self.entries.async_schedule_reload.call_count, 3)

    async def test_reauthentication_can_switch_back_to_manual(self):
        entry = NS(entry_id='ours', data={'device_id': 'pump', 'smartthings_entry': 'removed'})
        self.flow._get_reauth_entry = lambda: entry
        await self.flow.async_step_reauth(entry.data)
        await self.flow.async_step_token({'token': 'replacement'})
        result = await self.flow.async_step_device({'device_id': 'pump'})
        self.assertEqual(result['data'], {'device_id': 'pump', 'token': 'replacement'})

    async def test_discovery_auth_failure_is_not_hidden_as_no_devices(self):
        self.client.status.side_effect = api['SmartThingsAuthError']('secret')
        result = await self.flow.async_step_smartthings({'smartthings_entry': 'source'})
        self.assertEqual(result['errors']['base'], 'invalid_auth')
        self.assertEqual(self.flow._devices, {})

    async def test_factory_failure_and_retry_do_not_retain_stale_devices(self):
        await self.flow.async_step_token({'token': 'old'})
        self.factory.side_effect = api['SmartThingsError']('offline')
        result = await self.flow.async_step_smartthings({'smartthings_entry': 'source'})
        self.assertEqual(result['errors']['base'], 'cannot_connect')
        self.assertEqual(self.flow._devices, {})


class ReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_rotation_does_not_reload_but_changed_options_do(self):
        tree = ast.parse((BASE / '__init__.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'async_update_options')
        scope = {'HomeAssistant': object, 'ConfigEntry': object, 'DOMAIN': 'samsung_ehs_monitor'}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '__init__.py', 'exec'), scope)
        entry = NS(entry_id='ours', options={})
        hass = NS(data={'samsung_ehs_monitor': {'ours': NS(configured_options={})}},
                  config_entries=NS(async_reload=AsyncMock()))
        await scope['async_update_options'](hass, entry)
        hass.config_entries.async_reload.assert_not_awaited()
        entry.options = {'create_dashboard': True}
        await scope['async_update_options'](hass, entry)
        hass.config_entries.async_reload.assert_awaited_once_with('ours')
