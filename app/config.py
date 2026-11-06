# -*- coding: utf-8 -*-
"""
RFID 一卡通刷卡考勤系统 —— 全局配置文件

硬件：KL9007R 系列 UHF 超高频一体机读写器（UHFReader18 协议，ISO18000-6C / EPC C1G2）
通信：RS232 串口（一体机内部 USB 转串口，电脑上显示为 COM 口 / 虚拟串口）
协议：自定义命令帧  [Len][Adr][Cmd][Data...][CRC16]
"""

# ================== 串口通信 ==================
SERIAL_PORT = "COM3"        # 读卡器对应串口，按「设备管理器 -> 端口(COM和LPT)」实际值修改
BAUD_RATE = 57600           # 波特率：UHFReader18 缺省 57600（8 数据位、1 停止位、无校验）
SERIAL_TIMEOUT = 0.5        # 单次读取超时（秒）

READER_ADDR = 0x00          # 读写器地址，出厂默认 0x00；0xFF 为广播地址

# ================== 盘存（读卡）参数 ==================
POLL_INTERVAL = 0.3         # 盘存轮询间隔（秒）：每隔这么久发一次「询查标签」命令
PRESENCE_TIMEOUT = 3.0      # 标签离开判定（秒）：连续 N 秒没盘存到该标签，认为已离开

# ================== 卡号（EPC）校验 ==================
MIN_CARD_HEX_LEN = 4        # EPC 最短十六进制长度（2 字节）
MAX_CARD_HEX_LEN = 40       # EPC 最长十六进制长度（20 字节）

# ================== 业务规则 ==================
from pathlib import Path
DB_PATH = str(Path(__file__).resolve().parent / "attendance.db")

# 按卡号设置短时间重复保护；在设备与设置页面可修改并持久化。
# 读卡器本身还要求标签离开天线后重新进入，才触发下一次事件。
DEDUP_INTERVAL_SECONDS = 3

# 现场新增需求考点：签到/签退最小间隔（分钟）。0 = 不限制。
# 答辩演示时把 0 改成 30，即可实现「签到签退间隔不少于 30 分钟」。
MIN_INTERVAL_MINUTES = 0
