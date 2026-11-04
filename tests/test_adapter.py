"""Message-level tests using AstrBot API stubs; not a live QQ integration test."""
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import asyncio
import bootstrap  # register repository root as the plugin package
from test_service import FakeClient, workbook

COMMANDS={};TOOLS={}
def deco(*a,**k):return lambda f:f
def command(name,**kw):
    def bind(f):COMMANDS[name]=f;return f
    return bind
def group(*a,**kw):
    def bind(f):f.command=command;return f
    return bind
def tool(name,**kw):
    def bind(f):TOOLS[name]=f;return f
    return bind
class File:
    def __init__(self,name,file='',url=''):self.name=name;self.file_=file;self.url=url
    async def get_file(self,**kw):return self.url or self.file_
class Plain:
    def __init__(self,text):self.text=text
class Chain:
    def __init__(self,chain=None):self.chain=chain or []
    def message(self,text):self.chain.append(Plain(text));return self
class Star:
    def __init__(self,context):self.context=context
class Event:
    unified_msg_origin='bot:FriendMessage:123'
    def __init__(self,user='123',group='',platform='aiocqhttp',files=None):self.user=user;self.group=group;self.platform=platform;self.files=files or [];self.sent=[]
    def get_sender_id(self):return self.user
    def get_group_id(self):return self.group
    def get_platform_name(self):return self.platform
    def get_messages(self):return self.files
    def plain_result(self,text):return text
    def chain_result(self,chain):return Chain(chain)
    def stop_event(self):pass
    async def send(self,message):self.sent.append(message)

class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=self.tmp.name
        specs={
          'astrbot':{},'astrbot.api':{'logger':types.SimpleNamespace(warning=lambda *x:None)},
          'astrbot.api.event':{'AstrMessageEvent':Event,'filter':types.SimpleNamespace(command_group=group,command=command,llm_tool=tool,event_message_type=deco,EventMessageType=types.SimpleNamespace(ALL='all'))},
          'astrbot.api.star':{'Context':object,'Star':Star,'StarTools':types.SimpleNamespace(get_data_dir=lambda x:Path(root)),'register':deco},
          'astrbot.api.message_components':{'File':File,'Plain':Plain},
          'astrbot.core':{'file_token_service':types.SimpleNamespace(register_file=AsyncMock(return_value='opaque-test-token'))},
          'astrbot.core.message':{},'astrbot.core.message.message_event_result':{'MessageChain':Chain},
          'astrbot.core.utils':{},'astrbot.core.utils.astrbot_path':{'get_astrbot_temp_path':lambda:root}}
        self.old={name:sys.modules.get(name) for name in specs}
        for name,attrs in specs.items():
            m=types.ModuleType(name);m.__dict__.update(attrs);sys.modules[name]=m
        sys.modules.pop('astrbot_plugin_loomloom.main',None)
        mod=importlib.import_module('astrbot_plugin_loomloom.main')
        self.plugin=mod.LoomLoomPlugin(types.SimpleNamespace(send_message=AsyncMock(return_value=True)),{'allowed_users':['123']})
        self.fake=FakeClient();self.plugin.service.client=self.fake
    def tearDown(self):
        self.plugin.service.store.close();self.tmp.cleanup();sys.modules.pop('astrbot_plugin_loomloom.main',None)
        for name,old in self.old.items():
            if old is None:sys.modules.pop(name,None)
            else:sys.modules[name]=old
    async def test_empty_search_also_queries_market(self):
        self.fake.catalog=AsyncMock(return_value={'items':[]})
        result=await self.plugin.find_templates_tool(Event())
        self.assertEqual(self.fake.catalog.call_args_list[1].args, ('market',''))
        self.assertIn('市场',result)

    async def test_market_failure_not_reported_as_absence(self):
        from astrbot_plugin_loomloom.client import LoomError
        self.fake.catalog=AsyncMock(side_effect=[{'items':[]},LoomError('网络中断')])
        result=await self.plugin.find_templates_tool(Event(),'电商')
        self.assertIn('市场查询失败',result)
        self.assertIn('不能据此判断',result)

    async def test_unauthorized_search_does_not_contact_server(self):
        self.fake.catalog=AsyncMock()
        await self.plugin.find_templates_tool(Event(user='other'),'电商')
        self.fake.catalog.assert_not_awaited()

    async def test_list_includes_both_catalogs(self):
        self.fake.catalog=AsyncMock(return_value={'items':[]})
        result=[x async for x in self.plugin.list_cmd(Event())]
        self.assertIn('市场',result[0])
        self.assertEqual(self.fake.catalog.await_count,2)

    async def test_command_registration(self):
        self.assertTrue({'help','list','market','use','confirm','cancel','tasks','status','result','resolve'}<=COMMANDS.keys())
        self.assertEqual(len(TOOLS),7);self.assertFalse(any('confirm' in n or 'execute' in n for n in TOOLS))
    async def test_confirm_command_calls_service_once(self):
        event=Event();owner=self.plugin.identity(event)
        r=await self.plugin.service.prepare(owner,'official','text-v1','test.xlsx',workbook(),event.unified_msg_origin)
        replies=[x async for x in self.plugin.confirm_cmd(event,r['code'])]
        self.assertIn('run-demo',replies[0]);self.assertEqual(sum(isinstance(c,tuple) for c in self.fake.calls),1)
    async def test_other_user_cannot_confirm(self):
        r=await self.plugin.service.prepare(self.plugin.identity(Event()),'official','text-v1','test.xlsx',workbook(),'private')
        replies=[x async for x in self.plugin.confirm_cmd(Event(user='456'),r['code'])]
        self.assertIn('允许',replies[0]);self.assertFalse(any(isinstance(c,tuple) for c in self.fake.calls))
    async def test_webchat_cannot_impersonate_qq(self):
        replies=[x async for x in self.plugin.help_cmd(Event(platform='webchat'))]
        self.assertIn('NapCat',replies[0])
    async def test_excel_upload_prepares_but_does_not_run(self):
        p=Path(self.tmp.name)/'input.xlsx';p.write_bytes(workbook());event=Event(files=[File(p.name,file=str(p))])
        import time
        self.plugin.selected[self.plugin.identity(event)]=('official','text-v1',time.time()+100)
        replies=[x async for x in self.plugin.receive_workbook(event)]
        self.assertIn('/loom confirm',replies[0]);self.assertFalse(any(isinstance(c,tuple) for c in self.fake.calls))
    async def test_docker_output_uses_token_url(self):
        p=Path(self.tmp.name)/'result.xlsx';p.write_bytes(workbook())
        f=await self.plugin.outgoing_file(p)
        self.assertEqual(f.url,'http://astrbot:6185/api/file/opaque-test-token');self.assertEqual(f.file_,'')
    async def test_llm_tool_cannot_bypass_authorization(self):
        reply=await self.plugin.status_tool(Event(user='456'),'run-demo')
        self.assertIn('允许',reply)

    async def test_completion_notification_includes_result_file(self):
        event=Event();owner=self.plugin.identity(event)
        r=await self.plugin.service.prepare(owner,'official','text-v1','test.xlsx',workbook(),event.unified_msg_origin)
        r,_=await self.plugin.service.confirm(owner,r['code'])
        self.fake.result=AsyncMock(return_value=workbook())
        with patch('astrbot_plugin_loomloom.main.asyncio.sleep',new=AsyncMock(side_effect=[None,asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):await self.plugin.watch()
        sent=self.plugin.context.send_message.call_args.args[1]
        self.assertTrue(any(isinstance(x,File) for x in sent.chain))
        self.assertTrue(self.plugin.service.store.get(r['code'],owner)['notified'])

    async def test_missing_platform_keeps_notification_pending(self):
        event=Event();owner=self.plugin.identity(event)
        r=await self.plugin.service.prepare(owner,'official','text-v1','test.xlsx',workbook(),event.unified_msg_origin)
        r,_=await self.plugin.service.confirm(owner,r['code'])
        self.fake.result=AsyncMock(return_value=workbook())
        self.plugin.context.send_message.return_value=False
        with patch('astrbot_plugin_loomloom.main.asyncio.sleep',new=AsyncMock(side_effect=[None,asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):await self.plugin.watch()
        self.assertFalse(self.plugin.service.store.get(r['code'],owner)['notified'])

    async def test_plain_confirmation_requires_exact_code_and_text(self):
        event=Event();owner=self.plugin.identity(event)
        r=await self.plugin.service.prepare(owner,'official','text-v1','test.xlsx',workbook(),event.unified_msg_origin)
        # Merely mentioning confirmation in ordinary text must not execute.
        for text in ['确认', '请确认执行 '+r['code'], '确认执行 '+r['code']+' 可以吗']:
            replies=[x async for x in self.plugin.receive_workbook(Event(files=[Plain(text)]))]
            self.assertEqual(replies,[])
        self.assertFalse(any(isinstance(x,tuple) for x in self.fake.calls))
        replies=[x async for x in self.plugin.receive_workbook(Event(files=[Plain('确认执行 '+r['code'])]))]
        self.assertIn('任务已提交',replies[0])

    async def test_natural_workflow_generates_preview_then_quote_only(self):
        import json
        from test_drafts import DraftClient
        self.fake=DraftClient();self.plugin.service.client=self.fake
        event=Event()
        result=await self.plugin.inspect_inputs_tool(event,'official','text-v1')
        data=json.loads(result.split('\n按这些字段')[0])
        await self.plugin.draft_inputs_tool(event,data['input_token'],'[{"prompt":"我的任务"}]')
        entry=self.plugin.drafts.sessions[self.plugin.identity(event)]
        self.assertTrue(entry['delivered']);self.assertEqual(self.fake.calls,[])
        self.assertTrue(any(isinstance(x,File) for x in event.sent[0].chain))
        replies=[x async for x in self.plugin.receive_workbook(Event(files=[Plain('核对完成 '+entry['draft_code'])]))]
        self.assertIn('预估费用',replies[0]);self.assertEqual(self.fake.calls,['quote'])

    async def test_failed_preview_delivery_blocks_quote(self):
        import json
        from test_drafts import DraftClient
        self.plugin.service.client=DraftClient();event=Event()
        result=await self.plugin.inspect_inputs_tool(event,'official','text-v1')
        data=json.loads(result.split('\n按这些字段')[0])
        event.send=AsyncMock(side_effect=RuntimeError('offline'))
        response=await self.plugin.draft_inputs_tool(event,data['input_token'],'[{"prompt":"test"}]')
        self.assertIn('未成功',response)
        entry=self.plugin.drafts.sessions[self.plugin.identity(event)]
        replies=[x async for x in self.plugin.quote_cmd(event,entry['draft_code'])]
        self.assertIn('还没有成功发出',replies[0])

    async def test_result_tool_checks_ownership(self):
        self.fake.result=AsyncMock(return_value=workbook())
        response=await self.plugin.get_result_tool(Event(),'run-other')
        self.assertIn('不属于',response);self.fake.result.assert_not_awaited()

    async def test_whoami_only_discloses_own_private_id_without_auth(self):
        replies=[x async for x in self.plugin.whoami_cmd(Event(user='456'))]
        self.assertIn('456',replies[0]);self.assertNotIn('123',replies[0])
        replies=[x async for x in self.plugin.whoami_cmd(Event(user='456',group='999'))]
        self.assertIn('私聊',replies[0]);self.assertNotIn('456',replies[0])

    async def test_doctor_missing_key_does_not_make_network_calls(self):
        self.fake.token=''
        replies=[x async for x in self.plugin.doctor_cmd(Event())]
        self.assertIn('尚未配置',replies[0]);self.assertEqual(self.fake.calls,[])

    async def test_doctor_validates_without_execution_or_key_disclosure(self):
        self.fake.token='do-not-echo-this-placeholder'
        self.fake.catalog=AsyncMock(return_value={'items':[]})
        self.fake.balance=AsyncMock(return_value={'availableBalanceT':12300000,'currency':'CNY'})
        replies=[x async for x in self.plugin.doctor_cmd(Event())]
        self.assertIn('1.2300000 CNY',replies[0]);self.assertNotIn(self.fake.token,replies[0])
        self.assertFalse(any(isinstance(x,tuple) for x in self.fake.calls))
        self.fake.balance.assert_awaited_once()

    async def test_doctor_unauthorized_does_not_query_balance(self):
        self.fake.balance=AsyncMock()
        replies=[x async for x in self.plugin.doctor_cmd(Event(user='456'))]
        self.assertIn('允许',replies[0]);self.fake.balance.assert_not_awaited()
