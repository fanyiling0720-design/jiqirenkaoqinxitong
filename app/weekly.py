"""Fixed weekly timetables: dated lessons are generated idempotently on open/scan."""
from datetime import datetime, timedelta
import database as db
import courses

WEEKDAYS = ['星期一','星期二','星期三','星期四','星期五','星期六','星期日']


def init_db():
    conn = db._conn()
    try:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS weekly_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                class_name TEXT NOT NULL, weekday INTEGER NOT NULL,
                start_clock TEXT NOT NULL,end_clock TEXT NOT NULL,
                grace_minutes INTEGER NOT NULL,lead_minutes INTEGER NOT NULL,
                first_date TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS weekly_lessons (
                rule_id INTEGER NOT NULL,lesson_date TEXT NOT NULL,
                session_id INTEGER NOT NULL UNIQUE,
                PRIMARY KEY(rule_id,lesson_date)
            );
            CREATE TABLE IF NOT EXISTS weekly_preferences (
                key TEXT PRIMARY KEY,value INTEGER NOT NULL
            );
        ''')
    finally:
        conn.close()


def auto_enabled():
    conn = db._conn()
    try:
        row = conn.execute("SELECT value FROM weekly_preferences WHERE key='auto'").fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def set_auto(value):
    conn = db._conn()
    try:
        with conn:
            conn.execute("INSERT INTO weekly_preferences VALUES('auto',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(int(bool(value)),))
    finally:
        conn.close()


def list_rules():
    conn = db._conn()
    try:
        return conn.execute('SELECT * FROM weekly_rules ORDER BY enabled DESC,weekday,start_clock,id').fetchall()
    finally:
        conn.close()


def _validate(title, cls, weekday, start, end, grace, lead, now):
    try:
        weekday = int(weekday)
        if not 0 <= weekday <= 6:
            raise ValueError()
        for clock in (start,end):
            if datetime.strptime(clock,'%H:%M').strftime('%H:%M') != clock:
                raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('请选择星期，时间请用 HH:MM 格式，例如 14:00')
    day = now.date()+timedelta(days=(weekday-now.weekday())%7)
    if datetime.strptime(day.isoformat()+' '+end,'%Y-%m-%d %H:%M') <= now:
        day += timedelta(days=7)
    values = courses._validate(title,cls,day.isoformat()+' '+start+':00',day.isoformat()+' '+end+':00',grace,lead)
    return values[0],values[1],weekday,start,end,values[4],values[5],day.isoformat()


def _ensure(conn, now):
    # Materialize all planned dates through next week; previous lessons stay intact.
    horizon = now.date()+timedelta(days=13-now.weekday())
    for rule in conn.execute('SELECT * FROM weekly_rules WHERE enabled=1').fetchall():
        day = datetime.strptime(rule['first_date'],'%Y-%m-%d').date()
        existing = {r[0] for r in conn.execute('SELECT lesson_date FROM weekly_lessons WHERE rule_id=?',(rule['id'],))}
        while day <= horizon:
            value = day.isoformat()
            if value not in existing:
                sid = conn.execute('''INSERT INTO course_sessions(title,class_name,start_time,end_time,grace_minutes,lead_minutes)
                    VALUES(?,?,?,?,?,?)''',(rule['title'],rule['class_name'],value+' '+rule['start_clock']+':00',
                        value+' '+rule['end_clock']+':00',rule['grace_minutes'],rule['lead_minutes'])).lastrowid
                conn.execute('INSERT INTO weekly_lessons VALUES(?,?,?)',(rule['id'],value,sid))
                session = conn.execute('SELECT * FROM course_sessions WHERE id=?',(sid,)).fetchone()
                courses._roster(conn,session)
            day += timedelta(days=7)


def ensure_sessions(now=None):
    conn = db._conn()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            _ensure(conn,now or datetime.now())
    finally:
        conn.close()


def save_rule(title, cls, weekday, start, end, grace=5, lead=30, rule_id=None, now=None):
    now = now or datetime.now()
    values = _validate(title,cls,weekday,start,end,grace,lead,now)
    conn = db._conn()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if values[1]=='全部班级':
                count = conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]
            else:
                count = conn.execute("SELECT COUNT(*) FROM students WHERE COALESCE(NULLIF(class_name,''),'未分班')=?",(values[1],)).fetchone()[0]
            if not count:
                raise ValueError('所选班级没有学生，请先登记学生')
            if rule_id is None:
                rule_id = conn.execute('''INSERT INTO weekly_rules(title,class_name,weekday,start_clock,end_clock,
                    grace_minutes,lead_minutes,first_date) VALUES(?,?,?,?,?,?,?,?)''',values).lastrowid
            else:
                if not conn.execute('SELECT 1 FROM weekly_rules WHERE id=?',(rule_id,)).fetchone():
                    raise ValueError('固定课程不存在')
                future = conn.execute('''SELECT s.id FROM weekly_lessons l JOIN course_sessions s ON s.id=l.session_id
                    WHERE l.rule_id=? AND s.start_time>?''',(rule_id,now.strftime(courses.FORMAT))).fetchall()
                for row in future:
                    sid = row[0]
                    if conn.execute('SELECT 1 FROM course_punches WHERE session_id=?',(sid,)).fetchone() or conn.execute('SELECT 1 FROM course_leaves WHERE session_id=?',(sid,)).fetchone():
                        raise ValueError('未来课次已有打卡或请假，不能覆盖；请先处理该课次记录，或暂停本规则后新建固定课程')
                for row in future:
                    sid = row[0]
                    conn.execute('DELETE FROM course_roster WHERE session_id=?',(sid,))
                    conn.execute('DELETE FROM weekly_lessons WHERE session_id=?',(sid,))
                    conn.execute('DELETE FROM course_sessions WHERE id=?',(sid,))
                conn.execute('''UPDATE weekly_rules SET title=?,class_name=?,weekday=?,start_clock=?,end_clock=?,
                    grace_minutes=?,lead_minutes=?,first_date=? WHERE id=?''',(*values,rule_id))
            _ensure(conn,now)
            return rule_id
    finally:
        conn.close()


def set_rule_enabled(rule_id, enabled, now=None):
    conn = db._conn()
    try:
        with conn:
            if enabled:
                rule = conn.execute('SELECT * FROM weekly_rules WHERE id=?',(rule_id,)).fetchone()
                if not rule:
                    raise ValueError('固定课程不存在')
                values = _validate(rule['title'],rule['class_name'],rule['weekday'],rule['start_clock'],rule['end_clock'],rule['grace_minutes'],rule['lead_minutes'],now or datetime.now())
                conn.execute('UPDATE weekly_rules SET enabled=1,first_date=? WHERE id=?',(values[-1],rule_id))
            else:
                conn.execute('UPDATE weekly_rules SET enabled=0 WHERE id=?',(rule_id,))
    finally:
        conn.close()


def lesson_rule(sid):
    conn = db._conn()
    try:
        return conn.execute('SELECT w.* FROM weekly_rules w JOIN weekly_lessons l ON l.rule_id=w.id WHERE l.session_id=?',(sid,)).fetchone()
    finally:
        conn.close()


def recommended_session(now=None):
    now = now or datetime.now()
    conn = db._conn()
    try:
        row = conn.execute('''SELECT s.id FROM course_sessions s JOIN weekly_lessons l ON s.id=l.session_id
            JOIN weekly_rules w ON w.id=l.rule_id WHERE w.enabled=1 AND s.end_time>=?
            ORDER BY s.start_time,s.id LIMIT 1''',((now-timedelta(hours=2)).strftime(courses.FORMAT),)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def resolve_for_card(card, now=None, action='签到'):
    if action not in ('签到','签退'):
        raise ValueError('请选择签到或签退功能')
    now = now or datetime.now()
    ensure_sessions(now)
    conn = db._conn()
    try:
        sessions = conn.execute('''SELECT s.* FROM course_sessions s JOIN weekly_lessons l ON s.id=l.session_id
            JOIN weekly_rules w ON w.id=l.rule_id WHERE w.enabled=1 AND s.start_time<=? AND s.end_time>=?
            ORDER BY s.start_time,s.id''',((now+timedelta(minutes=120)).strftime(courses.FORMAT),
                (now-timedelta(hours=2)).strftime(courses.FORMAT))).fetchall()
    finally:
        conn.close()
    choices, completed = [],[]
    for session in sessions:
        start = datetime.strptime(session['start_time'],courses.FORMAT)
        end = datetime.strptime(session['end_time'],courses.FORMAT)
        if not start-timedelta(minutes=session['lead_minutes']) <= now <= end+timedelta(hours=2):
            continue
        courses.sync_roster(session['id'])
        student = next((r for r in courses.attendance(session['id'],now) if r['card_id']==card),None)
        if not student:
            continue
        already_done = student['first_in'] if action=='签到' else student['last_out']
        if already_done:
            completed.append(session['id'])
        elif action=='签退' or now<=end:
            choices.append(session['id'])
    if len(choices)>1:
        raise ValueError('同一时段匹配到多个课次，请到课程页选择具体课次并“启用本课刷卡”')
    if choices:
        return choices[0]
    if completed:
        raise ValueError('当前固定课程已登记'+action+'，请勿重复刷卡')
    raise ValueError('当前没有该学生可'+action+'的固定课程，请检查星期、时间和班级；不会转为日常记录')
