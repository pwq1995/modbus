#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus 核心功能模块（协议无关）
============================================================
功能说明:
    提供 Modbus 协议的核心功能，与传输层（RTU/TCP）无关

包含方法:
    1. calc_crc16: 计算 Modbus RTU CRC16 校验值
    2. build_single_message: 构建单条 Modbus RTU 请求报文
    3. parse_bit_response: 解析位响应数据（01/02 功能码）
    4. parse_bit_response_from_offset: 从指定偏移位置解析位响应
    5. parse_register_response: 解析寄存器响应数据（03/04 功能码）
    6. get_parse_mode: 根据功能码和数据类型判断解析方式
    7. validate_quantity: 校验寄存器数量是否合法

作者: Modbus Test Tool
版本: v1.0
"""

import crcmod
import logging
from config import DATA_TYPE_INFO
from utils import reorder_bytes, format_hex_bytes, parse_value_by_type


def calc_crc16(data):
    """计算 Modbus RTU CRC16 校验值"""
    crc = crcmod.predefined.mkCrcFun('modbus')(data)
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_single_message(addr, func, start, num):
    """构建单条 Modbus RTU 请求报文"""
    data = bytes([addr, func, (start>>8)&0xFF, start&0xFF, (num>>8)&0xFF, num&0xFF])
    return data + calc_crc16(data)


def parse_bit_response(data_bytes, names_str, quantity):
    """解析位响应数据（01/02 功能码）"""
    names = [n.strip() for n in str(names_str).split(',')]
    bits = []
    bit_index = 0
    
    for i, byte_val in enumerate(data_bytes):
        for bit_pos in range(8):
            if bit_index >= quantity:
                break
            val = (byte_val >> bit_pos) & 1
            if bit_index < len(names) and names[bit_index]:
                name = names[bit_index]
            else:
                name = f"Bit[{bit_index}] (字节{i} bit{bit_pos})"
            bits.append((name, val))
            bit_index += 1
        if bit_index >= quantity:
            break
    return bits


def parse_bit_response_from_offset(data_bytes, names_str, quantity, bit_offset):
    """从指定偏移位置解析位响应数据"""
    names = [n.strip() for n in str(names_str).split(',')]
    bits = []
    bit_index = 0
    
    for i, byte_val in enumerate(data_bytes):
        start_pos = bit_offset if i == 0 else 0
        for bit_pos in range(start_pos, 8):
            if bit_index >= quantity:
                break
            val = (byte_val >> bit_pos) & 1
            if bit_index < len(names) and names[bit_index]:
                name = names[bit_index]
            else:
                name = f"Bit[{bit_index}] (字节{i} bit{bit_pos})"
            bits.append((name, val))
            bit_index += 1
        if bit_index >= quantity:
            break
    return bits


def parse_register_response(data_bytes, desc_str, data_type='UINT16', byte_order='ABCD'):
    """解析寄存器响应数据（03/04 功能码）"""
    if not data_bytes or len(data_bytes) == 0:
        return []
    
    raw_descs = [d.strip() for d in str(desc_str).split(',')]
    descs = []
    for d in raw_descs:
        if d and d.lower() not in ['nan', 'none', 'null']:
            descs.append(d)
        else:
            descs.append('')
    
    reordered_data = reorder_bytes(data_bytes, byte_order)
    
    if byte_order.upper() != 'ABCD' and data_bytes:
        logging.info(f"  [字节重排] 原始: {format_hex_bytes(data_bytes)}")
        logging.info(f"  [字节重排] 重排后: {format_hex_bytes(reordered_data)} (顺序: {byte_order})")
    
    byte_order_type = 'big'
    type_info = DATA_TYPE_INFO.get(data_type, DATA_TYPE_INFO['UINT16'])
    bytes_needed = type_info['bytes']
    is_bit = type_info['is_bit']
    
    vals = []
    pos = 0
    idx = 0
    
    while pos < len(reordered_data):
        chunk = reordered_data[pos:pos+bytes_needed]
        if len(chunk) < bytes_needed:
            break
        
        if is_bit:
            reg_val = (chunk[0] << 8) | chunk[1]
            for bit_pos in range(16):
                val = (reg_val >> bit_pos) & 1
                if idx < len(descs) and descs[idx]:
                    name = descs[idx]
                else:
                    name = f"Reg[{idx//16}].Bit{bit_pos}"
                vals.append((name, val, 'BIT', 1))
                idx += 1
        else:
            val = parse_value_by_type(chunk, data_type, byte_order_type)
            if idx < len(descs) and descs[idx]:
                name = descs[idx]
            else:
                name = f"Register[{idx}]"
            vals.append((name, val, data_type, bytes_needed//2))
            idx += 1
        
        pos += bytes_needed
    
    return vals


def get_parse_mode(func_code, data_type):
    """根据功能码和数据类型判断解析方式"""
    if func_code in [6, 16]:
        return 'write'
    if func_code in [1, 2]:
        return 'bit'
    if func_code in [3, 4]:
        type_info = DATA_TYPE_INFO.get(data_type, DATA_TYPE_INFO['UINT16'])
        if type_info['is_bit']:
            return 'bit'
        else:
            return 'register'
    return 'write'


def validate_quantity(func_code, data_type, quantity):
    """校验寄存器/线圈数量是否合法"""
    from config import MAX_QUANTITY_PER_FRAME
    
    if func_code in [1, 2]:
        if quantity < 1 or quantity > 2000:
            return False, f"线圈数量 {quantity} 超出范围 (1-2000)"
        return True, ""
    
    if func_code in [3, 4]:
        type_info = DATA_TYPE_INFO.get(data_type, DATA_TYPE_INFO['UINT16'])
        expected_regs = type_info['regs']
        if quantity != expected_regs:
            return False, f"数据类型 {data_type} 需要 {expected_regs} 个寄存器，但当前数量为 {quantity}"
        if quantity < 1 or quantity > 125:
            return False, f"寄存器数量 {quantity} 超出范围 (1-125)"
        return True, ""
    
    return True, ""