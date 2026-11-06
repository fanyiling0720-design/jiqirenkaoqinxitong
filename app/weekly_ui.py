"""Set a weekly course without typing dates each week."""
from datetime import datetime, timedelta
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
import weekly
import database as db
from dashboard import label, WHITE, MUTED, FONT


def manage(app):
    win = tk.Toplevel(app.root)
    win.title('固定周课表 · 日期自动更新')
    win.geometry('1000x480')
    win.minsize(900,420)
    win.configure(bg=WHITE)
    win.transient(app.root)
    win.grab_set()
    label(win,'固定周课表',17,bold=True).pack(anchor='w',padx=22,pady=(18,6))
    label(win,'每周按固定时间上课，日期自动更新。',10,MUTED).pack(anchor='w',padx=22,pady=(0,14))
    holder = tk.Frame(win,bg=WHITE)
    holder.pack(fill='both',expand=True,padx=22)
    tree = app.table(holder,[('title','课程名称',170),('class_name','班级',120),('weekday','每周',85),
        ('start_clock','上课',80),('end_clock','下课',80),('state','状态',75),('next_date','下次日期',130)])
    def refresh():
        tree.delete(*tree.get_children())
        for rule in weekly.list_rules():
            now = datetime.now()
            day = now.date()+timedelta(days=(rule['weekday']-now.weekday())%7)
            if datetime.strptime(day.isoformat()+' '+rule['end_clock'],'%Y-%m-%d %H:%M')<now:
                day += timedelta(days=7)
            tree.insert('','end',iid=str(rule['id']),values=[rule['title'],rule['class_name'],weekly.WEEKDAYS[rule['weekday']],
                rule['start_clock'],rule['end_clock'],'启用' if rule['enabled'] else '已暂停',day.isoformat() if rule['enabled'] else '—'])
    def selected():
        ids = tree.selection()
        if not ids:
            messagebox.showinfo('选择课程','请先选中一门固定课程',parent=win)
            return None
        return next((r for r in weekly.list_rules() if r['id']==int(ids[0])),None)
    def edit():
        rule = selected()
        if rule:
            editor(app,rule,parent=win,on_saved=refresh)
    def toggle():
        rule = selected()
        if not rule:
            return
        if not rule['enabled'] or messagebox.askyesno('暂停固定课程','暂停后不再自动排新课或自动匹配刷卡；已生成课次、打卡和请假保留。',parent=win):
            if not app.auto_backup():
                return
            weekly.set_rule_enabled(rule['id'],not rule['enabled'])
            app.refresh()
            refresh()
    row = tk.Frame(win,bg=WHITE)
    row.pack(side='bottom',before=holder,fill='x',padx=22,pady=16)
    app.button(row,'新建固定课程',lambda:editor(app,parent=win,on_saved=refresh),True)
    app.button(row,'编辑选中',edit)
    app.button(row,'暂停 / 启用',toggle)
    app.button(row,'启用自动周课表',app.enable_weekly)
    app.button(row,'关闭',win.destroy,side='right')
    tree.bind('<Double-1>',lambda _:edit())
    refresh()
    return win


def editor(app, rule=None, parent=None, on_saved=None, prefill=None):
    parent = parent or app.root
    win = tk.Toplevel(parent)
    win.title('编辑固定课程' if rule else '新建固定课程')
    win.geometry('600x600')
    win.resizable(False,False)
    win.configure(bg=WHITE)
    win.transient(parent)
    win.grab_set()
    label(win,'每周固定上课，日期自动更新',17,bold=True).pack(anchor='w',padx=26,pady=(20,8))
    label(win,'按原课次填写每周课程，原有课次与记录保留。' if prefill else '设置课程、星期和上课时间即可。',10,MUTED).pack(anchor='w',padx=26,pady=(0,14))
    values = dict(title='',class_name='全部班级',weekday=weekly.WEEKDAYS[datetime.now().weekday()],
                  start_clock='14:00',end_clock='15:00',grace='5',lead='30')
    if prefill:
        values.update(prefill)
    if rule:
        values.update(title=rule['title'],class_name=rule['class_name'],weekday=weekly.WEEKDAYS[rule['weekday']],
                      start_clock=rule['start_clock'],end_clock=rule['end_clock'],grace=str(rule['grace_minutes']),lead=str(rule['lead_minutes']))
    fields = {k:tk.StringVar(value=v) for k,v in values.items()}
    form = tk.Frame(win,bg=WHITE)
    form.pack(fill='x',padx=26)
    form.columnconfigure(1,weight=1)
    classes = ['全部班级']+sorted({s['class_name'] or '未分班' for s in db.list_students()})
    for i,(key,title) in enumerate([('title','课程名称'),('class_name','上课班级'),('weekday','每周几上课'),
            ('start_clock','上课时间 HH:MM'),('end_clock','下课时间 HH:MM'),('grace','迟到宽限（分钟）'),('lead','提前签到（分钟）')]):
        label(form,title,10,MUTED).grid(row=i,column=0,sticky='w',padx=(0,20),pady=6)
        choices = classes if key=='class_name' else weekly.WEEKDAYS if key=='weekday' else None
        entry = ttk.Combobox(form,textvariable=fields[key],values=choices,state='readonly') if choices else ttk.Entry(form,textvariable=fields[key])
        entry.grid(row=i,column=1,sticky='ew',pady=6)
    automatic = tk.BooleanVar(value=app.auto_weekly if rule else True)
    tk.Checkbutton(win,text='保存后启用自动周课表，重启程序后仍生效',variable=automatic,
                   bg=WHITE,font=(FONT,10)).pack(anchor='w',padx=26,pady=(10,4))
    label(win,'同日上课和下课；修改只影响尚未开始且没有打卡或请假的课次。',9,MUTED).pack(anchor='w',padx=26)
    def save():
        try:
            if rule and app.active_session_id:
                active = weekly.lesson_rule(app.active_session_id)
                if active and active['id']==rule['id']:
                    raise ValueError('请先返回日常考勤，再修改当前手动启用的固定课程')
            if rule and not app.auto_backup():
                return
            weekly.save_rule(fields['title'].get(),fields['class_name'].get(),weekly.WEEKDAYS.index(fields['weekday'].get()),
                fields['start_clock'].get().strip(),fields['end_clock'].get().strip(),fields['grace'].get(),fields['lead'].get(),
                rule_id=rule['id'] if rule else None)
            if automatic.get():
                app.enable_weekly()
            app.course_page.follow_current = True
            app.refresh()
            if on_saved:
                on_saved()
            win.destroy()
            if parent!=app.root:
                parent.grab_set()
            app.notice.set('固定课程已保存：日期会随星期自动更新；往期记录保留')
        except (ValueError,sqlite3.Error) as exc:
            messagebox.showerror('保存固定课程失败',str(exc),parent=win)
    def cancel():
        win.destroy()
        if parent!=app.root:
            parent.grab_set()
    row = tk.Frame(win,bg=WHITE)
    row.pack(side='bottom',before=form,fill='x',padx=26,pady=16)
    app.button(row,'保存固定课程',save,True)
    app.button(row,'取消',cancel)
    win.protocol('WM_DELETE_WINDOW',cancel)
    return win
