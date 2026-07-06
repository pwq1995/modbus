#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus TCP 通信层模块
============================================================
功能说明:
    负责 Modbus TCP 协议的 Socket 通信、报文组帧/解析

TCP 报文结构（从1开始计数）：
    1-2: 事务ID
    3-4: 协议ID (固定 0x0000)
    5-6: 长度 (后续字节数)
    7:   单元ID
    8:   功能码
    9:   数据字节数 (正常响应) 或 错误码 (错误响应)
    10+: 数据

PDU 结构: [单元ID] [功能码] [数据字节数] [数据...]

包含方法:
    1. build_tcp_request: 构建 TCP 请求报文
    2. build_tcp_read_request: 构建 TCP 读请求报文
    3. recv_full_response: 循环接收完整 TCP 响应
    4. parse_tcp_response: 解析 TCP 响应，返回 PDU
    5. tcp_send_single: TCP 单条发送（构建报文）
    6. tcp_send_merged: TCP 合并发送（构建报文）
    7. tcp_recv: TCP 接收响应
    8. tcp_parse_single: TCP 单条响应解析
    9. tcp_parse_merged: TCP 合并响应解析
    10. tcp_connect: TCP 连接
    11. run_test_batch_tcp: TCP 批量执行入口

作者: Modbus Test Tool
版本: v1.0
"""

import logging
import time
import socket
from utils import ms_to_seconds, format_hex_bytes
from modbus_common import (
    get_case_params,
    parse_response_data,
    check_error_response,
    parse_merged_response,
    run_batch,
)


# ==================== TCP 工具函数 ====================

def build_tcp_request(transaction_id, protocol_id, length, unit_id, func_code, data):
    """构建 Modbus TCP 请求报文"""
    header = bytearray()
    header.append((transaction_id >> 8) & 0xFF)
    header.append(transaction_id & 0xFF)
    header.append((protocol_id >> 8) & 0xFF)
    header.append(protocol_id & 0xFF)
    header.append((length >> 8) & 0xFF)
    header.append(length & 0xFF)
    header.append(unit_id)
    pdu = bytearray([func_code]) + data
    return bytes(header + pdu)


def build_tcp_read_request(transaction_id, slave_id, func_code, start, num):
    """构建 TCP 读请求报文"""
    data = bytearray([(start >> 8) & 0xFF, start & 0xFF, (num >> 8) & 0xFF, num & 0xFF])
    return build_tcp_request(transaction_id, 0, 1 + 1 + 4, slave_id, func_code, data)


def recv_full_response(sock, timeout_sec):
    """循环接收完整 TCP 响应"""
    sock.settimeout(timeout_sec)
    
    try:
        header = sock.recv(6)
    except socket.timeout:
        raise Exception("TCP 响应超时（MBAP头）")
    
    if not header:
        raise Exception("连接已关闭，无响应")
    
    while len(header) < 6:
        try:
            more = sock.recv(6 - len(header))
            if not more:
                break
            header += more
        except socket.timeout:
            raise Exception(f"MBAP 头不完整: 接收 {len(header)} 字节，期望 6 字节")
    
    if len(header) < 6:
        raise Exception(f"MBAP 头不完整: 接收 {len(header)} 字节，期望 6 字节")
    
    length = (header[4] << 8) | header[5]
    
    data = bytearray()
    remaining = length
    recv_timeout = timeout_sec
    retry_count = 0
    max_retries = 3
    
    while remaining > 0:
        try:
            sock.settimeout(recv_timeout)
            chunk = sock.recv(min(remaining, 1024))
        except socket.timeout:
            retry_count += 1
            if retry_count <= max_retries:
                recv_timeout = timeout_sec * (1 + retry_count * 0.5)
                continue
            else:
                received = len(header) + len(data)
                raise Exception(f"数据接收超时，已接收 {received} 字节")
        
        if not chunk:
            received = len(header) + len(data)
            raise Exception(f"连接关闭，数据不完整: 已接收 {received} 字节")
        
        data.extend(chunk)
        remaining -= len(chunk)
        retry_count = 0
    
    sock.settimeout(timeout_sec)
    return bytes(header + data)


def parse_tcp_response(data):
    """解析 TCP 响应，返回 PDU"""
    if len(data) < 8:
        raise Exception(f"响应长度不足: {len(data)} 字节（至少需要8字节）")
    
    length = (data[4] << 8) | data[5]
    expected = 6 + length
    
    if len(data) < expected:
        logging.warning(f"  ⚠ 响应数据不完整: 期望 {expected} 字节，实际 {len(data)} 字节")
        length = len(data) - 6
    
    return data[6:6 + length]


# ==================== TCP 发送函数 ====================

def tcp_send_single(row, conn, slave_id=1, **kwargs):
    """TCP 单条发送（构建报文）"""
    _, func_code, start, quantity, _, _ = get_case_params(row)
    transaction_id = int(time.time() * 1000) % 65535
    return build_tcp_read_request(transaction_id, slave_id, func_code, start, quantity)


def tcp_send_merged(group, func_code, start, total_num, conn, slave_id=1, **kwargs):
    """TCP 合并发送（构建报文）"""
    transaction_id = int(time.time() * 1000) % 65535
    return build_tcp_read_request(transaction_id, slave_id, func_code, start, total_num)


def tcp_recv(conn, timeout_ms, **kwargs):
    """TCP 接收响应"""
    try:
        return recv_full_response(conn, ms_to_seconds(timeout_ms))
    except Exception as e:
        raise Exception(f"接收失败: {e}")


# ==================== TCP 解析函数 ====================

def tcp_parse_single(resp, row, conn, func_code, slave_id=1, **kwargs):
    """TCP 单条响应解析"""
    pdu = parse_tcp_response(resp)
    
    # ===== 先检查错误响应（错误响应 PDU 只有 3 字节） =====
    check_error_response(pdu, func_code)
    
    # ===== 正常响应 PDU 至少 4 字节 =====
    if len(pdu) < 4:
        raise Exception("PDU 长度不足（至少需要4字节）")
    
    byte_count = pdu[2]
    actual_data = pdu[3:3+byte_count]
    
    if len(actual_data) < 1:
        raise Exception("PDU 数据为空")
    
    _, _, _, quantity, data_type, byte_order = get_case_params(row)
    return parse_response_data(actual_data, row, func_code, data_type, byte_order, quantity)


def tcp_parse_merged(resp, group, func_code, **kwargs):
    """TCP 合并响应解析"""
    pdu = parse_tcp_response(resp)
    
    # ===== 先检查错误响应（错误响应 PDU 只有 3 字节） =====
    check_error_response(pdu, func_code)
    
    # ===== 正常响应 PDU 至少 4 字节 =====
    if len(pdu) < 4:
        raise Exception("PDU 长度不足（至少需要4字节）")
    
    byte_count = pdu[2]
    all_data = pdu[3:3+byte_count]
    
    return parse_merged_response(all_data, group, func_code)


# ==================== TCP 连接函数 ====================

def tcp_connect(ip, port, timeout_ms, **kwargs):
    """TCP 连接"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(ms_to_seconds(timeout_ms))
    sock.connect((ip, port))
    return sock


# ==================== TCP 批量执行 ====================

def run_test_batch_tcp(excel_path, ip, port=502, slave_id=1, timeout_ms=1000, 
                       interval_ms=200, batch_mode=True, retry_count=3):
    """TCP 批量执行入口"""
    
    def send_single_wrapper(row, conn, slave_id=1):
        msg = tcp_send_single(row, conn, slave_id)
        conn.send(msg)
        return msg
    
    def send_merged_wrapper(group, func_code, start, total_num, conn, slave_id=1):
        msg = tcp_send_merged(group, func_code, start, total_num, conn, slave_id)
        conn.send(msg)
        return msg
    
    run_batch(
        excel_path=excel_path,
        protocol='tcp',
        connect_func=tcp_connect,
        send_func=send_single_wrapper,
        send_merged_func=send_merged_wrapper,
        recv_func=tcp_recv,
        parse_func=tcp_parse_single,
        parse_merged_func=tcp_parse_merged,
        batch_mode=batch_mode,
        retry_count=retry_count,
        timeout_ms=timeout_ms,
        interval_ms=interval_ms,
        ip=ip,
        port=port,
        slave_id=slave_id,
        target=f"{ip}:{port}"
    )