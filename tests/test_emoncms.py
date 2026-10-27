"""Durable, finalized Emoncms publishing with paired energy and safe retries."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from math import isfinite
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.parse import urlsplit
import unittest

from test_auth import production, ClientError
from test_external_power import Options, flow_scope

scope = production('emoncms.py', dict(asyncio=asyncio, sha256=sha256, json=json,
    isfinite=isfinite, urlsplit=urlsplit, ClientError=ClientError,
    ClientTimeout=lambda **kwargs: kwargs))
Publisher = scope['EmoncmsPublisher']
prepare = scope['prepare_batch']


class Store:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


class Response:
    def __init__(self, status=200, text='ok'):
        self.status, self.body = status, text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def text(self):
        return self.body


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def make(self, store=None, session=None, node=28, source='sensor.power'):
        publisher = Publisher(session or NS(post=Mock(return_value=Response())),
                              store or Store(), 'https://emoncms.org', node, 'secret-key', source, 'UTC')
        await publisher.load(0)
        return publisher

    async def test_finalization_cursor_counters_and_no_double_publish(self):
        p = await self.make()
        ledger = {0: [1, 1, 60], 60: [4/60, 1/60, 60]}
        await p.publish(ledger, 1919)
        p.session.post.assert_not_called()
        await p.publish(ledger, 1920)
        args, kwargs = p.session.post.call_args
        self.assertEqual(args[0], 'https://emoncms.org/input/bulk.json')
        self.assertNotIn('secret-key', args[0])
        self.assertEqual(kwargs['data']['time'], '0')
        self.assertEqual(kwargs['data']['apikey'], 'secret-key')
        self.assertFalse(kwargs['allow_redirects'])
        rows = json.loads(kwargs['data']['data'])
        self.assertEqual(rows[0][:2], [120,28])
        self.assertEqual(rows[0][2]['heatpump_heat'], 4000)
        self.assertEqual(rows[0][2]['heatpump_elec'], 1000)
        self.assertAlmostEqual(rows[0][2]['heatpump_heat_kwh'], 4/60)
        await p.publish(ledger, 1980)
        self.assertEqual(p.session.post.call_count, 1)
        restored = await self.make(store=p.store)
        await restored.publish(ledger, 1980)
        restored.session.post.assert_not_called()

    async def test_timeout_and_reload_retry_identical_outbox_without_recalculation(self):
        session = NS(post=Mock(side_effect=asyncio.TimeoutError))
        p = await self.make(session=session)
        await p.publish({60:[4/60,1/60,60]}, 1920)
        original = session.post.call_args.kwargs['data']['data']
        restored = await self.make(store=p.store)
        await restored.publish({60:[99,99,60]}, 1980)
        self.assertEqual(restored.session.post.call_args.kwargs['data']['data'], original)
        self.assertIn('Publishing', restored.status)

    async def test_rejected_response_retains_pending_and_never_echoes_body(self):
        p = await self.make(session=NS(post=Mock(return_value=Response(text='secret-key invalid'))))
        await p.publish({60:[1,1,60]},1920)
        self.assertIsNotNone(p.state['streams'][p.target]['pending'])
        self.assertNotIn('secret-key',p.status)

    async def test_supported_success_response_formats(self):
        for body in ('ok', '"ok"', '{"success": true}'):
            p = await self.make(session=NS(post=Mock(return_value=Response(text=body))))
            await p.publish({60:[1,1,60]},1920)
            self.assertIsNone(p.state['streams'][p.target]['pending'])

    async def test_source_change_blocks_and_new_node_is_independent(self):
        p = await self.make()
        changed = await self.make(store=p.store, source='sensor.other')
        await changed.publish({60:[1,1,60]},1920)
        changed.session.post.assert_not_called()
        self.assertIn('Choose a new',changed.status)
        other = await self.make(store=p.store, node=29, source='sensor.other')
        await other.publish({60:[1,1,60]},1920)
        self.assertEqual(other.session.post.call_count,1)
        self.assertEqual(len(other.state['streams']),2)

    def test_partial_minutes_signed_defrost_and_gaps(self):
        data = prepare({60:[-1/60,1/60,30], 180:[0,0,60]},60,0,0,2100,28)
        first, second = data['rows']
        self.assertEqual(first[2]['paired_coverage'],50)
        self.assertEqual(first[2]['heatpump_heat'],-2000)
        self.assertEqual(second[2]['heatpump_heat_kwh'],first[2]['heatpump_heat_kwh'])
        self.assertEqual([r[0] for r in data['rows']],[120,240])

    def test_url_validation(self):
        for url in ('https://emoncms.org','http://emonpi.local/emoncms'):
            self.assertTrue(scope['valid_url'](url))
        for url in ('file:///tmp/test','https://key@emoncms.org','https://emoncms.org?apikey=secret','https://emoncms.org#key'):
            self.assertFalse(scope['valid_url'](url))


class PublisherOptionsTests(unittest.IsolatedAsyncioTestCase):
    def flow(self):
        flow = Options()
        flow.config_entry = NS(entry_id='entry', options={'emoncms_api_key':'a'*32})
        flow.hass = NS(config_entries=NS(async_entries=lambda domain: []),
                       states=NS(get=lambda entity:NS(attributes={'unit_of_measurement':'W'})),
                       registry=NS(async_get=lambda entity:NS(platform='shelly')))
        return flow

    async def test_blank_key_preserves_saved_secret_and_defaults_node28(self):
        result = await self.flow().async_step_init({'power_sensor':'sensor.power','emoncms_enabled':True,'emoncms_api_key':''})
        self.assertEqual(result['data']['emoncms_api_key'],'a'*32)
        self.assertEqual(result['data']['emoncms_node'],28)

    async def test_requires_external_power_and_valid_key(self):
        flow = self.flow()
        result = await flow.async_step_init({'emoncms_enabled':True,'emoncms_api_key':'bad'})
        self.assertIn('power_sensor',result['errors'])
        self.assertIn('emoncms_api_key',result['errors'])

    async def test_key_is_never_suggested_to_form(self):
        flow = self.flow()
        flow.add_suggested_values_to_schema = Mock(side_effect=lambda schema, values:schema)
        await flow.async_step_init()
        self.assertNotIn('emoncms_api_key',flow.add_suggested_values_to_schema.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
