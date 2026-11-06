# 教育机器人考勤系统

基于 Python、Tkinter 和 SQLite 的 Windows 桌面 RFID 学生考勤系统。本仓库提供最新源码，包含首页签到签退、个人历史查询、学生档案、固定周课表和语音播报。

## 环境与启动

- Windows 10/11，推荐 Python 3.12，安装时保留 Tcl/Tk 支持。
- 数据库 SQLite、界面 Tkinter 均使用 Python 标准库。
- 串口通信使用 pyserial，Excel 读写使用 openpyxl。
- 姓名语音播报依赖 Windows PowerShell 和系统已安装的中文语音。

在仓库根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app\main.py
```

首次启动会自动在 app 目录创建空的 attendance.db，无需手动建表。新增学生或导入名单后即可使用。

没有读卡器时，可以在首页输入已登记学生的卡号，使用模拟签到或模拟签退。独立演示入口：

```powershell
.venv\Scripts\python.exe app\demo.py
```

演示入口使用单独的临时数据库，不修改正式考勤数据。

## 功能与操作

1. **学生档案**：新增、编辑、删除和查询；新卡号可手动输入或刷卡读取。档案窗口内刷卡不登记考勤。
2. **批量导入导出**：支持 CSV 和 Excel .xlsx，可从界面下载模板；旧版 .xls 需另存为 .xlsx。导入表头为卡号、学号、姓名、班级。
3. **首页签到签退**：在首页选择操作模式后刷卡。允许没有签到记录直接签退，保留重复刷卡和最小间隔保护。
4. **即时个人记录**：成功打卡后，首页右侧自动显示该学生的历史，包含本次操作。
5. **独立历史查询**：查询页面刷卡仅查询，不登记考勤；支持筛选和导出。
6. **今日状态**：查看在场、已签退、未签到及当天打卡次数。
7. **课程考勤**：可以手动启用某一课次，或启用自动周课表；点击返回日常考勤即可切回普通签到签退。
8. **固定周课表**：设置课程名称、班级、星期和起止时间，日期自动生成；规则支持暂停、启用。
9. **请假与统计**：每课独立记录请假、迟到、缺勤；课程结束后判断缺勤。只有签退没有签到时标记缺签到，不补造签到时间。
10. **维护与语音**：设备连接、语音试听与播报、数据库备份；清除运行日志与清空历史记录为两个独立操作。

使用真实硬件时，在设备与设置中选择实际 COM 端口和波特率。支持 KL9007R / UHFReader18 协议读卡器；同一张卡需要移开天线后重新靠近才再次触发。

## 代码结构

| 文件 | 用途 |
| --- | --- |
| app/main.py | 正式入口 |
| app/dashboard.py | 主界面、首页模式和刷卡事件分发 |
| app/branding.py、app/robot.ico | 机器人标志和窗口图标 |
| app/database.py | 学生、考勤记录、查询和备份 |
| app/courses.py、app/course_ui.py | 课程考勤、请假与报表 |
| app/weekly.py、app/weekly_ui.py | 固定周课表与自动日期 |
| app/rfid_reader.py | 读卡器串口通信 |
| app/services.py | 导入导出、校验与本地设置 |
| app/speech.py、app/speech_worker.ps1 | Windows 离线语音 |
| app/config.py | 默认硬件参数及数据库路径 |
| app/demo.py | 独立演示数据与入口 |

## 数据库

使用本地 SQLite。主要表为 students、records、course_sessions、course_roster、course_punches、course_leaves、weekly_rules、weekly_lessons、weekly_preferences。

学生与记录通过卡号关联，课次通过课次编号关联。使用参数化 SQL 和事务保存关键操作，数据库关联主要由程序校验维护。备份使用 SQLite 在线备份接口。

## 源码与本地数据

此仓库仅包含源码、图标、依赖清单和说明，不含实际学生资料、考勤数据库、个人设置、日志或备份，也不包含免安装版 Python 运行环境。运行产生的数据已列入 .gitignore。
