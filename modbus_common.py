#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus RTU/TCP 公共执行逻辑模块
============================================================
功能说明:
    提供 RTU 和 TCP 通用的执行逻辑，包括日志、重试、解析、批量执行

包含方法:
    1. log_*: 日志打印工具函数
    2. get_case_params: 从行数据中提取用例参数
    3. parse_response_data: 解析响应数据（RTU/TCP 通用）
    4. check_error_response: 检查响应是否为错误响应
    5. parse_merged_response: 解析合并响应数据
    6. prepare_merge_groups: 准备合并组
    7. run_case_with_retry: 执行单条用例（带重试，失败不中断）
    8. run_batch: 通用批量执行
    9. process_merged_group: 通用合并组处理

作者: Modbus Test Tool
版本: v1.0
"""

import logging
import time
import pandas as pd
from collections import defaultdict

from config import ERROR_CODES, DATA_TYPE_INFO, MAX_QUANTITY_PER_FRAME, DEFAULT_RETRY_COUNT
from utils import format_hex_bytes, ms_to_seconds
from modbus_core import (
    parse_bit_response,
    parse_bit_response_from_offset,
    parse_register_response,
    get_parse_mode,
    validate_quantity,
)


# ==================== 日志工具函数 ====================

def log_separator():
    """打印分隔线，用于区分不同的报文交换"""
    logging.info("  ──────────────────────────────────────────────")


def log_start(protocol, target, slave_id=None, timeout_ms=1000, interval_ms=200,
              retry_count=3, total=0, batch_mode=True):
    """打印测试启动信息"""
    logging.info("")
    logging.info("═" * 60)
    logging.info(f"  📡 Modbus {protocol.upper()} 测试")
    logging.info("═" * 60)
    logging.info(f"  ├─ 目标:      {target}" + (f" | 从站: {slave_id}" if slave_id else ""))
    logging.info(f"  ├─ 超时:      {timeout_ms} ms")
    logging.info(f"  ├─ 发送间隔:  {interval_ms} ms")
    logging.info(f"  ├─ 重试次数:  {retry_count}")
    logging.info(f"  ├─ 用例总数:  {total}")
    logging.info(f"  └─ 批量模式:  {'开启' if batch_mode else '关闭'}")
    logging.info("═" * 60)


def log_result(total, passed, failed):
    """打印测试结果"""
    logging.info("")
    logging.info("═" * 60)
    logging.info(f"  📊 测试结果")
    logging.info("═" * 60)
    logging.info(f"  ├─ 总计:   {total}")
    logging.info(f"  ├─ 通过:   {passed}  ✅")
    logging.info(f"  ├─ 失败:   {failed}  ❌")
    if total > 0:
        pass_rate = (passed / total) * 100
        logging.info(f"  └─ 通过率: {pass_rate:.1f}%")
    logging.info("═" * 60)


def log_case_header(case_id):
    """打印用例开始分隔线"""
    logging.info("")
    logging.info(f"  ──── {case_id} ────")


def log_case_pass(case_id):
    """打印单条用例通过"""
    logging.info(f"  └─ ✅ {case_id} 通过")
    log_separator()


def log_case_fail(case_id, error):
    """打印单条用例失败"""
    logging.error(f"  └─ ❌ {case_id} 失败: {error}")
    log_separator()


def log_merged_start(group, group_idx, total_groups):
    """打印合并请求开始"""
    case_ids = "、".join([str(row[1]['用例编号']) for row in group])
    logging.info("")
    logging.info(f"  ┌─ 合并请求 [{group_idx}/{total_groups}] ── {len(group)} 条用例: {case_ids}")


def log_merged_end(sub_passed, sub_failed):
    """打印合并请求结束"""
    logging.info(f"  └─ 合并完成: 通过 {sub_passed} | 失败 {sub_failed}")
    log_separator()


def log_send(data, label="→"):
    """打印发送报文"""
    logging.info(f"  ├─ {label} {format_hex_bytes(data)}")


def log_recv(data, label="←"):
    """打印接收报文"""
    logging.info(f"  ├─ {label} {format_hex_bytes(data)}")


def log_retry(attempt, retry_count):
    """打印重试信息"""
    logging.info(f"  │  🔄 重试 {attempt}/{retry_count}")


def log_warning(msg):
    """打印警告"""
    logging.warning(f"  │  ⚠ {msg}")


def log_parse_result(regs, data_type):
    """打印解析结果"""
    if not regs:
        return
    
    if not isinstance(regs, list) or len(regs) == 0:
        return
    
    first = regs[0]
    first_is_tuple = isinstance(first, tuple)
    first_is_int = isinstance(first, int)
    
    if first_is_int:
        logging.info(f"  ├─ 解析结果: {len(regs)} 个值")
        for i, val in enumerate(regs):
            status = "ON" if val else "OFF"
            logging.info(f"  │    Bit[{i}] = {val} ({status})")
        return
    
    is_bit = data_type == 'BIT'
    if not is_bit and first_is_tuple:
        if len(first) == 2:
            is_bit = True
        elif len(first) >= 3 and first[2] == 'BIT':
            is_bit = True
    
    if is_bit:
        logging.info(f"  ├─ 按位解析: {len(regs)} 个位")
        for item in regs:
            if isinstance(item, tuple) and len(item) >= 2:
                name, val = item[0], item[1]
                status = "ON" if val else "OFF"
                logging.info(f"  │    {name} = {val} ({status})")
            else:
                logging.info(f"  │    {item}")
        return
    
    logging.info(f"  ├─ 寄存器解析: {len(regs)} 个寄存器")
    for item in regs:
        if isinstance(item, tuple):
            if len(item) >= 3:
                name, val, dtype = item[0], item[1], item[2]
                if dtype in ['FLOAT']:
                    logging.info(f"  │    {name} = {val:.6f}")
                elif dtype in ['UINT32', 'INT32']:
                    logging.info(f"  │    {name} = {val} (0x{val:08X})")
                else:
                    logging.info(f"  │    {name} = {val} (0x{val:04X})")
            elif len(item) >= 2:
                name, val = item[0], item[1]
                logging.info(f"  │    {name} = {val}")
            else:
                logging.info(f"  │    {item}")
        elif isinstance(item, int):
            logging.info(f"  │    {item}")
        else:
            logging.info(f"  │    {item}")


def log_merged_case_result(case, result, data_type):
    """打印合并用例中的单个结果"""
    if not result or not isinstance(result, list) or len(result) == 0:
        return
    
    first = result[0]
    first_is_tuple = isinstance(first, tuple)
    first_is_int = isinstance(first, int)
    
    if first_is_int:
        logging.info(f"  │    {case}: {len(result)} 个值")
        for i, val in enumerate(result):
            status = "ON" if val else "OFF"
            logging.info(f"  │      Bit[{i}] = {val} ({status})")
        return
    
    is_bit = data_type == 'BIT'
    if not is_bit and first_is_tuple:
        if len(first) == 2:
            is_bit = True
        elif len(first) >= 3 and first[2] == 'BIT':
            is_bit = True
    
    if is_bit:
        logging.info(f"  │    {case}: {len(result)} 位")
        for item in result:
            if isinstance(item, tuple) and len(item) >= 2:
                name, val = item[0], item[1]
                status = "ON" if val else "OFF"
                logging.info(f"  │      {name} = {val} ({status})")
            else:
                logging.info(f"  │      {item}")
        return
    
    logging.info(f"  │    {case}:")
    for item in result:
        if isinstance(item, tuple):
            if len(item) >= 3:
                name, val, dtype = item[0], item[1], item[2]
                if dtype in ['FLOAT']:
                    logging.info(f"  │      {name} = {val:.6f}")
                elif dtype in ['UINT32', 'INT32']:
                    logging.info(f"  │      {name} = {val} (0x{val:08X})")
                else:
                    logging.info(f"  │      {name} = {val} (0x{val:04X})")
            elif len(item) >= 2:
                name, val = item[0], item[1]
                logging.info(f"  │      {name} = {val}")
            else:
                logging.info(f"  │      {item}")
        elif isinstance(item, int):
            logging.info(f"  │      {item}")
        else:
            logging.info(f"  │      {item}")


# ==================== 获取用例参数 ====================

def get_case_params(row):
    """从行数据中提取用例参数"""
    addr = int(row['设备地址'])
    func_code = int(row['功能码'])
    start = int(row['起始地址'])
    quantity = int(row['寄存器数量'])
    data_type = str(row.get('数据类型', 'UINT16')).strip().upper()
    if data_type not in DATA_TYPE_INFO:
        data_type = 'UINT16'
    byte_order = str(row.get('字节顺序', 'ABCD')).strip().upper()
    if byte_order not in ['ABCD', 'BADC', 'CDAB', 'DCBA']:
        byte_order = 'ABCD'
    return addr, func_code, start, quantity, data_type, byte_order


# ==================== 解析响应数据 ====================

def parse_response_data(resp_data, row, func_code, data_type, byte_order, quantity):
    """解析响应数据（RTU 和 TCP 通用）"""
    parse_mode = get_parse_mode(func_code, data_type)
    
    if parse_mode == 'bit':
        if func_code in [1, 2]:
            if len(resp_data) < 1:
                raise Exception("响应数据为空")
            names_str = row.get('位名称', '')
            bits = parse_bit_response(resp_data, names_str, quantity)
            
            if bits and isinstance(bits[0], int):
                bits = [(f"Bit[{i}]", val) for i, val in enumerate(bits)]
            
            log_parse_result(bits, 'BIT')
            return [v for _, v in bits]
        else:
            if len(resp_data) < 1:
                raise Exception("响应数据为空")
            names_str = row.get('位名称', '')
            if not names_str or str(names_str).strip() == '' or str(names_str).lower() == 'nan':
                names_str = row.get('寄存器描述', '')
            regs = parse_register_response(resp_data, names_str, data_type, byte_order)
            log_parse_result(regs, 'BIT')
            return [v for _, v, _, _ in regs]
    
    elif parse_mode == 'register':
        if len(resp_data) < 1:
            raise Exception("响应数据为空")
        desc_str = row.get('寄存器描述', '')
        regs = parse_register_response(resp_data, desc_str, data_type, byte_order)
        log_parse_result(regs, data_type)
        return [v for _, v, _, _ in regs]
    
    else:
        logging.info(f"  ├─ 写入操作完成")
        return resp_data


# ==================== 检查错误响应 ====================

def check_error_response(pdu, func_code):
    """
    检查响应是否为错误响应
    
    输入参数:
        pdu: bytes - 响应 PDU（含功能码）
        func_code: int - 请求功能码
    
    异常抛出:
        Exception: 检测到错误响应时抛出
    
    说明:
        RTU PDU: [功能码] [错误码]
        TCP PDU: [单元ID] [功能码] [错误码]
    """
    if len(pdu) < 2:
        return
    
    # 检测 PDU 格式
    # 如果 pdu[0] 在 1-247 范围内，且 pdu[1] > 0x80，则为 TCP 格式（含单元ID）
    # 否则为 RTU 格式（不含单元ID）
    is_tcp = (1 <= pdu[0] <= 247) and (pdu[1] > 0x80) if len(pdu) >= 2 else False
    
    if is_tcp:
        # TCP 格式: [单元ID] [功能码] [错误码]
        resp_func = pdu[1]
        error_code = pdu[2] if len(pdu) >= 3 else 0x00
    else:
        # RTU 格式: [功能码] [错误码]
        resp_func = pdu[0]
        error_code = pdu[1] if len(pdu) >= 2 else 0x00
    
    if resp_func == (func_code | 0x80):
        error_msg = ERROR_CODES.get(error_code, f"未知错误 (0x{error_code:02X})")
        raise Exception(f"0x{resp_func:02X} 错误码: 0x{error_code:02X} ({error_msg})")


# ==================== 解析合并响应 ====================

def parse_merged_response(all_data, group, func_code):
    """解析合并响应数据，将数据分发给各个子用例"""
    bit_pos_in_data = 0
    sub_passed, sub_failed = 0, 0
    
    for idx, row in group:
        case = row['用例编号']
        num = int(row['寄存器数量'])
        byte_order = str(row.get('字节顺序', 'ABCD')).strip().upper()
        if byte_order not in ['ABCD', 'BADC', 'CDAB', 'DCBA']:
            byte_order = 'ABCD'
        data_type = str(row.get('数据类型', 'UINT16')).strip().upper()
        if data_type not in DATA_TYPE_INFO:
            data_type = 'UINT16'
        
        try:
            if func_code in [1, 2]:
                start_bit = bit_pos_in_data
                end_bit = bit_pos_in_data + num
                start_byte = start_bit // 8
                end_byte = (end_bit + 7) // 8
                chunk = all_data[start_byte:end_byte]
                if len(chunk) < end_byte - start_byte:
                    chunk = chunk + b'\x00' * ((end_byte - start_byte) - len(chunk))
                names_str = row.get('位名称', '')
                bits = parse_bit_response_from_offset(chunk, names_str, num, start_bit % 8)
                
                if bits and isinstance(bits[0], int):
                    bits = [(f"Bit[{i}]", val) for i, val in enumerate(bits)]
                
                log_merged_case_result(case, bits, 'BIT')
                bit_pos_in_data += num
            else:
                chunk = all_data[bit_pos_in_data:bit_pos_in_data + num * 2]
                bit_pos_in_data += num * 2
                desc_str = row.get('寄存器描述', '')
                parse_mode = get_parse_mode(func_code, data_type)
                
                if parse_mode == 'bit':
                    bit_names = row.get('位名称', '')
                    name_str = bit_names if bit_names and str(bit_names).strip() and str(bit_names).lower() != 'nan' else desc_str
                    regs = parse_register_response(chunk, name_str, data_type, byte_order)
                    log_merged_case_result(case, regs, 'BIT')
                elif parse_mode == 'register':
                    regs = parse_register_response(chunk, desc_str, data_type, byte_order)
                    log_merged_case_result(case, regs, data_type)
                else:
                    logging.info(f"  │    写入操作完成")
            sub_passed += 1
        except Exception as e:
            sub_failed += 1
            logging.error(f"  │    ❌ {case} 失败: {e}")
    
    return sub_passed, sub_failed


# ==================== 准备合并组 ====================

def prepare_merge_groups(df, batch_mode):
    """准备合并组"""
    if not batch_mode:
        return [[(idx, row)] for idx, row in df.iterrows()]
    
    groups = defaultdict(list)
    for idx, row in df.iterrows():
        key = (int(row['设备地址']), int(row['功能码']))
        groups[key].append((idx, row))
    
    all_groups = []
    for (addr, func_code), items in groups.items():
        if func_code in [6, 16]:
            for idx, row in items:
                all_groups.append([(idx, row)])
            continue
        
        items_sorted = sorted(items, key=lambda x: int(x[1]['起始地址']))
        merged_groups, current_group = [], [items_sorted[0]]
        for i in range(1, len(items_sorted)):
            prev_row, curr_row = items_sorted[i-1][1], items_sorted[i][1]
            prev_start, prev_num = int(prev_row['起始地址']), int(prev_row['寄存器数量'])
            curr_start = int(curr_row['起始地址'])
            if curr_start == prev_start + prev_num:
                current_group.append(items_sorted[i])
            else:
                merged_groups.append(current_group)
                current_group = [items_sorted[i]]
        merged_groups.append(current_group)
        
        for group in merged_groups:
            if len(group) == 1:
                all_groups.append(group)
                continue
            first_row, last_row = group[0][1], group[-1][1]
            start = int(first_row['起始地址'])
            total_num = int(last_row['起始地址']) + int(last_row['寄存器数量']) - start
            if total_num <= MAX_QUANTITY_PER_FRAME.get(func_code, 125):
                all_groups.append(group)
            else:
                split_groups, temp_group, temp_start, temp_total = [], [], None, 0
                for item in group:
                    row, num = item[1], int(item[1]['寄存器数量'])
                    if temp_start is None:
                        temp_start, temp_group, temp_total = int(row['起始地址']), [item], num
                    elif int(row['起始地址']) == temp_start + temp_total:
                        if temp_total + num <= MAX_QUANTITY_PER_FRAME.get(func_code, 125):
                            temp_group.append(item)
                            temp_total += num
                        else:
                            split_groups.append(temp_group)
                            temp_group, temp_start, temp_total = [item], int(row['起始地址']), num
                    else:
                        split_groups.append(temp_group)
                        temp_group, temp_start, temp_total = [item], int(row['起始地址']), num
                if temp_group:
                    split_groups.append(temp_group)
                all_groups.extend(split_groups)
    
    return all_groups


# ==================== 执行单条用例（带重试，失败不中断） ====================

def run_case_with_retry(send_func, recv_func, parse_func, row, retry_count=3, **kwargs):
    """
    通用单条用例执行（带重试，失败不中断）
    注意: send_func 内部已完成实际发送
    """
    case_id = row['用例编号']
    last_error = None
    
    log_case_header(case_id)
    
    for attempt in range(1, retry_count + 1):
        try:
            if attempt > 1:
                log_retry(attempt, retry_count)
            
            conn = kwargs.get('conn')
            if conn is None:
                raise Exception("连接对象不存在")
            
            slave_id = kwargs.get('slave_id', 1)
            
            msg = send_func(row, conn=conn, slave_id=slave_id)
            log_send(msg)
            
            resp = recv_func(conn=conn, timeout_ms=kwargs.get('timeout_ms', 1000))
            log_recv(resp)
            
            result = parse_func(resp, row, conn=conn, func_code=kwargs.get('func_code'))
            log_case_pass(case_id)
            return True, result
            
        except Exception as e:
            last_error = e
            log_warning(f"尝试 {attempt}/{retry_count} 失败: {e}")
            if attempt < retry_count:
                time.sleep(0.1)
    
    log_case_fail(case_id, last_error)
    return False, str(last_error)


# ==================== 通用批量执行 ====================

def run_batch(excel_path, protocol, connect_func, send_func, recv_func, parse_func,
              send_merged_func=None, parse_merged_func=None,
              batch_mode=True, retry_count=3, timeout_ms=1000, interval_ms=200, **kwargs):
    """通用批量执行"""
    df = pd.read_excel(excel_path, engine='openpyxl')
    total, passed, failed = len(df), 0, 0
    
    if send_merged_func is None:
        send_merged_func = send_func
    if parse_merged_func is None:
        parse_merged_func = parse_func
    
    target = kwargs.get('target', '')
    slave_id = kwargs.get('slave_id', None)
    log_start(protocol, target, slave_id, timeout_ms, interval_ms, retry_count, total, batch_mode)
    
    conn = None
    try:
        conn_kwargs = {**kwargs, 'timeout_ms': timeout_ms}
        conn = connect_func(**conn_kwargs)
        logging.info(f"  ✅ 连接已建立")
        logging.info("")
        
        groups = prepare_merge_groups(df, batch_mode)
        total_groups = len(groups)
        
        for group_idx, group in enumerate(groups, 1):
            func_code = int(group[0][1]['功能码'])
            
            if len(group) == 1:
                idx, row = group[0]
                success, _ = run_case_with_retry(
                    send_func, recv_func, parse_func, row,
                    retry_count=retry_count, 
                    conn=conn,
                    func_code=func_code,
                    protocol=protocol,
                    timeout_ms=timeout_ms,
                    slave_id=kwargs.get('slave_id', 1)
                )
                if success:
                    passed += 1
                else:
                    failed += 1
            else:
                sub_passed, sub_failed = process_merged_group(
                    conn=conn,
                    group=group,
                    func_code=func_code,
                    send_func=send_merged_func,
                    recv_func=recv_func,
                    parse_func=parse_merged_func,
                    single_parse_func=parse_func,
                    group_idx=group_idx,
                    total_groups=total_groups,
                    retry_count=retry_count,
                    timeout_ms=timeout_ms,
                    protocol=protocol,
                    slave_id=kwargs.get('slave_id', 1),
                    single_send_func=send_func
                )
                passed += sub_passed
                failed += sub_failed
            
            time.sleep(ms_to_seconds(interval_ms))
        
        if conn:
            conn.close()
            logging.info("")
            logging.info(f"  ✅ 连接已断开")
    
    except Exception as e:
        logging.error(f"  ❌ 连接失败: {e}")
        if conn:
            conn.close()
        return
    
    log_result(total, passed, failed)


# ==================== 通用合并组处理 ====================

def process_merged_group(conn, group, func_code, send_func, recv_func, parse_func,
                         group_idx, total_groups, retry_count, timeout_ms, protocol, 
                         single_send_func=None, single_parse_func=None, **kwargs):
    """
    通用合并组处理
    注意: send_func 内部已完成实际发送
    """
    log_merged_start(group, group_idx, total_groups)
    
    first_row, last_row = group[0][1], group[-1][1]
    start = int(first_row['起始地址'])
    total_num = int(last_row['起始地址']) + int(last_row['寄存器数量']) - start
    
    last_error = None
    for attempt in range(1, retry_count + 1):
        try:
            if attempt > 1:
                log_retry(attempt, retry_count)
            
            slave_id = kwargs.get('slave_id', 1)
            
            msg = send_func(group, func_code, start, total_num, conn=conn, slave_id=slave_id)
            log_send(msg, "📦→")
            logging.info(f"  │  地址范围: {start} ~ {start + total_num - 1}")
            
            resp = recv_func(conn=conn, timeout_ms=timeout_ms)
            log_recv(resp, "📦←")
            
            result = parse_func(resp, group, func_code, conn=conn)
            
            if isinstance(result, tuple):
                sub_passed, sub_failed = result
            else:
                sub_passed, sub_failed = len(group), 0
            
            log_merged_end(sub_passed, sub_failed)
            return sub_passed, sub_failed
            
        except Exception as e:
            last_error = e
            log_warning(f"合并尝试 {attempt}/{retry_count} 失败: {e}")
            if attempt < retry_count:
                time.sleep(0.1)
    
    retry_send_func = single_send_func if single_send_func is not None else send_func
    retry_parse_func = single_parse_func if single_parse_func is not None else parse_func
    
    logging.error(f"  ❌ 合并失败，切换逐条")
    
    sub_passed, sub_failed = 0, 0
    for idx, row in group:
        case = row['用例编号']
        success, _ = run_case_with_retry(
            retry_send_func, recv_func, retry_parse_func, row,
            retry_count=retry_count, 
            conn=conn, 
            func_code=func_code,
            protocol=protocol, 
            timeout_ms=timeout_ms,
            slave_id=kwargs.get('slave_id', 1)
        )
        if success:
            sub_passed += 1
        else:
            sub_failed += 1
        time.sleep(0.2)
    
    return sub_passed, sub_failed