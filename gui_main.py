#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus GUI 测试工具 v2.1
功能：支持批量测试（Excel驱动）和手动发送原始报文
特性：RTU模式自动添加CRC校验，TCP模式自动添加MBAP头
兼容你的 modbus_rtu.py 和 modbus_tcp.py 模块
"""

import sys
import os
import time
import threading
from datetime import datetime
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
import configparser
import pandas as pd
from pathlib import Path
import struct
from typing import Optional, Dict, Any, List, Tuple

# ===== 导入你的 Modbus 模块 =====
from modbus_tcp import tcp_connect, tcp_recv
from modbus_rtu import rtu_connect, rtu_recv
from modbus_core import (
    build_single_message,
    calc_crc16,
    format_hex_bytes,
    parse_bit_response,
    parse_register_response,
    validate_quantity
)
from modbus_common import (
    check_error_response,
    get_case_params,
    parse_response_data,
    run_batch
)


# ===== 常量定义 =====
ERROR_CODES = {
    1: '非法功能码',
    2: '非法数据地址',
    3: '非法数据值',
    4: '从站设备故障',
    5: '确认',
    6: '从站设备忙',
    8: '存储奇偶校验错误',
    10: '不可用网关路径',
    11: '网关目标设备无响应'
}

# 表格列索引
COL_CASE_ID = 0
COL_DEVICE_ADDR = 1
COL_FUNC_CODE = 2
COL_ADDRESS = 3
COL_QUANTITY = 4
COL_DATA_TYPE = 5
COL_BYTE_ORDER = 6
COL_RESULT = 7
COL_STATUS = 8


# ===== 配置管理器 =====
class ConfigManager:
    """配置管理类"""
    def __init__(self, config_path: str = 'config.ini'):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load()
    
    def load(self):
        """加载配置文件"""
        if os.path.exists(self.config_path):
            self.config.read(self.config_path, encoding='utf-8')
            self._ensure_defaults()
        else:
            self._create_default()
    
    def _ensure_defaults(self):
        """确保必要的配置项存在"""
        if 'tcp' not in self.config:
            self.config['tcp'] = {'host': '127.0.0.1', 'port': '502'}
        if 'rtu' not in self.config:
            self.config['rtu'] = {'port': 'COM1', 'baudrate': '9600'}
        if 'timing' not in self.config:
            self.config['timing'] = {'timeout_sec': '3'}
    
    def _create_default(self):
        """创建默认配置"""
        self.config['tcp'] = {'host': '127.0.0.1', 'port': '502'}
        self.config['rtu'] = {'port': 'COM1', 'baudrate': '9600'}
        self.config['timing'] = {'timeout_sec': '3'}
        self.save()
    
    def save(self):
        """保存配置"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)
    
    def get_tcp_config(self) -> Dict[str, str]:
        return {
            'host': self.config.get('tcp', 'host', fallback='127.0.0.1'),
            'port': self.config.get('tcp', 'port', fallback='502')
        }
    
    def get_rtu_config(self) -> Dict[str, str]:
        return {
            'port': self.config.get('rtu', 'port', fallback='COM1'),
            'baudrate': self.config.get('rtu', 'baudrate', fallback='9600')
        }
    
    def get_timeout(self) -> int:
        return self.config.getint('timing', 'timeout_sec', fallback=3)
    
    def set_tcp_config(self, host: str, port: str):
        self.config['tcp'] = {'host': host, 'port': port}
    
    def set_rtu_config(self, port: str, baudrate: str):
        self.config['rtu'] = {'port': port, 'baudrate': baudrate}


# ===== 日志组件 =====
class LogRedirector(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setLineWrapMode(QTextEdit.NoWrap)
        self.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 5px;
                font-family: Consolas, monospace;
            }
        """)
        self.max_lines = 10000
        self.log_filter = "所有"
        self._html_cache = []
        
    def set_log_filter(self, level: str):
        """设置日志过滤器"""
        self.log_filter = level
        
    def append_log(self, message: str, level: str = "INFO"):
        """添加日志消息，支持颜色区分和过滤"""
        if self.log_filter != "所有" and self.log_filter != level:
            return
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "#d4d4d4", "WARNING": "#ffcc00", "ERROR": "#ff4444",
            "DEBUG": "#808080", "SUCCESS": "#4ec9b0", "SEND": "#569cd6",
            "RECV": "#ce9178", "RESULT": "#c586c0"
        }
        color = color_map.get(level, "#d4d4d4")
        
        html = f'<span style="color:#808080;">[{timestamp}]</span> '
        html += f'<span style="color:{color}; font-weight:bold;">[{level}]</span> '
        html += f'<span style="color:#d4d4d4;">{self._html_escape(message)}</span><br>'
        self.append(html)
        self._html_cache.append(html)
        
        if self.document().blockCount() > self.max_lines:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, 100)
            cursor.removeSelectedText()
            self._html_cache = self._html_cache[100:]
        
        scrollbar = self.verticalScrollBar()
        QTimer.singleShot(10, lambda: scrollbar.setValue(scrollbar.maximum()))
    
    def get_full_log(self) -> str:
        """获取完整的日志文本"""
        return self.toPlainText()
    
    def _html_escape(self, text: str) -> str:
        return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ===== 数据解析工具类 =====
class ModbusDataParser:
    """Modbus响应数据解析器"""
    
    @staticmethod
    def parse_bit_data(data_bytes: bytes, start_bit: int = 0, bit_count: int = None, 
                       byte_order: str = 'ABCD') -> List[Dict]:
        """解析位数据（线圈/离散输入/寄存器值的位）"""
        if not data_bytes:
            return []
        
        if len(data_bytes) == 2 and byte_order in ['BADC', 'CDAB', 'DCBA']:
            data_bytes = bytes([data_bytes[1], data_bytes[0]])
        
        if bit_count is None:
            bit_count = len(data_bytes) * 8
        
        results = []
        bit_index = 0
        for byte_idx, byte_val in enumerate(data_bytes):
            for bit_pos in range(8):
                if bit_index >= bit_count:
                    break
                if bit_index >= start_bit:
                    val = (byte_val >> bit_pos) & 1
                    results.append({
                        'index': bit_index,
                        'byte': byte_idx,
                        'bit': bit_pos,
                        'value': val,
                        'display': 'ON' if val else 'OFF',
                        'byte_hex': f'{byte_val:02X}'
                    })
                bit_index += 1
            if bit_index >= bit_count:
                break
        return results
    
    @staticmethod
    def parse_register_data(data_bytes: bytes, data_type: str = 'UINT16', 
                            byte_order: str = 'ABCD', start_index: int = 0, 
                            count: int = None) -> List[Dict]:
        """解析寄存器数据"""
        if not data_bytes:
            return []
        
        order_map = {
            'ABCD': lambda x: x,
            'BADC': lambda x: bytes([x[1], x[0], x[3], x[2]]) if len(x) >= 4 else x,
            'CDAB': lambda x: bytes([x[2], x[3], x[0], x[1]]) if len(x) >= 4 else x,
            'DCBA': lambda x: bytes([x[3], x[2], x[1], x[0]]) if len(x) >= 4 else x,
        }
        
        type_map = {
            'UINT16': ('H', 2, False),
            'INT16': ('h', 2, False),
            'UINT32': ('I', 4, False),
            'INT32': ('i', 4, False),
            'FLOAT': ('f', 4, False),
            'BIT': (None, 2, True),
        }
        
        if data_type not in type_map:
            data_type = 'UINT16'
        
        fmt, bytes_per_reg, is_bit = type_map[data_type]
        
        if count is None:
            if data_type in ['UINT16', 'INT16']:
                count = len(data_bytes) // 2
            else:
                count = len(data_bytes) // bytes_per_reg
        
        if len(data_bytes) == 2 and count == 0:
            count = 1
        
        results = []
        pos = 0
        
        for i in range(count):
            if pos + bytes_per_reg > len(data_bytes):
                break
            
            chunk = data_bytes[pos:pos + bytes_per_reg]
            
            if byte_order in order_map:
                chunk = order_map[byte_order](chunk)
            
            if is_bit:
                reg_val = (chunk[0] << 8) | chunk[1]
                for bit_pos in range(16):
                    bit_val = (reg_val >> bit_pos) & 1
                    results.append({
                        'index': i * 16 + bit_pos,
                        'register': i,
                        'bit': bit_pos,
                        'value': bit_val,
                        'display': 'ON' if bit_val else 'OFF',
                        'type': 'BIT'
                    })
            else:
                try:
                    if len(chunk) < bytes_per_reg:
                        chunk = chunk + b'\x00' * (bytes_per_reg - len(chunk))
                    val = struct.unpack('>' + fmt, chunk)[0]
                    results.append({
                        'index': i,
                        'value': val,
                        'display': str(val),
                        'type': data_type,
                        'hex': ' '.join([f'{b:02X}' for b in chunk])
                    })
                except:
                    results.append({
                        'index': i,
                        'value': None,
                        'display': '解析错误',
                        'type': data_type,
                        'hex': ' '.join([f'{b:02X}' for b in chunk])
                    })
            
            pos += bytes_per_reg
        
        return results
    
    @staticmethod
    def parse_response_full(response_bytes: bytes, is_rtu: bool = True) -> Tuple[Optional[bytes], Optional[Dict]]:
        """解析完整响应报文，提取PDU和数据"""
        if not response_bytes:
            return None, None
        
        if is_rtu:
            if len(response_bytes) < 4:
                return None, None
            pdu = response_bytes[1:-2]
            if len(pdu) < 2:
                return None, None
            func_code = pdu[0]
            if func_code & 0x80:
                error_code = pdu[1] if len(pdu) > 1 else 0
                error_msg = ERROR_CODES.get(error_code, f'未知错误(0x{error_code:02X})')
                return pdu, {'error': True, 'func_code': func_code, 'error_code': error_code, 'message': error_msg}
            
            if func_code in [5, 6, 15, 16]:
                data_bytes = b''
            elif func_code in [3, 4]:
                if len(pdu) >= 2:
                    byte_count = pdu[1]
                    data_bytes = pdu[2:2+byte_count] if byte_count > 0 else b''
                else:
                    data_bytes = b''
            elif func_code in [1, 2]:
                if len(pdu) >= 2:
                    byte_count = pdu[1]
                    data_bytes = pdu[2:2+byte_count] if byte_count > 0 else b''
                else:
                    data_bytes = b''
            else:
                data_bytes = b''
            
            return pdu, {
                'func_code': func_code,
                'byte_count': len(data_bytes),
                'data_bytes': data_bytes,
                'error': False
            }
        
        else:  # TCP
            if len(response_bytes) < 8:
                return None, None
            pdu = response_bytes[6:]
            if len(pdu) < 2:
                return None, None
            
            unit_id = pdu[0]
            func_code = pdu[1]
            
            if func_code & 0x80:
                error_code = pdu[2] if len(pdu) > 2 else 0
                error_msg = ERROR_CODES.get(error_code, f'未知错误(0x{error_code:02X})')
                return pdu, {'error': True, 'func_code': func_code, 'error_code': error_code, 'message': error_msg}
            
            if func_code in [5, 6, 15, 16]:
                data_bytes = b''
            elif func_code in [3, 4]:
                if len(pdu) >= 3:
                    byte_count = pdu[2]
                    data_bytes = pdu[3:3+byte_count] if byte_count > 0 else b''
                else:
                    data_bytes = b''
            elif func_code in [1, 2]:
                if len(pdu) >= 3:
                    byte_count = pdu[2]
                    data_bytes = pdu[3:3+byte_count] if byte_count > 0 else b''
                else:
                    data_bytes = b''
            else:
                data_bytes = b''
            
            return pdu, {
                'func_code': func_code,
                'byte_count': len(data_bytes),
                'data_bytes': data_bytes,
                'error': False,
                'unit_id': unit_id
            }


# ===== Modbus 管理器 =====
class ModbusManager:
    def __init__(self, timeout: int = 3):
        self.connection = None
        self.protocol = None
        self.is_connected = False
        self.connection_params = {}
        self.timeout = timeout
        self.log_callback = None
    
    # ---- 连接管理 ----
    def connect_tcp(self, host: str = '127.0.0.1', port: int = 502) -> bool:
        try:
            port = int(port)
            sock = tcp_connect(host, port, self.timeout * 1000)
            if sock:
                self.connection = sock
                self.protocol = 'tcp'
                self.connection_params = {'host': host, 'port': port}
                self.is_connected = True
                return True
            return False
        except Exception as e:
            self._log_to_gui(f"TCP连接失败: {e}", "ERROR")
            return False
    
    def connect_rtu(self, port: str = 'COM1', baudrate: int = 9600, timeout_ms: int = 3000, 
                    data_bits: int = 8, parity: str = 'N', stop_bits: float = 1) -> bool:
        try:
            baudrate = int(baudrate)
            timeout_ms = int(timeout_ms)
            data_bits = int(data_bits)
            if isinstance(stop_bits, str):
                stop_bits = 1.5 if stop_bits == '1.5' else float(stop_bits)
            
            ser = rtu_connect(
                port=port,
                baudrate=baudrate,
                timeout_ms=timeout_ms,
                data_bits=data_bits,
                parity=parity,
                stop_bits=stop_bits
            )
            if ser:
                self.connection = ser
                self.protocol = 'rtu'
                self.connection_params = {
                    'port': port, 'baudrate': baudrate,
                    'timeout_ms': timeout_ms, 'data_bits': data_bits,
                    'parity': parity, 'stop_bits': stop_bits
                }
                self.is_connected = True
                return True
            return False
        except Exception as e:
            self._log_to_gui(f"RTU连接失败: {e}", "ERROR")
            return False
    
    def disconnect(self):
        try:
            if self.connection:
                self.connection.close()
            self.is_connected = False
            self.connection = None
            self._log_to_gui("已断开连接", "INFO")
        except Exception as e:
            self._log_to_gui(f"断开失败: {e}", "ERROR")
    
    # ---- 请求构建（协议无关） ----
    def _build_request(self, slave_id: int, function_code: int, address: int, data) -> bytes:
        """构建请求报文（协议无关）"""
        pdu = build_single_message(
            addr=slave_id,
            func=function_code,
            start=address,
            num=data
        )
        
        if self.protocol == 'tcp':
            transaction_id = int(time.time() * 1000) % 65535
            tcp_request = bytearray()
            tcp_request.append((transaction_id >> 8) & 0xFF)
            tcp_request.append(transaction_id & 0xFF)
            tcp_request.append(0x00)
            tcp_request.append(0x00)
            tcp_request.append(0x00)
            tcp_request.append(len(pdu))
            tcp_request.extend(pdu)
            return bytes(tcp_request)
        return pdu
    
    # ---- 响应解析（协议无关） ----
    def _parse_response(self, response: bytes) -> Optional[Dict]:
        """解析响应报文（协议无关）"""
        if not response:
            return None
        
        if self.protocol == 'tcp':
            if len(response) <= 6:
                self._log_to_gui("响应太短", "ERROR")
                return None
            pdu = response[6:]
            # TCP: [单元ID] [功能码] [字节数] [数据...]
            func_idx, byte_count_idx, data_start_idx = 1, 2, 3
        else:  # RTU
            if len(response) <= 3:
                self._log_to_gui("响应太短", "ERROR")
                return None
            pdu = response[1:-2]
            # RTU: [功能码] [字节数] [数据...]
            func_idx, byte_count_idx, data_start_idx = 0, 1, 2
        
        if len(pdu) < 3:
            self._log_to_gui("PDU太短", "ERROR")
            return None
        
        func_code = pdu[func_idx]
        
        # 检查错误响应
        if func_code & 0x80:
            error_code = pdu[func_idx + 1] if len(pdu) > func_idx + 1 else 0
            error_msg = ERROR_CODES.get(error_code, f'未知错误(0x{error_code:02X})')
            self._log_to_gui(f"错误: 0x{func_code:02X} 错误码: 0x{error_code:02X} ({error_msg})", "ERROR")
            return None
        
        byte_count = pdu[byte_count_idx]
        data_bytes = pdu[data_start_idx:data_start_idx + byte_count]
        
        return {
            'func_code': func_code,
            'byte_count': byte_count,
            'data_bytes': data_bytes,
            'pdu': pdu
        }
    
    # ---- 高层接口 ----
    def read(self, slave_id: int, address: int, count: int, function_code: int, 
             data_type: str = 'UINT16', byte_order: str = 'ABCD') -> Optional[List]:
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return None
        
        try:
            request = self._build_request(slave_id, function_code, address, count)
            self._log_to_gui(f"发送: {format_hex_bytes(request)}", "SEND")
            
            if not self._send_raw(request):
                return None
            
            response = self._recv()
            if response is None:
                self._log_to_gui("接收超时", "WARNING")
                return None
            
            self._log_to_gui(f"接收: {format_hex_bytes(response)}", "RECV")
            
            parsed = self._parse_response(response)
            if parsed is None:
                return None
            
            data_bytes = parsed['data_bytes']
            self._log_to_gui(f"数据字节: {format_hex_bytes(data_bytes)}", "DEBUG")
            
            if function_code in [1, 2]:
                result = parse_bit_response(data_bytes, "", count)
                if result:
                    values = [val for _, val in result]
                    self._log_to_gui(f"结果: {values}", "RESULT")
                    return values
                return []
            else:
                result = parse_register_response(data_bytes, "", data_type, byte_order)
                if result:
                    values = []
                    for item in result:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            values.append(item[1])
                        else:
                            values.append(item)
                    self._log_to_gui(f"结果: {values}", "RESULT")
                    return values
                return []
            
        except Exception as e:
            self._log_to_gui(f"读取失败: {e}", "ERROR")
            return None
    
    def write(self, slave_id: int, address: int, values, function_code: int) -> bool:
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return False
        
        try:
            request = self._build_request(slave_id, function_code, address, values)
            self._log_to_gui(f"写入: {format_hex_bytes(request)}", "SEND")
            
            if not self._send_raw(request):
                return False
            
            response = self._recv()
            if response is None:
                self._log_to_gui("写入超时", "WARNING")
                return False
            
            self._log_to_gui(f"响应: {format_hex_bytes(response)}", "RECV")
            
            parsed = self._parse_response(response)
            return parsed is not None
            
        except Exception as e:
            self._log_to_gui(f"写入失败: {e}", "ERROR")
            return False
    
    def send_raw_and_receive(self, raw_bytes: bytes) -> Optional[bytes]:
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return None
        
        try:
            if self.protocol == 'tcp':
                is_full_tcp = False
                if len(raw_bytes) >= 7:
                    protocol_id = (raw_bytes[2] << 8) | raw_bytes[3]
                    length = (raw_bytes[4] << 8) | raw_bytes[5]
                    if protocol_id == 0 and (length + 6) == len(raw_bytes):
                        is_full_tcp = True
                
                if not is_full_tcp:
                    transaction_id = int(time.time() * 1000) % 65535
                    tcp_packet = bytearray()
                    tcp_packet.append((transaction_id >> 8) & 0xFF)
                    tcp_packet.append(transaction_id & 0xFF)
                    tcp_packet.append(0x00)
                    tcp_packet.append(0x00)
                    pdu_length = len(raw_bytes)
                    tcp_packet.append((pdu_length >> 8) & 0xFF)
                    tcp_packet.append(pdu_length & 0xFF)
                    tcp_packet.extend(raw_bytes)
                    raw_bytes = bytes(tcp_packet)
                    self._log_to_gui(f"添加MBAP头: {format_hex_bytes(raw_bytes)}", "DEBUG")
            
            self._log_to_gui(f"发送原始: {format_hex_bytes(raw_bytes)}", "SEND")
            
            if not self._send_raw(raw_bytes):
                return None
            
            response = self._recv()
            if response is None:
                self._log_to_gui("接收超时", "WARNING")
                return None
            
            self._log_to_gui(f"接收: {format_hex_bytes(response)}", "RECV")
            return response
        except Exception as e:
            self._log_to_gui(f"收发异常: {e}", "ERROR")
            return None
    
    def _send_raw(self, raw_bytes: bytes) -> bool:
        if self.protocol == 'tcp':
            try:
                self.connection.send(raw_bytes)
                return True
            except Exception as e:
                self._log_to_gui(f"TCP发送失败: {e}", "ERROR")
                return False
        else:
            try:
                bytes_written = self.connection.write(raw_bytes)
                self._log_to_gui(f"实际发送字节数: {bytes_written}", "DEBUG")
                return True
            except Exception as e:
                self._log_to_gui(f"RTU发送失败: {e}", "ERROR")
                return False
    
    def _recv(self) -> Optional[bytes]:
        if self.protocol == 'tcp':
            try:
                return tcp_recv(self.connection, int(self.timeout * 1000))
            except Exception as e:
                self._log_to_gui(f"TCP接收异常: {e}", "DEBUG")
                return None
        else:
            try:
                return rtu_recv(self.connection, int(self.timeout * 1000))
            except Exception as e:
                self._log_to_gui(f"RTU接收异常: {e}", "DEBUG")
                return None
    
    def _log_to_gui(self, message: str, level: str = "INFO"):
        if self.log_callback:
            self.log_callback(message, level)


# ===== 主GUI窗口 =====
class ModbusGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()
        self.modbus_manager = ModbusManager(self.config_manager.get_timeout())
        self.modbus_manager.log_callback = self.on_modbus_log
        self.excel_df = None
        self.current_row_index = 0
        self.batch_index = 0
        self.batch_success = 0
        self.batch_total = 0
        self.timer = None
        self.init_ui()
        self.load_config()
    
    def on_modbus_log(self, message: str, level: str = "INFO"):
        self.log_display.append_log(message, level)
    
    def init_ui(self):
        self.setWindowTitle("Modbus GUI 测试工具 v2.1")
        self.setGeometry(100, 100, 1200, 900)
        self.setStyleSheet("""
            QMainWindow { background-color: #2d2d2d; }
            QGroupBox { color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 5px; margin-top: 10px; font-weight: bold; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
            QLabel { color: #d4d4d4; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #1177bb; }
            QPushButton:disabled { background-color: #3c3c3c; color: #808080; }
            QLineEdit, QSpinBox, QComboBox, QTextEdit { background-color: #3c3c3c; color: #d4d4d4; border: 1px solid #555555; border-radius: 3px; padding: 5px; }
            QTabWidget::pane { border: 1px solid #3c3c3c; background-color: #2d2d2d; }
            QTabBar::tab { background-color: #3c3c3c; color: #d4d4d4; padding: 8px 15px; margin-right: 2px; }
            QTabBar::tab:selected { background-color: #0e639c; }
            QTableWidget { background-color: #1e1e1e; color: #d4d4d4; gridline-color: #3c3c3c; }
            QHeaderView::section { background-color: #3c3c3c; color: #d4d4d4; padding: 4px; }
            QTextEdit { font-family: Consolas, monospace; }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # ---- 连接配置 ----
        config_group = QGroupBox("连接配置")
        config_layout = QGridLayout()
        config_layout.setSpacing(8)
        config_layout.setContentsMargins(10, 15, 10, 10)
        
        config_layout.addWidget(QLabel("协议:"), 0, 0)
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["TCP", "RTU"])
        self.protocol_combo.currentTextChanged.connect(self.on_protocol_changed)
        config_layout.addWidget(self.protocol_combo, 0, 1)
        
        # TCP参数
        self.tcp_widget = QWidget()
        tcp_layout = QHBoxLayout(self.tcp_widget)
        tcp_layout.setContentsMargins(0, 0, 0, 0)
        tcp_layout.addWidget(QLabel("IP:"))
        self.ip_edit = QLineEdit("127.0.0.1")
        tcp_layout.addWidget(self.ip_edit)
        tcp_layout.addWidget(QLabel("端口:"))
        self.port_edit = QLineEdit("502")
        self.port_edit.setMaximumWidth(60)
        tcp_layout.addWidget(self.port_edit)
        
        # RTU参数
        self.rtu_widget = QWidget()
        rtu_layout = QHBoxLayout(self.rtu_widget)
        rtu_layout.setContentsMargins(0, 0, 0, 0)
        rtu_layout.addWidget(QLabel("串口:"))
        self.serial_combo = QComboBox()
        self.serial_combo.addItems([f"COM{i}" for i in range(1, 9)])
        rtu_layout.addWidget(self.serial_combo)
        rtu_layout.addWidget(QLabel("波特率:"))
        self.baudrate_combo = QComboBox()
        self.baudrate_combo.addItems(["9600", "19200", "38400", "115200"])
        rtu_layout.addWidget(self.baudrate_combo)
        rtu_layout.addWidget(QLabel("校验位:"))
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(["N", "E", "O"])
        rtu_layout.addWidget(self.parity_combo)
        rtu_layout.addWidget(QLabel("停止位:"))
        self.stopbits_combo = QComboBox()
        self.stopbits_combo.addItems(["1", "1.5", "2"])
        rtu_layout.addWidget(self.stopbits_combo)
        rtu_layout.addWidget(QLabel("数据位:"))
        self.bytesize_combo = QComboBox()
        self.bytesize_combo.addItems(["8", "7"])
        rtu_layout.addWidget(self.bytesize_combo)
        
        self.protocol_stack = QStackedWidget()
        self.protocol_stack.addWidget(self.tcp_widget)
        self.protocol_stack.addWidget(self.rtu_widget)
        config_layout.addWidget(self.protocol_stack, 0, 2, 1, 5)
        
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.connect_btn.setMinimumWidth(100)
        config_layout.addWidget(self.connect_btn, 0, 7)
        
        config_group.setLayout(config_layout)
        main_layout.addWidget(config_group)
        
        # ---- Tab区域 ----
        self.tab_widget = QTabWidget()
        
        # Tab1: 批量测试
        tab_batch = QWidget()
        batch_layout = QVBoxLayout(tab_batch)
        
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("Excel文件:"))
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("选择Excel文件")
        file_layout.addWidget(self.file_path_edit)
        self.browse_btn = QPushButton("浏览")
        self.browse_btn.clicked.connect(self.browse_excel)
        file_layout.addWidget(self.browse_btn)
        self.load_btn = QPushButton("加载")
        self.load_btn.clicked.connect(self.load_excel)
        file_layout.addWidget(self.load_btn)
        self.run_all_btn = QPushButton("运行全部")
        self.run_all_btn.clicked.connect(self.run_all)
        self.run_all_btn.setEnabled(False)
        file_layout.addWidget(self.run_all_btn)
        batch_layout.addLayout(file_layout)
        
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(9)
        self.table_widget.setHorizontalHeaderLabels(["用例编号", "设备地址", "功能码", "起始地址", "寄存器数量", "数据类型", "字节顺序", "读取结果", "状态"])
        batch_layout.addWidget(self.table_widget)
        
        step_layout = QHBoxLayout()
        self.prev_btn = QPushButton("上一步")
        self.prev_btn.clicked.connect(self.prev_row)
        self.prev_btn.setEnabled(False)
        step_layout.addWidget(self.prev_btn)
        self.next_btn = QPushButton("下一步")
        self.next_btn.clicked.connect(self.next_row)
        self.next_btn.setEnabled(False)
        step_layout.addWidget(self.next_btn)
        self.run_step_btn = QPushButton("执行当前行")
        self.run_step_btn.clicked.connect(self.run_current_row)
        self.run_step_btn.setEnabled(False)
        step_layout.addWidget(self.run_step_btn)
        self.row_info_label = QLabel("未加载数据")
        step_layout.addWidget(self.row_info_label)
        step_layout.addStretch()
        batch_layout.addLayout(step_layout)
        
        self.tab_widget.addTab(tab_batch, "批量测试")
        
        # Tab2: 手动发送
        tab_manual = QWidget()
        manual_layout = QVBoxLayout(tab_manual)
        
        param_group = QGroupBox("构建报文")
        param_layout = QGridLayout()
        param_layout.addWidget(QLabel("从站ID:"), 0, 0)
        self.manual_slave = QSpinBox()
        self.manual_slave.setRange(1, 255)
        self.manual_slave.setValue(1)
        param_layout.addWidget(self.manual_slave, 0, 1)
        param_layout.addWidget(QLabel("功能码:"), 0, 2)
        self.manual_func = QComboBox()
        self.manual_func.addItems(["01-读线圈", "02-读离散输入", "03-读保持寄存器",
                                   "04-读输入寄存器", "05-写单线圈", "06-写单寄存器",
                                   "15-写多线圈", "16-写多寄存器"])
        param_layout.addWidget(self.manual_func, 0, 3)
        param_layout.addWidget(QLabel("起始地址:"), 0, 4)
        self.manual_addr = QSpinBox()
        self.manual_addr.setRange(0, 65535)
        param_layout.addWidget(self.manual_addr, 0, 5)
        param_layout.addWidget(QLabel("数量/数据:"), 0, 6)
        self.manual_qty = QLineEdit()
        self.manual_qty.setPlaceholderText("读:数量, 写:1,2,3 或 3.14,2.71")
        param_layout.addWidget(self.manual_qty, 0, 7)
        param_group.setLayout(param_layout)
        manual_layout.addWidget(param_group)
        
        raw_group = QGroupBox("原始报文 (十六进制)")
        raw_layout = QVBoxLayout()
        self.raw_hex_edit = QTextEdit()
        self.raw_hex_edit.setPlaceholderText("输入十六进制报文，如: 01 03 00 00 00 01 (RTU自动加CRC)")
        self.raw_hex_edit.setMaximumHeight(60)
        raw_layout.addWidget(self.raw_hex_edit)
        raw_btn_layout = QHBoxLayout()
        self.build_btn = QPushButton("从参数构建")
        self.build_btn.clicked.connect(self.build_raw)
        raw_btn_layout.addWidget(self.build_btn)
        self.send_raw_btn = QPushButton("发送原始报文")
        self.send_raw_btn.clicked.connect(self.send_raw)
        self.send_raw_btn.setEnabled(False)
        raw_btn_layout.addWidget(self.send_raw_btn)
        self.clear_raw_btn = QPushButton("清空")
        self.clear_raw_btn.clicked.connect(lambda: self.raw_hex_edit.clear())
        raw_btn_layout.addWidget(self.clear_raw_btn)
        raw_layout.addLayout(raw_btn_layout)
        raw_group.setLayout(raw_layout)
        manual_layout.addWidget(raw_group)
        
        resp_group = QGroupBox("响应报文与数据解析")
        resp_layout = QVBoxLayout()
        
        resp_display_layout = QHBoxLayout()
        resp_display_layout.addWidget(QLabel("响应:"))
        self.manual_response = QLineEdit()
        self.manual_response.setReadOnly(True)
        self.manual_response.setPlaceholderText("发送后显示响应报文")
        resp_display_layout.addWidget(self.manual_response)
        resp_layout.addLayout(resp_display_layout)
        
        parse_layout = QGridLayout()
        parse_layout.addWidget(QLabel("数据类型:"), 0, 0)
        self.parse_type_combo = QComboBox()
        self.parse_type_combo.addItems(["BIT", "UINT16", "INT16", "UINT32", "INT32", "FLOAT"])
        parse_layout.addWidget(self.parse_type_combo, 0, 1)
        
        parse_layout.addWidget(QLabel("字节顺序:"), 0, 2)
        self.parse_order_combo = QComboBox()
        self.parse_order_combo.addItems(["ABCD", "BADC", "CDAB", "DCBA"])
        parse_layout.addWidget(self.parse_order_combo, 0, 3)
        
        parse_layout.addWidget(QLabel("起始位/索引:"), 0, 4)
        self.parse_start = QSpinBox()
        self.parse_start.setRange(0, 9999)
        self.parse_start.setValue(0)
        parse_layout.addWidget(self.parse_start, 0, 5)
        
        parse_layout.addWidget(QLabel("数量:"), 0, 6)
        self.parse_count = QSpinBox()
        self.parse_count.setRange(1, 9999)
        self.parse_count.setValue(16)
        parse_layout.addWidget(self.parse_count, 0, 7)
        
        self.parse_btn = QPushButton("解析数据")
        self.parse_btn.clicked.connect(self.parse_response_data)
        parse_layout.addWidget(self.parse_btn, 0, 8)
        
        resp_layout.addLayout(parse_layout)
        
        self.parse_result_text = QTextEdit()
        self.parse_result_text.setReadOnly(True)
        self.parse_result_text.setMaximumHeight(200)
        self.parse_result_text.setPlaceholderText("解析结果将显示在这里")
        resp_layout.addWidget(self.parse_result_text)
        
        resp_group.setLayout(resp_layout)
        manual_layout.addWidget(resp_group)
        
        self.tab_widget.addTab(tab_manual, "手动发送")
        main_layout.addWidget(self.tab_widget)
        
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout()
        log_toolbar = QHBoxLayout()
        log_toolbar.addWidget(QLabel("级别:"))
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["所有", "INFO", "SUCCESS", "WARNING", "ERROR", "SEND", "RECV"])
        self.log_level_combo.currentTextChanged.connect(self.on_log_level_changed)
        log_toolbar.addWidget(self.log_level_combo)
        log_toolbar.addStretch()
        self.clear_log_btn = QPushButton("清空")
        self.clear_log_btn.clicked.connect(self.clear_log)
        log_toolbar.addWidget(self.clear_log_btn)
        self.save_log_btn = QPushButton("另存为")
        self.save_log_btn.clicked.connect(self.save_log)
        log_toolbar.addWidget(self.save_log_btn)
        log_layout.addLayout(log_toolbar)
        self.log_display = LogRedirector()
        log_layout.addWidget(self.log_display)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)
        
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet("QStatusBar { color: #d4d4d4; background-color: #2d2d2d; }")
        self.status_bar.showMessage("就绪")
        
        self.on_protocol_changed("TCP")
        self.log_display.append_log("Modbus GUI 测试工具 v2.1 启动成功", "SUCCESS")
        self.log_display.append_log("支持BIT/UINT16/INT16/UINT32/INT32/FLOAT数据解析", "INFO")
        self.log_display.append_log("RTU模式自动添加CRC校验", "INFO")
    
    # ---- 辅助方法 ----
    def on_protocol_changed(self, protocol: str):
        if self.modbus_manager.is_connected:
            self.modbus_manager.disconnect()
            self.connect_btn.setText("连接")
            self.connect_btn.setStyleSheet("")
            self.send_raw_btn.setEnabled(False)
            self.run_all_btn.setEnabled(False)
            self.run_step_btn.setEnabled(False)
            self.status_bar.showMessage("已断开", 3000)
            self.log_display.append_log("协议切换，已自动断开连接", "INFO")
        
        self.protocol_stack.setCurrentIndex(0 if protocol == "TCP" else 1)
    
    def on_log_level_changed(self, level: str):
        self.log_display.set_log_filter(level)
    
    def load_config(self):
        try:
            tcp_config = self.config_manager.get_tcp_config()
            self.ip_edit.setText(tcp_config['host'])
            self.port_edit.setText(tcp_config['port'])
            
            rtu_config = self.config_manager.get_rtu_config()
            idx = self.serial_combo.findText(rtu_config['port'])
            if idx >= 0:
                self.serial_combo.setCurrentIndex(idx)
            idx = self.baudrate_combo.findText(rtu_config['baudrate'])
            if idx >= 0:
                self.baudrate_combo.setCurrentIndex(idx)
        except Exception as e:
            pass
    
    def save_config(self):
        try:
            self.config_manager.set_tcp_config(
                self.ip_edit.text(),
                self.port_edit.text()
            )
            self.config_manager.set_rtu_config(
                self.serial_combo.currentText(),
                self.baudrate_combo.currentText()
            )
            self.config_manager.save()
        except Exception as e:
            pass
    
    def toggle_connection(self):
        if not self.modbus_manager.is_connected:
            protocol = self.protocol_combo.currentText()
            try:
                success = False
                if protocol == "TCP":
                    ip = self.ip_edit.text()
                    port = int(self.port_edit.text())
                    success = self.modbus_manager.connect_tcp(ip, port)
                    if success:
                        self.log_display.append_log(f"TCP连接成功: {ip}:{port}", "SUCCESS")
                        self.status_bar.showMessage(f"已连接 - {protocol}", 3000)
                else:
                    port = self.serial_combo.currentText()
                    baudrate = int(self.baudrate_combo.currentText())
                    parity = self.parity_combo.currentText()
                    stop_bits = self.stopbits_combo.currentText()
                    data_bits = int(self.bytesize_combo.currentText())
                    success = self.modbus_manager.connect_rtu(port, baudrate, 3000, data_bits, parity, stop_bits)
                    if success:
                        self.log_display.append_log(f"RTU连接成功: {port} {baudrate}", "SUCCESS")
                        self.status_bar.showMessage(f"已连接 - {protocol}", 3000)
                if success:
                    self.connect_btn.setText("断开")
                    self.connect_btn.setStyleSheet("QPushButton { background-color: #a1260d; }")
                    self.send_raw_btn.setEnabled(True)
                    self.run_all_btn.setEnabled(True)
                    self.run_step_btn.setEnabled(True)
                    self.save_config()
                else:
                    self.log_display.append_log("连接失败", "ERROR")
            except Exception as e:
                self.log_display.append_log(f"连接错误: {e}", "ERROR")
        else:
            self.modbus_manager.disconnect()
            self.connect_btn.setText("连接")
            self.connect_btn.setStyleSheet("")
            self.send_raw_btn.setEnabled(False)
            self.run_all_btn.setEnabled(False)
            self.run_step_btn.setEnabled(False)
            self.status_bar.showMessage("已断开", 3000)
            self.log_display.append_log("已断开", "INFO")
    
    # ---- 批量测试 ----
    def browse_excel(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择Excel", "", "Excel文件 (*.xlsx *.xls)")
        if file_path:
            self.file_path_edit.setText(file_path)
    
    def load_excel(self):
        file_path = self.file_path_edit.text()
        if not file_path or not os.path.exists(file_path):
            self.log_display.append_log("请选择有效Excel文件", "WARNING")
            return
        try:
            self.excel_df = pd.read_excel(file_path)
            required = ['用例编号', '设备地址', '功能码', '起始地址', '寄存器数量']
            for col in required:
                if col not in self.excel_df.columns:
                    self.log_display.append_log(f"缺少列: {col}", "ERROR")
                    return
            self.current_row_index = 0
            self.update_table()
            self.row_info_label.setText(f"共 {len(self.excel_df)} 行")
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            self.run_step_btn.setEnabled(True)
            self.run_all_btn.setEnabled(True)
            self.log_display.append_log(f"加载 {len(self.excel_df)} 条用例", "SUCCESS")
        except Exception as e:
            self.log_display.append_log(f"加载失败: {e}", "ERROR")
    
    def update_table(self):
        if self.excel_df is None:
            return
        self.table_widget.setRowCount(len(self.excel_df))
        for i, row in self.excel_df.iterrows():
            self.table_widget.setItem(i, COL_CASE_ID, QTableWidgetItem(str(row.get('用例编号', ''))))
            self.table_widget.setItem(i, COL_DEVICE_ADDR, QTableWidgetItem(str(row.get('设备地址', ''))))
            self.table_widget.setItem(i, COL_FUNC_CODE, QTableWidgetItem(str(row.get('功能码', ''))))
            self.table_widget.setItem(i, COL_ADDRESS, QTableWidgetItem(str(row.get('起始地址', ''))))
            self.table_widget.setItem(i, COL_QUANTITY, QTableWidgetItem(str(row.get('寄存器数量', ''))))
            self.table_widget.setItem(i, COL_DATA_TYPE, QTableWidgetItem(str(row.get('数据类型', ''))))
            self.table_widget.setItem(i, COL_BYTE_ORDER, QTableWidgetItem(str(row.get('字节顺序', ''))))
            self.table_widget.setItem(i, COL_RESULT, QTableWidgetItem(""))
            self.table_widget.setItem(i, COL_STATUS, QTableWidgetItem("待执行"))
        self.table_widget.resizeColumnsToContents()
    
    def next_row(self):
        if self.excel_df is not None and self.current_row_index < len(self.excel_df) - 1:
            self.current_row_index += 1
            self.row_info_label.setText(f"第 {self.current_row_index+1}/{len(self.excel_df)} 行")
            self.table_widget.selectRow(self.current_row_index)
    
    def prev_row(self):
        if self.excel_df is not None and self.current_row_index > 0:
            self.current_row_index -= 1
            self.row_info_label.setText(f"第 {self.current_row_index+1}/{len(self.excel_df)} 行")
            self.table_widget.selectRow(self.current_row_index)
    
    def _format_result_for_display(self, result: list, data_type: str, byte_order: str, 
                                    bit_names: str, desc: str) -> str:
        """格式化结果用于显示 - 带描述信息，全部显示不截断"""
        if result is None:
            return "无数据"
        
        if not isinstance(result, list):
            return str(result)
        
        if len(result) == 0:
            return "空"
        
        # BIT类型 - 使用位名称，全部显示
        if data_type.upper() == 'BIT':
            bit_names_list = [n.strip() for n in bit_names.split(',') if n.strip()]
            lines = []
            for i, val in enumerate(result):
                if i < len(bit_names_list) and bit_names_list[i]:
                    lines.append(f"{bit_names_list[i]}={'ON' if val else 'OFF'}")
                else:
                    lines.append(f"位{i}={'ON' if val else 'OFF'}")
            return '; '.join(lines)
        
        # 寄存器类型 - 使用寄存器描述
        desc_list = [d.strip() for d in desc.split(',') if d.strip()]
        
        try:
            if data_type.upper() == 'FLOAT':
                if desc_list:
                    lines = []
                    for i, v in enumerate(result):
                        if i < len(desc_list) and desc_list[i]:
                            try:
                                lines.append(f"{desc_list[i]}: {float(v):.2f}")
                            except (ValueError, TypeError):
                                lines.append(f"{desc_list[i]}: {v}")
                        else:
                            try:
                                lines.append(f"寄存器{i}: {float(v):.2f}")
                            except (ValueError, TypeError):
                                lines.append(f"寄存器{i}: {v}")
                    return '; '.join(lines)
                else:
                    formatted = []
                    for v in result:
                        try:
                            formatted.append(f"{float(v):.2f}")
                        except (ValueError, TypeError):
                            formatted.append(str(v))
                    return ', '.join(formatted)
            else:
                if desc_list:
                    lines = []
                    for i, v in enumerate(result):
                        if i < len(desc_list) and desc_list[i]:
                            lines.append(f"{desc_list[i]}: {v}")
                        else:
                            lines.append(f"寄存器{i}: {v}")
                    return '; '.join(lines)
                else:
                    return ', '.join([str(v) for v in result])
        except Exception as e:
            return f"解析错误: {e}"
    
    def run_current_row(self):
        """执行当前行 - 根据Excel模板解析"""
        if self.excel_df is None or not self.modbus_manager.is_connected:
            self.log_display.append_log("请先连接并加载数据", "WARNING")
            return
        try:
            row = self.excel_df.iloc[self.current_row_index]
            slave = int(row.get('设备地址', 1))
            func = int(row.get('功能码', 3))
            addr = int(row.get('起始地址', 0))
            quantity = int(row.get('寄存器数量', 1))
            data_type = str(row.get('数据类型', 'UINT16')).strip()
            byte_order = str(row.get('字节顺序', 'ABCD')).strip()
            bit_names = str(row.get('位名称', '')).strip()
            desc = str(row.get('寄存器描述', '')).strip()
            
            is_read = func in [1, 2, 3, 4]
            
            self.log_display.append_log(
                f"执行用例 {self.current_row_index+1}: 从站={slave}, 功能码={func}, "
                f"地址={addr}, 数量={quantity}, 类型={data_type}, 顺序={byte_order}",
                "INFO"
            )
            
            if is_read:
                result = self.modbus_manager.read(
                    slave, addr, quantity, func, data_type, byte_order
                )
                if result is not None:
                    display_value = self._format_result_for_display(
                        result, data_type, byte_order, bit_names, desc
                    )
                    self.table_widget.setItem(self.current_row_index, COL_RESULT, 
                                              QTableWidgetItem(display_value))
                    status = "成功"
                    self.log_display.append_log(f"✓ 用例 {self.current_row_index+1} 成功: {display_value}", "SUCCESS")
                else:
                    self.table_widget.setItem(self.current_row_index, COL_RESULT, 
                                              QTableWidgetItem("读取失败"))
                    status = "失败"
                    self.log_display.append_log(f"✗ 用例 {self.current_row_index+1} 失败", "ERROR")
            else:
                self.log_display.append_log("写入操作需要填写数据，当前用例暂不支持自动写入", "WARNING")
                self.table_widget.setItem(self.current_row_index, COL_RESULT, 
                                          QTableWidgetItem("写入操作"))
                status = "跳过"
            
            self.table_widget.setItem(self.current_row_index, COL_STATUS, 
                                      QTableWidgetItem(status))
            self._set_status_color(self.current_row_index, status)
                
        except Exception as e:
            self.log_display.append_log(f"执行失败: {e}", "ERROR")
            self.table_widget.setItem(self.current_row_index, COL_RESULT, 
                                      QTableWidgetItem("异常"))
            self.table_widget.setItem(self.current_row_index, COL_STATUS, 
                                      QTableWidgetItem("异常"))
            self.table_widget.item(self.current_row_index, COL_STATUS).setBackground(QColor(80, 0, 80))
    
    def _set_status_color(self, row: int, status: str):
        color_map = {
            "成功": QColor(0, 80, 0),
            "失败": QColor(80, 0, 0),
            "跳过": QColor(80, 80, 0),
            "待执行": QColor(30, 30, 30),
            "异常": QColor(80, 0, 80)
        }
        color = color_map.get(status, QColor(30, 30, 30))
        self.table_widget.item(row, COL_STATUS).setBackground(color)
    
    def run_all(self):
        """异步批量执行所有用例"""
        if self.excel_df is None or not self.modbus_manager.is_connected:
            self.log_display.append_log("请先连接并加载数据", "WARNING")
            return
        
        self.run_all_btn.setEnabled(False)
        self.run_step_btn.setEnabled(False)
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        
        self.batch_index = 0
        self.batch_success = 0
        self.batch_total = len(self.excel_df)
        
        self.timer = QTimer()
        self.timer.timeout.connect(self._run_batch_next)
        self.timer.start(50)
        self.log_display.append_log(f"开始批量执行 {self.batch_total} 条用例...", "INFO")
    
    def _run_batch_next(self):
        """执行下一行（定时器回调）"""
        if self.batch_index >= self.batch_total:
            self.timer.stop()
            self.timer = None
            self.run_all_btn.setEnabled(True)
            self.run_step_btn.setEnabled(True)
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            self.log_display.append_log(
                f"批量执行完成: 成功 {self.batch_success}/{self.batch_total}", 
                "SUCCESS"
            )
            self.status_bar.showMessage(f"批量执行完成: 成功 {self.batch_success}/{self.batch_total}", 5000)
            return
        
        self.current_row_index = self.batch_index
        self.run_current_row()
        
        item = self.table_widget.item(self.batch_index, COL_STATUS)
        if item and item.text() == "成功":
            self.batch_success += 1
        
        self.batch_index += 1
        self.row_info_label.setText(f"执行中: {self.batch_index}/{self.batch_total}")
    
    # ---- 手动发送 ----
    def _float_to_registers(self, val: float, byte_order: str = 'ABCD') -> List[int]:
        """将浮点数转换为寄存器值列表"""
        packed = struct.pack('>f', val)
        if byte_order.upper() == 'BADC':
            packed = bytes([packed[1], packed[0], packed[3], packed[2]])
        elif byte_order.upper() == 'CDAB':
            packed = bytes([packed[2], packed[3], packed[0], packed[1]])
        elif byte_order.upper() == 'DCBA':
            packed = bytes([packed[3], packed[2], packed[1], packed[0]])
        
        reg1 = (packed[0] << 8) | packed[1]
        reg2 = (packed[2] << 8) | packed[3]
        return [reg1, reg2]
    
    def build_raw(self):
        try:
            slave = self.manual_slave.value()
            func_text = self.manual_func.currentText()
            func = int(func_text.split('-')[0])
            addr = self.manual_addr.value()
            qty_text = self.manual_qty.text().strip()
            
            if not qty_text:
                self.log_display.append_log("请填写数量或数据", "WARNING")
                return
            
            protocol = self.protocol_combo.currentText()
            data_type = self.parse_type_combo.currentText()
            byte_order = self.parse_order_combo.currentText()
            
            if ',' in qty_text:
                raw_values = [v.strip() for v in qty_text.split(',')]
            else:
                raw_values = [qty_text]
            
            if func in [1, 2, 3, 4]:
                num = int(qty_text)
                pdu = bytearray([
                    slave,
                    func,
                    (addr >> 8) & 0xFF,
                    addr & 0xFF,
                    (num >> 8) & 0xFF,
                    num & 0xFF
                ])
            elif func == 5:
                values = [int(v.strip()) for v in raw_values]
                if not values:
                    self.log_display.append_log("请填写数据值", "WARNING")
                    return
                value = 0xFF00 if values[0] != 0 else 0x0000
                pdu = bytearray([
                    slave,
                    func,
                    (addr >> 8) & 0xFF,
                    addr & 0xFF,
                    (value >> 8) & 0xFF,
                    value & 0xFF
                ])
            elif func == 6:
                if data_type.upper() == 'FLOAT':
                    try:
                        self.log_display.append_log("⚠️ 功能码06只能写单个寄存器(16位)，浮点数建议使用功能码16写2个寄存器", "WARNING")
                        val = float(raw_values[0])
                        int_val = int(val) & 0xFFFF
                        pdu = bytearray([
                            slave,
                            func,
                            (addr >> 8) & 0xFF,
                            addr & 0xFF,
                            (int_val >> 8) & 0xFF,
                            int_val & 0xFF
                        ])
                    except ValueError:
                        self.log_display.append_log("数据格式错误: 请输入有效的浮点数", "ERROR")
                        return
                else:
                    values = [int(v.strip()) for v in raw_values]
                    if not values:
                        self.log_display.append_log("请填写数据值", "WARNING")
                        return
                    pdu = bytearray([
                        slave,
                        func,
                        (addr >> 8) & 0xFF,
                        addr & 0xFF,
                        (values[0] >> 8) & 0xFF,
                        values[0] & 0xFF
                    ])
            elif func == 15:
                values = [int(v.strip()) for v in raw_values]
                if not values:
                    self.log_display.append_log("请填写数据值", "WARNING")
                    return
                byte_count = (len(values) + 7) // 8
                data_bytes = bytearray()
                for i in range(0, len(values), 8):
                    byte_val = 0
                    for j in range(8):
                        if i + j < len(values) and values[i + j] != 0:
                            byte_val |= (1 << j)
                    data_bytes.append(byte_val)
                pdu = bytearray([
                    slave,
                    func,
                    (addr >> 8) & 0xFF,
                    addr & 0xFF,
                    (len(values) >> 8) & 0xFF,
                    len(values) & 0xFF,
                    byte_count
                ])
                pdu.extend(data_bytes)
            elif func == 16:
                if data_type.upper() == 'FLOAT':
                    try:
                        reg_values = []
                        for v_str in raw_values:
                            val = float(v_str)
                            regs = self._float_to_registers(val, byte_order)
                            reg_values.extend(regs)
                        
                        register_count = len(reg_values)
                        byte_count = register_count * 2
                        
                        pdu = bytearray([
                            slave,
                            func,
                            (addr >> 8) & 0xFF,
                            addr & 0xFF,
                            (register_count >> 8) & 0xFF,
                            register_count & 0xFF,
                            byte_count
                        ])
                        for reg_val in reg_values:
                            pdu.append((reg_val >> 8) & 0xFF)
                            pdu.append(reg_val & 0xFF)
                        
                        self.log_display.append_log(
                            f"浮点数 {raw_values} 转换为寄存器值: {reg_values}",
                            "DEBUG"
                        )
                    except ValueError:
                        self.log_display.append_log("数据格式错误: 请输入有效的浮点数", "ERROR")
                        return
                else:
                    values = [int(v.strip()) for v in raw_values]
                    if not values:
                        self.log_display.append_log("请填写数据值", "WARNING")
                        return
                    byte_count = len(values) * 2
                    pdu = bytearray([
                        slave,
                        func,
                        (addr >> 8) & 0xFF,
                        addr & 0xFF,
                        (len(values) >> 8) & 0xFF,
                        len(values) & 0xFF,
                        byte_count
                    ])
                    for v in values:
                        pdu.append((v >> 8) & 0xFF)
                        pdu.append(v & 0xFF)
            else:
                self.log_display.append_log(f"不支持的功能码: {func}", "ERROR")
                return
            
            if protocol == "RTU":
                crc = calc_crc16(pdu)
                request = pdu + crc
            else:
                request = bytes(pdu)
            
            hex_str = ' '.join([f'{b:02X}' for b in request])
            self.raw_hex_edit.setText(hex_str)
            self.log_display.append_log(f"构建报文: {hex_str} ({protocol})", "SUCCESS")
            
        except ValueError as e:
            self.log_display.append_log(f"数据格式错误: {e}", "ERROR")
        except Exception as e:
            self.log_display.append_log(f"构建失败: {e}", "ERROR")
    
    def send_raw(self):
        """发送原始报文（RTU自动CRC，TCP自动MBAP头）"""
        hex_text = self.raw_hex_edit.toPlainText().strip()
        if not hex_text:
            self.log_display.append_log("请输入报文", "WARNING")
            return
        try:
            hex_str = hex_text.replace(' ', '').replace('\n', '')
            raw_bytes = bytes.fromhex(hex_str)
            
            # 如果是RTU模式，自动计算CRC并附加（用户输入不含CRC）
            if self.protocol_combo.currentText() == "RTU":
                crc = calc_crc16(raw_bytes)
                raw_bytes = raw_bytes + crc
                self.log_display.append_log(f"RTU自动添加CRC: {format_hex_bytes(crc)}", "DEBUG")
                # 更新显示报文（含CRC）
                self.raw_hex_edit.setText(' '.join([f'{b:02X}' for b in raw_bytes]))
            
            response = self.modbus_manager.send_raw_and_receive(raw_bytes)
            if response is not None:
                response_hex = ' '.join([f'{b:02X}' for b in response])
                self.manual_response.setText(response_hex)
                self.log_display.append_log("发送成功", "SUCCESS")
                self.parse_response_data()
            else:
                self.manual_response.setText("无响应")
        except Exception as e:
            self.log_display.append_log(f"发送失败: {e}", "ERROR")
    
    # ---- 数据解析 ----
    def _parse_write_response(self, pdu: bytes, func_code: int) -> str:
        try:
            func_name = {
                5: '写单个线圈',
                6: '写单个寄存器',
                15: '写多个线圈',
                16: '写多个寄存器'
            }.get(func_code, f'0x{func_code:02X}')
            
            if len(pdu) < 4:
                return f"写入响应异常: PDU长度不足 ({len(pdu)}字节)"
            
            if func_code in [5, 6]:
                addr = (pdu[1] << 8) | pdu[2]
                value = (pdu[3] << 8) | pdu[4]
                
                if func_code == 5:
                    val_display = 'ON' if value == 0xFF00 else 'OFF'
                else:
                    val_display = str(value)
                
                return (
                    f"✓ 写入成功!\n"
                    f"  功能码: {func_name} (0x{func_code:02X})\n"
                    f"  地址: 0x{addr:04X} ({addr})\n"
                    f"  写入值: {val_display}\n"
                    f"  响应报文: {format_hex_bytes(pdu)}"
                )
            
            elif func_code in [15, 16]:
                if len(pdu) < 4:
                    return f"写入响应异常: PDU长度不足 ({len(pdu)}字节)"
                addr = (pdu[1] << 8) | pdu[2]
                quantity = (pdu[3] << 8) | pdu[4]
                
                return (
                    f"✓ 写入成功!\n"
                    f"  功能码: {func_name} (0x{func_code:02X})\n"
                    f"  起始地址: 0x{addr:04X} ({addr})\n"
                    f"  寄存器数量: {quantity}\n"
                    f"  响应报文: {format_hex_bytes(pdu)}"
                )
            else:
                return f"未知写入功能码: 0x{func_code:02X}"
                
        except Exception as e:
            return f"解析写入响应失败: {e}"
    
    def parse_response_data(self):
        try:
            response_hex = self.manual_response.text().strip()
            if not response_hex or response_hex == "无响应":
                self.parse_result_text.setText("无响应数据可解析")
                return
            
            hex_str = response_hex.replace(' ', '').replace('\n', '')
            response_bytes = bytes.fromhex(hex_str)
            
            if len(response_bytes) < 4:
                self.parse_result_text.setText("响应数据太短，无法解析")
                return
            
            is_rtu = self.protocol_combo.currentText() == "RTU"
            pdu, info = ModbusDataParser.parse_response_full(response_bytes, is_rtu)
            
            if pdu is None:
                self.parse_result_text.setText("无法解析响应报文")
                return
            
            func_code = info.get('func_code', 0)
            is_write = func_code in [5, 6, 15, 16]
            
            if info.get('error'):
                self.parse_result_text.setText(
                    f"错误响应:\n"
                    f"  功能码: 0x{info['func_code']:02X}\n"
                    f"  错误码: 0x{info['error_code']:02X}\n"
                    f"  错误信息: {info['message']}"
                )
                return
            
            if is_write:
                write_result = self._parse_write_response(pdu, func_code)
                self.parse_result_text.setText(write_result)
                return
            
            data_bytes = info.get('data_bytes', b'')
            if not data_bytes:
                self.parse_result_text.setText("响应中无数据")
                return
            
            data_type = self.parse_type_combo.currentText()
            byte_order = self.parse_order_combo.currentText()
            start_index = self.parse_start.value()
            count = self.parse_count.value()
            
            if data_type == 'BIT':
                results = ModbusDataParser.parse_bit_data(
                    data_bytes, start_index, count, byte_order
                )
                if results:
                    lines = ["BIT数据解析结果:"]
                    lines.append("=" * 50)
                    lines.append(f"原始数据: {' '.join([f'{b:02X}' for b in data_bytes])}")
                    lines.append("=" * 50)
                    
                    if len(data_bytes) == 2:
                        if byte_order in ['BADC', 'CDAB', 'DCBA']:
                            reg_val = (data_bytes[1] << 8) | data_bytes[0]
                        else:
                            reg_val = (data_bytes[0] << 8) | data_bytes[1]
                        lines.append(f"寄存器值: 0x{reg_val:04X} ({reg_val})")
                        lines.append("=" * 50)
                    
                    for r in results[:50]:
                        lines.append(f"  位 {r['index']:3d} (字节{r['byte']} bit{r['bit']}): {r['display']}")
                    if len(results) > 50:
                        lines.append(f"  ... 共 {len(results)} 位")
                    self.parse_result_text.setText('\n'.join(lines))
                else:
                    self.parse_result_text.setText("无BIT数据")
            else:
                results = ModbusDataParser.parse_register_data(
                    data_bytes, data_type, byte_order, start_index, count
                )
                if results:
                    lines = [f"{data_type}数据解析结果 (功能码0x{func_code:02X}):"]
                    lines.append("=" * 50)
                    lines.append(f"原始数据: {' '.join([f'{b:02X}' for b in data_bytes])}")
                    lines.append("=" * 50)
                    for r in results[:50]:
                        if r.get('type') == 'BIT':
                            lines.append(f"  寄存器 {r['register']} 位 {r['bit']:2d}: {r['display']}")
                        else:
                            hex_val = r.get('hex', '')
                            lines.append(f"  索引 {r['index']:2d}: {r['display']:15s}  (HEX: {hex_val})")
                    if len(results) > 50:
                        lines.append(f"  ... 共 {len(results)} 个")
                    self.parse_result_text.setText('\n'.join(lines))
                else:
                    self.parse_result_text.setText("无寄存器数据")
            
        except Exception as e:
            self.parse_result_text.setText(f"解析失败: {e}")
            self.log_display.append_log(f"解析错误: {e}", "ERROR")
    
    def clear_log(self):
        self.log_display.clear()
        self.log_display.append_log("日志已清空", "INFO")
    
    def save_log(self):
        try:
            content = self.log_display.get_full_log()
            if not content.strip():
                self.log_display.append_log("没有日志可保存", "WARNING")
                return
            filename, _ = QFileDialog.getSaveFileName(self, "保存日志", 
                f"modbus_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", 
                "文本文件 (*.txt)")
            if filename:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.log_display.append_log(f"日志已保存: {os.path.basename(filename)}", "SUCCESS")
                self.status_bar.showMessage(f"日志已保存: {os.path.basename(filename)}", 3000)
        except Exception as e:
            self.log_display.append_log(f"保存失败: {e}", "ERROR")
    
    def closeEvent(self, event):
        if self.modbus_manager.is_connected:
            self.modbus_manager.disconnect()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = ModbusGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()