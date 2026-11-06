"""教育机器人考勤系统 2.0 · 工作台界面。"""
import queue
import sqlite3
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import config
import database as db
import services
import courses
import weekly
from rfid_reader import UHFReader18
from speech import SpeechAnnouncer
from branding import robot_mark, nav_mark

FONT = 'Microsoft YaHei UI'
BG, WHITE, NAV = '#F2F5FA', '#FFFFFF', '#14243B'
TEXT, MUTED, BORDER = '#1C304B', '#718097', '#E2E8F1'
BLUE, GREEN, ORANGE, RED = '#4165E8', '#109881', '#CB8131', '#D45566'


def label(parent, text='', size=10, color=TEXT, bold=False, bg=WHITE, **kw):
    size = {9:10, 10:11, 12:13}.get(size,size)
    return tk.Label(parent, text=text, bg=bg, fg=color,
                    font=(FONT, size, 'bold' if bold else 'normal'), **kw)


def panel(parent, title=None, subtitle=None):
    outer = tk.Frame(parent, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
    body = tk.Frame(outer, bg=WHITE)
    body.pack(fill='both', expand=True, padx=16, pady=10)
    if title:
        label(body, title, 13, bold=True).pack(anchor='w')
    if subtitle:
        label(body, subtitle, 9, MUTED).pack(anchor='w', pady=(4, 10))
    return outer, body


class AttendanceApp:
    def __init__(self, root):
        self.root = root
        # Keep layout consistent on Windows displays with large system scaling.
        root.tk.call('tk', 'scaling', 4 / 3)
        root.title('教育机器人考勤系统（最终）')
        root.geometry('1280x860')
        root.minsize(1120, 760)
        root.configure(bg=BG)
        try:
            root.iconbitmap(str(Path(__file__).with_name('robot.ico')))
        except tk.TclError:
            pass
        self.reader = None
        self.q = queue.Queue()
        self.speech = SpeechAnnouncer(lambda kind, text: self.q.put((kind, text)))
        self.scan_times = {}
        self.scan_action = '签到'
        self.home_action = '签到'
        self.home_history_card = None
        self._capture_session = None
        self.last_seen_card = None
        self.active_session_id = None
        self._closing = False
        self._after = None
        self.settings = services.load_settings()
        config.MIN_INTERVAL_MINUTES = self.settings['interval']
        self._styles()
        db.init_db()
        courses.init_db()
        weekly.init_db()
        weekly.ensure_sessions()
        self.auto_weekly = weekly.auto_enabled()
        self._build_ui()
        self._refresh_ports()
        self.refresh()
        root.protocol('WM_DELETE_WINDOW', self.close)
        self._poll()

    def _styles(self):
        s = ttk.Style(self.root)
        s.theme_use('clam')
        s.configure('.', font=(FONT, 11))
        s.configure('TButton', padding=(10, 8), background=WHITE, foreground=TEXT,
                    bordercolor=BORDER, focuscolor=WHITE, relief='flat')
        s.map('TButton', background=[('active', '#EDF4FF')])
        s.configure('Primary.TButton', background=BLUE, foreground=WHITE, borderwidth=0)
        s.map('Primary.TButton', background=[('active', '#3154CF'), ('disabled', '#B8C5F0')])
        s.configure('Danger.TButton', foreground=RED)
        s.configure('TEntry', padding=7, fieldbackground=WHITE, bordercolor=BORDER)
        s.configure('TCombobox', padding=6, bordercolor=BORDER, fieldbackground=WHITE, background=WHITE)
        s.map('TCombobox', fieldbackground=[('readonly', WHITE)], selectbackground=[('readonly', WHITE)], selectforeground=[('readonly', TEXT)])
        s.configure('TScrollbar', background='#D8E2EF', troughcolor='#F4F7FB', bordercolor='#F4F7FB', arrowcolor=MUTED)
        s.configure('Treeview', rowheight=40, borderwidth=0, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, background=WHITE,
                    fieldbackground=WHITE, foreground=TEXT, font=(FONT, 11))
        s.configure('Treeview.Heading', background='#F5F8FC', foreground=MUTED,
                    relief='flat', font=(FONT, 10, 'bold'), padding=(8, 10))
        s.map('Treeview', background=[('selected', '#E6F0FF')], foreground=[('selected', '#135AB4')])
        s.map('Treeview.Heading', background=[('active', '#EDF3FA')])

    def button(self, parent, text, command, primary=False, side='left'):
        b = ttk.Button(parent, text=text, command=command, style='Primary.TButton' if primary else 'TButton')
        b.pack(side=side, padx=(0, 8))
        return b

    def table(self, parent, columns):
        holder = tk.Frame(parent, bg=WHITE)
        holder.pack(fill='both', expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=[c[0] for c in columns], show='headings', selectmode='browse')
        for key, title, width in columns:
            tree.heading(key, text=title, command=lambda k=key: self.sort(tree, k))
            tree.column(key, width=width, minwidth=65, anchor='w')
        vs = ttk.Scrollbar(holder, orient='vertical', command=tree.yview)
        hs = ttk.Scrollbar(holder, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vs.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        tree.tag_configure('odd', background='#FAFCFF')
        tree.tag_configure('在场', foreground=GREEN)
        tree.tag_configure('未签到', foreground=ORANGE)
        return tree

    def sort(self, tree, key):
        reverse = getattr(tree, '_reverse', {}).get(key, False)
        items = [(tree.set(i, key), i) for i in tree.get_children()]
        items.sort(key=lambda x: int(x[0] or 0) if key in ('id', 'punches') else x[0], reverse=reverse)
        for n, (_, item) in enumerate(items):
            tree.move(item, '', n)
        tree._reverse = {key: not reverse}

    def fill(self, tree, rows, keys, status=False):
        tree.delete(*tree.get_children())
        for i, row in enumerate(rows):
            tags = (('odd',) if i % 2 else ()) + ((row['status'],) if status else ())
            tree.insert('', 'end', values=[row[k] if row[k] is not None else '—' for k in keys], tags=tags)

    def _build_ui(self):
        sidebar = tk.Frame(self.root, bg=NAV, width=190)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        robot_mark(sidebar,64,NAV).pack(pady=(26,12))
        label(sidebar, '教育机器人', 17, WHITE, True, NAV).pack()
        label(sidebar, '考勤系统', 11, '#9FB2CD', bg=NAV).pack(pady=(4,20))
        tk.Frame(sidebar,bg='#29394F',height=1).pack(fill='x',padx=20,pady=(0,16))
        self.titles = {'home':'考勤首页', 'history':'个人历史查询', 'students':'学生档案', 'today':'今日人员状态', 'courses':'课程考勤与请假', 'settings':'设备与设置'}
        self.nav = {}
        self.nav_rows = {}
        for key, title in self.titles.items():
            row = tk.Frame(sidebar,bg=NAV)
            row.pack(fill='x',padx=10,pady=4)
            icon = nav_mark(row,key,NAV)
            icon.pack(side='left',padx=(10,3))
            b = tk.Button(row, text=title, anchor='w', bg=NAV, fg='#BCCADD',
                activebackground='#28436A', activeforeground=WHITE, bd=0, padx=7, pady=14,
                font=(FONT, 11), cursor='hand2', command=lambda k=key: self.show(k))
            b.pack(side='left',fill='x',expand=True)
            icon.bind('<Button-1>',lambda event,k=key:self.show(k))
            self.nav[key] = b
            self.nav_rows[key] = (row,icon)
        main = tk.Frame(self.root, bg=BG)
        main.pack(side='left', fill='both', expand=True)
        header = tk.Frame(main, bg=WHITE, height=74)
        header.pack(fill='x')
        header.pack_propagate(False)
        self.page_title = label(header, '考勤工作台', 20, bold=True)
        self.page_title.pack(side='left', padx=24)
        self.clock = label(header, '', 10, MUTED)
        self.clock.pack(side='right', padx=(12, 24))
        self.header_status = label(header, '●  设备未连接', 10, ORANGE)
        self.header_status.pack(side='right')
        content = tk.Frame(main, bg=BG)
        content.pack(fill='both', expand=True, padx=22, pady=12)
        self.pages = {k:tk.Frame(content, bg=BG) for k in self.nav}
        self._home(self.pages['home'])
        self.context_tag = label(header,textvariable=self.mode_var,size=10,color=MUTED)
        self._today(self.pages['today'])
        self._students(self.pages['students'])
        self._history(self.pages['history'])
        self._settings(self.pages['settings'])
        from course_ui import CoursePage
        self.course_page = CoursePage(self,self.pages['courses'])
        self.notice = tk.StringVar(value='')
        label(main, textvariable=self.notice, size=9, color=MUTED, bg=BG, anchor='w').pack(side='bottom', before=content, fill='x', padx=24, pady=(0, 10))
        self.show('home')

    def set_home_action(self, action):
        if action not in ('签到','签退'):
            raise ValueError('请选择签到或签退')
        self.home_action = action
        self.show('home')

    def show(self, key):
        # Keep compatibility for callers; checkout is a home mode, never a nav page.
        if key=='checkout':
            self.home_action = '签退'
            key = 'home'
        self.current_page = key
        self.scan_action = self.home_action if key=='home' else '查询' if key=='history' else None
        for name,page in self.pages.items():
            page.pack_forget()
            active = name==key
            shade = '#28436A' if active else NAV
            self.nav[name].configure(bg=shade,fg=WHITE if active else '#BCCADD')
            row,icon = self.nav_rows[name]
            row.configure(bg=shade)
            icon.configure(bg=shade)
        self.pages[key].pack(fill='both',expand=True)
        self.page_title.configure(text=self.titles[key])
        if hasattr(self,'notice'):
            self.notice.set('')
        self.context_tag.pack_forget()
        if key=='home':
            self.context_tag.pack(side='left')
        if key=='home':
            action = self.home_action
            color = GREEN if action=='签到' else ORANGE
            self.action_label.configure(text=action+'登记',fg=color)
            self.action_help.set('请将学生卡靠近读卡器')
            self.name_var.set('等待学生刷卡')
            self.name_label.configure(fg=TEXT)
            self.card_var.set('等待读取卡号')
            self.msg_var.set('')
            self.badge.configure(text='  等待'+action+'  ',fg=color,bg='#E8F7F2' if action=='签到' else '#FFF4E6')
            self.scan_button.configure(text='模拟'+action)
            self.refresh_home_history()
        for action,button in self.action_buttons.items():
            color = GREEN if action=='签到' else ORANGE
            selected = action==self.home_action
            button.configure(bg=color if selected else '#EAF0F7',fg=WHITE if selected else MUTED)
        if hasattr(self,'notice') and key in ('today','students','history'):
            getattr(self,'refresh_'+key)()
        elif key=='courses' and hasattr(self,'course_page'):
            self.course_page.refresh()

    def _home(self, page):
        self.mode_var = tk.StringVar(value='周课表考勤' if self.auto_weekly else '日常考勤')
        switches = tk.Frame(page,bg=BG)
        switches.pack(fill='x',pady=(0,12))
        self.action_buttons = {}
        for i,action in enumerate(('签到','签退')):
            switches.columnconfigure(i,weight=1,uniform='actions')
            button = tk.Button(switches,text=action,font=(FONT,15,'bold'),bd=0,relief='flat',cursor='hand2',
                pady=10,command=lambda a=action:self.set_home_action(a),activebackground='#D9E4F3')
            button.grid(row=0,column=i,sticky='ew',padx=(0 if i==0 else 10,0))
            self.action_buttons[action] = button
        cards = tk.Frame(page,bg=BG)
        cards.pack(fill='x',pady=(0,12))
        self.stats_vars = []
        for i,(title,color) in enumerate([('登记学生',TEXT),('今日已签到',GREEN),('当前在场',BLUE),('今日未签到',ORANGE)]):
            cards.columnconfigure(i,weight=1,uniform='stats')
            box,body = panel(cards)
            box.grid(row=0,column=i,sticky='nsew',padx=(0 if i==0 else 10,0))
            label(body,title,10,MUTED).pack(anchor='w')
            value = tk.StringVar(value='0')
            self.stats_vars.append(value)
            label(body,textvariable=value,size=27,color=color,bold=True).pack(anchor='w')
        workspace = tk.Frame(page,bg=BG)
        workspace.pack(fill='both',expand=True)
        workspace.columnconfigure(0,weight=1,uniform='workspace')
        workspace.columnconfigure(1,weight=1,uniform='workspace')
        workspace.rowconfigure(1,weight=1)
        box,body = panel(workspace)
        box.grid(row=0,column=0,sticky='nsew',padx=(0,12),pady=(0,12))
        row = tk.Frame(body,bg=WHITE)
        row.pack(fill='x')
        self.action_label = label(row,'签到登记',23,GREEN,True)
        self.action_label.pack(side='left')
        self.badge = label(row,'  等待签到  ',9,GREEN,bg='#E8F7F2')
        self.badge.pack(side='right')
        self.action_help = tk.StringVar()
        label(body,textvariable=self.action_help,size=9,color=MUTED).pack(anchor='w',pady=(3,10))
        self.name_var = tk.StringVar(value='等待学生刷卡')
        self.name_label = label(body,textvariable=self.name_var,size=22,bold=True)
        self.name_label.pack(anchor='w',pady=(2,0))
        self.card_var = tk.StringVar(value='等待读取卡号')
        label(body,textvariable=self.card_var,size=9,color=MUTED).pack(anchor='w',pady=(5,3))
        self.msg_var = tk.StringVar()
        self.msg_label = label(body,textvariable=self.msg_var,size=9,color=MUTED,anchor='w',wraplength=430,justify='left')
        self.msg_label.pack(fill='x',pady=(0,8))
        sim = tk.Frame(body,bg=WHITE)
        sim.pack(fill='x')
        self.sim_var = tk.StringVar()
        self.sim_entry = ttk.Entry(sim,textvariable=self.sim_var,width=20)
        self.sim_entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        self.sim_entry.bind('<Return>',lambda _:self.simulate())
        self.scan_button = self.button(sim,'模拟签到',self.simulate,True)
        choose = label(body,'选择学生卡  →',9,BLUE,cursor='hand2')
        choose.pack(anchor='w',pady=(6,0))
        choose.bind('<Button-1>',lambda _:self.choose_card())
        box,body = panel(workspace)
        box.grid(row=1,column=0,sticky='nsew',padx=(0,12))
        row = tk.Frame(body,bg=WHITE)
        row.pack(fill='x',pady=(0,8))
        label(row,'运行日志',12,bold=True).pack(side='left')
        self.button(row,'清除日志',self.clear_log,side='right')
        self.log = tk.Text(body,state='disabled',height=3,bg='#F5F8FC',fg=TEXT,
            font=(FONT,10),relief='flat',wrap='word',padx=10,pady=8)
        self.log.pack(fill='both',expand=True)
        for key,color in [('warn',RED),('signin',GREEN),('signout',BLUE),('muted',MUTED)]:
            self.log.tag_configure(key,foreground=color)
        box,body = panel(workspace,'个人考勤记录')
        box.grid(row=0,column=1,rowspan=2,sticky='nsew')
        self.home_person_var = tk.StringVar(value='等待刷卡')
        self.home_person_detail = tk.StringVar(value='刷卡后显示个人记录')
        label(body,textvariable=self.home_person_var,size=22,bold=True).pack(anchor='w',pady=(10,4))
        label(body,textvariable=self.home_person_detail,size=9,color=MUTED,wraplength=430,justify='left').pack(anchor='w',pady=(0,12))
        self.home_history_count = tk.StringVar(value='暂无记录')
        bottom = label(body,textvariable=self.home_history_count,size=9,color=MUTED)
        bottom.pack(side='bottom',anchor='w',pady=(10,0))
        self.home_history_tree = self.table(body,[('type','操作',65),('time','打卡时间',160),('course_title','考勤范围',140)])
        self.home_history_rows = []

    def refresh_home_history(self):
        if not self.home_history_card:
            self.home_history_rows = []
            self.fill(self.home_history_tree,[],['type','time','course_title'])
            return
        card = self.home_history_card
        rows = db.search_records(card_id=card)
        self.home_history_rows = rows
        student = db.find_student(card)
        identity = student or (rows[0] if rows else None)
        name = identity['name'] if identity and identity['name'] else '档案已删除'
        no = identity['student_no'] if identity and identity['student_no'] else '—'
        cls = identity['class_name'] if identity and identity['class_name'] else '未分班'
        self.home_person_var.set(name)
        self.home_person_detail.set(f'学号 {no}  /  {cls}\n卡号 {card}')
        self.fill(self.home_history_tree,rows,['type','time','course_title'])
        self.home_history_count.set(f'共 {len(rows)} 条记录' if rows else '暂无考勤记录')

    def filters(self, page, class_var, search_var):
        bar = tk.Frame(page, bg=BG)
        bar.pack(fill='x', pady=(0, 12))
        label(bar, '班级', 10, MUTED, bg=BG).pack(side='left', padx=(0, 8))
        combo = ttk.Combobox(bar, textvariable=class_var, state='readonly', width=13)
        combo.pack(side='left', padx=(0, 12))
        label(bar, '搜索', 10, MUTED, bg=BG).pack(side='left', padx=(0, 8))
        ttk.Entry(bar, textvariable=search_var, width=20).pack(side='left', padx=(0, 10))
        return bar, combo

    def _today(self, page):
        self.today_class = tk.StringVar(value='全部班级')
        self.today_search = tk.StringVar()
        self.today_state = tk.StringVar(value='全部状态')
        bar, self.today_classes = self.filters(page, self.today_class, self.today_search)
        ttk.Combobox(bar, textvariable=self.today_state, state='readonly', width=10,
            values=['全部状态','在场','已签退','未签到']).pack(side='left')
        self.button(bar, '导出名单', self.export_today, side='right')
        self.today_count = tk.StringVar()
        label(page, textvariable=self.today_count, size=10, color=MUTED, bg=BG).pack(anchor='w', pady=(0,12))
        box, body = panel(page)
        box.pack(fill='both', expand=True)
        self.today_tree = self.table(body, [('student_no','学号',100),('name','姓名',85),('class_name','班级',110),
            ('status','今日状态',85),('first_in','首次签到',150),('last_time','末次打卡',150),('punches','次数',65)])
        for v in (self.today_class,self.today_search,self.today_state):
            v.trace_add('write', lambda *_: self.refresh_today())

    def _students(self, page):
        self.student_class = tk.StringVar(value='全部班级')
        self.student_search = tk.StringVar()
        bar, self.student_classes = self.filters(page,self.student_class,self.student_search)
        self.button(bar,'新增学生',self.student_dialog,True,side='right')
        row = tk.Frame(page,bg=BG)
        row.pack(fill='x',pady=(0,12))
        for title, action in [('编辑选中',self.edit_student),('删除选中',self.delete_student),('导入学生',self.import_students),
            ('下载模板',lambda:self.export('学生导入模板',services.HEADERS,[])),('导出名单',self.export_students)]:
            self.button(row,title,action)
        self.student_count = tk.StringVar()
        label(row,textvariable=self.student_count,size=9,color=MUTED,bg=BG).pack(side='right')
        box,body = panel(page)
        box.pack(fill='both',expand=True)
        self.student_tree = self.table(body,[('card_id','RFID 卡号',240),('student_no','学号',140),('name','姓名',120),('class_name','班级',150)])
        self.student_tree.bind('<Double-1>',lambda _:self.edit_student())
        for v in (self.student_class,self.student_search):
            v.trace_add('write',lambda *_:self.refresh_students())

    def student_dialog(self, student=None):
        win = tk.Toplevel(self.root)
        win.title('编辑学生' if student else '新增学生 · 支持刷卡读取')
        win.geometry('620x530')
        win.resizable(False,False)
        win.configure(bg=WHITE)
        win.transient(self.root)
        win.grab_set()
        label(win,'编辑学生档案' if student else '建立学生档案',19,bold=True).pack(anchor='w',padx=26,pady=(22,6))
        label(win,'卡号已绑定，不可修改。' if student else '手动输入或刷卡读取卡号，此处不登记考勤。',10,MUTED).pack(anchor='w',padx=26,pady=(0,14))
        form = tk.Frame(win,bg=WHITE)
        form.pack(fill='x',padx=26)
        form.columnconfigure(1,weight=1)
        fields = {}
        status = tk.StringVar(value='编辑期间刷卡不会产生考勤记录。' if student else '等待读卡器读取卡号，也可手动输入。')
        for i,(key,title) in enumerate([('card_id','卡号 *'),('student_no','学号 *'),('name','姓名 *'),('class_name','班级')]):
            label(form,title,10,MUTED).grid(row=i,column=0,sticky='w',padx=(0,18),pady=7)
            initial = student[key] if student else (self.last_seen_card or '' if key=='card_id' else '')
            value = tk.StringVar(value=initial)
            entry = ttk.Entry(form,textvariable=value)
            entry.grid(row=i,column=1,sticky='ew',pady=7)
            if key=='card_id' and student:
                entry.configure(state='readonly')
            fields[key] = value
        capture = dict(window=win,field=fields['card_id'],status=status,armed=student is None)
        self._capture_session = capture
        def arm():
            capture['armed'] = True
            status.set('请将新卡靠近读卡器，读取后会自动填入上方卡号。')
        if student is None:
            self.button_row = tk.Frame(win,bg=WHITE)
            self.button_row.pack(fill='x',padx=26,pady=(6,0))
            self.button(self.button_row,'刷卡读取卡号',arm,True)
        label(win,textvariable=status,size=9,color=BLUE,wraplength=550,justify='left').pack(anchor='w',padx=26,pady=(8,10))
        if student is None:
            sim = tk.Frame(win,bg=WHITE)
            sim.pack(fill='x',padx=26,pady=(0,8))
            label(sim,'无硬件演示',9,MUTED).pack(side='left',padx=(0,8))
            simulated = tk.StringVar()
            ttk.Entry(sim,textvariable=simulated,width=23).pack(side='left',fill='x',expand=True,padx=(0,8))
            self.button(sim,'模拟读取',lambda:self.handle_card(simulated.get()))
        def cleanup(event):
            if event.widget==win and self._capture_session is capture:
                self._capture_session = None
        win.bind('<Destroy>',cleanup)
        def save():
            try:
                values = services.validate_student(*(fields[k].get() for k in ('card_id','student_no','name','class_name')))
                if student:
                    if not db.update_student(*values):
                        raise ValueError('该学生已被删除，请刷新名单')
                else:
                    db.add_student(*values)
                self.scan_times.clear()
                self.refresh()
                win.destroy()
                self.notice.set('学生档案已保存，未产生考勤记录')
            except (ValueError,sqlite3.Error) as exc:
                messagebox.showerror('保存失败','卡号或学号已被使用' if isinstance(exc,sqlite3.IntegrityError) else str(exc),parent=win)
        row = tk.Frame(win,bg=WHITE)
        row.pack(side='bottom',fill='x',padx=26,pady=20)
        self.button(row,'保存档案',save,True)
        self.button(row,'取消',win.destroy)
        win.student_fields = fields
        return win

    def selected_student(self):
        sel = self.student_tree.selection()
        if not sel:
            messagebox.showinfo('选择学生','请先选中一名学生',parent=self.root)
            return None
        return db.find_student(self.student_tree.item(sel[0],'values')[0])

    def edit_student(self):
        row = self.selected_student()
        if row:
            self.student_dialog(row)

    def delete_student(self):
        row = self.selected_student()
        if row and messagebox.askyesno('删除学生',f"删除 {row['name']} 的档案？\n历史刷卡记录保留。删除前自动备份数据库。",parent=self.root) and self.auto_backup():
            db.delete_student(row['card_id'])
            self.refresh()
            self.notice.set('学生已删除 · 删除前数据库已自动备份')

    def _history(self,page):
        self.history_card = None
        self.history_scan_var = tk.StringVar()
        self.history_identity = tk.StringVar(value='请刷卡，或输入卡号查询')
        box,body = panel(page)
        box.pack(fill='x',pady=(0,12))
        heading = tk.Frame(body,bg=WHITE)
        heading.pack(fill='x')
        label(heading,'刷卡查询个人历史',16,bold=True).pack(side='left')
        label(heading,'  只查询 · 不打卡  ',9,BLUE,bg='#EBEEFF').pack(side='right')
        row = tk.Frame(body,bg=WHITE)
        row.pack(fill='x',pady=(10,8))
        entry = ttk.Entry(row,textvariable=self.history_scan_var,width=30)
        entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        entry.bind('<Return>',lambda _:self.show_person_history(self.history_scan_var.get()))
        self.button(row,'查询此卡',lambda:self.show_person_history(self.history_scan_var.get()),True)
        self.button(row,'查看全部历史',self.reset_history)
        label(body,textvariable=self.history_identity,size=9,color=BLUE,wraplength=800,justify='left').pack(anchor='w')
        self.history_search = tk.StringVar()
        self.history_class = tk.StringVar(value='全部班级')
        self.history_type = tk.StringVar(value='全部动作')
        self.date_start,self.date_end = tk.StringVar(),tk.StringVar()
        bar,self.history_classes = self.filters(page,self.history_class,self.history_search)
        ttk.Combobox(bar,textvariable=self.history_type,state='readonly',width=10,
            values=['全部动作','签到','签退']).pack(side='left')
        row = tk.Frame(page,bg=BG)
        row.pack(fill='x',pady=(0,12))
        label(row,'日期范围',10,MUTED,bg=BG).pack(side='left',padx=(0,8))
        for v in (self.date_start,self.date_end):
            ttk.Entry(row,textvariable=v,width=12).pack(side='left',padx=(0,8))
        self.button(row,'查询',self.refresh_history,True)
        self.button(row,'今日',self.history_today)
        self.button(row,'重置',self.reset_history)
        self.button(row,'导出结果',self.export_history,side='right')
        label(row,'YYYY-MM-DD',9,MUTED,bg=BG).pack(side='left',padx=6)
        box,body = panel(page)
        box.pack(fill='both',expand=True)
        self.history_tree = self.table(body,[('id','ID',65),('card_id','RFID 卡号',200),('student_no','学号',100),
            ('name','姓名',85),('class_name','班级',110),('type','动作',70),('course_title','场景 / 课次',160),('time','时间',165)])
        row = tk.Frame(page,bg=BG)
        row.pack(side='bottom',before=box,fill='x',pady=(12,0))
        self.history_count = tk.StringVar()
        label(row,textvariable=self.history_count,size=9,color=MUTED,bg=BG).pack(side='left')
        self.button(row,'删除选中记录',self.delete_record,side='right')
        self.clear_history_button = self.button(row,'清空全部历史记录',self.clear_history,side='right')
        self.clear_history_button.configure(style='Danger.TButton')
        for v in (self.history_search,self.history_class,self.history_type):
            v.trace_add('write',lambda *_:self.refresh_history(quiet=True))

    def _settings(self,page):
        box,body = panel(page,'读卡器连接')
        box.pack(fill='x',pady=(0,14))
        row = tk.Frame(body,bg=WHITE)
        row.pack(fill='x')
        self.port_var = tk.StringVar(value=self.settings['port'])
        self.baud_var = tk.StringVar(value=str(self.settings['baud']))
        label(row,'串口',10,MUTED).pack(side='left',padx=(0,8))
        self.port_combo = ttk.Combobox(row,textvariable=self.port_var,width=12)
        self.port_combo.pack(side='left',padx=(0,14))
        label(row,'波特率',10,MUTED).pack(side='left',padx=(0,8))
        ttk.Combobox(row,textvariable=self.baud_var,width=9,state='readonly',values=[9600,19200,38400,57600,115200]).pack(side='left',padx=(0,14))
        self.button(row,'刷新串口',self._refresh_ports)
        self.btn_connect = self.button(row,'连接设备',self.connect,True)
        box,body = panel(page,'考勤与语音')
        box.pack(fill='x',pady=(0,14))
        form = tk.Frame(body,bg=WHITE)
        form.pack(fill='x')
        self.interval_var = tk.StringVar(value=str(self.settings['interval']))
        self.dedup_var = tk.StringVar(value=str(self.settings['dedup']))
        for i,(title,var,note) in enumerate([('最小打卡间隔',self.interval_var,'分钟 · 0 不限制，最大 1440'),
            ('重复刷卡保护',self.dedup_var,'秒 · 1–60；同卡在保护时间内忽略')]):
            label(form,title,10,MUTED).grid(row=i,column=0,sticky='w',pady=7,padx=(0,16))
            ttk.Entry(form,textvariable=var,width=8).grid(row=i,column=1,pady=7)
            label(form,note,9,MUTED).grid(row=i,column=2,sticky='w',padx=14)
        self.sound_var = tk.BooleanVar(value=self.settings['sound'])
        tk.Checkbutton(body,text='成功打卡时播放系统提示音',variable=self.sound_var,bg=WHITE,
            activebackground=WHITE,font=(FONT,10)).pack(anchor='w',pady=8)
        voice_row = tk.Frame(body,bg=WHITE)
        voice_row.pack(fill='x')
        self.voice_var = tk.BooleanVar(value=self.settings['voice'])
        tk.Checkbutton(voice_row,text='播报姓名和签到 / 签退结果',variable=self.voice_var,bg=WHITE,
            activebackground=WHITE,font=(FONT,10)).pack(side='left',padx=(0,12))
        self.button(voice_row,'试听语音',self.test_voice)
        row = tk.Frame(body,bg=WHITE)
        row.pack(fill='x')
        self.button(row,'保存设置',self.save_settings,True)
        box,body = panel(page,'数据管理')
        box.pack(fill='x')
        row = tk.Frame(body,bg=WHITE)
        row.pack(fill='x')
        self.button(row,'备份数据库',self.backup)
        self.button(row,'刷新所有数据',self.refresh)


    def _refresh_ports(self):
        try:
            names = [p[0] for p in UHFReader18.list_ports()]
        except Exception:
            names = []
        self.port_combo['values'] = names
        if not self.port_var.get() and names:
            self.port_var.set(names[0])

    def connect(self):
        if self.reader:
            self.disconnect()
            return
        try:
            port,baud = self.port_var.get().strip(),int(self.baud_var.get())
            if not port or baud not in (9600,19200,38400,57600,115200):
                raise ValueError('请选择串口和有效波特率')
            self.reader = UHFReader18(on_card=lambda c:self.q.put(('card',c)),on_error=lambda e:self.q.put(('error',e)))
            self.reader.connect(port=port,baudrate=baud)
            self.header_status.configure(text=f'●  已连接 {port}',fg=GREEN)
            self.btn_connect.configure(text='断开设备')
            self.log_event(f'设备连接成功：{port} / {baud}','signin')
        except Exception as exc:
            self.disconnect()
            messagebox.showerror('连接失败',str(exc),parent=self.root)

    def disconnect(self):
        if self.reader:
            self.reader.disconnect()
        self.reader = None
        self.btn_connect.configure(text='连接设备')
        self.header_status.configure(text='●  设备未连接',fg=ORANGE)

    def _poll(self):
        if self._closing:
            return
        self.clock.configure(text=datetime.now().strftime('%Y.%m.%d  %H:%M:%S'))
        try:
            for _ in range(30):
                kind,value = self.q.get_nowait()
                if kind=='card':
                    self.handle_card(value)
                elif kind=='speech_ready':
                    self.log_event('中文语音已就绪：'+value)
                elif kind=='speech_error':
                    self.log_event('语音播报未完成：'+value,'warn')
                    self.notice.set('语音未完成；考勤记录不受影响。可在设备与设置中试听检查。')
                else:
                    self.log_event('设备通信异常：'+value,'warn')
                    self.header_status.configure(text='●  通信异常',fg=RED)
                    self.notice.set('请检查 USB 或串口连接，可断开后重新连接')
        except queue.Empty:
            pass
        if getattr(self,'_stats_day','') != datetime.now().strftime('%Y-%m-%d'):
            self.scan_times.clear()
            self.refresh()
        if self.current_page=='courses' and time.monotonic()-getattr(self,'_course_tick',0)>5:
            self._course_tick = time.monotonic()
            self.course_page.refresh_status()
        self._after = self.root.after(200,self._poll)

    def handle_card(self,card):
        card = card.strip().upper()
        if not card:
            self.notice.set('请先输入或读取卡号')
            return
        self.last_seen_card = card
        capture = self._capture_session
        if capture and capture['window'].winfo_exists():
            if capture['armed']:
                capture['field'].set(card)
                capture['status'].set('已读取卡号：'+card+'；请填写学号和姓名后保存。')
            return
        if self.root.grab_current() is not None:
            self.notice.set('正在编辑资料；本次刷卡未登记考勤')
            return
        if self.current_page=='history':
            self.show_person_history(card)
            return
        if self.current_page!='home':
            self.notice.set('请在首页选择签到或签退；主动查记录请进入“个人历史查询”')
            return
        action = self.home_action
        now = time.monotonic()
        key = (action,card)
        if now-self.scan_times.get(key,-1e12)<self.settings['dedup']:
            self.msg_var.set(f"重复刷卡保护中，请等待 {self.settings['dedup']} 秒")
            return
        self.card_var.set(card)
        try:
            student = db.find_student(card)
            if student is None:
                self.name_var.set('未登记的学生卡')
                self.name_label.configure(fg=RED)
                self.badge.configure(text='  未登记  ',fg=RED,bg='#FFF0F1')
                self.msg_var.set('到学生档案新增学生，可直接刷卡读取卡号')
                self.log_event('未登记卡：'+card,'warn')
                return
            self.name_var.set(f"{student['name']} · {student['student_no']}")
            self.name_label.configure(fg=TEXT)
            target = self.active_session_id
            if self.auto_weekly:
                target = weekly.resolve_for_card(card,action=action)
            late = False
            if target:
                kind,stamp,late = courses.punch(target,card,min_interval=config.MIN_INTERVAL_MINUTES,action=action)
            else:
                stamp = db.record_action(card,action,min_interval=config.MIN_INTERVAL_MINUTES)
                kind = action
            self.scan_times[key] = now
            self.home_history_card = card
            self.badge.configure(text=f'  {kind}成功  ',fg=GREEN if kind=='签到' else ORANGE,
                bg='#E8F8F3' if kind=='签到' else '#FFF4E6')
            self.msg_var.set(('迟到 · ' if late and kind=='签到' else '')+f"{'课程' if target else '日常'}{kind}已保存 · {stamp}")
            if target and self.auto_weekly:
                self.course_page.sid = target
                self.course_page.follow_current = False
            self.log_event(f"{student['name']} {kind}成功 · {stamp[11:]}",'signin' if kind=='签到' else 'signout')
            self.refresh()
            if self.settings['sound']:
                self.root.bell()
            if self.settings['voice']:
                self.speech.say(f"{student['name']}，"+('迟到，' if late and kind=='签到' else '')+f'{kind}成功')
        except ValueError as exc:
            self.msg_var.set(str(exc))
            self.badge.configure(text='  未重复录入  ',fg=ORANGE,bg='#FFF6E9')
            self.log_event(str(exc),'warn')
        except sqlite3.Error as exc:
            self.badge.configure(text='  保存失败  ',fg=RED,bg='#FFF0F1')
            self.msg_var.set('数据保存失败，请检查数据库权限后重试')
            self.log_event('数据库错误：'+str(exc),'warn')

    def show_person_history(self,card):
        card = card.strip().upper()
        if not card:
            self.history_identity.set('请刷卡或输入卡号，仅查询不会产生考勤记录')
            return
        self.history_card = card
        self.history_scan_var.set(card)
        for var in (self.history_search,self.date_start,self.date_end):
            var.set('')
        self.history_class.set('全部班级')
        self.history_type.set('全部动作')
        self.refresh_history()
        student = db.find_student(card)
        row = student or (self.history_rows[0] if self.history_rows else None)
        name = row['name'] if row and row['name'] else '未登记 / 档案已删除'
        number = row['student_no'] if row and row['student_no'] else '—'
        self.history_identity.set(f'{name}  /  学号 {number}  /  卡号 {card}  /  共 {len(self.history_rows)} 条历史')
        self.notice.set('本次为个人历史查询，没有新增签到或签退记录')

    def simulate(self):
        self.handle_card(self.sim_var.get())

    def choose_card(self):
        students = db.list_students()
        if not students:
            self.show('students')
            self.notice.set('请先新增或导入学生，再使用模拟刷卡')
            return
        win = tk.Toplevel(self.root)
        win.title('选择模拟刷卡学生')
        win.geometry('560x410')
        win.transient(self.root)
        win.grab_set()
        tree = self.table(win,[('name','姓名',100),('student_no','学号',120),('card_id','卡号',240)])
        self.fill(tree,students,['name','student_no','card_id'])
        def choose(_=None):
            sel = tree.selection()
            if sel:
                self.sim_var.set(tree.item(sel[0],'values')[2])
                win.destroy()
                self.sim_entry.focus_set()
        tree.bind('<Double-1>',choose)
        ttk.Button(win,text='选用卡号',command=choose).pack(pady=12)
        return win

    def log_event(self,text,tag='muted'):
        self.log.configure(state='normal')
        self.log.insert('end',f"{time.strftime('%H:%M:%S')}  {text}\n",tag)
        if int(self.log.index('end-1c').split('.')[0])>300:
            self.log.delete('1.0','2.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0','end')
        self.log.configure(state='disabled')
        self.notice.set('运行日志已清空')

    def test_voice(self):
        self.speech.say('张同学，签到成功。李同学，签退成功。')
        self.notice.set('正在试听中文语音，请确认扬声器已开启')

    def refresh(self):
        weekly.ensure_sessions()
        self._stats_day = datetime.now().strftime('%Y-%m-%d')
        for var,value in zip(self.stats_vars,db.daily_summary()):
            var.set(str(value))
        self.refresh_home_history()
        classes = ['全部班级']+sorted({r['class_name'] or '未分班' for r in db.list_students()})
        for combo,var in [(self.today_classes,self.today_class),(self.student_classes,self.student_class),(self.history_classes,self.history_class)]:
            combo['values'] = classes
            if var.get() not in classes:
                var.set('全部班级')
        self.refresh_today()
        self.refresh_students()
        self.refresh_history(quiet=True)
        self.course_page.refresh()

    def disable_course(self):
        self.auto_weekly = False
        weekly.set_auto(False)
        self.active_session_id = None
        self.scan_times.clear()
        self.mode_var.set('日常考勤')
        self.notice.set('已返回日常考勤，后续刷卡不计入课次')
        self.course_page.refresh()

    def enable_weekly(self):
        if not any(r['enabled'] for r in weekly.list_rules()):
            messagebox.showinfo('固定周课表','请先在“固定周课表”中新建并启用一门固定课程',parent=self.root)
            return
        weekly.ensure_sessions()
        weekly.set_auto(True)
        self.auto_weekly = True
        self.active_session_id = None
        self.scan_times.clear()
        self.mode_var.set('周课表考勤')
        self.course_page.follow_current = True
        self.course_page.refresh()
        self.notice.set('自动周课表已启用：每次刷卡自动匹配当日课次；重启后仍生效')

    def matches(self,row,cls,keyword):
        return (cls=='全部班级' or (row['class_name'] or '未分班')==cls) and (not keyword or
            keyword.casefold() in ' '.join(str(row[k] or '') for k in ('card_id','student_no','name','class_name')).casefold())

    def refresh_today(self):
        self.today_rows = [r for r in db.student_statuses() if self.matches(r,self.today_class.get(),self.today_search.get().strip())
            and (self.today_state.get()=='全部状态' or r['status']==self.today_state.get())]
        self.fill(self.today_tree,self.today_rows,['student_no','name','class_name','status','first_in','last_time','punches'],True)
        self.today_count.set(f"当前筛选 {len(self.today_rows)} 人  /  在场 {sum(r['status']=='在场' for r in self.today_rows)} 人  /  未签到 {sum(r['status']=='未签到' for r in self.today_rows)} 人")

    def refresh_students(self):
        self.student_rows = [r for r in db.list_students() if self.matches(r,self.student_class.get(),self.student_search.get().strip())]
        self.fill(self.student_tree,self.student_rows,['card_id','student_no','name','class_name'])
        self.student_count.set(f'当前 {len(self.student_rows)} 名学生')

    def history_query(self):
        start,end = self.date_start.get().strip(),self.date_end.get().strip()
        for value in (start,end):
            if value:
                try:
                    if datetime.strptime(value,'%Y-%m-%d').strftime('%Y-%m-%d')!=value:
                        raise ValueError()
                except ValueError:
                    raise ValueError('日期请使用 YYYY-MM-DD 格式')
        if start and end and start>end:
            raise ValueError('开始日期不能晚于结束日期')
        return db.search_records(self.history_search.get().strip(),'' if self.history_type.get()=='全部动作' else self.history_type.get(),
            self.history_class.get(),start,end,card_id=self.history_card)

    def refresh_history(self,quiet=False):
        try:
            rows = self.history_query()
        except ValueError as exc:
            self.history_count.set(str(exc))
            if not quiet:
                messagebox.showwarning('查询条件',str(exc),parent=self.root)
            return False
        self.history_rows = rows
        self.fill(self.history_tree,rows,['id','card_id','student_no','name','class_name','type','course_title','time'])
        self.history_count.set(f"共 {len(rows)} 条 · 签到 {sum(r['type']=='签到' for r in rows)} / 签退 {sum(r['type']=='签退' for r in rows)}")
        return True

    def history_today(self):
        day = datetime.now().strftime('%Y-%m-%d')
        self.date_start.set(day)
        self.date_end.set(day)
        self.refresh_history()

    def reset_history(self):
        self.history_card = None
        self.history_scan_var.set('')
        self.history_identity.set('显示全部历史；在本页刷卡可查询指定学生，不会打卡')
        for v in (self.history_search,self.date_start,self.date_end):
            v.set('')
        self.history_class.set('全部班级')
        self.history_type.set('全部动作')
        self.refresh_history()

    def delete_record(self):
        sel = self.history_tree.selection()
        if not sel:
            messagebox.showinfo('选择记录','请先选择一条记录',parent=self.root)
            return
        rid = self.history_tree.item(sel[0],'values')[0]
        if messagebox.askyesno('删除记录',f'确定删除记录 #{rid}？\n删除前自动备份，今日统计会重新计算。',parent=self.root) and self.auto_backup():
            try:
                db.delete_record(rid)
            except (ValueError,sqlite3.Error) as exc:
                messagebox.showerror('删除失败',str(exc),parent=self.root)
                return
            self.scan_times.clear()
            self.refresh()
            self.notice.set('记录已删除 · 删除前数据库已自动备份')

    def clear_history(self):
        count = db.count_records()
        if not count:
            self.notice.set('历史记录已经为空')
            return
        if not messagebox.askyesno('清空全部历史记录',
            f'确定删除全部 {count} 条考勤历史记录？\n包括日常、课程打卡及筛选外的记录。\n\n学生档案、课程设置和请假保留；操作前自动备份。\n清空后重启也不会重新出现，并返回日常签到模式。',parent=self.root):
            return
        if not self.auto_backup():
            return
        try:
            deleted = db.clear_records()
        except sqlite3.Error as exc:
            messagebox.showerror('清空失败',str(exc),parent=self.root)
            return
        self.speech.clear()
        self.disable_course()
        self.scan_times.clear()
        self.name_var.set('等待学生刷卡')
        self.name_label.configure(fg=TEXT)
        self.card_var.set('RFID  /  尚未读取卡号')
        self.badge.configure(text='  准备就绪  ',fg=BLUE,bg='#EDF4FF')
        self.msg_var.set('历史已清空，可按所选模式继续刷卡')
        self.reset_history()
        self.refresh()
        self.log_event(f'已清空全部 {deleted} 条历史记录，学生档案保留')
        self.notice.set(f'已永久清空 {deleted} 条历史记录 · 原数据已备份至 backups 文件夹')

    def export(self,name,headers,rows):
        path = filedialog.asksaveasfilename(parent=self.root,title='导出 '+name,
            initialfile=name+'_'+datetime.now().strftime('%Y%m%d')+'.csv',defaultextension='.csv',filetypes=[('CSV 表格','*.csv')])
        if path:
            try:
                services.write_csv(path,headers,rows)
                self.notice.set('已导出至 '+path)
            except OSError as exc:
                messagebox.showerror('导出失败',str(exc),parent=self.root)

    def export_today(self):
        self.refresh_today()
        self.export('今日人员状态',['学号','姓名','班级','状态','首次签到','末次打卡','次数'],
            [[r[k] for k in ('student_no','name','class_name','status','first_in','last_time','punches')] for r in self.today_rows])

    def export_students(self):
        self.refresh_students()
        self.export('学生名单',services.HEADERS,[[r[k] for k in ('card_id','student_no','name','class_name')] for r in self.student_rows])

    def export_history(self):
        if self.refresh_history():
            self.export('考勤记录',['ID','卡号','学号','姓名','班级','动作','课次','时间'],
                [[r[k] for k in ('id','card_id','student_no','name','class_name','type','course_title','time')] for r in self.history_rows])

    def import_students(self):
        path = filedialog.askopenfilename(parent=self.root,title='导入学生（CSV / Excel）',filetypes=[('学生名单（CSV / Excel）','*.csv *.xlsx'),('CSV 表格','*.csv'),('Excel 工作簿','*.xlsx')])
        if not path:
            return
        try:
            rows = services.read_students(path)
            if not messagebox.askyesno('导入预览',f'文件共 {len(rows)} 名学生。\n前 3 名：'+'、'.join(r[2] for r in rows[:3])+
                '\n\n仅新增档案；卡号或学号重复时整批取消。确认导入？',parent=self.root):
                return
            if self.auto_backup():
                count = db.import_students(rows)
                self.refresh()
                self.notice.set(f'已成功导入 {count} 名学生')
        except (OSError,UnicodeError,ValueError,sqlite3.Error) as exc:
            messagebox.showerror('导入失败',str(exc),parent=self.root)

    def save_settings(self):
        try:
            interval,dedup,baud = int(self.interval_var.get()),int(self.dedup_var.get()),int(self.baud_var.get())
            if not 0<=interval<=1440 or not 1<=dedup<=60:
                raise ValueError('打卡间隔需为 0–1440 分钟，重复保护需为 1–60 秒')
            if baud not in (9600,19200,38400,57600,115200):
                raise ValueError('波特率无效')
            value = dict(port=self.port_var.get().strip(),baud=baud,interval=interval,dedup=dedup,sound=self.sound_var.get(),voice=self.voice_var.get())
            services.save_settings(value)
            self.settings = value
            config.MIN_INTERVAL_MINUTES = interval
            self.notice.set('设置已保存 · 串口参数在下次连接时生效')
        except (ValueError,OSError) as exc:
            messagebox.showerror('保存失败',str(exc),parent=self.root)

    def auto_backup(self):
        try:
            folder = Path(config.DB_PATH).resolve().parent/'backups'
            folder.mkdir(exist_ok=True)
            db.backup_database(folder/('attendance_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.db'))
            return True
        except (OSError,sqlite3.Error,ValueError) as exc:
            messagebox.showerror('自动备份失败','本次操作已取消：'+str(exc),parent=self.root)
            return False

    def backup(self):
        path = filedialog.asksaveasfilename(parent=self.root,title='备份考勤数据库',
            initialfile='attendance_backup_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.db',
            defaultextension='.db',filetypes=[('SQLite 数据库','*.db')])
        if path:
            try:
                db.backup_database(path)
                self.notice.set('数据库已备份至 '+path)
            except (OSError,sqlite3.Error,ValueError) as exc:
                messagebox.showerror('备份失败',str(exc),parent=self.root)

    def close(self):
        self._closing = True
        if self._after:
            self.root.after_cancel(self._after)
        self.disconnect()
        self.speech.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    AttendanceApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()


