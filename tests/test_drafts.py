import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook, load_workbook
import bootstrap  # register repository root as the plugin package
from astrbot_plugin_loomloom.client import LoomError
from astrbot_plugin_loomloom.workbooks import public_fields, describe, fill
from astrbot_plugin_loomloom.drafts import Drafts
from astrbot_plugin_loomloom.service import Service
from test_service import FakeClient

SCHEMA = {'fields':[{'key':'prompt','label':'需求','required':True,'type':'string'},
                    {'key':'style','label':'风格','required':False,'type':'string'}]}
FIELDS=public_fields(SCHEMA,'official')

def real_workbook(duplicate=False):
    wb=Workbook();ws=wb.active;ws.title='输入'
    ws.append(['风格','需求']);ws.append(['示例风格','示例任务1']);ws.append(['示例','示例任务2'])
    if duplicate:
        wb.copy_worksheet(ws)
    guide=wb.create_sheet('填写说明');guide.append(['每行一项任务'])
    out=io.BytesIO();wb.save(out);wb.close();return out.getvalue()

class WorkbookTests(unittest.TestCase):
    def test_exact_header_mapping_and_samples_removed(self):
        data,count=fill(real_workbook(),FIELDS,'[{"prompt":"我的需求"}]')
        wb=load_workbook(io.BytesIO(data));ws=wb['输入']
        self.assertEqual(count,1);self.assertEqual(ws.max_row,2)
        self.assertIsNone(ws['A2'].value);self.assertEqual(ws['B2'].value,'我的需求')
        self.assertEqual(wb['填写说明']['A1'].value,'每行一项任务');wb.close()
    def test_formula_is_literal(self):
        data,_=fill(real_workbook(),FIELDS,json.dumps([{'prompt':'=HYPERLINK("https://example.com")'}]))
        wb=load_workbook(io.BytesIO(data));self.assertEqual(wb['输入']['B2'].data_type,'s');wb.close()
    def test_required_field_checked(self):
        with self.assertRaises(LoomError):fill(real_workbook(),FIELDS,'[{"style":"轻松"}]')
    def test_unknown_field_rejected(self):
        with self.assertRaises(LoomError):fill(real_workbook(),FIELDS,'[{"prompt":"a","hiddenPrompt":"b"}]')
    def test_multiple_matching_sheets_rejected(self):
        with self.assertRaises(LoomError):describe(real_workbook(True),FIELDS)
    def test_too_many_rows_rejected(self):
        with self.assertRaises(LoomError):fill(real_workbook(),FIELDS,json.dumps([{'prompt':'a'}]*101))
    def test_invalid_json_and_complex_values_rejected(self):
        for value in ['bad','{}','[]','[{"prompt":["a"]}]']:
            with self.subTest(value=value),self.assertRaises(LoomError):fill(real_workbook(),FIELDS,value)
    def test_market_uses_only_public_snapshot(self):
        fields=public_fields({'inputSchemaSnapshot':json.dumps(SCHEMA),'templateSpec':{'secret':'hidden'}},'market')
        self.assertEqual(fields,FIELDS);self.assertNotIn('secret',str(fields))
    def test_market_missing_snapshot_no_guessing(self):
        with self.assertRaises(LoomError):public_fields({'input':SCHEMA},'market')
    def test_schema_duplicate_keys_rejected(self):
        with self.assertRaises(LoomError):public_fields({'fields':[SCHEMA['fields'][0]]*2},'official')
    def test_numeric_types_checked(self):
        fields=public_fields({'fields':[{'key':'prompt','label':'需求','required':True,'type':'integer'},SCHEMA['fields'][1]]},'official')
        for v in [True,'123',1.5]:
            with self.assertRaises(LoomError):fill(real_workbook(),fields,json.dumps([{'prompt':v}]))
    def test_control_characters_rejected(self):
        with self.assertRaises(LoomError):fill(real_workbook(),FIELDS,json.dumps([{'prompt':'bad\x00'}]))

class DraftClient(FakeClient):
    async def schema(self,k,r):return SCHEMA
    async def workbook(self,k,r):return real_workbook()

class DraftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.client=DraftClient()
        self.service=Service({'allowed_users':['123']},self.tmp.name,self.client)
        self.drafts=Drafts(self.service);self.owner='123|private'
    def tearDown(self):self.service.store.close();self.tmp.cleanup()
    async def build(self):
        shape=await self.drafts.inspect(self.owner,'official','text-v1')
        code,data,count=await self.drafts.build(self.owner,shape['input_token'],'[{"prompt":"hello"}]')
        return code,shape['input_token']
    async def test_prepare_has_no_quote_or_execution(self):
        await self.build();self.assertEqual(self.client.calls,[])
    async def test_quote_requires_delivered_preview(self):
        code,_=await self.build()
        with self.assertRaises(LoomError):await self.drafts.quote(self.owner,code,'private')
        self.assertEqual(self.client.calls,[])
    async def test_quote_then_explicit_execute(self):
        code,_=await self.build();self.drafts.delivered(self.owner,code)
        record=await self.drafts.quote(self.owner,code,'private')
        self.assertEqual(record['row_count'],1);self.assertEqual(self.client.calls,['quote'])
        await self.service.confirm(self.owner,record['code'])
        self.assertEqual(sum(isinstance(x,tuple) for x in self.client.calls),1)
    async def test_revised_input_invalidates_previous_quote(self):
        code,token=await self.build();self.drafts.delivered(self.owner,code)
        record=await self.drafts.quote(self.owner,code,'private')
        await self.drafts.build(self.owner,token,'[{"prompt":"changed"}]')
        with self.assertRaises(LoomError):await self.service.confirm(self.owner,record['code'])
        with self.assertRaises(LoomError):await self.drafts.quote(self.owner,code,'private')
        self.assertFalse(any(isinstance(x,tuple) for x in self.client.calls))
    async def test_other_owner_cannot_use_token_or_draft(self):
        code,token=await self.build();self.drafts.delivered(self.owner,code)
        with self.assertRaises(LoomError):await self.drafts.build('456|private',token,'[{"prompt":"x"}]')
        with self.assertRaises(LoomError):await self.drafts.quote('456|private',code,'private')
    async def test_expired_draft_and_restart_rejected(self):
        code,_=await self.build();self.drafts.delivered(self.owner,code)
        self.drafts.sessions[self.owner]['expires']=0
        with self.assertRaises(LoomError):await self.drafts.quote(self.owner,code,'private')
        with self.assertRaises(LoomError):await Drafts(self.service).quote(self.owner,code,'private')
    async def test_template_change_requires_new_input(self):
        code,_=await self.build();self.drafts.delivered(self.owner,code);self.client.version='v2'
        with self.assertRaises(LoomError):await self.drafts.quote(self.owner,code,'private')
        self.assertEqual(self.client.calls,[])
    async def test_guard_rechecked_under_lock(self):
        code,token=await self.build();self.drafts.delivered(self.owner,code)
        import asyncio
        async with self.service.lock(self.owner):
            task=asyncio.create_task(self.drafts.quote(self.owner,code,'private'))
            await asyncio.sleep(0)
            self.drafts.sessions[self.owner]['draft_code']='changed'
        with self.assertRaises(LoomError):await task
        self.assertEqual(self.client.calls,[])
