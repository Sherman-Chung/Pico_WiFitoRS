# hold_register_map.py - 512 寄存器映射定義與編碼轉換層
#
# 維護導讀：
# - hold_register_map.REGIONS 定義各區域的用途和邊界
# - encode_value() / decode_value() 處理編碼（例如波特率 2->9600）
# - update_memory_from_config() 在啟動時從 config_store 填充記憶體
# - prune_config_from_memory() 在保存時從記憶體提取配置回 config_store
# - 所有讀寫操作都經過此層，確保一致性

"""
Hold Register 記憶體配置（共 512 個 16-bit 暫存器）

配置區域 (0-49)：可讀可寫，對應系統配置
  0-4:    Modbus TCP 設定
  5-10:   CH0 參數
  11-16:  CH1 參數
  20-22:  AP & Poller 控制
  
狀態區域 (50-59)：系統狀態，唯讀
  50-56:  系統運行狀態
  
控制區域 (60-69)：特殊命令，寫入觸發動作
  60:     保存配置到 Flash
  61:     重置配置
  62:     重啟系統
  63:     上次命令狀態
  64:     套用配置（將 Hold Register 配置套用到系統）
  
輪詢資料區域 (100-355)：Poller 回填結果，唯讀
  每個位置對應一個設備的最新查詢結果
  
保留區域 (356-511)：未來擴展
"""

try:
    import ujson as json
except ImportError:
    import json

# =============== 區域定義 ===============
REGIONS = {
    "config": {"start": 0, "end": 49, "desc": "配置區域"},
    "status": {"start": 50, "end": 59, "desc": "狀態區域（唯讀）"},
    "control": {"start": 60, "end": 69, "desc": "控制區域"},
    "poll_data": {"start": 100, "end": 355, "desc": "輪詢資料區域（唯讀）"},
    "reserved": {"start": 356, "end": 511, "desc": "保留區域"},
}

# =============== 詳細暫存器定義 ===============
REGISTER_DEFS = {
    # Modbus TCP 設定 (0-4)
    0: {"name": "TCP_PORT", "default": 502, "desc": "Modbus TCP 監聽端口"},
    1: {"name": "TCP_SLAVE_ID", "default": 1, "min": 1, "max": 247, "desc": "TCP 本地讀取的 Slave ID"},
    2: {"name": "TCP_TIMEOUT", "default": 120, "desc": "回覆超時 (ms/10)，即實際值 * 10"},
    3: {"name": "TCP_RS485_MODE", "default": 0, "enum": {0: "disabled", 1: "ch0", 2: "ch1"}, "desc": "TCP 間接轉向 RS485 模式"},

    # CH0 設定 (5-10)
    5: {"name": "CH0_ENABLED", "default": 0, "enum": {0: "disabled", 1: "enabled"}, "desc": "CH0 啟用控制"},
    6: {"name": "CH0_MODE", "default": 0, "enum": {0: "rtu", 1: "ascii"}, "desc": "CH0 通訊模式"},
    7: {
        "name": "CH0_BAUDRATE",
        "default": 2,
        "enum": {0: 2400, 1: 4800, 2: 9600, 3: 38400, 4: 115200},
        "desc": "CH0 波特率編碼",
    },
    8: {"name": "CH0_PARITY", "default": 0, "enum": {0: "N", 1: "E", 2: "O"}, "desc": "CH0 校驗位"},
    9: {"name": "CH0_STOPBITS", "default": 0, "enum": {0: 1, 1: 2}, "desc": "CH0 停止位"},
    10: {"name": "CH0_BITS", "default": 0, "enum": {0: 8, 1: 7}, "desc": "CH0 資料位"},

    # CH1 設定 (11-16)
    11: {"name": "CH1_ENABLED", "default": 0, "enum": {0: "disabled", 1: "enabled"}, "desc": "CH1 啟用控制"},
    12: {"name": "CH1_MODE", "default": 0, "enum": {0: "rtu", 1: "ascii"}, "desc": "CH1 通訊模式"},
    13: {
        "name": "CH1_BAUDRATE",
        "default": 2,
        "enum": {0: 2400, 1: 4800, 2: 9600, 3: 38400, 4: 115200},
        "desc": "CH1 波特率編碼",
    },
    14: {"name": "CH1_PARITY", "default": 0, "enum": {0: "N", 1: "E", 2: "O"}, "desc": "CH1 校驗位"},
    15: {"name": "CH1_STOPBITS", "default": 0, "enum": {0: 1, 1: 2}, "desc": "CH1 停止位"},
    16: {"name": "CH1_BITS", "default": 0, "enum": {0: 8, 1: 7}, "desc": "CH1 資料位"},

    # AP & Poller 控制 (20-22)
    20: {"name": "AP_ENABLED", "default": 1, "enum": {0: "disabled", 1: "enabled"}, "desc": "AP 啟用控制"},
    21: {"name": "POLLER_ENABLED", "default": 0, "enum": {0: "disabled", 1: "enabled"}, "desc": "Poller 啟用"},
    22: {"name": "POLLER_INTERVAL", "default": 100, "desc": "Poller 間隔 (ms/10)，即實際值 * 10"},

    # 系統狀態 (50-59, 唯讀)
    50: {"name": "SYS_RUN_TIME", "readonly": True, "desc": "系統運行時間 (s)"},
    51: {"name": "SYS_CPU_TEMP", "readonly": True, "desc": "CPU 溫度 (°C)"},
    52: {"name": "SYS_BATT_V", "readonly": True, "desc": "電池電壓 (0.01V)"},
    53: {"name": "SYS_BATT_I", "readonly": True, "desc": "電池電流 (0.1mA)"},
    54: {"name": "SYS_BATT_P", "readonly": True, "desc": "電池百分比 (%)"},
    55: {"name": "SYS_STA_CONNECTED", "readonly": True, "enum": {0: "disconnected", 1: "connected"}, "desc": "STA 連接狀態"},
    56: {"name": "SYS_AP_ACTIVE", "readonly": True, "enum": {0: "inactive", 1: "active"}, "desc": "AP 啟用狀態"},

    # 控制命令 (60-64)
    60: {
        "name": "CMD_SAVE_CONFIG",
        "writeonly": True,
        "desc": "寫入 1 保存配置到 Flash；狀態將在 63 更新",
    },
    61: {"name": "CMD_RESET_CONFIG", "writeonly": True, "desc": "寫入 1 重置為預設值"},
    62: {"name": "CMD_REBOOT", "writeonly": True, "desc": "寫入 1 重啟系統"},
    63: {
        "name": "CMD_STATUS",
        "readonly": True,
        "enum": {0: "idle", 1: "busy", 2: "success", 3: "error"},
        "desc": "最後命令狀態",
    },
    64: {"name": "CMD_APPLY_CONFIG", "writeonly": True, "desc": "寫入 1 套用配置（特別用於 UART 參數）"},
    65: {
        "name": "CMD_COMM_LOG",
        "default": 1,
        "enum": {0: "disabled", 1: "enabled"},
        "desc": "是否記錄通訊日誌（TCP/RS485）",
    },
}


# =============== 編碼轉換函式 ===============
def encode_value(reg_index: int, value) -> int:
    """
    將應用層值轉換為鸘存器值。
    例如：波特率 9600 → 2
    """
    if reg_index < 0 or reg_index >= 512 or reg_index not in REGISTER_DEFS:
        return int(value) & 0xFFFF

    defn = REGISTER_DEFS[reg_index]
    if "enum" not in defn:
        return int(value) & 0xFFFF

    enum_map = defn["enum"]
    # 反向查找：值 → 鍵
    for k, v in enum_map.items():
        if v == value:
            return k

    return int(value) & 0xFFFF


def decode_value(reg_index: int, raw_value: int):
    """
    將鸘存器值轉換回應用層值。
    例如：2 → 9600
    """
    value = int(raw_value) & 0xFFFF

    if reg_index < 0 or reg_index >= 512 or reg_index not in REGISTER_DEFS:
        return value

    defn = REGISTER_DEFS[reg_index]
    if "enum" not in defn:
        return value

    enum_map = defn["enum"]
    return enum_map.get(value, value)


# =============== 啟動時同步：Flash → Memory ===============
def update_memory_from_config(config: dict, regs_write_fn):
    """
    啟動時執行一次，將 config_store 的設定同步到 Hold Register。
    regs_write_fn: set_regs(start, values) 函式
    """
    if not config:
        return

    # TCP 設定 (0-3)
    mb_cfg = config.get("modbus") or {}
    tcp_rs485_mode = (mb_cfg.get("tcp_rs485_mode") or "").strip().lower()
    if tcp_rs485_mode not in ("disabled", "ch0", "ch1"):
        tcp_rs485_mode = "ch0" if mb_cfg.get("tcp_indirect_control") else "disabled"
    regs = [
        mb_cfg.get("tcp_port") or 502,  # 0
        mb_cfg.get("tcp_slave_id") or 1,  # 1
        (mb_cfg.get("response_timeout_ms") or 1200) // 10,  # 2: 轉換為 ms/10
        encode_value(3, tcp_rs485_mode),  # 3
    ]
    # 跳過 4
    regs.append(0)
    regs_write_fn(0, regs)

    # CH0 設定 (5-10)
    ch0 = mb_cfg.get("ch0") or {}
    ch0_regs = [
        1 if mb_cfg.get("ch0_enabled") else 0,  # 5: 啟用
        encode_value(6, ch0.get("mode") or "rtu"),  # 6: 模式
        encode_value(7, ch0.get("baudrate") or 9600),  # 7: 波特率
        encode_value(8, ch0.get("parity") or "N"),  # 8: 校驗
        encode_value(9, ch0.get("stopbits") or 1),  # 9: 停止位
        encode_value(10, ch0.get("bits") or 8),  # 10: 資料位
    ]
    regs_write_fn(5, ch0_regs)

    # CH1 設定 (11-16)
    ch1 = mb_cfg.get("ch1") or {}
    ch1_regs = [
        1 if mb_cfg.get("ch1_enabled") else 0,  # 11: 啟用
        encode_value(12, ch1.get("mode") or "rtu"),  # 12: 模式
        encode_value(13, ch1.get("baudrate") or 9600),  # 13: 波特率
        encode_value(14, ch1.get("parity") or "N"),  # 14: 校驗
        encode_value(15, ch1.get("stopbits") or 1),  # 15: 停止位
        encode_value(16, ch1.get("bits") or 8),  # 16: 資料位
    ]
    regs_write_fn(11, ch1_regs)

    # AP & Poller (20-22)
    ap_cfg = config.get("ap") or {}
    poller_cfg = config.get("poller") or {}
    ap_enabled = ap_cfg.get("enabled", None)
    if ap_enabled is None:
        ap_enabled = bool(ap_cfg.get("ssid"))
    ap_poller_regs = [
        1 if ap_enabled else 0,  # 20: AP 啟用
        1 if poller_cfg.get("enabled") else 0,  # 21: Poller 啟用
        (poller_cfg.get("interval_ms") or 1000) // 10,  # 22: Poller 間隔 ms/10
    ]
    regs_write_fn(20, ap_poller_regs)

    print("[hold_register_map] Memory initialized from config")


# =============== 保存時同步：Memory → Flash ===============
def prune_config_from_memory(regs_read_fn, current_config: dict) -> dict:
    """
    保存時調用，從 Hold Register 提取修改的配置。
    regs_read_fn: get_regs(start, count) → list
    current_config: 保存前的當前設定（用於回填未修改的欄位）
    回傳修改後的 config dict（可直接傳給 update_config）
    """
    result = {}

    # 讀取 0-22 的配置區
    config_regs = regs_read_fn(0, 23)
    if len(config_regs) < 23:
        print("[hold_register_map] ERROR: 無法讀取配置區")
        return result

    # Modbus TCP (0-3)
    reg3_mode = decode_value(3, config_regs[3])
    if reg3_mode not in ("disabled", "ch0", "ch1"):
        reg3_mode = "disabled"
    result["modbus"] = current_config.get("modbus", {}).copy()
    result["modbus"].update(
        {
            "tcp_port": config_regs[0],
            "tcp_slave_id": config_regs[1],
            "response_timeout_ms": config_regs[2] * 10,  # 轉換回 ms
            "tcp_rs485_mode": reg3_mode,
            "tcp_indirect_control": reg3_mode != "disabled",
        }
    )

    # CH0 (5-10)
    ch0_cfg = current_config.get("modbus", {}).get("ch0", {}).copy()
    ch0_cfg.update(
        {
            "mode": decode_value(6, config_regs[6]),
            "baudrate": decode_value(7, config_regs[7]),
            "parity": decode_value(8, config_regs[8]),
            "stopbits": decode_value(9, config_regs[9]),
            "bits": decode_value(10, config_regs[10]),
        }
    )
    result["modbus"]["ch0_enabled"] = bool(config_regs[5])
    result["modbus"]["ch0"] = ch0_cfg

    # CH1 (11-16)
    ch1_cfg = current_config.get("modbus", {}).get("ch1", {}).copy()
    ch1_cfg.update(
        {
            "mode": decode_value(12, config_regs[12]),
            "baudrate": decode_value(13, config_regs[13]),
            "parity": decode_value(14, config_regs[14]),
            "stopbits": decode_value(15, config_regs[15]),
            "bits": decode_value(16, config_regs[16]),
        }
    )
    result["modbus"]["ch1_enabled"] = bool(config_regs[11])
    result["modbus"]["ch1"] = ch1_cfg

    # AP (20)
    result["ap"] = current_config.get("ap", {}).copy()
    result["ap"]["enabled"] = bool(config_regs[20])

    # Poller (21-22)
    result["poller"] = current_config.get("poller", {}).copy()
    result["poller"].update(
        {
            "enabled": bool(config_regs[21]),
            "interval_ms": config_regs[22] * 10,  # 轉換回 ms
        }
    )

    print("[hold_register_map] Config extracted from memory")
    return result


# =============== 工具函式 ===============
def is_readonly(reg_index: int) -> bool:
    """檢查暫存器是否唯讀。"""
    if reg_index not in REGISTER_DEFS:
        return False
    return REGISTER_DEFS[reg_index].get("readonly", False)


def get_register_name(reg_index: int) -> str:
    """取得暫存器名稱。"""
    if reg_index in REGISTER_DEFS:
        return REGISTER_DEFS[reg_index]["name"]
    return f"REG_{reg_index}"


def get_register_description(reg_index: int) -> str:
    """取得暫存器描述。"""
    if reg_index in REGISTER_DEFS:
        return REGISTER_DEFS[reg_index]["desc"]
    return ""


def validate_write(reg_index: int, value: int) -> tuple:
    """
    驗證對暫存器的寫入。
    回傳 (is_valid, error_msg)
    """
    if reg_index < 0 or reg_index >= 512:
        return False, f"暫存器索引超出範圍: {reg_index}"

    if reg_index not in REGISTER_DEFS:
        # 未定義的暫存器允許寫入（未來擴展用）
        return True, ""

    defn = REGISTER_DEFS[reg_index]

    # 檢查唯讀
    if defn.get("readonly") or defn.get("writeonly") is None and "readonly" in defn:
        return False, f"暫存器唯讀: {defn['name']}"

    # 檢查寫入專用（控制命令）
    if defn.get("writeonly"):
        return True, ""

    # 檢查範圍
    if "min" in defn and value < defn["min"]:
        return False, f"值 {value} 小於最小值 {defn['min']}"
    if "max" in defn and value > defn["max"]:
        return False, f"值 {value} 大於最大值 {defn['max']}"

    return True, ""
