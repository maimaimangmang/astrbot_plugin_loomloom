import asyncio
import base64
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
import bootstrap  # register repository root as the plugin package
from astrbot_plugin_loomloom.client import LoomError, APIError, Client, workbook_check, money
from astrbot_plugin_loomloom.service import Service


def workbook():
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('[Content_Types].xml','<Types/>')
        z.writestr('xl/workbook.xml','<workbook/>')
    return b.getvalue()

class FakeClient:
    def __init__(self):
        self.calls=[];self.amount=1000000;self.version='v1';self.fail=False;self.package_data=None;self.delay=0
    async def metadata(self,k,r):
        return {'templateId':r,'name':'测试','version':self.version,'skillPackage': self.package_data or {}, 'saleStatus':'listed', 'executionAvailabilityStatus':'available', 'listingVersionId':self.version}
    async def validate_quote(self,k,r,c,f):
        self.calls.append('quote')
        field='estimatedTotalCost' if k=='official' else 'estimatedBuyerPayable'
        return {field+'T':self.amount,field:{'amount':'0.1','currency':'CNY'},'listingVersionId':self.version, 'taskCount':1}
    async def execute(self,k,r,c,f,rid,q):
        self.calls.append(('execute',rid,c))
        if self.delay:await asyncio.sleep(self.delay)
        if self.fail:raise TimeoutError()
        return {'runId':'run-demo'}
    async def package(self,k,r):return b'not-a-zip'
    async def run(self,rid):return {'run':{'status':'completed','totalTasks':1,'completedTasks':1}}

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.client=FakeClient()
        self.s=Service({'allowed_users':['123'],'cloud_market_ids':['market-demo']},self.tmp.name,self.client)
    def tearDown(self):self.s.store.close();self.tmp.cleanup()
    async def prepare(self,kind='official'):
        return await self.s.prepare('123|private',kind,'market-demo' if kind=='market' else 'text-v1','input.xlsx',workbook(),'private')
    def test_auth_private_only(self):
        self.s.authorize('123')
        for args in [('456',''),('123','group')]:
            with self.assertRaises(LoomError):self.s.authorize(*args)
    async def test_prepare_never_executes(self):
        r=await self.prepare();self.assertEqual(self.client.calls,['quote']);self.assertIn('/loom confirm',self.s.confirmation(r))
    async def test_only_owner_can_confirm(self):
        r=await self.prepare()
        with self.assertRaises(LoomError):await self.s.confirm('456|private',r['code'])
        self.assertEqual(self.client.calls,['quote'])
    async def test_confirm_exactly_once_under_concurrency(self):
        r=await self.prepare();self.client.delay=.02
        await asyncio.gather(self.s.confirm(r['owner'],r['code']),self.s.confirm(r['owner'],r['code']))
        self.assertEqual(len([c for c in self.client.calls if isinstance(c,tuple)]),1)
    async def test_expired_never_submits(self):
        r=await self.prepare();r['expires']=0;self.s.store.put(r)
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
    async def test_changed_input_rejected(self):
        r=await self.prepare();(Path(self.tmp.name)/(r['code']+'.xlsx')).write_bytes(b'changed')
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
    async def test_changed_price_requires_fresh_confirmation(self):
        r=await self.prepare();self.client.amount=2000000
        new,changed=await self.s.confirm(r['owner'],r['code'])
        self.assertTrue(changed);self.assertNotEqual(r['code'],new['code']);self.assertNotEqual(r['request_id'],new['request_id'])
        self.assertFalse(any(isinstance(c,tuple) for c in self.client.calls))
        await self.s.confirm(new['owner'],new['code'])
        self.assertEqual(len([c for c in self.client.calls if isinstance(c,tuple)]),1)
    async def test_changed_version_requires_fresh_confirmation(self):
        r=await self.prepare('market');self.client.version='v2'
        _,changed=await self.s.confirm(r['owner'],r['code']);self.assertTrue(changed)
    async def test_timeout_blocks_retry_and_new_quote(self):
        r=await self.prepare();self.client.fail=True
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
        with self.assertRaises(LoomError):await self.prepare()
        self.assertEqual(len([c for c in self.client.calls if isinstance(c,tuple)]),1)
        self.assertEqual(self.s.store.get(r['code'],r['owner'])['state'],'uncertain')
    async def test_restart_recovers_submitting_as_uncertain(self):
        r=await self.prepare();r['state']='submitting';self.s.store.put(r);self.s.store.close()
        self.s=Service({'allowed_users':['123']},self.tmp.name,self.client)
        self.assertEqual(self.s.store.get(r['code'],r['owner'])['state'],'uncertain')
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
    async def test_cancel_does_not_run(self):
        r=await self.prepare();await self.s.cancel(r['owner'],r['code'])
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
    async def test_new_quote_supersedes_old(self):
        r=await self.prepare();await self.prepare()
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
    async def test_cost_cap_and_currency(self):
        self.client.amount=60000000
        with self.assertRaises(LoomError):await self.prepare()
        with self.assertRaises(LoomError):self.s.cap({'estimatedTotalCostT':1,'currency':'USD'},'official')
    def test_missing_money_fails_closed(self):
        for d in [{},{'estimatedTotalCostT':1},{'estimatedTotalCostT':-1,'currency':'CNY'},{'estimatedTotalCostT':True,'currency':'CNY'}]:
            with self.assertRaises(LoomError):money(d,'estimatedTotalCost')
    async def test_market_no_longer_requires_id_allowlist(self):
        self.s.config.pop('cloud_market_ids')
        meta = await self.s.select('market','other-market')
        self.assertEqual(meta['saleStatus'], 'listed')
        self.assertEqual(self.client.calls, [])

    async def test_market_rechecks_availability_before_submit(self):
        from unittest.mock import AsyncMock
        r = await self.prepare('market')
        self.client.metadata = AsyncMock(return_value={'saleStatus':'listed','executionAvailabilityStatus':'blocked','listingVersionId':'v1'})
        with self.assertRaises(LoomError): await self.s.confirm(r['owner'], r['code'])
        self.assertFalse(any(isinstance(c,tuple) for c in self.client.calls))
    async def test_package_hash_mismatch_blocks(self):
        self.client.package_data={'available':True,'archiveHash':'sha256:'+'a'*64}
        with self.assertRaises(LoomError):await self.prepare()
    async def test_only_own_run_visible(self):
        r=await self.prepare();await self.s.confirm(r['owner'],r['code'])
        with self.assertRaises(LoomError):await self.s.status('another-owner','run-demo')
        self.assertIn('completed',await self.s.status(r['owner'],'run-demo'))
    async def test_manual_resolution_never_executes(self):
        r=await self.prepare();self.client.fail=True
        with self.assertRaises(LoomError):await self.s.confirm(r['owner'],r['code'])
        await self.s.resolve(r['owner'],r['code'],'run-existing')
        self.assertEqual(len([c for c in self.client.calls if isinstance(c,tuple)]),1)
    def test_invalid_workbook(self):
        with self.assertRaises(LoomError):workbook_check(b'not-excel')
    async def test_real_client_payload_and_routes(self):
        c=Client('fake-key');captured=[]
        async def request(method,path,payload=None,**kw):
            captured.append((method,path,payload))
            if path.endswith(':validateWorkbook'):return {'valid':True}
            return {'estimatedTotalCostT':100,'estimatedTotalCost':{'currency':'CNY'}}
        c.request=request
        await c.validate_quote('official','text-v1',workbook(),'demo.xlsx')
        self.assertEqual(captured[0][1],'/officialTemplates/text-v1:validateWorkbook')
        self.assertEqual(base64.b64decode(captured[0][2]['content']),workbook())
        self.assertFalse(any(x[1].endswith(':runWorkbook') for x in captured))

if __name__=='__main__':unittest.main()

class RevisedInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_new_quote_still_invalidates_old_input(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as root:
            client=FakeClient();service=Service({'allowed_users':['123']},root,client)
            try:
                old=await service.prepare('123|p','official','text-v1','a.xlsx',workbook(),'p')
                client.validate_quote=AsyncMock(side_effect=LoomError('unavailable'))
                with self.assertRaises(LoomError):
                    await service.prepare('123|p','official','text-v1','b.xlsx',workbook(),'p')
                with self.assertRaises(LoomError):await service.confirm('123|p',old['code'])
                self.assertFalse(any(isinstance(x,tuple) for x in client.calls))
            finally:service.store.close()

class SubmissionRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_cannot_claim_success_during_submission(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as root:
            client=FakeClient();service=Service({'allowed_users':['123']},root,client)
            entered=asyncio.Event();release=asyncio.Event()
            try:
                record=await service.prepare('123|p','official','text-v1','a.xlsx',workbook(),'p')
                async def execute(*args):
                    entered.set();await release.wait();return {'runId':'run-race'}
                client.execute=execute
                submitting=asyncio.create_task(service.confirm('123|p',record['code']))
                await entered.wait()
                cancelling=asyncio.create_task(service.cancel('123|p',record['code']))
                await asyncio.sleep(0);self.assertFalse(cancelling.done())
                release.set();await submitting
                with self.assertRaises(LoomError):await cancelling
                self.assertEqual(service.store.get(record['code'],'123|p')['state'],'submitted')
            finally:service.store.close()

    async def test_interrupted_submit_is_durably_uncertain(self):
        with tempfile.TemporaryDirectory() as root:
            client=FakeClient();service=Service({'allowed_users':['123']},root,client)
            entered=asyncio.Event()
            try:
                record=await service.prepare('123|p','official','text-v1','a.xlsx',workbook(),'p')
                async def execute(*args):
                    entered.set();await asyncio.Event().wait()
                client.execute=execute
                task=asyncio.create_task(service.confirm('123|p',record['code']))
                await entered.wait();task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertEqual(service.store.get(record['code'],'123|p')['state'],'uncertain')
                with self.assertRaises(LoomError):await service.confirm('123|p',record['code'])
            finally:service.store.close()
