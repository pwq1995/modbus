#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus GUI 测试工具
功能：支持批量测试（Excel驱动）和手动发送原始报文
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
        
    def append_log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "#d4d4d4", "WARNING": "#ffcc00", "ERROR": "#ff4444",
            "DEBUG": "#808080", "SUCCESS": "#4ec9b0", "SEND": "#569cd6",
            "RECV": "#ce9178", "RESULT": "#c586c0"
        }
        color = color_map.get(level, "#d4d4d4")
        html = f'<span style="color:#808080;">[{timestamp}]</span> '
        html += f'<span style="color:{color}; font-weight:bold;">[{level}]</span> '
        html += f'<span style="color:#d4d4d4;">{self.html_escape(message)}</span><br>'
        self.append(html)
        if self.document().blockCount() > self.max_lines:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, 100)
            cursor.removeSelectedText()
        scrollbar = self.verticalScrollBar()
        QTimer.singleShot(10, lambda: scrollbar.setValue(scrollbar.maximum()))
    
    def html_escape(self, text):
        return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ===== 数据解析工具类 =====
class ModbusDataParser:
    """Modbus响应数据解析器"""
    
    @staticmethod
    def parse_bit_data(data_bytes, start_bit=0, bit_count=None, byte_order='ABCD'):
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
    def parse_register_data(data_bytes, data_type='UINT16', byte_order='ABCD', start_index=0, count=None):
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
    def parse_response_full(response_bytes, is_rtu=True):
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
                error_msg = {
                    1: '非法功能码', 2: '非法数据地址', 3: '非法数据值',
                    4: '从站设备故障', 5: '确认', 6: '从站设备忙',
                    8: '存储奇偶校验错误', 10: '不可用网关路径',
                    11: '网关目标设备无响应'
                }.get(error_code, f'未知错误(0x{error_code:02X})')
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
                error_msg = {
                    1: '非法功能码', 2: '非法数据地址', 3: '非法数据值',
                    4: '从站设备故障', 5: '确认', 6: '从站设备忙',
                    8: '存储奇偶校验错误', 10: '不可用网关路径',
                    11: '网关目标设备无响应'
                }.get(error_code, f'未知错误(0x{error_code:02X})')
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
    def __init__(self):
        self.connection = None
        self.protocol = None
        self.is_connected = False
        self.connection_params = {}
        self.timeout = 3
        self.log_callback = None
    
    def connect_tcp(self, host='127.0.0.1', port=502):
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
    
    def connect_rtu(self, port='COM1', baudrate=9600, timeout_ms=3000, 
                    data_bits=8, parity='N', stop_bits=1):
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
    
    def _send_raw(self, raw_bytes):
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
    
    def _recv(self):
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
    
    def read(self, slave_id, address, count, function_code, data_type='UINT16', byte_order='ABCD'):
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return None
        
        try:
            pdu = build_single_message(
                addr=slave_id,
                func=function_code,
                start=address,
                num=count
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
                request = bytes(tcp_request)
            else:
                request = pdu
            
            self._log_to_gui(f"发送: {format_hex_bytes(request)}", "SEND")
            
            if not self._send_raw(request):
                return None
            
            response = self._recv()
            if response is None:
                self._log_to_gui("接收超时", "WARNING")
                return None
            
            self._log_to_gui(f"接收: {format_hex_bytes(response)}", "RECV")
            
            # 提取PDU - TCP和RTU不同
            if self.protocol == 'tcp':
                if len(response) > 6:
                    pdu_response = response[6:]
                else:
                    self._log_to_gui("响应太短", "ERROR")
                    return None
                # TCP: [单元ID] [功能码] [字节数] [数据...]
                func_idx = 1
                byte_count_idx = 2
                data_start_idx = 3
            else:  # RTU
                if len(response) > 3:
                    pdu_response = response[1:-2]
                else:
                    self._log_to_gui("响应太短", "ERROR")
                    return None
                # RTU: [功能码] [字节数] [数据...]
                func_idx = 0
                byte_count_idx = 1
                data_start_idx = 2
            
            if len(pdu_response) < 3:
                self._log_to_gui("PDU太短", "ERROR")
                return None
            
            resp_func_code = pdu_response[func_idx]
            
            # 检查错误响应
            if resp_func_code & 0x80:
                error_code = pdu_response[func_idx + 1] if len(pdu_response) > func_idx + 1 else 0
                error_msgs = {
                    1: '非法功能码', 2: '非法数据地址', 3: '非法数据值',
                    4: '从站设备故障', 5: '确认', 6: '从站设备忙',
                    8: '存储奇偶校验错误', 10: '不可用网关路径',
                    11: '网关目标设备无响应'
                }
                error_msg = error_msgs.get(error_code, f'未知错误(0x{error_code:02X})')
                self._log_to_gui(f"错误: 0x{resp_func_code:02X} 错误码: 0x{error_code:02X} ({error_msg})", "ERROR")
                return None
            
            byte_count = pdu_response[byte_count_idx]
            data_bytes = pdu_response[data_start_idx:data_start_idx + byte_count]
            
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
    
    def write(self, slave_id, address, values, function_code):
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return False
        
        try:
            pdu = build_single_message(
                addr=slave_id,
                func=function_code,
                start=address,
                num=values
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
                request = bytes(tcp_request)
            else:
                request = pdu
            
            self._log_to_gui(f"写入: {format_hex_bytes(request)}", "SEND")
            
            if not self._send_raw(request):
                return False
            
            response = self._recv()
            if response is None:
                self._log_to_gui("写入超时", "WARNING")
                return False
            
            self._log_to_gui(f"响应: {format_hex_bytes(response)}", "RECV")
            
            if self.protocol == 'tcp':
                if len(response) > 6:
                    pdu_response = response[6:]
                else:
                    return False
                func_idx = 1
            else:
                if len(response) > 3:
                    pdu_response = response[1:-2]
                else:
                    return False
                func_idx = 0
            
            if len(pdu_response) < 2:
                return False
            
            resp_func_code = pdu_response[func_idx]
            
            if resp_func_code & 0x80:
                error_code = pdu_response[func_idx + 1] if len(pdu_response) > func_idx + 1 else 0
                error_msgs = {
                    1: '非法功能码', 2: '非法数据地址', 3: '非法数据值',
                    4: '从站设备故障', 5: '确认', 6: '从站设备忙',
                    8: '存储奇偶校验错误', 10: '不可用网关路径',
                    11: '网关目标设备无响应'
                }
                error_msg = error_msgs.get(error_code, f'未知错误(0x{error_code:02X})')
                self._log_to_gui(f"错误: 0x{resp_func_code:02X} 错误码: 0x{error_code:02X} ({error_msg})", "ERROR")
                return False
            
            return True
            
        except Exception as e:
            self._log_to_gui(f"写入失败: {e}", "ERROR")
            return False
    
    def send_raw_and_receive(self, raw_bytes):
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
    
    def _log_to_gui(self, message, level="INFO"):
        if self.log_callback:
            self.log_callback(message, level)


# ===== 主GUI窗口 =====
class ModbusGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.modbus_manager = ModbusManager()
        self.modbus_manager.log_callback = self.on_modbus_log
        self.excel_df = None
        self.current_row_index = 0
        self.init_ui()
        self.load_config()
    
    def on_modbus_log(self, message, level="INFO"):
        self.log_display.append_log(message, level)
    
    def init_ui(self):
        self.setWindowTitle("Modbus GUI 测试工具")
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
        self.raw_hex_edit.setPlaceholderText("输入十六进制报文，如: 01 03 00 00 00 0A")
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
        self.log_display.append_log("Modbus GUI 测试工具启动成功", "SUCCESS")
        self.log_display.append_log("支持BIT/UINT16/INT16/UINT32/INT32/FLOAT数据解析", "INFO")
    
    # ---- 辅助方法 ----
    def on_protocol_changed(self, protocol):
        if self.modbus_manager.is_connected:
            self.modbus_manager.disconnect()
            self.connect_btn.setText("连接")
            self.connect_btn.setStyleSheet("")
            self.send_raw_btn.setEnabled(False)
            self.run_all_btn.setEnabled(False)
            self.run_step_btn.setEnabled(False)
            self.status_bar.showMessage("已断开")
            self.log_display.append_log("协议切换，已自动断开连接", "INFO")
        
        self.protocol_stack.setCurrentIndex(0 if protocol == "TCP" else 1)
    
    def load_config(self):
        try:
            if not os.path.exists('config.ini'):
                return
            config = configparser.ConfigParser()
            config.read('config.ini', encoding='utf-8')
            if 'tcp' in config:
                self.ip_edit.setText(config['tcp'].get('host', '127.0.0.1'))
                self.port_edit.setText(config['tcp'].get('port', '502'))
            if 'rtu' in config:
                port = config['rtu'].get('port', 'COM1')
                idx = self.serial_combo.findText(port)
                if idx >= 0:
                    self.serial_combo.setCurrentIndex(idx)
                baud = config['rtu'].get('baudrate', '9600')
                idx = self.baudrate_combo.findText(baud)
                if idx >= 0:
                    self.baudrate_combo.setCurrentIndex(idx)
        except Exception as e:
            pass
    
    def save_config(self):
        try:
            config = configparser.ConfigParser()
            config['tcp'] = {'host': self.ip_edit.text(), 'port': self.port_edit.text()}
            config['rtu'] = {'port': self.serial_combo.currentText(), 'baudrate': self.baudrate_combo.currentText()}
            with open('config.ini', 'w', encoding='utf-8') as f:
                config.write(f)
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
                else:
                    port = self.serial_combo.currentText()
                    baudrate = int(self.baudrate_combo.currentText())
                    parity = self.parity_combo.currentText()
                    stop_bits = self.stopbits_combo.currentText()
                    data_bits = int(self.bytesize_combo.currentText())
                    success = self.modbus_manager.connect_rtu(port, baudrate, 3000, data_bits, parity, stop_bits)
                    if success:
                        self.log_display.append_log(f"RTU连接成功: {port} {baudrate}", "SUCCESS")
                if success:
                    self.connect_btn.setText("断开")
                    self.connect_btn.setStyleSheet("QPushButton { background-color: #a1260d; }")
                    self.send_raw_btn.setEnabled(True)
                    self.run_all_btn.setEnabled(True)
                    self.run_step_btn.setEnabled(True)
                    self.status_bar.showMessage(f"已连接 - {protocol}")
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
            self.status_bar.showMessage("已断开")
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
            self.table_widget.setItem(i, 0, QTableWidgetItem(str(row.get('用例编号', ''))))
            self.table_widget.setItem(i, 1, QTableWidgetItem(str(row.get('设备地址', ''))))
            self.table_widget.setItem(i, 2, QTableWidgetItem(str(row.get('功能码', ''))))
            self.table_widget.setItem(i, 3, QTableWidgetItem(str(row.get('起始地址', ''))))
            self.table_widget.setItem(i, 4, QTableWidgetItem(str(row.get('寄存器数量', ''))))
            self.table_widget.setItem(i, 5, QTableWidgetItem(str(row.get('数据类型', ''))))
            self.table_widget.setItem(i, 6, QTableWidgetItem(str(row.get('字节顺序', ''))))
            self.table_widget.setItem(i, 7, QTableWidgetItem(""))  # 读取结果列，初始为空
            self.table_widget.setItem(i, 8, QTableWidgetItem("待执行"))
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
    
    def _format_result_for_display(self, result, data_type, byte_order, bit_names, desc):
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
                    self.table_widget.setItem(self.current_row_index, 7, QTableWidgetItem(display_value))
                    status = "成功"
                    self.log_display.append_log(f"✓ 用例 {self.current_row_index+1} 成功: {display_value}", "SUCCESS")
                else:
                    self.table_widget.setItem(self.current_row_index, 7, QTableWidgetItem("读取失败"))
                    status = "失败"
                    self.log_display.append_log(f"✗ 用例 {self.current_row_index+1} 失败", "ERROR")
            else:
                self.log_display.append_log("写入操作需要填写数据，当前用例暂不支持自动写入", "WARNING")
                self.table_widget.setItem(self.current_row_index, 7, QTableWidgetItem("写入操作"))
                status = "跳过"
            
            self.table_widget.setItem(self.current_row_index, 8, QTableWidgetItem(status))
            if status == "成功":
                self.table_widget.item(self.current_row_index, 8).setBackground(QColor(0, 80, 0))
            elif status == "失败":
                self.table_widget.item(self.current_row_index, 8).setBackground(QColor(80, 0, 0))
            else:
                self.table_widget.item(self.current_row_index, 8).setBackground(QColor(80, 80, 0))
                
        except Exception as e:
            self.log_display.append_log(f"执行失败: {e}", "ERROR")
            self.table_widget.setItem(self.current_row_index, 7, QTableWidgetItem("异常"))
            self.table_widget.setItem(self.current_row_index, 8, QTableWidgetItem("异常"))
            self.table_widget.item(self.current_row_index, 8).setBackground(QColor(80, 0, 80))
    
    def run_all(self):
        if self.excel_df is None or not self.modbus_manager.is_connected:
            self.log_display.append_log("请先连接并加载数据", "WARNING")
            return
        success_count = 0
        total = len(self.excel_df)
        for i in range(total):
            self.current_row_index = i
            self.run_current_row()
            item = self.table_widget.item(i, 8)
            if item and item.text() == "成功":
                success_count += 1
            QApplication.processEvents()
        self.log_display.append_log(f"批量执行完成: 成功 {success_count}/{total}", "SUCCESS")
    
    # ---- 手动发送 ----
    def _float_to_registers(self, val, byte_order='ABCD'):
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
        hex_text = self.raw_hex_edit.toPlainText().strip()
        if not hex_text:
            self.log_display.append_log("请输入报文", "WARNING")
            return
        try:
            hex_str = hex_text.replace(' ', '').replace('\n', '')
            raw_bytes = bytes.fromhex(hex_str)
            
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
    def _parse_write_response(self, pdu, func_code):
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
            content = self.log_display.toPlainText()
            if not content.strip():
                return
            filename, _ = QFileDialog.getSaveFileName(self, "保存日志", 
                f"modbus_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", "文本文件 (*.txt)")
            if filename:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.log_display.append_log(f"日志已保存: {os.path.basename(filename)}", "SUCCESS")
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