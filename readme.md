modbus_test/
├── main.py              # 主程序入口
├── config.py            # 配置常量
├── utils.py             # 工具函数
├── modbus_core.py       # Modbus 核心功能
├── modbus_common.py     # 公共执行逻辑
├── modbus_rtu.py        # Modbus RTU 通信层
├── modbus_tcp.py        # Modbus TCP 通信层
├── protocol_selector.py # 协议选择器
├── 1.xlsx               # 测试用例 Excel 文件
└── logs/                # 日志文件夹（自动生成）

图形化界面 (GUI)
├── 配置区域
│   ├── 协议选择 (TCP/RTU 下拉框)
│   ├── 连接参数 (根据协议动态显示)
│   │   ├── TCP: IP地址、端口
│   │   └── RTU: 串口号、波特率、数据位等
│   ├── Modbus参数
│   │   ├── 从站ID
│   │   ├── 功能码 (下拉选择)
│   │   ├── 起始地址
│   │   └── 寄存器数量
│   └── 操作按钮 (连接/断开/读取/写入)
├── 日志显示区域
│   ├── 实时日志输出 (支持不同颜色区分级别)
│   ├── 清空日志按钮
│   └── 另存为日志按钮
└── 状态栏 (显示当前连接状态)
