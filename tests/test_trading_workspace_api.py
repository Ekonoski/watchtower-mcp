import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from starlette.requests import Request
from dashboard import api

class Registry:
    def __init__(self):self.routes={}
    def custom_route(self,path,**kwargs):
        def register(fn):self.routes[path]=fn;return fn
        return register

class StreamRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry=Registry();api.register_routes(self.registry)
        self.route=self.registry.routes['/api/trading/stream']
    def request(self,token='',query=b''):
        return Request({'type':'http','method':'GET','path':'/api/trading/stream',
            'query_string':query,'headers':[(b'cookie',('wt_session='+token).encode())]})
    async def test_unauthenticated_request_cannot_start_worker(self):
        with patch.dict(os.environ,{'DASHBOARD_PASSWORD':'test-only-signing-secret'}), patch('analysis.live_tape.live_tape.ensure_started',new_callable=AsyncMock) as start:
            response=await self.route(self.request())
            self.assertEqual(response.status_code,401);start.assert_not_awaited()
    async def test_invalid_symbol_cannot_start_worker(self):
        with patch.dict(os.environ,{'DASHBOARD_PASSWORD':'test-only-signing-secret'}):
            with patch('analysis.live_tape.live_tape.ensure_started',new_callable=AsyncMock) as start:
                response=await self.route(self.request(api._session_token('test'),b'symbol=%3Cscript%3E'))
                self.assertEqual(response.status_code,400);start.assert_not_awaited()
    async def test_disabled_frame_is_honest_and_expiry_ends_stream(self):
        with patch.dict(os.environ,{'DASHBOARD_PASSWORD':'test-only-signing-secret','WATCHTOWER_LIVE_TAPE':''}):
            request=self.request(api._session_token('test'),b'symbol=SPY')
            with patch.object(request,'is_disconnected',new_callable=AsyncMock,return_value=False):
                response=await self.route(request)
                self.assertEqual(response.headers['x-accel-buffering'],'no')
                iterator=response.body_iterator
                payload=json.loads((await anext(iterator)).split('data: ')[1])
                self.assertEqual(payload['state'],'disabled')
                self.assertFalse(payload['enabled']);self.assertEqual(payload['parity_status'],'unverified')
                with patch.object(api,'_is_authed',return_value=False):
                    self.assertIn('event: auth',await anext(iterator))
                with self.assertRaises(StopAsyncIteration):await anext(iterator)
    async def test_assets_are_allowlisted(self):
        request=self.request();request.scope['path_params']={'asset':'../.env'}
        response=await self.registry.routes['/dashboard/trading-assets/{asset}'](request)
        self.assertEqual(response.status_code,404)

if __name__=='__main__':unittest.main()
