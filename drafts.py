"""Conversation-scoped workbook drafts. No paid endpoints are reachable here."""
import hashlib
import secrets
import time
from .client import LoomError
from .workbooks import public_fields, describe, fill


def version(meta):
    return (meta.get('version'), meta.get('listingVersionId'), (meta.get('skillPackage') or {}).get('archiveHash'))


class Drafts:
    def __init__(self, service):
        self.service = service
        self.sessions = {}

    def get(self, owner, token):
        entry = self.sessions.get(owner)
        if not entry or entry['token'] != token or entry['expires'] < time.time():
            raise LoomError('这份输入草稿已过期或被替换，请重新准备。重载插件后也需要重新准备草稿。')
        return entry

    async def inspect(self, owner, kind, resource):
        async with self.service.lock(owner):
            meta = await self.service.select(kind, resource)
            schema = await self.service.client.schema(kind, resource)
            content = await self.service.client.workbook(kind, resource)
            fields = public_fields(schema, kind)
            shape = describe(content, fields)
            # Verify source did not change between schema and workbook requests.
            if version(await self.service.client.metadata(kind, resource)) != version(meta):
                raise LoomError('模板在准备期间更新了，请重新读取模板。')
            token = secrets.token_hex(8)
            self.sessions[owner] = dict(token=token, expires=time.time()+1800,
                kind=kind, resource=resource, content=content, fields=fields, version=version(meta))
            return dict(input_token=token, name=meta.get('name') or meta.get('displayName') or resource,
                kind=kind, resource=resource, **shape)

    async def build(self, owner, token, rows_json):
        async with self.service.lock(owner):
            entry = self.get(owner, token)
            content, count = fill(entry['content'], entry['fields'], rows_json)
            # A revised input invalidates prior quotes, including before a new quote succeeds.
            for old in self.service.store.list(owner):
                if old['state'] == 'quoted':
                    old['state'] = 'superseded'
                    self.service.store.put(old)
            code = secrets.token_hex(6)
            entry.update(draft_code=code, draft=content, rows=count, expires=time.time()+1800,
                         delivered=False, sha256=hashlib.sha256(content).hexdigest())
            entry.pop('quote_code', None)
            return code, content, count

    def delivered(self, owner, code):
        entry = self.sessions.get(owner)
        if entry and entry.get('draft_code') == code:
            entry['delivered'] = True

    def check(self, owner, code):
        entry = self.sessions.get(owner)
        if not entry or entry.get('draft_code') != code or entry['expires'] < time.time():
            raise LoomError('草稿已过期或替换，请重新生成表格。')
        if not entry.get('delivered'):
            raise LoomError('输入表格还没有成功发出，请重新生成并核对后报价。')
        return entry

    async def quote(self, owner, code, umo):
        entry = self.check(owner, code)
        def guard():
            self.check(owner, code)
        return await self.service.prepare(owner, entry['kind'], entry['resource'],
            'loomloom-input-' + code + '.xlsx', entry['draft'], umo,
            expected_version=entry['version'], guard=guard, row_count=entry['rows'])

    def cleanup(self):
        for owner, entry in list(self.sessions.items()):
            if entry['expires'] < time.time():
                self.sessions.pop(owner, None)
