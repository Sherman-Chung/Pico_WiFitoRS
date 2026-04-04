# config_store.py - persistent settings for AP/Modbus gateway
#
# 維護導讀：
# - 此檔負責「設定檔 schema + 驗證 + 持久化」。
# - 外部模組請只呼叫 get_config()/update_config()/reset_config()。
# - _sanitize_* 系列是欄位合法性邊界，改 schema 時要同步更新。

try:
    import ujson as json
except ImportError:
    import json

_CFG_PATH = "config_store.json"


# =============== 預設設定建立 ===============
# 說明：
# 定義系統完整預設值，作為設定檔缺漏欄位時的回填基準。
def _default_config():
    """專案完整預設設定（作為 schema baseline）。"""
    return {
        "ap": {"ssid": "PicoSetup", "password": "pico1234"},
        "sta": {"ssid": "", "password": ""},
        "poller": {"enabled": False, "interval_ms": 1000, "rows": []},
        "modbus": {
            "tcp_port": 502,
            "response_timeout_ms": 1200,
            "tcp_slave_id": 1,
            "unit_map_ch0": "1-127",
            "unit_map_ch1": "",
            "ch0_enabled": False,
            "ch1_enabled": False,
            "ch0": {"mode": "rtu", "baudrate": 9600, "parity": "N", "stopbits": 1, "bits": 8},
            "ch1": {"mode": "rtu", "baudrate": 9600, "parity": "N", "stopbits": 1, "bits": 8},
        },
    }


# =============== 設定遞迴合併 ===============
# 說明：
# 將來源設定遞迴覆蓋到目標設定，保留未覆蓋的既有欄位。
def _merge(dst, src):
    """遞迴合併 dict；src 覆蓋 dst。"""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


# =============== 設定檔載入 ===============
# 說明：
# 從磁碟讀取設定檔，若不存在或格式錯誤則回退到預設設定。
def load_config():
    """從檔案讀設定，讀不到時回預設值。"""
    cfg = _default_config()
    try:
        with open(_CFG_PATH, "r") as f:
            data = json.load(f)
        _merge(cfg, data)
    except Exception:
        pass
    return cfg


_cache = None


# =============== 取得設定快取 ===============
# 說明：
# 提供外部模組統一讀取入口；首次呼叫才會觸發實際讀檔。
def get_config():
    """取得快取設定；首次呼叫才實際讀檔。"""
    global _cache
    if _cache is None:
        _cache = load_config()
    return _cache


# =============== Gateway Response Timeout 讀取 ===============
# 說明：
# 統一由 modbus.response_timeout_ms 讀取通訊等待時間，供 Gateway/Poller/RS HEX 共用。
def get_response_timeout_ms(cfg=None) -> int:
    """取得 Gateway response timeout（毫秒）。"""
    if cfg is None:
        cfg = get_config()
    modbus = (cfg or {}).get("modbus") or {}
    try:
        timeout_ms = int(modbus.get("response_timeout_ms") or 1200)
    except Exception:
        timeout_ms = 1200
    if timeout_ms < 1:
        timeout_ms = 1
    return timeout_ms


# =============== AP 設定驗證 ===============
# 說明：
# 驗證 AP SSID/密碼欄位是否合法。
def _sanitize_ap(ap):
    """驗證 AP 設定。"""
    ssid = (ap.get("ssid") or "").strip()
    password = (ap.get("password") or "").strip()
    if not ssid:
        return None, "ssid required"
    if password and len(password) < 8:
        return None, "password must be >= 8 chars or empty"
    return {"ssid": ssid, "password": password}, None


# =============== STA 設定驗證 ===============
# 說明：
# 驗證 STA SSID/密碼欄位是否合法。
def _sanitize_sta(sta):
    """驗證 STA 設定。"""
    ssid = (sta.get("ssid") or "").strip()
    password = (sta.get("password") or "").strip()
    if ssid and password and len(password) < 8:
        return None, "password must be >= 8 chars or empty"
    return {"ssid": ssid, "password": password}, None


# =============== 單通道通訊參數驗證 ===============
# 說明：
# 驗證 CH0/CH1 的 mode/baudrate/parity/stopbits/bits 參數。
def _sanitize_ch(ch):
    """驗證單一 RS485 通道設定。"""
    out = {}
    mode = (ch.get("mode") or "rtu").strip().lower()
    if mode not in ("rtu", "ascii"):
        return None, "mode must be rtu or ascii"
    out["mode"] = mode
    try:
        out["baudrate"] = int(ch.get("baudrate") or 9600)
    except Exception:
        return None, "baudrate invalid"
    parity = (ch.get("parity") or "N").strip().upper()
    if parity not in ("N", "E", "O"):
        return None, "parity must be N/E/O"
    out["parity"] = parity
    try:
        out["stopbits"] = int(ch.get("stopbits") or 1)
    except Exception:
        return None, "stopbits invalid"
    if out["stopbits"] not in (1, 2):
        return None, "stopbits must be 1 or 2"
    try:
        out["bits"] = int(ch.get("bits") or 8)
    except Exception:
        return None, "bits invalid"
    if out["bits"] not in (7, 8):
        return None, "bits must be 7 or 8"
    return out, None


# =============== Modbus 設定驗證 ===============
# 說明：
# 驗證整體 Modbus 區塊，包含 TCP 參數、Unit 映射與兩通道設定。
def _sanitize_modbus(modbus):
    """驗證整體 Modbus 設定。"""
    out = {}
    try:
        out["tcp_port"] = int(modbus.get("tcp_port") or 502)
    except Exception:
        return None, "tcp_port invalid"
    try:
        out["response_timeout_ms"] = int(modbus.get("response_timeout_ms") or 1200)
    except Exception:
        return None, "response_timeout_ms invalid"
    try:
        out["tcp_slave_id"] = int(modbus.get("tcp_slave_id") or 1)
    except Exception:
        return None, "tcp_slave_id invalid"
    if not (1 <= out["tcp_slave_id"] <= 247):
        return None, "tcp_slave_id range 1-247"
    out["unit_map_ch0"] = (modbus.get("unit_map_ch0") or "1-127").strip()
    out["unit_map_ch1"] = (modbus.get("unit_map_ch1") or "").strip()
    out["ch0_enabled"] = _parse_bool(modbus.get("ch0_enabled", True))
    out["ch1_enabled"] = _parse_bool(modbus.get("ch1_enabled", True))
    ch0, err = _sanitize_ch(modbus.get("ch0") or {})
    if err:
        return None, "ch0: " + err
    ch1, err = _sanitize_ch(modbus.get("ch1") or {})
    if err:
        return None, "ch1: " + err
    out["ch0"] = ch0
    out["ch1"] = ch1
    return out, None


# =============== 布林值標準化 ===============
# 說明：
# 將 API 傳入值（bool/str/int）統一轉成布林值。
def _parse_bool(val):
    """將 API 輸入常見型別轉成 bool。"""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    if val is None:
        return False
    try:
        return bool(int(val))
    except Exception:
        return bool(val)


# =============== 設定更新與持久化 ===============
# 說明：
# 套用 patch、執行欄位驗證、寫回檔案並更新快取。
def update_config(patch):
    """
    套用 patch 並保存設定。

    回傳：(ok, error, cfg)
    - ok=True 代表已寫入並更新快取
    - ok=False 時 cfg 為舊設定
    """
    global _cache
    cfg = get_config()
    # 先從磁碟重新讀一次，避免覆蓋其他來源剛寫入的值
    new_cfg = load_config()
    _merge(new_cfg, cfg)
    _merge(new_cfg, patch or {})

    if "ap" in patch:
        ap, err = _sanitize_ap(new_cfg.get("ap") or {})
        if err:
            return False, err, cfg
        new_cfg["ap"] = ap

    if "sta" in patch:
        sta, err = _sanitize_sta(new_cfg.get("sta") or {})
        if err:
            return False, err, cfg
        new_cfg["sta"] = sta

    if "modbus" in patch:
        modbus, err = _sanitize_modbus(new_cfg.get("modbus") or {})
        if err:
            return False, err, cfg
        new_cfg["modbus"] = modbus

    if "poller" in patch:
        poller, err = _sanitize_poller(new_cfg.get("poller") or {})
        if err:
            return False, err, cfg
        new_cfg["poller"] = poller

    try:
        with open(_CFG_PATH, "w") as f:
            json.dump(new_cfg, f)
        _cache = new_cfg
        return True, None, new_cfg
    except Exception as e:
        return False, str(e), cfg


# =============== 設定重置 ===============
# 說明：
# 刪除設定檔並回復預設設定內容。
def reset_config():
    """刪除設定檔並回到預設設定。"""
    global _cache
    try:
        import os

        os.remove(_CFG_PATH)
    except Exception:
        pass
    _cache = _default_config()
    return _cache


# =============== 輪詢設定驗證 ===============
# 說明：
# 驗證 poller 參數與每列格式，並限制最大列數與最小間隔。
def _sanitize_poller(poller):
    """驗證 Poller 設定與每列欄位格式。"""
    out = {}
    out["enabled"] = _parse_bool(poller.get("enabled", False))
    try:
        out["interval_ms"] = int(poller.get("interval_ms") or 1000)
    except Exception:
        return None, "poller interval invalid"
    if out["interval_ms"] < 50:
        out["interval_ms"] = 50
    rows = poller.get("rows") or []
    if not isinstance(rows, list):
        return None, "poller rows invalid"
    norm_rows = []
    for row in rows[:256]:
        if not isinstance(row, dict):
            continue
        ch = int(row.get("ch") or 0)
        if ch not in (0, 1):
            continue
        station, err = _norm_hex(row.get("station"), 2)
        if err:
            return None, "row station invalid"
        cmd, err = _norm_hex(row.get("cmd"), 2)
        if err:
            return None, "row cmd invalid"
        reg, err = _norm_hex(row.get("reg"), 4)
        if err:
            return None, "row reg invalid"
        data, err = _norm_hex(row.get("data"), 4)
        if err:
            return None, "row data invalid"
        norm_rows.append(
            {"ch": ch, "station": station, "cmd": cmd, "reg": reg, "data": data}
        )
    out["rows"] = norm_rows
    return out, None


# =============== Hex 欄位正規化 ===============
# 說明：
# 將 hex 字串轉為固定寬度大寫格式，供設定檔保存。
def _norm_hex(val, width):
    """把 hex 字串正規化成固定寬度大寫字串。"""
    s = (str(val) if val is not None else "").strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    if not s:
        s = "0"
    for ch in s:
        if ch not in "0123456789abcdefABCDEF":
            return None, "hex"
    num = int(s, 16)
    if num < 0 or num > (2 ** (width * 4) - 1):
        return None, "range"
    return ("%0" + str(width) + "X") % num, None
