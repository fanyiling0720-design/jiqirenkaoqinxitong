# -*- coding: utf-8 -*-
"""
RFID 一卡通刷卡考勤系统 —— UHF 读写器串口通信层（UHFReader18 协议）

硬件：KL9007R 系列 UHF 超高频一体机读写器（ISO18000-6C / EPC C1G2）
通信：RS232 串口（USB 虚拟串口 COM 口），缺省波特率 57600，8 数据位、1 停止位、无校验

协议（自定义命令帧）：
    命令帧： [Len][Adr][Cmd][Data...][CRC16_LSB][CRC16_MSB]     Len = 4 + len(Data)
    响应帧： [Len][Adr][reCmd][Status][Data...][CRC16_LSB][CRC16_MSB]  Len = 5 + len(Data)
    CRC16 ： 多项式 0x8408，初值 0xFFFF，对「Len 到 Data」整段计算；
             收方对整帧（含 CRC）再算一次 CRC16，结果为 0x0000 即数据正确。

核心命令：
    询查标签（Inventory）Cmd = 0x01：盘存有效范围内所有标签，返回各标签的 EPC 号。
    EPC 号即「卡号」，是标签唯一的电子编码（十六进制字节，长度可变）。

关键点（答辩考点）：
    串口是「字节流」，没有消息边界；本模块靠帧首的 Len 字段精确定位一帧的结束，
    再靠 CRC16 校验过滤掉传输出错的脏数据。
"""

import threading
import time

import serial
import serial.tools.list_ports

import config


# ================== CRC16（照抄官方手册 C 算法） ==================

def crc16(data) -> int:
    """UHFReader18 协议 CRC16：多项式 0x8408，初值 0xFFFF。"""
    PRESET = 0xFFFF
    POLY = 0x8408
    crc = PRESET
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ POLY
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_command(addr: int, cmd: int, data: bytes = b"") -> bytes:
    """构造命令帧：[Len][Adr][Cmd][Data...][CRC16_LSB][CRC16_MSB]。"""
    frame = bytes([4 + len(data), addr, cmd]) + bytes(data)
    crc = crc16(frame)
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def check_frame(frame: bytes) -> bool:
    """校验一帧：对整帧（含 CRC）算 CRC16，结果应为 0x0000。"""
    return crc16(frame) == 0x0000


def parse_epcs(data: bytes) -> list:
    """解析「询查标签」响应的 Data 域，返回 EPC 十六进制字符串列表。

    Data 结构：[Num(1字节)] + 每个标签 [EPC长度(1字节) + EPC数据]
    例：b'\\x02\\x04\\xe2\\x00\\x00\\x01\\x02\\xaa\\xbb'
        -> 2 张标签，EPC 分别为 'E2000001'、'AABB'
    """
    if not data:
        return []
    num = data[0]
    epcs = []
    i = 1
    for _ in range(num):
        if i >= len(data):
            break
        length = data[i]
        i += 1
        epc = data[i:i + length]
        i += length
        if epc:
            epcs.append(epc.hex().upper())
    return epcs


# ================== 读写器封装类 ==================

class UHFReader18:
    """UHFReader18 读写器封装（串口）。

    用法：
        reader = UHFReader18(on_card=回调, on_error=错误回调)
        reader.connect(port="COM3", baudrate=57600)
        reader.disconnect()
    """

    def __init__(self, on_card=None, on_error=None):
        self.ser = None
        self.on_card = on_card
        self.on_error = on_error
        self._running = False
        self._thread = None
        self._present = {}        # 当前在场标签：{epc: 最近一次盘存到的时间戳}

    # ---------- 连接管理 ----------

    @staticmethod
    def list_ports():
        """列出系统当前可用串口 [(设备名, 描述), ...]。"""
        return [(p.device, p.description) for p in serial.tools.list_ports.comports()]

    def connect(self, port=None, baudrate=None):
        port = port or config.SERIAL_PORT
        baudrate = baudrate or config.BAUD_RATE
        self.ser = serial.Serial(port=port, baudrate=baudrate, timeout=config.SERIAL_TIMEOUT)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def disconnect(self):
        self._running = False
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    # ---------- 主循环：持续盘存 ----------

    def _loop(self):
        """后台线程：周期性发送「询查标签」命令，维护在场标签，触发刷卡事件。"""
        while self._running:
            try:
                epcs = self.inventory()          # 盘存当前范围内标签
                self._update_presence(epcs)      # 判断新出现的标签 -> 触发刷卡
                time.sleep(config.POLL_INTERVAL)
            except serial.SerialException as e:
                # USB 断开 / 通信异常：容错，不崩溃，通知上层并稍后重试
                if self.on_error:
                    self.on_error(str(e))
                time.sleep(1.0)
            except Exception as e:
                if self.on_error:
                    self.on_error(str(e))
                time.sleep(1.0)

    def _update_presence(self, epcs):
        """维护在场标签集合（这是「防连刷」的关键）：

        - 新出现的标签（之前不在场）-> 触发 on_card（视为一次刷卡）
        - 连续 PRESENCE_TIMEOUT 秒没盘存到的标签 -> 认为已离开，从集合移除；
          下次它再靠近才会再次触发。
        这样标签一直贴着不放只会触发一次，必须「拿开再放回」才算下一次刷卡。
        """
        now = time.time()
        current = set(epcs)

        for epc in list(self._present.keys()):
            if epc not in current and now - self._present[epc] > config.PRESENCE_TIMEOUT:
                del self._present[epc]

        for epc in current:
            if epc not in self._present:
                self._present[epc] = now
                if self.on_card:
                    self.on_card(epc)
            else:
                self._present[epc] = now

    # ---------- 询查标签（Inventory） ----------

    def inventory(self):
        """发送询查标签命令（Cmd=0x01），返回解析出的 EPC 列表。"""
        cmd = build_command(config.READER_ADDR, 0x01)   # 询查 EPC：Data 为空
        self.ser.reset_input_buffer()                   # 发命令前清空旧数据，避免残留脏数据
        self.ser.write(cmd)
        body = self._read_response()
        if body is None:
            return []
        if len(body) < 3:
            return []
        status = body[2]
        data = body[3:]
        # 状态 0x01~0x04 都可能携带标签数据；其它状态（如 0xFB 无标签可操作）视为无标签
        if status in (0x01, 0x02, 0x03, 0x04):
            return parse_epcs(data)
        return []

    def _read_response(self):
        """读取一帧响应，返回 body（不含 Len 和 CRC）；超时/CRC 错误返回 None。"""
        head = self.ser.read(1)
        if not head:
            return None
        length = head[0]
        if length < 5 or length > 96:        # Len 越界 -> 脏数据
            return None
        body = self.ser.read(length)
        if len(body) != length:              # 没读满一帧 -> 拆包/超时
            return None
        if not check_frame(head + body):     # CRC 校验失败 -> 脏数据
            return None
        return body[:-2]                     # 去掉末尾 CRC 两字节
