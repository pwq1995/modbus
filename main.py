#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus 自动化测试工具 - 主程序入口
============================================================
功能说明:
    支持 Modbus RTU 和 Modbus TCP 协议
    支持 01/02/03/04/06/16 功能码
    支持 BIT/UINT16/INT16/UINT32/INT32/FLOAT 数据类型
    支持 ABCD/BADC/CDAB/DCBA 字节顺序
    支持批量合并发送
    支持 RTU 串口参数配置（数据位、校验位、停止位）

包含方法:
    1. get_base_dir: 获取程序所在目录
    2. wait_key: 等待用户按键
    3. load_config: 加载配置文件，不存在则创建默认配置
    4. main: 程序主入口

作者: Modbus Test Tool
版本: v1.0
"""

import os
import sys
import configparser
import time
from utils import setup_logging
from protocol_selector import run_test_batch
from config import DATA_BITS_MAP, PARITY_MAP, STOP_BITS_MAP


def get_base_dir():
    """获取程序所在目录（支持 exe 和源码）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def wait_key():
    """等待用户按任意键（兼容 exe 和源码环境）"""
    print("\n按任意键退出...")
    try:
        import msvcrt
        msvcrt.getch()
    except (ImportError, AttributeError):
        try:
            input()
        except:
            print("3秒后自动退出...")
            time.sleep(3)


def load_config(config_path=None):
    """加载配置文件，如果不存在则创建默认配置"""
    if config_path is None:
        base_dir = get_base_dir()
        config_path = os.path.join(base_dir, 'config.ini')
    
    config = configparser.ConfigParser()
    
    if not os.path.exists(config_path):
        config['Protocol'] = {'protocol': 'rtu'}
        config['Timing'] = {'timeout_ms': '1000', 'interval_ms': '200'}
        config['Retry'] = {'retry_count': '3'}
        config['Files'] = {'excel_path': '1.xlsx'}
        config['Mode'] = {'batch_mode': 'True'}
        
        config['Serial'] = {
            'port': 'COM3',
            'baudrate': '9600',
            'data_bits': '8',
            'parity': 'N',
            'stop_bits': '1',
            'slave_id': '1'
        }
        
        config['TCP'] = {
            'ip': '192.168.1.100',
            'port': '502',
            'slave_id': '1'
        }
        
        with open(config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        print(f"[信息] 已创建默认配置文件: {config_path}")
    
    config.read(config_path, encoding='utf-8')
    
    protocol = config.get('Protocol', 'protocol', fallback='rtu')
    
    result = {
        'protocol': protocol,
        'timeout_ms': config.getint('Timing', 'timeout_ms', fallback=1000),
        'interval_ms': config.getint('Timing', 'interval_ms', fallback=200),
        'retry_count': config.getint('Retry', 'retry_count', fallback=3),
        'excel_path': config.get('Files', 'excel_path', fallback='1.xlsx'),
        'batch_mode': config.getboolean('Mode', 'batch_mode', fallback=True),
        'config_path': config_path,
    }
    
    if protocol.lower() == 'rtu':
        result['port'] = config.get('Serial', 'port', fallback='COM3')
        result['baudrate'] = config.getint('Serial', 'baudrate', fallback=9600)
        result['slave_id'] = config.getint('Serial', 'slave_id', fallback=1)
        
        data_bits_str = config.get('Serial', 'data_bits', fallback='8')
        result['data_bits'] = DATA_BITS_MAP.get(data_bits_str, 8)
        
        parity_str = config.get('Serial', 'parity', fallback='N')
        result['parity'] = PARITY_MAP.get(parity_str, 'N')
        
        stop_bits_str = config.get('Serial', 'stop_bits', fallback='1')
        result['stop_bits'] = STOP_BITS_MAP.get(stop_bits_str, 1)
        
        if result['data_bits'] not in [5, 6, 7, 8]:
            print(f"⚠️ 警告: data_bits={data_bits_str} 无效，使用默认值 8")
            result['data_bits'] = 8
        if result['parity'] not in ['N', 'E', 'O']:
            print(f"⚠️ 警告: parity={parity_str} 无效，使用默认值 N")
            result['parity'] = 'N'
        if result['stop_bits'] not in [1, 1.5, 2]:
            print(f"⚠️ 警告: stop_bits={stop_bits_str} 无效，使用默认值 1")
            result['stop_bits'] = 1
        
    else:
        result['ip'] = config.get('TCP', 'ip', fallback='192.168.1.100')
        result['tcp_port'] = config.getint('TCP', 'port', fallback=502)
        result['slave_id'] = config.getint('TCP', 'slave_id', fallback=1)
    
    return result


def main():
    """程序主入口"""
    print("=" * 60)
    print("  Modbus 自动化测试工具 v1.0")
    print("  支持 RTU / TCP")
    print("=" * 60)
    print()
    
    config = load_config()
    
    print("【配置信息】")
    print(f"  配置文件:    {config['config_path']}")
    print(f"  协议:        {config['protocol'].upper()}")
    
    if config['protocol'].lower() == 'rtu':
        print(f"  串口号:      {config['port']}")
        print(f"  波特率:      {config['baudrate']}")
        parity_display = {'N': '无', 'E': '偶校验', 'O': '奇校验'}.get(config['parity'], config['parity'])
        print(f"  数据位:      {config['data_bits']}")
        print(f"  校验位:      {parity_display}")
        print(f"  停止位:      {config['stop_bits']}")
        print(f"  从站ID:      {config['slave_id']}")
    else:
        print(f"  IP地址:      {config['ip']}")
        print(f"  TCP端口:     {config['tcp_port']}")
        print(f"  从站ID:      {config['slave_id']}")
    
    print(f"  超时时间:    {config['timeout_ms']} ms")
    print(f"  发送间隔:    {config['interval_ms']} ms")
    print(f"  重试次数:    {config['retry_count']}")
    print(f"  Excel 文件:  {config['excel_path']}")
    print(f"  批量模式:    {'开启' if config['batch_mode'] else '关闭'}")
    print("=" * 60)
    print()
    
    base_dir = get_base_dir()
    excel_full_path = config['excel_path']
    if not os.path.isabs(excel_full_path):
        excel_full_path = os.path.join(base_dir, excel_full_path)
    
    if not os.path.exists(excel_full_path):
        print(f"⚠️ 错误: Excel 文件 '{excel_full_path}' 不存在")
        print("请修改 config.ini 中的 excel_path 配置")
        wait_key()
        sys.exit(1)
    
    print("开始测试...")
    print("-" * 60)
    
    setup_logging()
    
    try:
        if config['protocol'].lower() == 'rtu':
            run_test_batch(
                protocol='rtu',
                excel_path=excel_full_path,
                port_or_ip=config['port'],
                baudrate=config['baudrate'],
                data_bits=config['data_bits'],
                parity=config['parity'],
                stop_bits=config['stop_bits'],
                slave_id=config['slave_id'],
                timeout_ms=config['timeout_ms'],
                interval_ms=config['interval_ms'],
                batch_mode=config['batch_mode'],
                retry_count=config['retry_count']
            )
        else:
            run_test_batch(
                protocol='tcp',
                excel_path=excel_full_path,
                port_or_ip=config['ip'],
                tcp_port=config['tcp_port'],
                slave_id=config['slave_id'],
                timeout_ms=config['timeout_ms'],
                interval_ms=config['interval_ms'],
                batch_mode=config['batch_mode'],
                retry_count=config['retry_count']
            )
    except Exception as e:
        print(f"\n❌ 测试执行异常: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("=" * 60)
    print("  测试完成!")
    print("=" * 60)
    
    wait_key()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 程序异常: {e}")
        import traceback
        traceback.print_exc()
        wait_key()