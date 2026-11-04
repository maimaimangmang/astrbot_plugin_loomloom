"""Bounded attachment download. Never attach LoomLoom credentials to QQ file URLs."""
import asyncio
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlsplit, unquote
import aiohttp
from aiohttp.abc import AbstractResolver
from .client import LoomError, MAX_FILE, workbook_check

class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        out = []
        for fam, _, proto, _, addr in infos:
            if not ipaddress.ip_address(addr[0]).is_global:
                raise LoomError('文件地址指向内网，插件没有下载。请通过 QQ 正常发送文件。')
            out.append(dict(hostname=host, host=addr[0], port=port, family=fam, proto=proto, flags=socket.AI_NUMERICHOST))
        return out

    async def close(self):
        pass

async def read_attachment(file_component, allowed_roots):
    source = await file_component.get_file(allow_return_url=True)
    if not source:
        raise LoomError('QQ 文件暂时无法读取，请重新发送文件。')
    parsed = urlsplit(source)
    if parsed.scheme in ('http', 'https'):
        if not parsed.hostname or parsed.username or parsed.password:
            raise LoomError('无效的 QQ 文件地址。')
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                raise LoomError('不下载内网文件地址。')
        except ValueError:
            pass
        connector = aiohttp.TCPConnector(resolver=PublicResolver())
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=45)) as s:
                async with s.get(source, allow_redirects=False) as r:
                    if r.status != 200:
                        raise LoomError('QQ 文件链接失效或重定向，请重新发送。')
                    if r.content_length and r.content_length > MAX_FILE:
                        raise LoomError('文件超过 10 MB，请拆分。')
                    data = bytearray()
                    async for chunk in r.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_FILE:
                            raise LoomError('文件超过 10 MB，请拆分。')
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise LoomError('下载 QQ 文件失败，请重新发送。') from None
        data = bytes(data)
    else:
        if parsed.scheme and parsed.scheme != 'file':
            raise LoomError('不支持这种文件地址。')
        path = Path(unquote(parsed.path) if parsed.scheme == 'file' else source).resolve()
        if not any(path.is_relative_to(Path(root).resolve()) for root in allowed_roots):
            raise LoomError('文件不在 AstrBot 的临时下载目录，插件没有读取。')
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise LoomError('本地文件不存在或超过 10 MB。')
        data = path.read_bytes()
    workbook_check(data)
    return data
