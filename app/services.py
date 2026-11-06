"""CSV exchange and local settings. No GUI dependencies."""
import csv
import json
import sys
import re
from datetime import date, datetime
from pathlib import Path
import config

HEADERS = ('卡号', '学号', '姓名', '班级')


def validate_student(card, number, name, class_name=''):
    values = tuple(str(v).strip() for v in (card, number, name, class_name))
    card, number, name, class_name = values
    card = card.upper()
    if not card or not number or not name:
        raise ValueError('卡号、学号和姓名不能为空')
    if len(card) > 128 or len(number) > 64 or len(name) > 80 or len(class_name) > 100:
        raise ValueError('字段过长：卡号最多128字，学号64字，姓名80字，班级100字')
    if any(any(ord(c) < 32 for c in value) for value in values):
        raise ValueError('学生信息不能包含换行或控制字符')
    return card, number, name, class_name


def read_students(path):
    suffix = Path(path).suffix.lower()
    if suffix == '.xlsx':
        return read_excel_students(path)
    if suffix != '.csv':
        raise ValueError('请选择 CSV 或 Excel .xlsx 文件；旧版 .xls 请先另存为 .xlsx')
    with open(path, encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not set(HEADERS).issubset(reader.fieldnames):
            raise ValueError('CSV 必须包含表头：卡号,学号,姓名,班级。请先下载导入模板。')
        rows = []
        for line, row in enumerate(reader, 2):
            if not any(row.values()):
                continue
            try:
                rows.append(validate_student(*(row.get(k) or '' for k in HEADERS)))
            except ValueError as exc:
                raise ValueError(f'第 {line} 行：{exc}') from exc
            if len(rows) > 20000:
                raise ValueError('单次导入最多 20000 名学生')
    if not rows:
        raise ValueError('CSV 中没有学生数据')
    return rows


def read_excel_students(path):
    # Ship the small, pure-Python reader with the desktop app for offline use.
    vendor = str(Path(__file__).resolve().parent / 'vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError('缺少 Excel 读取组件，请安装 requirements.txt 中的依赖') from exc
    book = None
    try:
        book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        sheet = book.worksheets[0]
        iterator = sheet.iter_rows()
        first = next(iterator, ())
        headers = [str(c.value or '').strip() for c in first]
        if not set(HEADERS).issubset(headers) or any(headers.count(h) != 1 for h in HEADERS):
            raise ValueError(f'首个工作表「{sheet.title}」第一行须包含且不重复：卡号、学号、姓名、班级')
        positions = [headers.index(h) for h in HEADERS]
        rows = []
        for line, cells in enumerate(iterator, 2):
            if line > 20001:
                raise ValueError('Excel 最多读取 20000 行学生数据，请删除多余空白行或拆分文件')
            if all(c.value is None or str(c.value).strip() == '' for c in cells):
                continue
            values = []
            for index in positions:
                cell = cells[index] if index < len(cells) else None
                value = cell.value if cell else None
                if cell and cell.data_type in ('f', 'e'):
                    raise ValueError(f'第 {line} 行「{headers[index]}」包含公式或错误，请转换为实际文本')
                if isinstance(value, (date, datetime, bool)):
                    raise ValueError(f'第 {line} 行「{headers[index]}」类型不正确，请使用文本')
                if isinstance(value, (int, float)):
                    if value != int(value):
                        raise ValueError(f'第 {line} 行「{headers[index]}」不能使用小数，请使用文本')
                    if headers[index] in ('卡号', '学号') and len(str(abs(int(value)))) > 15:
                        raise ValueError(f'第 {line} 行「{headers[index]}」为超过15位的数字，可能已丢失精度，请重新输入为文本')
                    value = str(int(value))
                    # Preserve common zero-padded identifier formats, e.g. 000000.
                    if re.fullmatch(r'0+', cell.number_format or '') and not value.startswith('-'):
                        value = value.zfill(len(cell.number_format))
                values.append('' if value is None else str(value))
            try:
                rows.append(validate_student(*values))
            except ValueError as exc:
                raise ValueError(f'第 {line} 行：{exc}') from exc
        if not rows:
            raise ValueError('Excel 中没有学生数据')
        return rows
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('无法读取 Excel，请确认文件未损坏且不是加密工作簿：' + str(exc)) from exc
    finally:
        if book:
            book.close()


def write_csv(path, headers, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as target:
        writer = csv.writer(target)
        writer.writerow(headers)
        for row in rows:
            # Excel formula protection; identifiers retain their leading zeroes.
            writer.writerow([("'" + str(v)) if str(v or '').startswith(('=', '+', '-', '@', '\t', '\r')) else v
                             for v in row])


def settings_path():
    return Path(config.DB_PATH).resolve().parent / 'settings.json'


def load_settings():
    defaults = dict(port=config.SERIAL_PORT, baud=config.BAUD_RATE,
                    interval=config.MIN_INTERVAL_MINUTES,
                    dedup=config.DEDUP_INTERVAL_SECONDS, sound=True, voice=True)
    try:
        value = json.loads(settings_path().read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            return defaults
        for key in defaults:
            if key in value and type(value[key]) is type(defaults[key]):
                defaults[key] = value[key]
        if defaults['baud'] not in (9600, 19200, 38400, 57600, 115200):
            defaults['baud'] = config.BAUD_RATE
        if not 0 <= defaults['interval'] <= 1440:
            defaults['interval'] = 0
        if not 1 <= defaults['dedup'] <= 60:
            defaults['dedup'] = 3
    except (OSError, ValueError):
        pass
    return defaults


def save_settings(value):
    path = settings_path()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
