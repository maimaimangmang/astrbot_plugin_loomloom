"""Small ShengSuanYun LoomLoom API client; no redirects or automatic POST retries."""
from __future__ import annotations
import asyncio
import base64
import io
import json
import re
import zipfile
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import aiohttp

BASE_URL = 'https://loomloom.shengsuanyun.com/loom/v1'
MAX_FILE = 10 * 1024 * 1024

class LoomError(Exception):
    pass

class APIError(LoomError):
    def __init__(self, status: int):
        self.status = status
        hints = {
            401: '密钥验证未通过，请在插件配置检查 LoomLoom API Key；不要在 QQ 发送密钥。',
            403: '当前密钥或账号没有此操作权限，请到胜算云控制台核对。',
            404: '没有找到此模板或任务，请刷新目录并核对 ID。',
            429: '请求过于频繁，请稍后再试；已提交任务不要重复执行。',
        }
        super().__init__(f'LoomLoom 请求失败（HTTP {status}）。' + hints.get(status, '请在管理页核对配置或稍后查询任务。'))


def safe_id(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', value):
        raise LoomError('模板或任务 ID 格式不正确，请复制列表中的完整 ID。')
    return quote(value, safe='')


def workbook_check(content: bytes) -> None:
    if not content or len(content) > MAX_FILE:
        raise LoomError('请上传不超过 10 MB 的 .xlsx 文件。')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = z.namelist()
            if '[Content_Types].xml' not in names or 'xl/workbook.xml' not in names:
                raise ValueError()
            if len(names) > 2000 or sum(x.file_size for x in z.infolist()) > 60 * 1024 * 1024:
                raise LoomError('工作簿解压后过大，请拆成较小批次。')
            if any('vbaProject' in n or '/externalLinks/' in n for n in names):
                raise LoomError('请使用不带宏和外部链接的 .xlsx 工作簿。')
    except (zipfile.BadZipFile, ValueError):
        raise LoomError('这不是有效的 Excel .xlsx 工作簿。') from None


def money(data: dict, field: str) -> tuple[int, str]:
    raw = data.get(field + 'T')
    amount = data.get(field) or {}
    currency = amount.get('currency') or data.get('currency') or (data.get('balanceCheck') or {}).get('currency')
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or not isinstance(currency, str) or not currency:
        raise LoomError('服务未返回完整的金额和币种，暂不能生成执行确认。')
    return raw, currency


def display_money(raw: int, currency: str) -> str:
    return f'{Decimal(raw) / Decimal(10_000_000):.7f} {currency}'


class Client:
    def __init__(self, token: str):
        self.token = token.strip()

    async def request(self, method: str, path: str, payload=None, binary=False, public=False):
        if not path.startswith('/') or '://' in path or '..' in path:
            raise LoomError('无效的 API 路径。')
        if not public and not self.token:
            raise LoomError('请先在插件配置填写胜算云 LoomLoom API Key。不要在 QQ 里发送密钥。')
        headers = {} if public else {'Authorization': 'Bearer ' + self.token}
        limit = MAX_FILE if binary else 4 * 1024 * 1024
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.request(method, BASE_URL + path, json=payload, headers=headers, allow_redirects=False) as resp:
                    if resp.status >= 300:
                        # Never relay raw server errors: they may include input content or credentials.
                        raise APIError(resp.status)
                    chunks, size = [], 0
                    async for chunk in resp.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > limit:
                            raise LoomError('服务返回内容超过插件大小限制，请在 LoomLoom 控制台取回。')
                        chunks.append(chunk)
                    data = b''.join(chunks)
                    if binary:
                        return data
                    result = json.loads(data)
                    if not isinstance(result, dict):
                        raise LoomError('LoomLoom 返回格式与当前插件不兼容。')
                    return result
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise LoomError('连接 LoomLoom 超时或中断。提交期间发生时，任务可能已经创建，请勿重复提交。') from None
        except (ValueError, UnicodeError):
            raise LoomError('LoomLoom 返回了无法解析的数据。') from None

    async def catalog(self, kind='official', keyword=''):
        if kind == 'official':
            return await self.request('GET', '/officialTemplates', public=True)
        if kind == 'market':
            return await self.request('GET', '/marketListings?pageSize=20&keyword=' + quote(keyword, safe=''), public=True)
        raise LoomError('模板类别请填 official 或 market。')

    def prefix(self, kind, resource):
        return ('/officialTemplates/' if kind == 'official' else '/marketListings/') + safe_id(resource)

    async def metadata(self, kind, resource):
        safe_id(resource)
        if kind == 'official':
            catalog = await self.catalog()
            for item in catalog.get('items', []):
                if item.get('templateId') == resource:
                    return item
            raise LoomError('没有找到这个官方模板。')
        if kind == 'market':
            return await self.request('GET', self.prefix(kind, resource), public=True)
        raise LoomError('模板类别请填 official 或 market。')

    async def schema(self, kind, resource):
        if kind == "official":
            return await self.request("GET", self.prefix(kind, resource) + "/schema", public=True)
        if kind == "market":
            return await self.metadata(kind, resource)
        raise LoomError("模板类别请填 official 或 market。")

    async def workbook(self, kind, resource):
        content = await self.request('GET', self.prefix(kind, resource) + '/workbook', binary=True)
        workbook_check(content)
        return content

    async def package(self, kind, resource):
        return await self.request('GET', self.prefix(kind, resource) + '/skillPackage/archive', binary=True, public=True)

    async def validate_quote(self, kind, resource, content, filename):
        workbook_check(content)
        payload = {'filename': filename, 'content': base64.b64encode(content).decode('ascii')}
        prefix = self.prefix(kind, resource)
        validation = await self.request('POST', prefix + ':validateWorkbook', payload)
        if validation.get('valid') is not True:
            raise LoomError('工作簿校验未通过，请对照下载的模板检查必填项和输入格式。')
        quote_data = await self.request('POST', prefix + (':precheckWorkbook' if kind == 'official' else ':quoteWorkbook'), payload)
        if (quote_data.get('balanceCheck') or {}).get('isSufficient') is False:
            raise LoomError('LoomLoom 可用余额不足，本次未提交任务。')
        if quote_data.get('requiresPaymentChoice'):
            raise LoomError('此技能需要选择订阅或支付方式，请先在 LoomLoom 控制台处理；插件未执行。')
        money(quote_data, 'estimatedTotalCost' if kind == 'official' else 'estimatedBuyerPayable')
        return quote_data

    async def execute(self, kind, resource, content, filename, request_id, quote_data):
        payload = {'filename': filename, 'content': base64.b64encode(content).decode('ascii'), 'clientRequestId': request_id}
        if kind == 'market':
            payload['confirm'] = True
            payload['expectedListingVersionId'] = quote_data['listingVersionId']
            if quote_data.get('effectivePaymentMode'):
                payload['expectedPaymentMode'] = quote_data['effectivePaymentMode']
        return await self.request('POST', self.prefix(kind, resource) + (':runWorkbook' if kind == 'official' else ':executeWorkbook'), payload)

    async def balance(self):
        return await self.request("GET", "/users/me/balance")

    async def run(self, run_id):
        return await self.request('GET', '/users/me/runs/' + safe_id(run_id))

    async def result(self, run_id):
        data = await self.request('GET', '/users/me/runs/' + safe_id(run_id) + '/resultWorkbook', binary=True)
        workbook_check(data)
        return data
