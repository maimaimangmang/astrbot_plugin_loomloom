from __future__ import annotations
import asyncio
import hashlib
import io
import json
import os
import re
import secrets
import time
import uuid
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from .client import Client, LoomError, MAX_FILE, money, display_money, safe_id, workbook_check
from .store import Store
from .compatibility import check_listing, check_package


def digest(data):
    return hashlib.sha256(data).hexdigest()


def quote_signature(q, kind):
    field = 'estimatedTotalCost' if kind == 'official' else 'estimatedBuyerPayable'
    amount, currency = money(q, field)
    keys = ['pricingRevision', 'listingVersionId', 'pricingRuleVersion', 'effectivePaymentMode', 'taskCount', 'taskFixedFeeT']
    return json.dumps([amount, currency] + [q.get(k) for k in keys], sort_keys=True)


class Service:
    def __init__(self, config, root, client=None):
        self.config = config
        self.client = client or Client(config.get('api_key', ''))
        self.store = Store(root)
        self.root = self.store.root
        self.locks = {}
        # A crashed process must never silently repeat a possibly accepted paid request.
        for r in self.store.all():
            if r['state'] == 'submitting':
                r['state'] = 'uncertain'
                self.store.put(r)

    def authorize(self, user, group=''):
        allowed = {str(x).strip() for x in self.config.get('allowed_users', []) if str(x).strip()}
        if str(user) not in allowed:
            raise LoomError('此功能仅向插件配置中允许的 QQ 账号开放。')
        if group:
            raise LoomError('目前只在私聊中使用 LoomLoom，请私聊机器人。')

    def lock(self, owner):
        return self.locks.setdefault(owner, asyncio.Lock())

    def cap(self, q, kind):
        field = 'estimatedTotalCost' if kind == 'official' else 'estimatedBuyerPayable'
        amount, currency = money(q, field)
        try:
            limit = Decimal(str(self.config.get('max_estimated_cost', '5')))
            if not limit.is_finite() or limit <= 0:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise LoomError('单次预估费用上限必须是大于零的数字。') from None
        if currency != self.config.get('cost_currency', 'CNY'):
            raise LoomError('服务返回币种与插件的费用上限币种不同，暂不执行。')
        if Decimal(amount) / 10_000_000 > limit:
            raise LoomError('本次预估费用超过插件设置的单次上限，未提交。')
        if kind == 'market' and not q.get('listingVersionId'):
            raise LoomError('市场报价没有返回版本 ID，无法锁定此次执行。')

    async def select(self, kind, resource):
        meta = await self.client.metadata(kind, resource)
        if kind == 'market':
            check_listing(meta)
        package = meta.get('skillPackage') or {}
        if package.get('available'):
            expected = package.get('archiveHash', '').removeprefix('sha256:')
            if not re.fullmatch(r'[a-fA-F0-9]{64}', expected):
                raise LoomError('技能包没有有效校验值，暂不能使用。')
            cache = self.root / 'packages' / expected
            if not cache.exists():
                blob = await self.client.package(kind, resource)
                if digest(blob) != expected:
                    raise LoomError('技能包校验失败，未使用该技能。')
                try:
                    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                        files = archive.infolist()
                        if len(files) > 300 or sum(f.file_size for f in files) > 20 * 1024 * 1024:
                            raise ValueError()
                        for f in files:
                            p = PurePosixPath(f.filename)
                            if p.is_absolute() or '..' in p.parts or '\\' in f.filename or (f.external_attr >> 16) & 0o170000 == 0o120000:
                                raise ValueError()
                        if not any(f.filename.endswith('/SKILL.md') or f.filename == 'SKILL.md' for f in files):
                            raise ValueError()
                        # Store the verified package for review. Do not run arbitrary package scripts.
                        cache.parent.mkdir(exist_ok=True)
                        temp = cache.with_suffix('.tmp-' + uuid.uuid4().hex)
                        temp.write_bytes(blob)
                        temp.chmod(0o600)
                        os.replace(temp, cache)
                except (ValueError, zipfile.BadZipFile):
                    raise LoomError('技能包结构不符合要求，未加载。') from None
            blob = cache.read_bytes()
            if digest(blob) != expected:
                raise LoomError('本地技能包缓存校验失败，请删除插件数据目录中对应的 packages 缓存后重试。')
            if kind == 'market':
                check_package(blob, resource)
        return meta

    async def download(self, kind, resource):
        await self.select(kind, resource)
        return await self.client.workbook(kind, resource)

    async def prepare(self, owner, kind, resource, filename, content, umo, expected_version=None, guard=None, row_count=None):
        async with self.lock(owner):
            if guard:
                guard()
            if any(r['state'] in ('submitting', 'uncertain') for r in self.store.list(owner)):
                raise LoomError('有一笔提交结果尚未核实。请先查 LoomLoom 控制台，并用 /loom resolve 处理，避免重复扣费。')
            for old in self.store.list(owner):
                if old['state'] == 'quoted':
                    old['state'] = 'superseded'
                    self.store.put(old)
            workbook_check(content)
            meta = await self.select(kind, resource)
            current_version = (meta.get('version'), meta.get('listingVersionId'), (meta.get('skillPackage') or {}).get('archiveHash'))
            if expected_version is not None and current_version != expected_version:
                raise LoomError('模板已更新，请重新准备输入并核对后报价。')
            q = await self.client.validate_quote(kind, resource, content, filename)
            self.cap(q, kind)
            code = secrets.token_hex(4)
            path = self.root / (code + '.xlsx')
            path.write_bytes(content)
            path.chmod(0o600)
            record = dict(code=code, owner=owner, kind=kind, resource=resource,
                name=meta.get('name') or meta.get('displayName') or resource, row_count=row_count,
                version=meta.get('version') or meta.get('listingVersionId'), package_hash=(meta.get('skillPackage') or {}).get('archiveHash'),
                filename=Path(filename).name, sha256=digest(content), size=len(content),
                umo=umo, quote=q, state='quoted', expires=time.time() + 300,
                request_id=str(uuid.uuid4()), created=time.time(), notified=False)
            self.store.put(record)
            return record

    def confirmation(self, r):
        q, kind = r['quote'], r['kind']
        amount, currency = money(q, 'estimatedTotalCost' if kind == 'official' else 'estimatedBuyerPayable')
        lines = [f"模板：{r['name']}（{'官方' if kind == 'official' else '市场'}）", f"ID：{r['resource']}",
            f"输入：{r['filename']}，{r['size']} 字节，将执行整个工作簿。"]
        if r.get('row_count') is not None:
            lines.append(f"输入行数：{r['row_count']}")
        if q.get('taskCount') is not None:
            lines.append(f"任务数：{q['taskCount']}")
        lines.append(f'预估费用：{display_money(amount, currency)}')
        if kind == 'market':
            lines.append(f"报价版本：{q['listingVersionId']}；下单时由服务核对当前版本。")
            if isinstance(q.get('taskFixedFeeT'), int) and isinstance(q.get('taskCount'), int):
                lines.append('创作者调用费：' + display_money(q['taskFixedFeeT'] * q['taskCount'], currency))
            if 'estimatedExecutionCostT' in q:
                raw, cur = money(q, 'estimatedExecutionCost')
                lines.append('模型/API 预估：' + display_money(raw, cur))
            else:
                lines.append('模型/API 费用未单独返回。')
        balance = q.get('balanceCheck') or {}
        if isinstance(balance.get('availableBalance'), int) and balance.get('currency'):
            lines.append('可用余额：' + display_money(balance['availableBalance'], balance['currency']))
        lines += ['文件内容会上传到胜算云 LoomLoom。预估不是最终账单，实际费用以服务结算为准。',
                  '官方任务即使失败，也可能产生已消耗的模型费用。' if kind == 'official' else '市场任务全部失败/取消时不收取最终费用；部分失败的结算可能仍待处理。',
                  f"5 分钟内发送「确认执行 {r['code']}」才会执行（也可 /loom confirm {r['code']}）；放弃请发 /loom cancel {r['code']}。"]
        return '\n'.join(lines)

    async def confirm(self, owner, code):
        async with self.lock(owner):
            r = self.store.get(code, owner)
            if not r:
                raise LoomError('没有找到属于当前会话的确认单。')
            if r['state'] == 'submitted':
                return r, False
            if r['state'] != 'quoted':
                raise LoomError('该确认单已失效或提交结果未明，不能重复执行。')
            if time.time() > r['expires']:
                r['state'] = 'expired'; self.store.put(r)
                raise LoomError('确认单已过期，请重新报价。')
            content = (self.root / (code + '.xlsx')).read_bytes()
            if digest(content) != r['sha256']:
                r['state'] = 'invalid'; self.store.put(r)
                raise LoomError('输入文件已变化，请重新上传和报价。')
            meta = await self.select(r['kind'], r['resource'])
            q = await self.client.validate_quote(r['kind'], r['resource'], content, r['filename'])
            self.cap(q, r['kind'])
            version = meta.get('version') or meta.get('listingVersionId')
            package_hash = (meta.get('skillPackage') or {}).get('archiveHash')
            if quote_signature(q, r['kind']) != quote_signature(r['quote'], r['kind']) or version != r['version'] or package_hash != r.get('package_hash'):
                r['state'] = 'superseded'; self.store.put(r)
                r.update(code=secrets.token_hex(4), quote=q, state='quoted', expires=time.time()+300,
                         request_id=str(uuid.uuid4()), version=version, package_hash=package_hash)
                path = self.root / (r['code'] + '.xlsx'); path.write_bytes(content); path.chmod(0o600)
                self.store.put(r)
                return r, True
            r['state'] = 'submitting'; self.store.put(r)
            try:
                answer = await self.client.execute(r['kind'], r['resource'], content, r['filename'], r['request_id'], q)
                run_id = answer.get('runId')
                if not isinstance(run_id, str) or not run_id:
                    raise LoomError('提交响应未包含任务 ID。')
                safe_id(run_id)
            except asyncio.CancelledError:
                r['state'] = 'uncertain'; self.store.put(r)
                raise
            except Exception:
                r['state'] = 'uncertain'; self.store.put(r)
                raise LoomError(f"本次提交结果未明，可能已经创建任务。已禁止自动重试。确认单：{code}；请求编号：{r['request_id']}。请在 LoomLoom 控制台核实后处理。") from None
            r.update(state='submitted', run_id=run_id)
            self.store.put(r)
            return r, False

    async def cancel(self, owner, code):
        # Cancellation and submission must serialize on the same conversation lock.
        async with self.lock(owner):
            r = self.store.get(code, owner)
            if not r or r['state'] != 'quoted':
                raise LoomError('只能取消尚未提交的报价；已提交任务不会被此命令取消。')
            r['state'] = 'cancelled'; self.store.put(r)
            (self.root / (code + '.xlsx')).unlink(missing_ok=True)

    def owned_run(self, owner, run_id):
        if not self.store.by_run(run_id, owner):
            raise LoomError('此任务不属于当前 QQ 会话，不能查询。')

    async def status(self, owner, run_id):
        self.owned_run(owner, run_id)
        data = await self.client.run(run_id)
        r = data.get('run') or {}
        lines = [f"任务：{run_id}", f"状态：{r.get('status', '未返回')}",
                 f"成功 {r.get('completedTasks', '?')} / 共 {r.get('totalTasks', '?')}，失败 {r.get('failedTasks', '?')}"]
        if isinstance(r.get('actualCost'), dict):
            m = r['actualCost']
            if m.get('amount') is not None and m.get('currency'):
                lines.append(f"已返回实际费用：{m['amount']} {m['currency']}")
        return '\n'.join(lines)

    async def resolve(self, owner, code, run_id):
        async with self.lock(owner):
            r = self.store.get(code, owner)
            if not r or r['state'] != 'uncertain':
                raise LoomError('只可处理提交结果未明的确认单。')
            if run_id == 'none':
                r['state'] = 'resolved_no_run'
            else:
                safe_id(run_id)
                await self.client.run(run_id)
                r.update(state='submitted', run_id=run_id)
            self.store.put(r)
            return r

    def cleanup(self):
        cutoff = time.time() - 7 * 86400
        for r in self.store.all():
            if r['created'] < cutoff:
                (self.root / (r['code'] + '.xlsx')).unlink(missing_ok=True)
        for p in (self.root / 'out').glob('*.xlsx') if (self.root / 'out').exists() else []:
            if p.stat().st_mtime < time.time() - 86400:
                p.unlink(missing_ok=True)
