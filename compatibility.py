"""Static compatibility checks, not a general-purpose skill runtime or sandbox.

Only known standard cloud wrappers can be substituted by our hosted API path.
Unknown packages are not inferred to be cloud-only from prose or a title.
"""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from .client import BASE_URL, LoomError


def check_listing(meta):
    if meta.get('saleStatus') != 'listed':
        raise LoomError('这个技能未上架，或平台未返回上架状态，暂不能执行。')
    if meta.get('executionAvailabilityStatus') != 'available':
        raise LoomError('平台未确认这个技能当前可执行，请稍后重试或在 LoomLoom 查看详情。')
    if not meta.get('listingVersionId'):
        raise LoomError('平台没有返回技能版本，暂不能执行。')


def check_package(blob, resource):
    """Caller already verified ZIP structure and its server-provided SHA-256."""
    message = ('这个技能带有尚未适配的自定义 Skill 包，当前 QQ 插件无法确认其完整执行条件。'
               '无需填写技能白名单；请在支持该技能所需工具的 Agent 中使用，或等待插件适配。'
               '仅开电脑权限也不会自动补齐能力。本次没有提交任务。')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            files = [n for n in archive.namelist() if not n.endswith('/')]
            if len(files) != 2:
                raise ValueError()
            skill = next(n for n in files if n.endswith('/SKILL.md'))
            root = skill.rsplit('/', 1)[0]
            script_path = root + '/scripts/loomloom_skill.py'
            if script_path not in files:
                raise ValueError()
            md = archive.read(skill).decode('utf-8')
            script = archive.read(script_path).decode('utf-8')
        configs = re.findall(r'^CONFIG = json.loads\((.*)\)$', script, flags=re.M)
        if len(configs) != 1:
            raise ValueError()
        config = json.loads(json.loads(configs[0]))
        if config.get('listingId') != resource or config.get('publicApiBaseUrl') != BASE_URL:
            raise ValueError()
        script = re.sub(r'^CONFIG = json.loads\(.*\)$', 'CONFIG = <PUBLIC_LISTING_CONFIG>', script, flags=re.M)
        md = re.sub(r'^(name: |description: |# |\- Listing ID: |\- Display name snapshot: |\- Description snapshot: ).*',
                    r'\1<SNAPSHOT>', md, flags=re.M)
        profile = {'script_sha256': hashlib.sha256(script.encode()).hexdigest(),
                   'skill_sha256': hashlib.sha256(md.encode()).hexdigest()}
        known = json.loads(Path(__file__).with_name('cloud_profiles.json').read_text())['profiles']
        if profile not in known:
            raise ValueError()
    except (ValueError, KeyError, StopIteration, TypeError, zipfile.BadZipFile, UnicodeError):
        raise LoomError(message) from None
    return '已识别标准云端调用包，可使用 QQ 工作簿流程；仍需输入校验、报价和确认。'
