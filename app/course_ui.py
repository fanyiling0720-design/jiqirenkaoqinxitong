"""Lesson attendance page and small lesson/leave dialogs."""
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import config
import courses
import weekly
import database as db
from dashboard import label, panel, BG, WHITE, TEXT, MUTED, BLUE, GREEN, ORANGE, RED, FONT


class CoursePage:
    def __init__(self, app, page):
        self.app = app
        self.page = page
        self.sid = None
        self.follow_current = True
        self.rows = []
        self.session_var = tk.StringVar()
        self.status_var = tk.StringVar(value='全部状态')
        self.search_var = tk.StringVar()
        self.session_map = {}
        row = tk.Frame(page,bg=BG)
        row.pack(fill='x',pady=(0,12))
        label(row,'选择课次',10,MUTED,bg=BG).pack(side='left',padx=(0,10))
        self.combo = ttk.Combobox(row,textvariable=self.session_var,state='readonly',width=57)
        self.combo.pack(side='left',fill='x',expand=True,padx=(0,12))
        self.combo.bind('<<ComboboxSelected>>',lambda _:self.select())
        app.button(row,'固定周课表',self.manage_weekly,primary=True,side='right')
        actions = tk.Frame(page,bg=BG)
        actions.pack(fill='x',pady=(0,12))
        for title, action in [('新建课程',self.new_session),('编辑课程',self.edit_session),('删除课次',self.delete_session),
                              ('同步名单',self.sync),('请假登记',self.leave_dialog),('撤销请假',self.revoke)]:
            app.button(actions,title,action,primary=title=='新建课程')
        box,body = panel(page)
        box.pack(fill='x',pady=(0,12))
        self.info = tk.StringVar(value='先新建课次，设置课程时间和班级')
        label(body,textvariable=self.info,size=10,color=TEXT,anchor='w',justify='left',wraplength=790).pack(fill='x')
        bar = tk.Frame(body,bg=WHITE)
        bar.pack(fill='x',pady=(10,0))
        app.button(bar,'启用本课刷卡',self.activate,True)
        app.button(bar,'周课表自动',app.enable_weekly)
        app.button(bar,'返回日常考勤',app.disable_course)
        self.active_var = tk.StringVar(value='当前：日常考勤')
        label(bar,textvariable=self.active_var,size=9,color=BLUE).pack(side='left',padx=8)
        filters = tk.Frame(page,bg=BG)
        filters.pack(fill='x',pady=(0,10))
        ttk.Combobox(filters,textvariable=self.status_var,state='readonly',width=11,
                     values=['全部状态','正常','迟到','请假','待签到','缺签到','缺勤','未开始']).pack(side='left',padx=(0,10))
        label(filters,'搜索',10,MUTED,bg=BG).pack(side='left',padx=(0,8))
        ttk.Entry(filters,textvariable=self.search_var,width=16).pack(side='left',padx=(0,10))
        app.button(filters,'刷新',self.refresh_status)
        app.button(filters,'导出缺勤名单',self.export_absent,side='right')
        app.button(filters,'导出本课报表',self.export_all,side='right')
        self.summary = tk.StringVar(value='尚无课次')
        label(page,textvariable=self.summary,size=10,color=MUTED,bg=BG).pack(anchor='w',pady=(0,10))
        box,body = panel(page)
        box.pack(fill='both',expand=True)
        self.tree = app.table(body,[('student_no','学号',95),('name','姓名',80),('class_name','班级',100),
            ('status','结果',75),('late_minutes','迟到分钟',75),('first_in','首次签到',155),
            ('last_out','签退时间',155),('leave_note','请假说明',160),('card_id','卡号',170)])
        for tag,color in [('正常',GREEN),('迟到',ORANGE),('缺勤',RED),('请假',BLUE)]:
            self.tree.tag_configure(tag,foreground=color)
        self.tree.bind('<Double-1>',lambda _:self.leave_dialog())
        for var in (self.status_var,self.search_var):
            var.trace_add('write',lambda *_:self.refresh_status())
        self._minute = ''

    def refresh(self):
        sessions = courses.list_sessions()
        self.session_map = {f"#{s['id']}  {s['start_time'][:16]}  ·  {s['title']}  /  {s['class_name']}":s['id'] for s in sessions}
        self.combo['values'] = list(self.session_map)
        recommended = weekly.recommended_session() if self.follow_current else None
        if recommended:
            self.sid = recommended
        elif self.sid not in self.session_map.values():
            self.sid = sessions[0]['id'] if sessions else None
        self.session_var.set(next((k for k,v in self.session_map.items() if v==self.sid),''))
        active = courses.get_session(self.app.active_session_id) if self.app.active_session_id else None
        self.active_var.set('当前：周课表自动匹配' if self.app.auto_weekly else (f"当前课次：#{active['id']} {active['title'][:12]}" if active else '当前：日常考勤'))
        self.refresh_status()

    def select(self, sid=None):
        self.follow_current = False
        self.sid = sid if sid is not None else self.session_map.get(self.session_var.get())
        self.status_var.set('全部状态')
        self.search_var.set('')
        self.refresh()

    def selected_session(self):
        session = courses.get_session(self.sid) if self.sid else None
        if not session:
            messagebox.showinfo('选择课次','请先新建或选择一个课次',parent=self.app.root)
        return session

    def refresh_status(self):
        self._minute = datetime.now().strftime('%Y-%m-%d %H:%M')
        session = courses.get_session(self.sid) if self.sid else None
        if not session:
            self.info.set('暂无课程，请新建课程或设置固定周课表')
            self.rows = []
        else:
            rule = weekly.lesson_rule(self.sid)
            repeat = f"（固定周课表 · {weekly.WEEKDAYS[datetime.strptime(session['start_time'],courses.FORMAT).weekday()]}）" if rule else '（单次课程）'
            self.info.set(f"{session['title']}  /  {session['class_name']} {repeat}\n{session['start_time']} — {session['end_time']}  ·  课前 {session['lead_minutes']} 分钟签到 / 宽限 {session['grace_minutes']} 分钟")
            self.rows = courses.attendance(self.sid)
        counts = {s:sum(r['status']==s for r in self.rows) for s in ['正常','迟到','请假','待签到','缺签到','缺勤','未开始']}
        self.summary.set('应到 '+str(len(self.rows))+' 人  /  '+'  ·  '.join(f'{s} {n}' for s,n in counts.items()))
        keyword = self.search_var.get().strip().casefold()
        visible = [r for r in self.rows if (self.status_var.get()=='全部状态' or r['status']==self.status_var.get())
                   and (not keyword or keyword in ' '.join(str(r[k]) for k in ('student_no','name','class_name','card_id')).casefold())]
        self.app.fill(self.tree,visible,['student_no','name','class_name','status','late_minutes','first_in','last_out','leave_note','card_id'],True)

    def new_session(self):
        self.session_dialog()

    def manage_weekly(self):
        from weekly_ui import manage
        return manage(self.app)

    def edit_session(self):
        session = self.selected_session()
        if session:
            self.session_dialog(session)

    def session_dialog(self, session=None):
        # All creation/edit entry points now use the weekday form.
        from weekly_ui import editor
        if session:
            rule = weekly.lesson_rule(session['id'])
            if rule:
                return editor(self.app,rule)
            start = datetime.strptime(session['start_time'],courses.FORMAT)
            return editor(self.app,prefill=dict(title=session['title'],class_name=session['class_name'],
                weekday=weekly.WEEKDAYS[start.weekday()],start_clock=session['start_time'][11:16],
                end_clock=session['end_time'][11:16],grace=str(session['grace_minutes']),lead=str(session['lead_minutes'])))
        return editor(self.app)

    def activate(self):
        session = self.selected_session()
        if session:
            self.app.auto_weekly = False
            weekly.set_auto(False)
            self.app.active_session_id = self.sid
            self.app.scan_times.clear()
            self.app.mode_var.set(f"课程刷卡：{session['title'][:22]}  /  {session['class_name']}")
            self.app.notice.set('课程已启用：请在签到登记或签退登记页刷卡；历史页只查询')
            self.refresh()
            self.app.show('home')

    def sync(self):
        if self.selected_session():
            try:
                count = courses.sync_roster(self.sid)
                self.refresh()
                self.app.notice.set(f'已补入 {count} 名学生；历史名单和出勤记录保持不变')
            except (ValueError,sqlite3.Error) as exc:
                messagebox.showerror('同步失败',str(exc),parent=self.app.root)

    def delete_session(self):
        session = self.selected_session()
        if not session:
            return
        if self.app.active_session_id==self.sid:
            messagebox.showinfo('当前课次','请先返回日常考勤，再删除此课次',parent=self.app.root)
            return
        if messagebox.askyesno('删除课次',f"删除“{session['title']}”及本课请假登记？\n有打卡历史的课次不能删除。操作前自动备份。",parent=self.app.root) and self.app.auto_backup():
            try:
                courses.delete_session(self.sid)
                self.sid = None
                self.refresh()
            except (ValueError,sqlite3.Error) as exc:
                messagebox.showerror('删除失败',str(exc),parent=self.app.root)

    def selected_card(self):
        selected = self.tree.selection()
        return self.tree.item(selected[0],'values')[-1] if selected else None

    def leave_dialog(self):
        if not self.selected_session():
            return
        roster = courses.attendance(self.sid)
        if not roster:
            return
        sid = self.sid
        win = tk.Toplevel(self.app.root)
        win.title('课程请假登记')
        win.geometry('570x400')
        win.configure(bg=WHITE)
        win.resizable(False,False)
        win.transient(self.app.root)
        win.grab_set()
        label(win,'登记本课请假',17,bold=True).pack(anchor='w',padx=26,pady=(20,10))
        label(win,'教师直接登记生效；已有打卡时按实际出勤计算。',10,MUTED).pack(anchor='w',padx=26,pady=(0,12))
        options = {f"{r['name']} / {r['student_no']} / {r['class_name']}":r for r in roster}
        selected = self.selected_card()
        initial = next((k for k,v in options.items() if v['card_id']==selected),next(iter(options)))
        who = tk.StringVar(value=initial)
        ttk.Combobox(win,textvariable=who,values=list(options),state='readonly',width=52).pack(fill='x',padx=26,pady=6)
        kind = tk.StringVar(value=options[initial]['leave_type'] or '事假')
        ttk.Combobox(win,textvariable=kind,values=['事假','病假','其他'],state='readonly').pack(fill='x',padx=26,pady=6)
        reason = tk.Text(win,height=4,font=(FONT,10),relief='solid',bd=1)
        reason.pack(fill='x',padx=26,pady=6)
        reason.insert('1.0',options[initial]['reason'] or '')
        def load(_=None):
            row = options[who.get()]
            kind.set(row['leave_type'] or '事假')
            reason.delete('1.0','end')
            reason.insert('1.0',row['reason'] or '')
        who.trace_add('write',lambda *_:load())
        def save():
            try:
                if not self.app.auto_backup():
                    return
                courses.set_leave(sid,options[who.get()]['card_id'],kind.get(),reason.get('1.0','end').strip())
                self.refresh()
                win.destroy()
                self.app.notice.set('请假已登记，仅对所选课次生效')
            except (ValueError,sqlite3.Error) as exc:
                messagebox.showerror('登记失败',str(exc),parent=win)
        row = tk.Frame(win,bg=WHITE)
        row.pack(fill='x',padx=26,pady=14)
        self.app.button(row,'保存请假',save,True)
        self.app.button(row,'取消',win.destroy)
        return win

    def revoke(self):
        card = self.selected_card()
        if not card:
            messagebox.showinfo('选择学生','请先选中需要撤销请假的学生',parent=self.app.root)
            return
        if messagebox.askyesno('撤销请假','撤销所选学生的本课请假？撤销后按实际签到情况计算。',parent=self.app.root) and self.app.auto_backup():
            try:
                courses.revoke_leave(self.sid,card)
                self.refresh()
            except sqlite3.Error as exc:
                messagebox.showerror('撤销失败',str(exc),parent=self.app.root)

    def export_rows(self, absent=False):
        session = self.selected_session()
        if not session:
            return
        self.refresh_status()
        rows = [r for r in self.rows if r['status']=='缺勤'] if absent else self.rows
        if absent and datetime.now()<=datetime.strptime(session['end_time'],courses.FORMAT):
            messagebox.showinfo('课程尚未结束','下课后才认定缺勤。现在可以导出本课报表查看待签到名单。',parent=self.app.root)
            return
        headers = ['课程','上课时间','下课时间','学号','姓名','班级','结果','迟到分钟','首次签到','签退时间','请假说明']
        values = [[session['title'],session['start_time'],session['end_time']]+[r[k] for k in ('student_no','name','class_name','status','late_minutes','first_in','last_out','leave_note')] for r in rows]
        self.app.export('课程缺勤名单' if absent else '课程考勤报表',headers,values)

    def export_absent(self):
        self.export_rows(True)

    def export_all(self):
        self.export_rows(False)

    def open_demo(self):
        if getattr(config,'DEMO_MODE',False):
            self.app.notice.set('当前已在独立演示模式，可选择已结束课次查看缺勤结果')
            return
        try:
            subprocess.Popen([sys.executable,str(Path(__file__).with_name('demo.py'))],
                cwd=str(Path(__file__).resolve().parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.app.notice.set('已打开独立演示窗口；示例数据不会写入真实数据库')
        except OSError as exc:
            messagebox.showerror('演示启动失败',str(exc),parent=self.app.root)
