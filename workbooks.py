"""Conservative workbook filling: public fields only, exact headers, no code execution."""
from __future__ import annotations
import io
import json
import math
from openpyxl import load_workbook
from .client import LoomError, workbook_check

MAX_ROWS = 100


def public_fields(schema, kind):
    if kind == 'market':
        schema = schema.get('inputSchemaSnapshot')
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except ValueError:
                raise LoomError('市场公开输入说明无法解析，请使用手动填写 Excel。') from None
    if not isinstance(schema, dict) or not isinstance(schema.get('fields'), list):
        raise LoomError('模板没有返回可识别的公开输入字段，请使用手动填写 Excel。')
    columns = {c.get('fieldKey'): c.get('headerLabel') for c in schema.get('columns', []) if isinstance(c, dict)}
    fields = []
    for f in schema['fields']:
        if not isinstance(f, dict) or not isinstance(f.get('key'), str) or not f['key']:
            raise LoomError('模板输入字段格式不兼容。')
        fields.append(dict(key=f['key'], label=f.get('label') or f['key'],
            header=columns.get(f['key']) or f.get('label') or f['key'],
            required=bool(f.get('required')), type=f.get('type') or f.get('value_type') or 'string',
            multi=bool(f.get('multiValue') or f.get('multi_value')),
            choices=f.get('enumValues') or f.get('enum_values') or [],
            hint=f.get('businessHint') or f.get('inputHint') or ''))
    if not fields or len(fields) > 100 or len({f['key'] for f in fields}) != len(fields):
        raise LoomError('模板字段为空、重复或过多，不能自动填写。')
    return fields


def open_book(content):
    workbook_check(content)
    try:
        wb = load_workbook(io.BytesIO(content), keep_links=False)
        # Bound traversal even if worksheet dimensions have been tampered with.
        if len(wb.worksheets) > 20 or any(s.max_row > 2000 or s.max_column > 100 for s in wb.worksheets):
            wb.close()
            raise LoomError('模板表格过大，暂不自动填写，请使用手动 Excel 流程。')
        return wb
    except LoomError:
        raise
    except Exception:
        raise LoomError('无法读取模板工作簿，请使用手动 Excel 流程。') from None


def layout(wb, fields):
    labels = [str(f['header']) for f in fields]
    if len(set(labels)) != len(labels):
        raise LoomError('模板列名重复，无法可靠对应输入。')
    matches = []
    for sheet in wb.worksheets:
        if sheet.sheet_state != 'visible':
            continue
        for row in sheet.iter_rows(max_row=min(sheet.max_row, 30)):
            values = [str(c.value).strip() if c.value is not None else '' for c in row]
            nonempty = [v for v in values if v]
            if len(nonempty) == len(labels) and set(nonempty) == set(labels):
                matches.append((sheet.title, row[0].row, {f['key']: values.index(str(f['header'])) + 1 for f in fields}))
    if len(matches) != 1:
        raise LoomError('无法唯一识别模板输入表头，请手动填写原始 Excel；插件不会猜测列的位置。')
    return matches[0]


def describe(content, fields):
    wb = open_book(content)
    try:
        sheet, row, _ = layout(wb, fields)
        return {'sheet': sheet, 'header_row': row, 'fields': fields, 'max_rows': MAX_ROWS}
    finally:
        wb.close()


def fill(content, fields, rows_json):
    if not isinstance(rows_json, str) or len(rows_json) > 200_000:
        raise LoomError('输入内容过大，请拆分批次。')
    try:
        rows = json.loads(rows_json)
    except ValueError:
        raise LoomError('整理后的输入格式有误，请重新整理。') from None
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise LoomError('一次可整理 1～100 行输入。')
    known = {f['key'] for f in fields}
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) - known:
            raise LoomError(f'第 {i} 行含有模板未定义的字段，不能提交。')
        for f in fields:
            value = row.get(f['key'])
            if value is None or isinstance(value, str) and not value.strip():
                if f['required']:
                    raise LoomError(f'第 {i} 行缺少「{f["label"]}」，请先补充。')
                continue
            if f['multi'] or isinstance(value, (list, dict)):
                raise LoomError(f'「{f["label"]}」包含复杂输入，请使用手动 Excel 流程。')
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and not math.isfinite(value):
                raise LoomError('输入含有不支持的值。')
            if isinstance(value, str) and (len(value) > 16000 or any(ord(c) < 32 and c not in '\t\r\n' for c in value)):
                raise LoomError('单个输入过长或包含控制字符，请修改后重试。')
            typ = f['type']
            if typ in ('number', 'integer', 'int', 'float') and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise LoomError(f'「{f["label"]}」需要数字。')
            if typ in ('integer', 'int') and not isinstance(value, int):
                raise LoomError(f'「{f["label"]}」需要整数。')
            if typ in ('boolean', 'bool') and not isinstance(value, bool):
                raise LoomError(f'「{f["label"]}」需要布尔值。')
    wb = open_book(content)
    try:
        title, header, columns = layout(wb, fields)
        sheet = wb[title]
        if any(r.max_row > header for r in sheet.merged_cells.ranges):
            raise LoomError('输入区域存在合并单元格，暂不自动填写。')
        # Remove every sample row. Never leave sample tasks in a generated batch.
        if sheet.max_row > header:
            sheet.delete_rows(header + 1, sheet.max_row - header)
        for idx, row in enumerate(rows, header + 1):
            for key, col in columns.items():
                value = row.get(key)
                cell = sheet.cell(idx, col, value)
                if isinstance(value, str):
                    cell.data_type = 's'  # =, +, -, @ remain literal input, never Excel formulas.
        buf = io.BytesIO()
        wb.save(buf)
        result = buf.getvalue()
        workbook_check(result)
        return result, len(rows)
    finally:
        wb.close()
