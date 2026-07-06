#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus RTU 通信层模块
============================================================
功能说明:
    负责 Modbus RTU 协议的串口通信、报文组帧/解析
    支持配置数据位、校验位、停止位

包含方法:
    1. rtu_send_single: RTU 单条发送（构建报文）
    2. rtu_send_merged: RTU 合并发送（构建报文）
    3. rtu_recv: RTU 接收响应
    4. rtu_parse_single: RTU 单条响应解析
    5. rtu_parse_merged: RTU 合并响应解析
    6. rtu_connect: RTU 串口连接
    7. run_test_batch: RTU 批量执行入口

作者: Modbus Test Tool
版本: v1.0
"""

import time
import serial
import logging
from utils import ms_to_seconds, format_hex_bytes
from modbus_core import build_single_message
from modbus_common import (
    get_case_params,
    parse_response_data,
    check_error_response,
    parse_merged_response,
    run_batch,
)


# ==================== RTU 发送函数 ====================

def rtu_send_single(row, conn, slave_id=1, **kwargs):
    """RTU 单条发送（构建报文）"""
    addr, func_code, start, quantity, _, _ = get_case_params(row)
    return build_single_message(addr, func_code, start, quantity)


def rtu_send_merged(group, func_code, start, total_num, conn, slave_id=1, **kwargs):
    """RTU 合并发送（构建报文）"""
    addr = int(group[0][1]['设备地址'])
    return build_single_message(addr, func_code, start, total_num)


# ==================== RTU 接收函数 ====================

def rtu_recv(conn, timeout_ms, **kwargs):
    """RTU 接收响应"""
    timeout_sec = ms_to_seconds(timeout_ms)
    
    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        if conn.in_waiting > 0:
            break
        time.sleep(0.01)
    
    if conn.in_waiting == 0:
        raise Exception("无响应")
    
    resp = conn.read(conn.in_waiting)
    if not resp:
        raise Exception("无响应")
    
    return resp


# ==================== RTU 解析函数 ====================

def rtu_parse_single(resp, row, conn, func_code, **kwargs):
    """RTU 单条响应解析"""
    if len(resp) < 4:
        raise Exception(f"响应长度异常: {len(resp)} 字节（至少需要4字节）")
    
    resp_pdu = resp[1:]  # 去掉地址
    check_error_response(resp_pdu, func_code)
    
    byte_count = resp[2]
    actual_data = resp[3:3+byte_count]
    
    if len(actual_data) < 1:
        raise Exception("寄存器数据为空")
    
    _, _, _, quantity, data_type, byte_order = get_case_params(row)
    return parse_response_data(actual_data, row, func_code, data_type, byte_order, quantity)


def rtu_parse_merged(resp, group, func_code, **kwargs):
    """RTU 合并响应解析"""
    if len(resp) < 4:
        raise Exception(f"响应长度异常: {len(resp)} 字节（至少需要4字节）")
    
    resp_pdu = resp[1:]  # 去掉地址
    check_error_response(resp_pdu, func_code)
    
    byte_count = resp[2]
    all_data = resp[3:3+byte_count]
    
    return parse_merged_response(all_data, group, func_code)


# ==================== RTU 连接函数 ====================

def rtu_connect(port, baudrate, timeout_ms, data_bits=8, parity='N', stop_bits=1, **kwargs):
    """RTU 串口连接"""
    timeout_sec = ms_to_seconds(timeout_ms)
    
    parity_map = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}
    parity_setting = parity_map.get(parity, serial.PARITY_NONE)
    
    stop_bits_map = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}
    stop_bits_setting = stop_bits_map.get(stop_bits, serial.STOPBITS_ONE)
    
    ser = serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=data_bits,
        parity=parity_setting,
        stopbits=stop_bits_setting,
        timeout=timeout_sec,
        write_timeout=timeout_sec
    )
    
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    
    parity_display = {'N': '无', 'E': '偶校验', 'O': '奇校验'}.get(parity, parity)
    logging.info(f"  ├─ 串口参数: {port} | {baudrate}bps | {data_bits}{parity}{stop_bits}")
    logging.info(f"  ├─ 串口状态: {'已打开' if ser.is_open else '打开失败'}")
    
    return ser


# ==================== RTU 批量执行 ====================

def run_test_batch(excel_path, port, baudrate=9600, data_bits=8, parity='N', stop_bits=1,
                   slave_id=1, timeout_ms=1000, interval_ms=200, batch_mode=False, retry_count=3):
    """RTU 批量执行入口"""
    
    def send_single_wrapper(row, conn, slave_id=1):
        msg = rtu_send_single(row, conn, slave_id)
        conn.write(msg)
        return msg
    
    def send_merged_wrapper(group, func_code, start, total_num, conn, slave_id=1):
        msg = rtu_send_merged(group, func_code, start, total_num, conn, slave_id)
        conn.write(msg)
        return msg
    
    run_batch(
        excel_path=excel_path,
        protocol='rtu',
        connect_func=rtu_connect,
        send_func=send_single_wrapper,
        send_merged_func=send_merged_wrapper,
        recv_func=rtu_recv,
        parse_func=rtu_parse_single,
        parse_merged_func=rtu_parse_merged,
        batch_mode=batch_mode,
        retry_count=retry_count,
        timeout_ms=timeout_ms,
        interval_ms=interval_ms,
        port=port,
        baudrate=baudrate,
        data_bits=data_bits,
        parity=parity,
        stop_bits=stop_bits,
        slave_id=slave_id,
        target=f"{port} ({baudrate}bps {data_bits}{parity}{stop_bits})"
    )