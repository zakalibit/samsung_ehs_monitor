"""Fresh API downloads preserve raw status and do not export credentials."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import unittest

from test_auth import production, api, Response


def redact(value, keys):
    if isinstance(value, dict):
        return {key: '**REDACTED**' if key in keys else redact(item, keys)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, keys) for item in value]
    return value


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_download_fetches_fresh_raw_status_and_redacts(self):
        payload = {'components': {'main': {'unsupported': {'value': [1, 2]}}},
                   'nested': [{'access_token': 'private', 'deviceId': 'pump'}]}
        client = NS(raw_status=AsyncMock(side_effect=[payload, {'next': 'response'}]))
        create = AsyncMock(return_value=client)
        scope = production('diagnostics.py', {
            'async_create_client': create, 'CONF_DEVICE_ID': 'device_id',
            'async_redact_data': redact})
        entry = NS(data={'device_id': 'pump', 'token': 'secret'})
        hass = object()
        original = deepcopy(payload)
        download = scope['async_get_config_entry_diagnostics']
        result = await download(hass, entry)
        self.assertEqual(result['components'], payload['components'])
        self.assertEqual(result['nested'][0],
                         {'access_token': '**REDACTED**', 'deviceId': '**REDACTED**'})
        self.assertNotIn('secret', str(result))
        self.assertEqual(payload, original)
        self.assertEqual(await download(hass, entry), {'next': 'response'})
        self.assertEqual(client.raw_status.await_count, 2)
        create.assert_awaited_with(hass, entry.data)
        client.raw_status.assert_awaited_with('pump')

    async def test_api_failure_does_not_return_cached_data(self):
        create = AsyncMock(side_effect=api['SmartThingsAuthError']('Access denied'))
        scope = production('diagnostics.py', {
            'async_create_client': create, 'CONF_DEVICE_ID': 'device_id',
            'async_redact_data': redact})
        with self.assertRaises(api['SmartThingsAuthError']):
            await scope['async_get_config_entry_diagnostics'](
                object(), NS(data={'device_id': 'pump'}))

    async def test_raw_status_allows_unsupported_structure_but_polling_rejects_it(self):
        payload = {'unexpected': 'status'}
        session = NS(get=lambda *args, **kwargs: Response(payload))
        client = api['SmartThingsClient'](session, 'secret')
        self.assertEqual(await client.raw_status('pump'), payload)
        with self.assertRaises(api['SmartThingsError']):
            await client.status('pump')


if __name__ == '__main__':
    unittest.main()
