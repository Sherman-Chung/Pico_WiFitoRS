# register_store.py - local Modbus register map (0-511, Hold Registers)
#
# 維護導讀：
# - 512 個 Hold Register 分為配置、狀態、控制、輪詢資料、保留等區域
# - 編碼轉換透過 hold_register_map 進行（自動編/解碼）
# - 寫入會自動驗證和轉換；對唯讀區域的寫入會被忽略
# - Poller 回填至 100-355；TCP 可讀取/修改 0-69（按照編碼規則）

import hold_register_map as hrm

REG_COUNT = 512
_regs = [0] * REG_COUNT
_regs[65] = 1  # CMD_COMM_LOG 默認開啟，保留現有通訊日誌行為
_write_hooks = {}


def set_write_hook(index: int, callback) -> None:
    """
    註冊單一暫存器寫入 hook。
    callback 介面：callback(index, encoded_value, source)
    """
    if index < 0 or index >= REG_COUNT:
        return
    if callback is None:
        _write_hooks.pop(index, None)
        return
    _write_hooks[index] = callback


def _emit_write_hook(index: int, encoded_value: int, source: str) -> None:
    """若該暫存器有註冊 hook，於成功寫入後觸發。"""
    cb = _write_hooks.get(index)
    if cb is None:
        return
    try:
        cb(index, encoded_value, source)
    except Exception as e:
        print("register write hook error:", index, e)


# =============== 單筆暫存器寫入 ===============
# 說明：
# 寫入單一 register；超出範圍、唯讀或驗證失敗時忽略或回報錯誤。
def set_reg(index: int, value: int, encode: bool = True, source: str = "") -> tuple:
    """
    寫入單一 16-bit register。
    encode=True 時自動編碼（例如波特率 9600 → 2）
    回傳 (success, error_msg)
    """
    if index < 0 or index >= REG_COUNT:
        return False, f"Index {index} out of range [0, {REG_COUNT})"

    # 驗證（唯讀、寫入專用、範圍檢查）
    valid, err = hrm.validate_write(index, value)
    if not valid:
        return False, err

    # 編碼轉換
    if encode:
        encoded_value = hrm.encode_value(index, value)
    else:
        encoded_value = int(value) & 0xFFFF

    _regs[index] = encoded_value
    _emit_write_hook(index, encoded_value, source)
    return True, ""


# =============== 多筆暫存器寫入 ===============
# 說明：
# 從指定起點連續寫入多筆資料；超出範圍或唯讀時忽略該筆。
def set_regs(start: int, values, encode: bool = False, source: str = "") -> tuple:
    """
    從 start 起連續寫入多筆 16-bit register。
    encode=True 時對每個值進行編碼（僅適用於特定場景）
    回傳 (success_count, error_count)
    """
    if values is None:
        return 0, 0
    
    success = 0
    errors = 0
    for i, v in enumerate(values):
        idx = start + i
        if 0 <= idx < REG_COUNT:
            ok, _ = set_reg(idx, v, encode=encode, source=source)
            if ok:
                success += 1
            else:
                errors += 1
        else:
            errors += 1
    
    return success, errors


# =============== 暫存器讀取 ===============
# 說明：
# 讀取連續 count 筆 register，越界位置補 0。
# 自動進行解碼（例如 2 → 9600）
def get_regs(start: int, count: int, decode: bool = False):
    """
    讀取 count 筆 register；越界位置以 0 補齊。
    decode=True 時自動解碼（例如波特率的編碼 2 → 9600）
    """
    if count <= 0:
        return []
    
    out = []
    for i in range(count):
        idx = start + i
        if 0 <= idx < REG_COUNT:
            raw_value = _regs[idx]
            if decode:
                decoded_value = hrm.decode_value(idx, raw_value)
            else:
                decoded_value = raw_value
            out.append(decoded_value)
        else:
            out.append(0)
    return out


# =============== 批量寫入暫存器（無驗證，內部用） ===============
# 說明：
# 供 Poller 等模組直接回填資料，跳過驗證以提升效率。
def set_regs_raw(start: int, values) -> None:
    """
    從 start 起連續寫入多筆，不進行驗證或編碼。
    僅供輪詢結果回填等內部使用。
    """
    if values is None:
        return
    for i, v in enumerate(values):
        idx = start + i
        if 0 <= idx < REG_COUNT:
            _regs[idx] = int(v) & 0xFFFF


# =============== 初始化記憶體（從配置） ===============
# 說明：
# 啟動時從 config_store 讀取設定並填入記憶體。
def initialize_from_config(config: dict) -> None:
    """
    啟動時調用一次，從 config_store 設定初始化記憶體區域。
    """
    def _write_fn(start, values):
        set_regs_raw(start, values)
    
    hrm.update_memory_from_config(config, _write_fn)


# =============== 保存配置（到 Flash） ===============
# 說明：
# 保存命令觸發時調用，從記憶體提取配置。
def export_config_from_memory(current_config: dict) -> dict:
    """
    從記憶體提取配置並回傳修改的設定 dict。
    通常由 config_store 在保存時調用。
    """
    def _read_fn(start, count):
        return get_regs(start, count, decode=False)
    
    return hrm.prune_config_from_memory(_read_fn, current_config)


# =============== 狀態更新（系統內部使用） ===============
# 說明：
# 定期更新系統狀態暫存器。
def update_status(state_dict: dict) -> None:
    """
    更新狀態區域 (50-56) 的值。
    state_dict 應包含對應鍵值，例如 {'run_time': 3600, 'cpu_temp': 45, ...}
    """
    # 此函式由 main.py 或其他系統模組定期呼叫
    # 例如：update_status({'run_time': time.ticks_ms() // 1000, 'cpu_temp': 42})
    
    # 狀態映射（暫存器索引 → 狀態鍵）
    status_map = {
        50: 'run_time',
        51: 'cpu_temp',
        52: 'batt_v',
        53: 'batt_i',
        54: 'batt_p',
        55: 'sta_connected',
        56: 'ap_active',
    }
    
    for reg_idx, key in status_map.items():
        if key in state_dict:
            value = state_dict[key]
            _regs[reg_idx] = int(value) & 0xFFFF


# =============== 控制命令處理 ===============
# 說明：
# 檢查控制區域 (60-64) 是否有待處理命令。
def check_and_clear_command() -> dict:
    """
    檢查控制區域是否有命令，回傳 {'cmd': 'save'|'reset'|'reboot'|'apply'|None, 'status': 0}
    並自動清除命令位。
    """
    cmd_save = _regs[60]
    cmd_reset = _regs[61]
    cmd_reboot = _regs[62]
    cmd_apply = _regs[64]
    
    result = {'cmd': None, 'status': 0}  # 0 = idle
    
    if cmd_save:
        result['cmd'] = 'save'
        _regs[60] = 0
        _regs[63] = 1  # status = busy
    elif cmd_reset:
        result['cmd'] = 'reset'
        _regs[61] = 0
        _regs[63] = 1  # status = busy
    elif cmd_reboot:
        result['cmd'] = 'reboot'
        _regs[62] = 0
        _regs[63] = 1  # status = busy
    elif cmd_apply:
        result['cmd'] = 'apply'
        _regs[64] = 0
        _regs[63] = 1  # status = busy

    return result


def set_command_status(status: int) -> None:
    """
    更新命令狀態 (63)。
    0 = idle, 1 = busy, 2 = success, 3 = error
    """
    _regs[63] = int(status) & 0xFFFF
