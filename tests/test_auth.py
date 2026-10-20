"""Standalone OAuth sessions, token exchanges, and migration from manual tokens."""
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


class UnknownImplementation(HAError):
    pass


class TokenError(HAError):
    def __init__(self, **kwargs):
        super().__init__('Invalid token response')


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
        self.entry = NS(entry_id='our-entry', domain='samsung_ehs_monitor', data={
            'auth_implementation': 'our-app', 'token': {'access_token': 'first', 'refresh_token': 'refresh'}})
        self.http = NS(get=Mock(side_effect=lambda *a, **kw: Response()))
        self.hass = NS()  # No SmartThings entry or runtime connection exists.
        self.oauth = NS(token=self.entry.data['token'], async_ensure_token_valid=AsyncMock())
        self.factory = Mock(return_value=self.oauth)
        self.implementation = NS()
        self.lookup = AsyncMock(return_value=self.implementation)
        self.scope = production('auth.py', dict(
            asyncio=asyncio, ClientError=ClientError, ClientResponseError=HTTPError,
            HomeAssistantError=HAError, ConfigEntryAuthFailed=AuthFailed,
            OAuth2Session=self.factory, UnknownImplementationError=UnknownImplementation,
            async_get_config_entry_implementation=self.lookup,
            async_get_clientsession=lambda h: self.http, CONF_TOKEN='token',
            SmartThingsAuthError=api['SmartThingsAuthError'], SmartThingsError=api['SmartThingsError'],
            SmartThingsClient=api['SmartThingsClient']))

    async def test_owns_oauth_session_and_checks_refresh_before_every_request(self):
        client = await self.scope['async_create_client'](self.hass, self.entry)
        await client.devices()
        self.oauth.token.update(access_token='rotated', refresh_token='rotated-refresh')
        await client.status('pump')
        self.lookup.assert_awaited_once_with(self.hass, self.entry)
        self.factory.assert_called_once_with(self.hass, self.entry, self.implementation)
        self.assertEqual(self.oauth.async_ensure_token_valid.await_count, 2)
        self.assertEqual([call.kwargs['headers']['Authorization'] for call in self.http.get.call_args_list],
                         ['Bearer first', 'Bearer rotated'])
        self.assertEqual(self.entry.data['token']['refresh_token'], 'rotated-refresh')

    async def test_manual_token_still_works_without_oauth_session(self):
        self.entry.data = {'token': 'manual'}
        client = await self.scope['async_create_client'](self.hass, self.entry)
        await client.devices()
        self.assertEqual(self.http.get.call_args.kwargs['headers']['Authorization'], 'Bearer manual')
        self.factory.assert_not_called()

    async def test_removed_credentials_requires_reauthentication(self):
        self.lookup.side_effect = UnknownImplementation()
        with self.assertRaises(api['SmartThingsAuthError']):
            await self.scope['async_create_client'](self.hass, self.entry)

    async def test_refresh_invalid_grant_requires_reauthentication(self):
        client = await self.scope['async_create_client'](self.hass, self.entry)
        self.oauth.async_ensure_token_valid.side_effect = HTTPError(400)
        with self.assertRaises(api['SmartThingsAuthError']):
            await client.devices()
        self.http.get.assert_not_called()

    async def test_refresh_network_failure_is_retryable_and_sanitized(self):
        client = await self.scope['async_create_client'](self.hass, self.entry)
        self.oauth.async_ensure_token_valid.side_effect = ClientError('sensitive details')
        with self.assertRaises(api['SmartThingsError']) as caught:
            await client.devices()
        self.assertNotIsInstance(caught.exception, api['SmartThingsAuthError'])
        self.assertNotIn('sensitive', str(caught.exception))


class Implementation:
    def __init__(self, hass, domain, client_id, client_secret, authorize_url, token_url):
        self.hass, self.client_id, self.client_secret = hass, client_id, client_secret
        self.authorize_url, self.token_url = authorize_url, token_url


class CredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.response = Response({'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 86400})
        self.http = NS(post=Mock(return_value=self.response))
        scope = production('application_credentials.py', dict(
            BasicAuth=lambda user, password: NS(login=user, password=password), ClientTimeout=lambda **kw: kw,
            OAuth2TokenRequestConnectionError=TokenError, LocalOAuth2Implementation=Implementation,
            async_get_clientsession=lambda h: self.http, DOMAIN='samsung_ehs_monitor',
            OAUTH_AUTHORIZE_URL='https://api.smartthings.com/oauth/authorize',
            OAUTH_TOKEN_URL='https://api.smartthings.com/oauth/token'))
        self.factory = scope['async_get_auth_implementation']

    async def test_code_and_refresh_exchange_use_basic_auth_and_form_data(self):
        impl = await self.factory(NS(), 'our-app', NS(client_id='client', client_secret='secret'))
        for grant in ('authorization_code', 'refresh_token'):
            token = await impl._token_request({'grant_type': grant, 'code': 'code', 'refresh_token': 'refresh'})
            self.assertEqual(token['access_token'], 'access')
            call = self.http.post.call_args
            self.assertEqual(call.args[0], 'https://api.smartthings.com/oauth/token')
            self.assertEqual(call.kwargs['auth'].login, 'client')
            self.assertEqual(call.kwargs['auth'].password, 'secret')
            self.assertEqual(call.kwargs['data']['client_id'], 'client')
            self.assertNotIn('client_secret', call.kwargs['data'])
            self.assertFalse(call.kwargs['allow_redirects'])

    async def test_invalid_token_response_is_not_saved(self):
        impl = await self.factory(NS(), 'our-app', NS(client_id='client', client_secret='secret'))
        for data in ({'access_token': 'access'}, {'access_token': 'access', 'expires_in': 10},
                     {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 0}, ['invalid']):
            self.response.data = data
            with self.assertRaises(TokenError):
                await impl._token_request({'grant_type': 'authorization_code'})


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

    async def async_step_pick_implementation(self, user_input):
        return {'type': 'oauth_authorize'}


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = NS(devices=AsyncMock(return_value=[{'deviceId': 'pump', 'label': 'EHS'},
                                                        {'deviceId': 'other', 'label': 'Other'}]),
                         status=AsyncMock(return_value={'ehs': True}))
        self.scope = production('config_flow.py', dict(
            vol=NS(Schema=lambda v: v, Required=lambda v: v, Optional=lambda v, **kw: v, In=lambda v: v),
            config_entries=NS(OptionsFlow=FlowBase), AbstractOAuth2FlowHandler=FlowBase, callback=lambda f: f,
            DOMAIN='samsung_ehs_monitor', CONF_TOKEN='token', CONF_DEVICE_ID='device_id',
            CONF_POWER_SENSOR='power_sensor', CONF_CREATE_DASHBOARD='create_dashboard',
            OAUTH_SCOPES=['r:devices:*'], async_get_clientsession=lambda h: NS(),
            SmartThingsAuthError=api['SmartThingsAuthError'], SmartThingsError=api['SmartThingsError'],
            SmartThingsClient=lambda *a, **kw: self.client, is_ehs_status=lambda data: data.get('ehs'),
            selector=NS(TextSelector=lambda v: v, TextSelectorConfig=lambda **kw: kw,
                        TextSelectorType=NS(PASSWORD='password'))))
        self.flow = self.scope['SamsungEHSConfigFlow']()
        self.flow.hass = NS(config_entries=NS(async_schedule_reload=Mock()))
        self.oauth_data = {'auth_implementation': 'our-app', 'token': {
            'access_token': 'access', 'refresh_token': 'refresh', 'scope': 'r:devices:*', 'expires_at': 123}}

    async def test_standalone_oauth_and_manual_menu(self):
        self.assertEqual((await self.flow.async_step_user())['menu_options'], ['oauth', 'token'])
        self.assertEqual((await self.flow.async_step_oauth())['type'], 'oauth_authorize')
        self.assertEqual(self.flow.extra_authorize_data, {'scope': 'r:devices:*'})
        self.assertEqual((await self.flow.async_oauth_create_entry(self.oauth_data))['step_id'], 'device')
        result = await self.flow.async_step_device({'device_id': 'pump'})
        self.assertEqual(result['data'], {**self.oauth_data, 'device_id': 'pump'})
        self.assertNotIn('smartthings_entry', result['data'])

    async def test_reconfiguration_preserves_device_and_history_identity(self):
        entry = NS(entry_id='ours', data={'device_id': 'pump', 'token': 'old-secret', 'other': 1},
                   options={'power_sensor': 'sensor.power'})
        self.flow._get_reconfigure_entry = lambda: entry
        await self.flow.async_step_reconfigure()
        await self.flow.async_oauth_create_entry(self.oauth_data)
        self.assertEqual(list(self.flow._devices), ['pump'])
        result = await self.flow.async_step_device({'device_id': 'pump'})
        self.assertEqual(result['data'], {'device_id': 'pump', 'other': 1, **self.oauth_data})
        self.assertEqual(entry.options, {'power_sensor': 'sensor.power'})
        self.flow.hass.config_entries.async_schedule_reload.assert_called_once_with('ours')

    async def test_manual_path_and_invalid_oauth_permissions(self):
        await self.flow.async_step_token({'token': ' manual '})
        result = await self.flow.async_step_device({'device_id': 'pump'})
        self.assertEqual(result['data']['token'], 'manual')
        self.assertEqual((await self.flow.async_oauth_create_entry({'token': {}}))['reason'], 'missing_refresh_token')
        self.oauth_data['token']['scope'] = 'x:devices:*'
        self.assertEqual((await self.flow.async_oauth_create_entry(self.oauth_data))['reason'], 'missing_scopes')


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
