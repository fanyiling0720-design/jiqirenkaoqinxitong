"""Per-lesson roster, attendance and leave. All writes share the main database."""
import math
from datetime import datetime, timedelta
import database as db

FORMAT = '%Y-%m-%d %H:%M:%S'


def init_db():
    conn = db._conn()
    try:
        with conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS course_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL, class_name TEXT NOT NULL,
                    start_time TEXT NOT NULL, end_time TEXT NOT NULL,
                    grace_minutes INTEGER NOT NULL DEFAULT 5,
                    lead_minutes INTEGER NOT NULL DEFAULT 30
                );
                CREATE TABLE IF NOT EXISTS course_roster (
                    session_id INTEGER NOT NULL, card_id TEXT NOT NULL,
                    student_no TEXT NOT NULL, name TEXT NOT NULL, class_name TEXT NOT NULL,
                    PRIMARY KEY(session_id,card_id)
                );
                CREATE TABLE IF NOT EXISTS course_punches (
                    session_id INTEGER NOT NULL, record_id INTEGER NOT NULL UNIQUE,
                    PRIMARY KEY(session_id,record_id)
                );
                CREATE TABLE IF NOT EXISTS course_leaves (
                    session_id INTEGER NOT NULL, card_id TEXT NOT NULL,
                    leave_type TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id,card_id)
                );
                CREATE INDEX IF NOT EXISTS idx_course_roster_card ON course_roster(card_id,session_id);
            ''')
    finally:
        conn.close()


def _validate(title, class_name, start_time, end_time, grace_minutes, lead_minutes):
    title, class_name = title.strip(), class_name.strip()
    if not title or len(title) > 80:
        raise ValueError('课程名称不能为空，且最多80字')
    if not class_name:
        raise ValueError('请选择班级')
    try:
        start, end = datetime.strptime(start_time, FORMAT), datetime.strptime(end_time, FORMAT)
        grace_minutes, lead_minutes = int(grace_minutes), int(lead_minutes)
    except (ValueError, TypeError) as exc:
        raise ValueError('请检查日期、时间和宽限分钟数') from exc
    if start >= end:
        raise ValueError('下课时间必须晚于上课时间')
    if end-start > timedelta(hours=24):
        raise ValueError('单个课次时长不能超过24小时')
    if not 0 <= grace_minutes <= 120 or not 0 <= lead_minutes <= 120:
        raise ValueError('迟到宽限和提前签到均需为0–120分钟')
    return title, class_name, start.strftime(FORMAT), end.strftime(FORMAT), grace_minutes, lead_minutes


def _roster(conn, session):
    if session['class_name'] == '全部班级':
        students = conn.execute('SELECT * FROM students').fetchall()
    else:
        students = conn.execute("SELECT * FROM students WHERE COALESCE(NULLIF(class_name,''),'未分班')=?",
                                (session['class_name'],)).fetchall()
    count = 0
    for s in students:
        count += conn.execute('INSERT OR IGNORE INTO course_roster VALUES(?,?,?,?,?)',
            (session['id'],s['card_id'],s['student_no'],s['name'],s['class_name'] or '')).rowcount
    return count


def create_session(title, class_name, start_time, end_time, grace_minutes=5, lead_minutes=30):
    values = _validate(title,class_name,start_time,end_time,grace_minutes,lead_minutes)
    conn = db._conn()
    try:
        with conn:
            cur = conn.execute('INSERT INTO course_sessions(title,class_name,start_time,end_time,grace_minutes,lead_minutes) VALUES(?,?,?,?,?,?)', values)
            sid = cur.lastrowid
            session = conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
            if not _roster(conn,session):
                raise ValueError('所选班级没有学生，请先新增或导入学生')
            return sid
    finally:
        conn.close()


def list_sessions():
    conn = db._conn()
    try:
        return conn.execute('SELECT * FROM course_sessions ORDER BY start_time DESC,id DESC').fetchall()
    finally:
        conn.close()


def get_session(sid):
    conn = db._conn()
    try:
        return conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
    finally:
        conn.close()


def sync_roster(sid):
    conn = db._conn()
    try:
        with conn:
            session = conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
            if not session:
                raise ValueError('该课次不存在')
            return _roster(conn,session)
    finally:
        conn.close()


def update_session(sid, title, class_name, start_time, end_time, grace_minutes=5, lead_minutes=30):
    values = _validate(title,class_name,start_time,end_time,grace_minutes,lead_minutes)
    conn = db._conn()
    try:
        with conn:
            session = conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
            if not session:
                raise ValueError('课次不存在')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='weekly_lessons'").fetchone() and conn.execute('SELECT 1 FROM weekly_lessons WHERE session_id=?',(sid,)).fetchone():
                raise ValueError('本课由固定周课表生成，请在“固定周课表”中修改课程，历史日期保持不变')
            used = conn.execute('SELECT COUNT(*) FROM course_punches WHERE session_id=?',(sid,)).fetchone()[0]
            leaves = conn.execute('SELECT COUNT(*) FROM course_leaves WHERE session_id=?',(sid,)).fetchone()[0]
            if used or leaves:
                raise ValueError('已有打卡或请假记录的课次不能修改时间或班级，请新建课次')
            conn.execute('UPDATE course_sessions SET title=?,class_name=?,start_time=?,end_time=?,grace_minutes=?,lead_minutes=? WHERE id=?', (*values,sid))
            conn.execute('DELETE FROM course_roster WHERE session_id=?',(sid,))
            session = conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
            if not _roster(conn,session):
                raise ValueError('所选班级没有学生')
    finally:
        conn.close()


def delete_session(sid):
    conn = db._conn()
    try:
        with conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='weekly_lessons'").fetchone() and conn.execute('SELECT 1 FROM weekly_lessons WHERE session_id=?',(sid,)).fetchone():
                raise ValueError('本课由固定周课表自动生成，请在“固定周课表”中暂停课程；已生成课次保留')
            if conn.execute('SELECT COUNT(*) FROM course_punches WHERE session_id=?',(sid,)).fetchone()[0]:
                raise ValueError('该课次有打卡历史，不能删除；可保留查询或先清空打卡历史')
            for table in ('course_roster','course_leaves','course_sessions'):
                conn.execute('DELETE FROM '+table+' WHERE '+('id' if table=='course_sessions' else 'session_id')+'=?',(sid,))
    finally:
        conn.close()


def set_leave(sid, card, leave_type, reason):
    if leave_type not in ('事假','病假','其他'):
        raise ValueError('请选择请假类型')
    reason = reason.strip()
    if not reason or len(reason)>300:
        raise ValueError('请填写1–300字的请假原因')
    conn = db._conn()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM course_roster WHERE session_id=? AND card_id=?',(sid,card)).fetchone():
                raise ValueError('该学生不在本课次名单中')
            if conn.execute('SELECT 1 FROM course_punches p JOIN records r ON r.id=p.record_id WHERE p.session_id=? AND r.card_id=?',(sid,card)).fetchone():
                raise ValueError('该学生已有本课打卡，按实际出勤计算，不能再登记请假')
            conn.execute('INSERT INTO course_leaves VALUES(?,?,?,?,?) ON CONFLICT(session_id,card_id) DO UPDATE SET leave_type=excluded.leave_type,reason=excluded.reason,created_at=excluded.created_at',
                (sid,card,leave_type,reason,datetime.now().strftime(FORMAT)))
    finally:
        conn.close()


def revoke_leave(sid, card):
    conn = db._conn()
    try:
        with conn:
            return conn.execute('DELETE FROM course_leaves WHERE session_id=? AND card_id=?',(sid,card)).rowcount
    finally:
        conn.close()


def attendance(sid, now=None):
    now = now or datetime.now()
    conn = db._conn()
    try:
        session = conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
        if not session:
            return []
        rows = conn.execute('''WITH punches AS (
            SELECT r.card_id, MIN(CASE WHEN r.type='签到' THEN r.time END) first_in,
            MAX(CASE WHEN r.type='签退' THEN r.time END) last_out,COUNT(*) punches
            FROM course_punches p JOIN records r ON r.id=p.record_id WHERE p.session_id=? GROUP BY r.card_id)
            SELECT s.*,l.leave_type,l.reason,p.first_in,p.last_out,COALESCE(p.punches,0) punches
            FROM course_roster s LEFT JOIN course_leaves l ON l.session_id=s.session_id AND l.card_id=s.card_id
            LEFT JOIN punches p ON p.card_id=s.card_id
            WHERE s.session_id=? ORDER BY s.class_name,s.student_no''',(sid,sid)).fetchall()
        start,end = datetime.strptime(session['start_time'],FORMAT),datetime.strptime(session['end_time'],FORMAT)
        result = []
        for row in rows:
            item = dict(row)
            minutes = 0
            if row['first_in']:
                first = datetime.strptime(row['first_in'],FORMAT)
                status = '迟到' if first > start+timedelta(minutes=session['grace_minutes']) else '正常'
                if status == '迟到':
                    minutes = math.ceil((first-start).total_seconds()/60)
            elif row['last_out']:
                status = '缺签到'
            elif row['leave_type']:
                status = '请假'
            elif now < start:
                status = '未开始'
            elif now <= end:
                status = '待签到'
            else:
                status = '缺勤'
            item.update(status=status, late_minutes=minutes,
                leave_note=(row['leave_type']+'：'+row['reason']) if row['leave_type'] else '',
                presence='已签退' if row['last_out'] else ('在场' if row['first_in'] else '—'))
            result.append(item)
        return result
    finally:
        conn.close()


def punch(sid, card, now=None, min_interval=0, action='签到'):
    """Atomically write a normal record and lesson linkage. Lesson state is independent."""
    now = (now or datetime.now()).replace(microsecond=0)
    if action not in ('签到','签退'):
        raise ValueError('请选择签到或签退功能')
    conn = db._conn()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            session = conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
            if not session:
                raise ValueError('当前课次不存在，请重新选择')
            student = conn.execute('SELECT * FROM course_roster WHERE session_id=? AND card_id=?',(sid,card)).fetchone()
            if not student:
                raise ValueError('该学生不在本课次名单中，请检查班级或同步名单')
            start,end = datetime.strptime(session['start_time'],FORMAT),datetime.strptime(session['end_time'],FORMAT)
            last = conn.execute('SELECT r.* FROM records r JOIN course_punches p ON r.id=p.record_id WHERE p.session_id=? AND r.card_id=? ORDER BY r.id DESC LIMIT 1',(sid,card)).fetchone()
            first = conn.execute("SELECT MIN(r.time) FROM records r JOIN course_punches p ON r.id=p.record_id WHERE p.session_id=? AND r.card_id=? AND r.type='签到'",(sid,card)).fetchone()[0]
            signed_out = conn.execute("SELECT 1 FROM records r JOIN course_punches p ON r.id=p.record_id WHERE p.session_id=? AND r.card_id=? AND r.type='签退' LIMIT 1",(sid,card)).fetchone()
            if now < start-timedelta(minutes=session['lead_minutes']):
                raise ValueError(f"尚未到签到时间，可在课前{session['lead_minutes']}分钟开始签到")
            if now > end+timedelta(hours=2):
                raise ValueError('该课次已结束超过2小时，请关闭课程模式或选择其他课次')
            if action=='签到' and first:
                raise ValueError('本课已签到；需要签退时请切换到“签退登记”')
            if action=='签退' and signed_out:
                raise ValueError('本课已登记签退，请勿重复刷卡')
            if action=='签到' and not first and now > end:
                raise ValueError('该课次已下课，不能再补刷首次签到')
            if last and (now-datetime.strptime(last['time'],FORMAT)).total_seconds() < min_interval*60:
                raise ValueError(f'距本课上次打卡未满{min_interval}分钟')
            kind = action
            stamp = now.strftime(FORMAT)
            rid = conn.execute('INSERT INTO records(card_id,type,time) VALUES(?,?,?)',(card,kind,stamp)).lastrowid
            conn.execute('INSERT INTO course_punches VALUES(?,?)',(sid,rid))
            first_time = datetime.strptime(first,FORMAT) if first else now
            late = bool(first or action=='签到') and first_time > start+timedelta(minutes=session['grace_minutes'])
            return kind, stamp, late
    finally:
        conn.close()
