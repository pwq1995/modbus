#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
协议选择器模块
============================================================
功能说明:
    根据配置选择 Modbus RTU 或 TCP 协议执行器

包含方法:
    1. run_test_batch: 根据协议类型选择执行器

作者: Modbus Test Tool
版本: v1.0
"""

import logging
import modbus_rtu
import modbus_tcp


def run_test_batch(protocol, excel_path, port_or_ip, **kwargs):
    """根据协议类型选择执行器"""
    if protocol.lower() == 'rtu':
        logging.info(f"使用 Modbus RTU 协议，串口: {port_or_ip}")
        modbus_rtu.run_test_batch(
            excel_path=excel_path,
            port=port_or_ip,
            baudrate=kwargs.get('baudrate', 9600),
            data_bits=kwargs.get('data_bits', 8),
            parity=kwargs.get('parity', 'N'),
            stop_bits=kwargs.get('stop_bits', 1),
            slave_id=kwargs.get('slave_id', 1),
            timeout_ms=kwargs.get('timeout_ms', 1000),
            interval_ms=kwargs.get('interval_ms', 200),
            batch_mode=kwargs.get('batch_mode', True),
            retry_count=kwargs.get('retry_count', 3)
        )
    elif protocol.lower() == 'tcp':
        logging.info(f"使用 Modbus TCP 协议，IP: {port_or_ip}:{kwargs.get('tcp_port', 502)}")
        modbus_tcp.run_test_batch_tcp(
            excel_path=excel_path,
            ip=port_or_ip,
            port=kwargs.get('tcp_port', 502),
            slave_id=kwargs.get('slave_id', 1),
            timeout_ms=kwargs.get('timeout_ms', 1000),
            interval_ms=kwargs.get('interval_ms', 200),
            batch_mode=kwargs.get('batch_mode', True),
            retry_count=kwargs.get('retry_count', 3)
        )
    else:
        raise ValueError(f"不支持的协议: {protocol}，请选择 'rtu' 或 'tcp'")