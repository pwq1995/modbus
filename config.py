#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
配置常量模块
============================================================
功能说明:
    定义 Modbus 测试工具所需的所有常量和配置项

包含内容:
    1. ERROR_CODES: Modbus 异常响应错误码映射表
    2. DATA_TYPE_INFO: 支持的数据类型定义
    3. MAX_QUANTITY_PER_FRAME: 各功能码单帧最大读取数量限制
    4. 默认时间参数、重试参数、TCP 参数
    5. 串口参数映射表（数据位、校验位、停止位）

作者: Modbus Test Tool
版本: v1.0
"""

# ==================== 错误码映射表 ====================
ERROR_CODES = {
    0x01: "非法功能 (Illegal Function)",
    0x02: "非法数据地址 (Illegal Data Address)",
    0x03: "非法数据值 (Illegal Data Value)",
    0x04: "从站设备故障 (Slave Device Failure)",
    0x05: "确认 (Acknowledge)"
}

# ==================== 数据类型定义 ====================
DATA_TYPE_INFO = {
    'BIT': {'bytes': 2, 'is_bit': True, 'name': '位', 'regs': 1},
    'UINT16': {'bytes': 2, 'is_bit': False, 'name': '无符号16位整数', 'regs': 1},
    'INT16': {'bytes': 2, 'is_bit': False, 'name': '有符号16位整数', 'regs': 1},
    'UINT32': {'bytes': 4, 'is_bit': False, 'name': '无符号32位整数', 'regs': 2},
    'INT32': {'bytes': 4, 'is_bit': False, 'name': '有符号32位整数', 'regs': 2},
    'FLOAT': {'bytes': 4, 'is_bit': False, 'name': '32位浮点数', 'regs': 2},
}

# ==================== 帧长度限制常量 ====================
MAX_QUANTITY_PER_FRAME = {
    1: 2000,
    2: 2000,
    3: 125,
    4: 125,
}

# ==================== 默认时间参数（毫秒） ====================
DEFAULT_RESPONSE_TIMEOUT_MS = 1000
DEFAULT_INTERVAL_MS = 200

# ==================== 默认重试参数 ====================
DEFAULT_RETRY_COUNT = 3

# ==================== 默认 TCP 参数 ====================
DEFAULT_TCP_PORT = 502
DEFAULT_SLAVE_ID = 1

# ==================== 串口参数映射表 ====================
DATA_BITS_MAP = {
    '5': 5, '6': 6, '7': 7, '8': 8,
    5: 5, 6: 6, 7: 7, 8: 8,
}

PARITY_MAP = {
    'N': 'N', 'E': 'E', 'O': 'O',
    'n': 'N', 'e': 'E', 'o': 'O',
    '无': 'N', '偶': 'E', '奇': 'O',
    'None': 'N', 'Even': 'E', 'Odd': 'O',
}

STOP_BITS_MAP = {
    '1': 1, '1.5': 1.5, '2': 2,
    1: 1, 1.5: 1.5, 2: 2,
}