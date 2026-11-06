# -*- coding: utf-8 -*-
"""
RFID 一卡通刷卡考勤系统 —— 数据库层

职责：
  1. 建表（学生表 + 打卡记录表）
  2. 学生登记（绑定卡号）
  3. 打卡记录写入
  4. 历史记录查询
  5. 判断「本次刷卡是签到还是签退」的核心逻辑

数据库选型：SQLite（单文件、零安装、适合课程演示，答辩易讲解）
"""

import sqlite3
from datetime import datetime

import config


def _conn():
    """获取数据库连接。每次操作单独开连接、用完即关，避免多线程同时写库冲突。"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row   # 让查询结果可以按字段名访问，如 row["name"]
    return conn


def init_db():
    """建表（表不存在时才创建）。核心表结构如下："""
    conn = _conn()
    cur = conn.cursor()

    # 表一：学生表 —— 一张卡唯一对应一个学生，因此用「卡号」做主键
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            card_id    TEXT PRIMARY KEY,      -- RFID 卡号，主键（一张卡只能绑定一个学生）
            student_no TEXT NOT NULL UNIQUE,  -- 学号，唯一
            name       TEXT NOT NULL,         -- 姓名
            class_name TEXT                   -- 班级
        )
        """
    )

    # 表二：打卡记录表 —— 每刷一次卡产生一条记录，用「自增 id」做主键
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,  -- 主键，自增，天然按时间排序
            card_id TEXT NOT NULL,                      -- 卡号（外键 -> students.card_id）
            type    TEXT NOT NULL,                      -- 签到 / 签退
            time    TEXT NOT NULL                       -- 打卡时间 YYYY-MM-DD HH:MM:SS
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_time ON records(time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_records_card_id ON records(card_id,id)")
    conn.commit()
    conn.close()


# ================== 学生登记 ==================

def add_student(card_id, student_no, name, class_name=""):
    """登记一个新学生（把卡号和学生信息绑定）。"""
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO students (card_id, student_no, name, class_name) VALUES (?, ?, ?, ?)",
                (card_id, student_no, name, class_name),
            )
    finally:
        conn.close()


def find_student(card_id):
    """按卡号查学生；查不到返回 None（用于识别陌生/空白卡）。"""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM students WHERE card_id = ?", (card_id,)
    ).fetchone()
    conn.close()
    return row


def list_students():
    """列出全部学生，按学号排序。"""
    conn = _conn()
    rows = conn.execute("SELECT * FROM students ORDER BY student_no").fetchall()
    conn.close()
    return rows


def delete_student(card_id):
    """删除指定学生（按卡号）。返回是否删除成功。"""
    conn = _conn()
    cur = conn.execute("DELETE FROM students WHERE card_id = ?", (card_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ================== 打卡记录 ==================

def get_last_record(card_id):
    """查该卡「最近一条」打卡记录 —— 判断签到/签退的关键查询。

    关键点：用 ORDER BY id DESC LIMIT 1 取最新一条，
    因为 id 是自增的，id 越大说明越晚，无需比较时间字符串。
    """
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM records WHERE card_id = ? ORDER BY id DESC LIMIT 1",
        (card_id,),
    ).fetchone()
    conn.close()
    return row


def decide_type(card_id):
    """判断本次刷卡是「签到」还是「签退」。

    规则（核心逻辑）：
      - 这张卡还没有任何记录  -> 签到（当天第一次刷卡）
      - 上一条是「签到」      -> 签退
      - 上一条是「签退」      -> 签到
    即：每次都看最新一条记录，把状态「翻转」。
    """
    last = get_last_record(card_id)
    if last is None or last["time"][:10] != datetime.now().strftime("%Y-%m-%d"):
        return "签到"
    return "签退" if last["type"] == "签到" else "签到"


def check_min_interval(card_id):
    """现场新增需求：签到/签退间隔不少于 MIN_INTERVAL_MINUTES 分钟。

    返回 (是否允许, 提示信息)。
    当 config.MIN_INTERVAL_MINUTES <= 0 时不作限制。
    """
    if config.MIN_INTERVAL_MINUTES <= 0:
        return True, ""

    last = get_last_record(card_id)
    if last is None:
        return True, ""

    last_time = datetime.strptime(last["time"], "%Y-%m-%d %H:%M:%S")
    diff_minutes = (datetime.now() - last_time).total_seconds() / 60.0
    if diff_minutes < config.MIN_INTERVAL_MINUTES:
        return False, f"距上次打卡仅 {diff_minutes:.1f} 分钟，未满 {config.MIN_INTERVAL_MINUTES} 分钟"
    return True, ""


def add_record(card_id, rtype):
    """写入一条打卡记录，返回写入的时间字符串。"""
    conn = _conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO records (card_id, type, time) VALUES (?, ?, ?)",
        (card_id, rtype, now),
    )
    conn.commit()
    conn.close()
    return now


# ================== 历史查询 ==================

def query_records(keyword="", rtype="", date=""):
    """历史记录查询，支持按关键字/类型/日期过滤。

    用 LEFT JOIN 把记录表和学生表连起来，这样记录里不用冗余存姓名，
    既符合第三范式，又能直接查出学生信息显示。
    """
    sql = (
        "SELECT r.id, r.card_id, s.student_no, s.name, s.class_name, r.type, r.time "
        "FROM records r LEFT JOIN students s ON r.card_id = s.card_id WHERE 1=1 "
    )
    args = []
    if keyword:
        like = f"%{keyword}%"
        sql += "AND (s.student_no LIKE ? OR s.name LIKE ? OR r.card_id LIKE ?) "
        args += [like, like, like]
    if rtype:
        sql += "AND r.type = ? "
        args.append(rtype)
    if date:
        sql += "AND r.time LIKE ? "
        args.append(f"{date}%")
    sql += "ORDER BY r.id DESC"

    conn = _conn()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def delete_record(record_id):
    """按记录 id 删除一条打卡记录。返回是否删除成功。"""
    conn = _conn()
    try:
        with conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='course_punches'").fetchone():
                linked = conn.execute('SELECT p.session_id,r.card_id,r.type FROM course_punches p JOIN records r ON r.id=p.record_id WHERE r.id=?',(record_id,)).fetchone()
                if linked and linked['type']=='签到' and conn.execute("SELECT 1 FROM course_punches p JOIN records r ON r.id=p.record_id WHERE p.session_id=? AND r.card_id=? AND r.type='签退'",(linked['session_id'],linked['card_id'])).fetchone():
                    raise ValueError('该课程签到已有对应签退，请先删除本课签退记录，再删除签到')
                conn.execute('DELETE FROM course_punches WHERE record_id=?',(record_id,))
            return conn.execute('DELETE FROM records WHERE id=?',(record_id,)).rowcount > 0
    finally:
        conn.close()


def clear_records():
    """清空所有打卡记录。返回删除的条数。"""
    conn = _conn()
    try:
        with conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='course_punches'").fetchone():
                conn.execute('DELETE FROM course_punches')
            return conn.execute('DELETE FROM records').rowcount
    finally:
        conn.close()


def count_records():
    conn = _conn()
    try:
        return conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    finally:
        conn.close()


def update_student(card_id, student_no, name, class_name=""):
    conn = _conn()
    try:
        with conn:
            result = conn.execute(
                "UPDATE students SET student_no=?, name=?, class_name=? WHERE card_id=?",
                (student_no, name, class_name, card_id))
            return result.rowcount > 0
    finally:
        conn.close()


def daily_summary():
    """登记人数、今日签到人数、今日最后状态为签到人数、未签到人数。"""
    conn = _conn()
    try:
        day = datetime.now().strftime("%Y-%m-%d")
        total = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        attended = conn.execute(
            "SELECT COUNT(DISTINCT r.card_id) FROM records r JOIN students s ON s.card_id=r.card_id "
            "WHERE r.time LIKE ? AND r.type='签到'", (day + '%',)).fetchone()[0]
        present = conn.execute(
            "SELECT COUNT(*) FROM records r JOIN students s ON s.card_id=r.card_id "
            "WHERE r.id IN (SELECT MAX(id) FROM records WHERE time LIKE ? GROUP BY card_id) "
            "AND r.type='签到'", (day + '%',)).fetchone()[0]
        return total, attended, present, total - attended
    finally:
        conn.close()


# ================== 工作台与数据维护 ==================
def student_statuses(day=None):
    day = day or datetime.now().strftime("%Y-%m-%d")
    conn = _conn()
    try:
        return conn.execute(
            "WITH today AS (SELECT * FROM records WHERE time >= ? AND time < date(?, '+1 day')), "
            "summary AS (SELECT card_id, MIN(CASE WHEN type='签到' THEN time END) first_in, "
            "MAX(id) last_id, COUNT(*) punches FROM today GROUP BY card_id) "
            "SELECT s.*, t.first_in, r.time last_time, COALESCE(t.punches,0) punches, "
            "CASE WHEN r.type='签到' THEN '在场' WHEN r.type='签退' THEN '已签退' ELSE '未签到' END status "
            "FROM students s LEFT JOIN summary t ON s.card_id=t.card_id "
            "LEFT JOIN records r ON r.id=t.last_id ORDER BY s.class_name,s.student_no", (day, day)).fetchall()
    finally:
        conn.close()


def import_students(rows):
    from services import validate_student
    values = [validate_student(*row) for row in rows]
    conn = _conn()
    try:
        # A single transaction: conflicting cards/numbers roll back the whole import.
        with conn:
            for line, row in enumerate(values, 2):
                try:
                    conn.execute("INSERT INTO students(card_id,student_no,name,class_name) VALUES(?,?,?,?)", row)
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"第 {line} 行卡号或学号重复：{row[0]} / {row[1]}；本次未导入任何数据") from exc
        return len(values)
    finally:
        conn.close()


def backup_database(path):
    from pathlib import Path
    if Path(path).resolve() == Path(config.DB_PATH).resolve():
        raise ValueError("备份文件不能覆盖正在使用的数据库")
    source = _conn()
    target = None
    try:
        target = sqlite3.connect(str(path))
        source.backup(target)
    finally:
        if target is not None:
            target.close()
        source.close()


def weekly_counts():
    from datetime import timedelta
    conn = _conn()
    try:
        today = datetime.now().date()
        start = (today - timedelta(days=6)).isoformat()
        rows = conn.execute("SELECT substr(time,1,10) day, COUNT(DISTINCT card_id) n FROM records "
                            "WHERE time >= ? AND type='签到' GROUP BY day", (start,)).fetchall()
        counts = {r['day']: r['n'] for r in rows}
        return [((today-timedelta(days=i)).isoformat(), counts.get((today-timedelta(days=i)).isoformat(), 0))
                for i in range(6, -1, -1)]
    finally:
        conn.close()


def search_records(keyword='', rtype='', class_name='全部班级', start='', end='', card_id=None):
    conn = _conn()
    try:
        has_courses = conn.execute("SELECT 1 FROM sqlite_master WHERE name='course_punches'").fetchone()
        if has_courses:
            base = ("SELECT r.id,r.card_id,COALESCE(cr.student_no,s.student_no) student_no,"
                    "COALESCE(cr.name,s.name) name,COALESCE(cr.class_name,s.class_name) class_name,"
                    "r.type,r.time,COALESCE(cs.title,'日常考勤') course_title FROM records r "
                    "LEFT JOIN students s ON r.card_id=s.card_id "
                    "LEFT JOIN course_punches p ON p.record_id=r.id "
                    "LEFT JOIN course_sessions cs ON cs.id=p.session_id "
                    "LEFT JOIN course_roster cr ON cr.session_id=p.session_id AND cr.card_id=r.card_id")
        else:
            base = ("SELECT r.id,r.card_id,s.student_no,s.name,s.class_name,r.type,r.time,"
                    "'日常考勤' course_title FROM records r LEFT JOIN students s ON r.card_id=s.card_id")
        sql = 'SELECT * FROM ('+base+') WHERE 1=1 '
        args = []
        if card_id is not None:
            sql += 'AND card_id=? '
            args.append(card_id)
        if keyword:
            sql += 'AND (card_id LIKE ? OR student_no LIKE ? OR name LIKE ? OR class_name LIKE ? OR course_title LIKE ?) '
            args += ['%' + keyword + '%'] * 5
        if rtype:
            sql += 'AND type=? '
            args.append(rtype)
        if class_name != '全部班级':
            sql += "AND COALESCE(NULLIF(class_name,''),'未分班')=? "
            args.append(class_name)
        if start:
            sql += 'AND time>=? '
            args.append(start)
        if end:
            sql += "AND time<date(?,'+1 day') "
            args.append(end)
        return conn.execute(sql + 'ORDER BY id DESC', args).fetchall()
    finally:
        conn.close()


def recent_records(limit=8):
    conn = _conn()
    try:
        return conn.execute(
            'SELECT r.*,s.student_no,s.name FROM records r '
            'LEFT JOIN students s ON r.card_id=s.card_id ORDER BY r.id DESC LIMIT ?', (limit,)).fetchall()
    finally:
        conn.close()


def record_action(card_id, action, min_interval=0, now=None):
    """Explicit daily sign-in/sign-out; validate and insert in one transaction."""
    if action not in ('签到','签退'):
        raise ValueError('请选择签到或签退功能')
    now = (now or datetime.now()).replace(microsecond=0)
    day = now.strftime('%Y-%m-%d')
    conn = _conn()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM students WHERE card_id=?',(card_id,)).fetchone():
                raise ValueError('卡号未登记，请先建立学生档案')
            sql = "SELECT * FROM records r WHERE r.card_id=? AND r.time>=? AND r.time<date(?,'+1 day') "
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='course_punches'").fetchone():
                sql += 'AND NOT EXISTS(SELECT 1 FROM course_punches p WHERE p.record_id=r.id) '
            last = conn.execute(sql+'ORDER BY r.id DESC LIMIT 1',(card_id,day,day)).fetchone()
            if action=='签到' and last and last['type']=='签到':
                raise ValueError('今日已签到；需要离开时请切换到“签退登记”')
            if action=='签退' and last and last['type']=='签退':
                raise ValueError('已登记签退，请勿重复刷卡')
            if last and (now-datetime.strptime(last['time'],'%Y-%m-%d %H:%M:%S')).total_seconds()<min_interval*60:
                raise ValueError(f'距上次打卡未满{min_interval}分钟')
            stamp = now.strftime('%Y-%m-%d %H:%M:%S')
            conn.execute('INSERT INTO records(card_id,type,time) VALUES(?,?,?)',(card_id,action,stamp))
            return stamp
    finally:
        conn.close()
