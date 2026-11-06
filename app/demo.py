"""Isolated, disposable demonstration database. Never opens attendance.db."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import tkinter as tk
import config
import database as db
import courses
import services
import weekly


def seed_demo(now=None):
    now = (now or datetime.now()).replace(second=0,microsecond=0)
    db.init_db()
    courses.init_db()
    weekly.init_db()
    db.import_students([(f'DEMO{i:04}',f'202600{i}',name,'机器人一班') for i,name in enumerate(
        ['林小满','陈星河','许知远','沈若宁','周知夏','陆嘉言'],1)])
    db.add_student('DEMO0007','2026007','江予安','机器人二班')
    weekly.save_rule('机器人固定周课示例','机器人一班',now.weekday(),'14:00','15:00',now=now)
    def create(title,start,end):
        return courses.create_session(title,'机器人一班',start.strftime(courses.FORMAT),end.strftime(courses.FORMAT),5,30)
    old_start,old_end = now-timedelta(hours=3),now-timedelta(hours=2)
    past = create('传感器与机器人 · 已结束示例',old_start,old_end)
    live_start,live_end = now-timedelta(minutes=25),now+timedelta(minutes=45)
    live = create('机器人编程实践 · 进行中示例',live_start,live_end)
    for sid,start,end in [(past,old_start,old_end),(live,live_start,live_end)]:
        courses.punch(sid,'DEMO0001',start-timedelta(minutes=3))
        courses.punch(sid,'DEMO0002',start+timedelta(minutes=2))
        courses.punch(sid,'DEMO0003',start+timedelta(minutes=12))
        courses.set_leave(sid,'DEMO0004','病假','身体不适，已向老师说明')
        if sid==past:
            for card in ['DEMO0001','DEMO0002','DEMO0003']:
                courses.punch(sid,card,end+timedelta(minutes=2),action='签退')
    value = services.load_settings()
    value.update(sound=False,voice=False)
    services.save_settings(value)
    return past,live


def main():
    # A unique database per window avoids interference between parallel demos.
    folder = Path(__file__).resolve().parent/'demo_data'
    folder.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='session_',dir=folder) as temporary:
        config.DB_PATH = str(Path(temporary)/'demo.db')
        config.DEMO_MODE = True
        past,live = seed_demo()
        from dashboard import AttendanceApp
        root = tk.Tk()
        app = AttendanceApp(root)
        root.title('教育机器人考勤系统 · 独立演示（不修改真实数据）')
        app.show('courses')
        app.course_page.select(past)
        app.notice.set('演示模式：已结束课次展示正常、迟到、请假、缺勤；切换进行中课次可体验刷卡')
        root.mainloop()


if __name__=='__main__':
    main()
