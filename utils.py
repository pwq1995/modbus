#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
工具函数模块
============================================================
功能说明:
    提供通用的工具函数，包括字节格式化、字节重排、数值解析和日志配置

包含方法:
    1. format_hex_bytes: 将字节数据格式化为带空格的十六进制字符串
    2. reorder_bytes: 根据指定的字节顺序重新排列字节
    3. parse_value_by_type: 根据数据类型解析字节数据
    4. ms_to_seconds: 毫秒转换为秒（浮点数）
    5. setup_logging: 配置日志输出（控制台 + 文件）

作者: Modbus Test Tool
版本: v1.0
"""

import struct
import logging
import os
from datetime import datetime


def format_hex_bytes(data):
    """将字节数据格式化为带空格的十六进制字符串"""
    if isinstance(data, bytes):
        return ' '.join([f'{b:02X}' for b in data])
    elif isinstance(data, str):
        s = data.replace(' ', '').upper()
        if len(s) % 2 != 0:
            return s
        return ' '.join([s[i:i+2] for i in range(0, len(s), 2)])
    return str(data)


def reorder_bytes(data_bytes, byte_order):
    """根据指定的字节顺序重新排列字节"""
    byte_order = byte_order.upper()
    data_len = len(data_bytes)
    if data_len == 0 or byte_order == 'ABCD':
        return data_bytes
    
    regs = [data_bytes[i:i+2] for i in range(0, data_len, 2)]
    
    if byte_order == 'BADC':
        result = bytearray()
        for reg in regs:
            if len(reg) == 2:
                result.append(reg[1])
                result.append(reg[0])
            else:
                result.extend(reg)
        return bytes(result)
    
    elif byte_order == 'CDAB':
        result = bytearray()
        for i in range(0, len(regs), 2):
            if i + 1 < len(regs):
                result.extend(regs[i+1])
                result.extend(regs[i])
            else:
                result.extend(regs[i])
        return bytes(result)
    
    elif byte_order == 'DCBA':
        result = bytearray()
        for i in range(0, len(regs), 2):
            if i + 1 < len(regs):
                reg1_rev = bytes([regs[i][1], regs[i][0]]) if len(regs[i]) == 2 else regs[i]
                reg2_rev = bytes([regs[i+1][1], regs[i+1][0]]) if len(regs[i+1]) == 2 else regs[i+1]
                result.extend(reg2_rev)
                result.extend(reg1_rev)
            else:
                if len(regs[i]) == 2:
                    result.append(regs[i][1])
                    result.append(regs[i][0])
                else:
                    result.extend(regs[i])
        return bytes(result)
    
    else:
        return data_bytes


def parse_value_by_type(data_bytes, data_type, byte_order_type='big'):
    """根据数据类型解析字节数据为数值"""
    if data_type == 'UINT16':
        return struct.unpack('>H' if byte_order_type == 'big' else '<H', data_bytes)[0]
    elif data_type == 'INT16':
        return struct.unpack('>h' if byte_order_type == 'big' else '<h', data_bytes)[0]
    elif data_type == 'UINT32':
        return struct.unpack('>I' if byte_order_type == 'big' else '<I', data_bytes)[0]
    elif data_type == 'INT32':
        return struct.unpack('>i' if byte_order_type == 'big' else '<i', data_bytes)[0]
    elif data_type == 'FLOAT':
        return struct.unpack('>f' if byte_order_type == 'big' else '<f', data_bytes)[0]
    else:
        return struct.unpack('>H' if byte_order_type == 'big' else '<H', data_bytes)[0]


def ms_to_seconds(ms):
    """毫秒转换为秒（浮点数）"""
    return ms / 1000.0


def setup_logging(log_dir='logs'):
    """配置日志输出（同时输出到控制台和文件）"""
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'test_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    
    logging.info(f"日志文件: {log_file}")
    return log_file