import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
import bootstrap  # register repository root as the plugin package
from astrbot_plugin_loomloom import client
from astrbot_plugin_loomloom.files import read_attachment, PublicResolver
from test_service import workbook

class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hits=[]
        async def handler(request):
            self.hits.append((request.path,request.headers.get('Authorization')))
            if request.path=='/redirect':raise web.HTTPFound('/secret-target')
            if request.path=='/fail':return web.Response(status=500,text='secret-api-key-do-not-echo')
            if request.path=='/large':return web.Response(body=b'x'*(4*1024*1024+1))
            return web.json_response({'ok':True})
        app=web.Application();app.router.add_route('*','/{path:.*}',handler)
        self.runner=web.AppRunner(app);await self.runner.setup()
        self.site=web.TCPSite(self.runner,'127.0.0.1',0);await self.site.start()
        port=self.site._server.sockets[0].getsockname()[1]
        self.patch=patch.object(client,'BASE_URL',f'http://127.0.0.1:{port}');self.patch.start()
        self.c=client.Client('test-only-key')
    async def asyncTearDown(self):
        self.patch.stop();await self.runner.cleanup()
    async def test_no_redirect_follow(self):
        with self.assertRaises(client.APIError):await self.c.request('GET','/redirect')
        self.assertEqual(len(self.hits),1)
    async def test_post_never_auto_retries_and_error_is_redacted(self):
        with self.assertRaises(client.APIError) as e:await self.c.request('POST','/fail',{})
        self.assertNotIn('secret-api-key',str(e.exception));self.assertEqual(len(self.hits),1)
    async def test_public_requests_have_no_key(self):
        await self.c.request('GET','/public',public=True)
        self.assertIsNone(self.hits[0][1])
    async def test_authenticated_requests_use_bearer(self):
        await self.c.request('GET','/private')
        self.assertEqual(self.hits[0][1],'Bearer test-only-key')
    async def test_response_bound(self):
        with self.assertRaises(client.LoomError):await self.c.request('GET','/large')
    async def test_no_unconfigured_auth_request(self):
        with self.assertRaises(client.LoomError):await client.Client('').request('GET','/private')
        self.assertEqual(self.hits,[])

class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_address_blocked(self):
        class File:
            async def get_file(self,**kw):return 'http://127.0.0.1/secret'
        with self.assertRaises(client.LoomError):await read_attachment(File(),[])
    async def test_local_outside_roots_blocked(self):
        class File:
            async def get_file(self,**kw):return '/etc/hosts'
        with self.assertRaises(client.LoomError):await read_attachment(File(),['/some/nonexistent/allowed/root'])
    async def test_safe_local_xlsx(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'input.xlsx';p.write_bytes(workbook())
            class File:
                async def get_file(self,**kw):return str(p)
            self.assertEqual(await read_attachment(File(),[root]),workbook())
    async def test_dns_private_result_blocked(self):
        with patch.object(asyncio.get_running_loop(),'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.1',80))]):
            with self.assertRaises(client.LoomError):await PublicResolver().resolve('example.test',80)
