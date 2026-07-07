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


# ===== Modbus 管理器（适配你的项目）=====
class ModbusManager:
    def __init__(self):
        self.connection = None
        self.protocol = None
        self.is_connected = False
        self.connection_params = {}
        self.timeout = 3  # 秒
        self.log_callback = None
    
    # ---- 连接管理 ----
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
    
    # ---- 核心收发 ----
    def _send_raw(self, raw_bytes):
        """发送原始字节"""
        if self.protocol == 'tcp':
            try:
                self.connection.send(raw_bytes)
                return True
            except Exception as e:
                self._log_to_gui(f"TCP发送失败: {e}", "ERROR")
                return False
        else:  # RTU
            try:
                bytes_written = self.connection.write(raw_bytes)
                self._log_to_gui(f"实际发送字节数: {bytes_written}", "DEBUG")
                return True
            except Exception as e:
                self._log_to_gui(f"RTU发送失败: {e}", "ERROR")
                return False
    
    def _recv(self):
        """接收响应"""
        if self.protocol == 'tcp':
            try:
                return tcp_recv(self.connection, int(self.timeout * 1000))
            except Exception as e:
                self._log_to_gui(f"TCP接收异常: {e}", "DEBUG")
                return None
        else:  # RTU
            try:
                return rtu_recv(self.connection, int(self.timeout * 1000))
            except Exception as e:
                self._log_to_gui(f"RTU接收异常: {e}", "DEBUG")
                return None
    
    # ---- 高层接口 ----
    def read(self, slave_id, address, count, function_code):
        """执行读取"""
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return None
        
        try:
            # build_single_message 已经包含CRC（RTU）或返回PDU（TCP）
            request = build_single_message(
                addr=slave_id,
                func=function_code,
                start=address,
                num=count
            )
            
            # TCP需要添加MBAP头
            if self.protocol == 'tcp':
                transaction_id = int(time.time() * 1000) % 65535
                tcp_request = bytearray()
                tcp_request.append((transaction_id >> 8) & 0xFF)
                tcp_request.append(transaction_id & 0xFF)
                tcp_request.append(0x00)  # 协议ID高字节
                tcp_request.append(0x00)  # 协议ID低字节
                tcp_request.append(0x00)  # 长度高字节
                tcp_request.append(len(request) + 1)  # 长度低字节（单元ID+PDU）
                tcp_request.append(slave_id)  # 单元ID
                tcp_request.extend(request)  # PDU
                request = bytes(tcp_request)
            
            self._log_to_gui(f"发送: {format_hex_bytes(request)}", "SEND")
            
            if not self._send_raw(request):
                return None
            
            response = self._recv()
            if response is None:
                self._log_to_gui("接收超时", "WARNING")
                return None
            
            self._log_to_gui(f"接收: {format_hex_bytes(response)}", "RECV")
            
            # 提取PDU进行解析
            if self.protocol == 'tcp':
                # TCP响应: MBAP头(6) + PDU
                if len(response) > 6:
                    pdu = response[6:]
                else:
                    self._log_to_gui("响应太短", "ERROR")
                    return None
            else:
                # RTU响应: 地址(1) + PDU + CRC(2)
                if len(response) > 3:
                    pdu = response[1:-2]  # 去掉地址和CRC
                else:
                    self._log_to_gui("响应太短", "ERROR")
                    return None
            
            # 检查错误
            error_msg = check_error_response(pdu)
            if error_msg:
                self._log_to_gui(f"错误: {error_msg}", "ERROR")
                return None
            
            # 解析数据
            if len(pdu) < 3:
                self._log_to_gui("PDU太短", "ERROR")
                return None
            
            byte_count = pdu[2]
            data_bytes = pdu[3:3+byte_count]
            
            if function_code in [1, 2, 5, 15]:
                result = parse_bit_response(data_bytes, "", count)
            else:
                result = parse_register_response(data_bytes, "", 'UINT16', 'ABCD')
            
            if result is not None:
                self._log_to_gui(f"结果: {result}", "RESULT")
            return result
            
        except Exception as e:
            self._log_to_gui(f"读取失败: {e}", "ERROR")
            return None
    
    def write(self, slave_id, address, values, function_code):
        """执行写入"""
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return False
        
        try:
            request = build_single_message(
                addr=slave_id,
                func=function_code,
                start=address,
                num=values
            )
            
            # TCP需要添加MBAP头
            if self.protocol == 'tcp':
                transaction_id = int(time.time() * 1000) % 65535
                tcp_request = bytearray()
                tcp_request.append((transaction_id >> 8) & 0xFF)
                tcp_request.append(transaction_id & 0xFF)
                tcp_request.append(0x00)
                tcp_request.append(0x00)
                tcp_request.append(0x00)
                tcp_request.append(len(request) + 1)
                tcp_request.append(slave_id)
                tcp_request.extend(request)
                request = bytes(tcp_request)
            
            self._log_to_gui(f"写入: {format_hex_bytes(request)}", "SEND")
            
            if not self._send_raw(request):
                return False
            
            response = self._recv()
            if response is None:
                self._log_to_gui("写入超时", "WARNING")
                return False
            
            self._log_to_gui(f"响应: {format_hex_bytes(response)}", "RECV")
            
            # 提取PDU
            if self.protocol == 'tcp':
                if len(response) > 6:
                    pdu = response[6:]
                else:
                    return False
            else:
                if len(response) > 3:
                    pdu = response[1:-2]
                else:
                    return False
            
            error_msg = check_error_response(pdu)
            if error_msg:
                self._log_to_gui(f"错误: {error_msg}", "ERROR")
                return False
            
            return True
            
        except Exception as e:
            self._log_to_gui(f"写入失败: {e}", "ERROR")
            return False
    
    def send_raw_and_receive(self, raw_bytes):
        """发送原始报文（手动模式）- 修复TCP组帧"""
        if not self.is_connected:
            self._log_to_gui("未连接设备", "WARNING")
            return None
        
        try:
            # TCP模式：添加MBAP头
            if self.protocol == 'tcp':
                # 更严格的MBAP头检测：
                # 1. 长度至少为7字节
                # 2. 第3-4字节为0x0000（协议ID）
                # 3. 第5-6字节为长度，且长度值 + 6 == 总长度
                is_full_tcp = False
                if len(raw_bytes) >= 7:
                    protocol_id = (raw_bytes[2] << 8) | raw_bytes[3]
                    length = (raw_bytes[4] << 8) | raw_bytes[5]
                    # 协议ID必须为0，且长度字段必须匹配
                    if protocol_id == 0 and (length + 6) == len(raw_bytes):
                        is_full_tcp = True
                
                if not is_full_tcp:
                    transaction_id = int(time.time() * 1000) % 65535
                    # 构建完整TCP报文
                    tcp_packet = bytearray()
                    # 事务ID
                    tcp_packet.append((transaction_id >> 8) & 0xFF)
                    tcp_packet.append(transaction_id & 0xFF)
                    # 协议ID (固定0x0000)
                    tcp_packet.append(0x00)
                    tcp_packet.append(0x00)
                    # 长度 = PDU的长度
                    pdu_length = len(raw_bytes)
                    tcp_packet.append((pdu_length >> 8) & 0xFF)
                    tcp_packet.append(pdu_length & 0xFF)
                    # PDU
                    tcp_packet.extend(raw_bytes)
                    raw_bytes = bytes(tcp_packet)
                    self._log_to_gui(f"添加MBAP头: {format_hex_bytes(raw_bytes)}", "DEBUG")
                else:
                    self._log_to_gui(f"已包含MBAP头: {format_hex_bytes(raw_bytes)}", "DEBUG")
            
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
        self.setGeometry(100, 100, 1100, 800)
        self.setStyleSheet("""
            QMainWindow { background-color: #2d2d2d; }
            QGroupBox { color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 5px; margin-top: 10px; font-weight: bold; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
            QLabel { color: #d4d4d4; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #1177bb; }
            QPushButton:disabled { background-color: #3c3c3c; color: #808080; }
            QLineEdit, QSpinBox, QComboBox { background-color: #3c3c3c; color: #d4d4d4; border: 1px solid #555555; border-radius: 3px; padding: 5px; }
            QTabWidget::pane { border: 1px solid #3c3c3c; background-color: #2d2d2d; }
            QTabBar::tab { background-color: #3c3c3c; color: #d4d4d4; padding: 8px 15px; margin-right: 2px; }
            QTabBar::tab:selected { background-color: #0e639c; }
            QTableWidget { background-color: #1e1e1e; color: #d4d4d4; gridline-color: #3c3c3c; }
            QHeaderView::section { background-color: #3c3c3c; color: #d4d4d4; padding: 4px; }
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
        self.table_widget.setColumnCount(6)
        self.table_widget.setHorizontalHeaderLabels(["从站ID", "功能码", "地址", "数量/数据", "期望值", "状态"])
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
        self.manual_qty.setPlaceholderText("读:数量, 写:1,2,3")
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
        
        resp_group = QGroupBox("响应")
        resp_layout = QVBoxLayout()
        self.manual_response = QTextEdit()
        self.manual_response.setReadOnly(True)
        self.manual_response.setMaximumHeight(80)
        resp_layout.addWidget(self.manual_response)
        resp_group.setLayout(resp_layout)
        manual_layout.addWidget(resp_group)
        
        self.tab_widget.addTab(tab_manual, "手动发送")
        main_layout.addWidget(self.tab_widget)
        
        # ---- 日志 ----
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
    
    # ---- 辅助方法 ----
    def on_protocol_changed(self, protocol):
        """切换协议时自动断开连接"""
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
            required = ['slave_id', 'func_code', 'address', 'quantity_or_data']
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
            self.table_widget.setItem(i, 0, QTableWidgetItem(str(row.get('slave_id', ''))))
            self.table_widget.setItem(i, 1, QTableWidgetItem(str(row.get('func_code', ''))))
            self.table_widget.setItem(i, 2, QTableWidgetItem(str(row.get('address', ''))))
            self.table_widget.setItem(i, 3, QTableWidgetItem(str(row.get('quantity_or_data', ''))))
            self.table_widget.setItem(i, 4, QTableWidgetItem(str(row.get('expect_value', ''))))
            self.table_widget.setItem(i, 5, QTableWidgetItem("待执行"))
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
    
    def run_current_row(self):
        if self.excel_df is None or not self.modbus_manager.is_connected:
            self.log_display.append_log("请先连接并加载数据", "WARNING")
            return
        try:
            row = self.excel_df.iloc[self.current_row_index]
            slave = int(row.get('slave_id', 1))
            func = int(row.get('func_code', 3))
            addr = int(row.get('address', 0))
            qty = row.get('quantity_or_data', 10)
            
            if func in [1, 2, 3, 4]:
                result = self.modbus_manager.read(slave, addr, int(qty), func)
                status = "成功" if result is not None else "失败"
            else:
                if isinstance(qty, str) and ',' in qty:
                    values = [int(v.strip()) for v in qty.split(',')]
                else:
                    values = [int(qty)]
                result = self.modbus_manager.write(slave, addr, values, func)
                status = "成功" if result else "失败"
            
            self.table_widget.setItem(self.current_row_index, 5, QTableWidgetItem(status))
            if status == "成功":
                self.table_widget.item(self.current_row_index, 5).setBackground(QColor(0, 80, 0))
            else:
                self.table_widget.item(self.current_row_index, 5).setBackground(QColor(80, 0, 0))
        except Exception as e:
            self.log_display.append_log(f"执行失败: {e}", "ERROR")
    
    def run_all(self):
        if self.excel_df is None or not self.modbus_manager.is_connected:
            self.log_display.append_log("请先连接并加载数据", "WARNING")
            return
        for i in range(len(self.excel_df)):
            self.current_row_index = i
            self.run_current_row()
            QApplication.processEvents()
        self.log_display.append_log("批量执行完成", "SUCCESS")
    
    # ---- 手动发送 ----
    def build_raw(self):
        """从参数构建报文 - TCP模式不含CRC"""
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
            
            # 构建PDU（不含CRC）
            if func in [1, 2, 3, 4]:
                # 读请求
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
                # 写单个线圈
                values = [int(v.strip()) for v in qty_text.split(',')]
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
                # 写单个寄存器
                values = [int(v.strip()) for v in qty_text.split(',')]
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
                # 写多个线圈
                values = [int(v.strip()) for v in qty_text.split(',')]
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
                # 写多个寄存器
                values = [int(v.strip()) for v in qty_text.split(',')]
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
            
            # 根据协议决定是否添加CRC
            if protocol == "RTU":
                crc = calc_crc16(pdu)
                request = pdu + crc
            else:
                request = bytes(pdu)
            
            # 转换为格式化的十六进制字符串
            hex_str = ' '.join([f'{b:02X}' for b in request])
            
            # 设置到文本框
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
                self.manual_response.setText(f"响应: {format_hex_bytes(response)}")
                self.log_display.append_log("发送成功", "SUCCESS")
            else:
                self.manual_response.setText("无响应")
        except Exception as e:
            self.log_display.append_log(f"发送失败: {e}", "ERROR")
    
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