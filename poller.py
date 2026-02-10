# poller.py - 依輪詢表格循環送出 Modbus RTU/ASCII，並回填結果
#
# 功能流程圖（文字版）
# 1) 讀取 config_store.json 的 poller 設定
# 2) 若未啟用或無表格列 → return
# 3) 依 interval 取出當前列 → 組成 PDU → RTU/ASCII 封包送出
# 4) 讀取 RS485 回覆 → 解析/驗證 → 更新 Return
# 5) 更新索引，等待下一輪
#
# 表格欄位說明（poller.rows 每列）
# - ch: 0 或 1，對應 RS485 CH0/CH1
# - station: 1 byte Hex（站號）
# - cmd: 1 byte Hex（功能碼）
# - reg: 2 bytes Hex（起始位址）
# - data: 2 bytes Hex（寫入值；03/04 代表讀取數量）
# - Return: 由系統回填（非儲存欄位）

import time

import Pico_RS485 as rs485
from config_store import get_config
from rs485_lock import acquire as lock_acquire, release as lock_release
from register_store import set_reg, set_regs

# 輪詢狀態
_last_enabled = None  # 記錄上次 enabled 狀態，狀態切換時重置索引
_last_tick = 0  # 上次送出時間
_idx = 0  # 目前輪到的表格列
_results = []  # 每列的 Return 顯示值
_last_comm = {"ch": 0, "tx": "", "rx": "", "rx_len": 0, "err": "", "ts": 0}  # 最近一次 RS485 通訊資訊
_force_enabled = None  # Web start/stop 強制覆蓋設定


def _crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (poly 0xA001)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def _lrc(data: bytes) -> int:
    """Modbus ASCII LRC."""
    lrc = 0
    for b in data:
        lrc = (lrc + b) & 0xFF
    lrc = ((-lrc) & 0xFF)
    return lrc


def _build_rtu_frame(unit_id: int, pdu: bytes) -> bytes:
    """組成 RTU frame: Unit ID + PDU + CRC."""
    base = bytes([unit_id]) + pdu
    crc = _crc16(base)
    return base + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _parse_rtu_frame(frame: bytes):
    """解析單一 RTU frame，驗證 CRC 後回傳 (unit_id, pdu)。"""
    if len(frame) < 5:
        return None, None
    body = frame[:-2]
    crc_rx = frame[-2] | (frame[-1] << 8)
    if _crc16(body) != crc_rx:
        return None, None
    return body[0], body[1:]


def _find_rtu_frame(raw: bytes):
    """在含雜訊/拼接的 raw 中尋找可通過 CRC 的 RTU frame。"""
    n = len(raw)
    if n < 5:
        return None, None
    # Try all windows to find a valid CRC frame.
    for start in range(0, n - 4):
        for end in range(start + 5, n + 1):
            unit, pdu = _parse_rtu_frame(raw[start:end])
            if unit is not None:
                return unit, pdu
    return None, None


def _build_ascii_frame(unit_id: int, pdu: bytes) -> bytes:
    """組成 ASCII frame: :HEX + LRC + CRLF."""
    base = bytes([unit_id]) + pdu
    lrc = _lrc(base)
    hex_txt = "".join("%02X" % b for b in (base + bytes([lrc])))
    return (":" + hex_txt + "\r\n").encode()


def _parse_ascii_frame(frame: bytes):
    """解析 ASCII frame，驗證 LRC 後回傳 (unit_id, pdu)。"""
    try:
        text = frame.decode("ascii", "ignore").strip()
    except Exception:
        return None, None
    if not text.startswith(":"):
        return None, None
    hex_txt = text[1:]
    if len(hex_txt) < 4 or (len(hex_txt) % 2) != 0:
        return None, None
    try:
        raw = bytes(int(hex_txt[i : i + 2], 16) for i in range(0, len(hex_txt), 2))
    except Exception:
        return None, None
    if len(raw) < 3:
        return None, None
    data = raw[:-1]
    lrc = raw[-1]
    if _lrc(data) != lrc:
        return None, None
    return data[0], data[1:]


def _read_rtu_response(ch: int, timeout_ms: int, baudrate: int) -> bytes:
    """依 baudrate 計算閒置間隔，收集 RTU 回覆直到 idle 或 timeout。"""
    buf = bytearray()
    t0 = time.ticks_ms()
    last_rx = None
    char_ms = int(1000 * 11 / max(1, baudrate))
    idle_ms = max(4, int(char_ms * 4))
    while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        chunk = rs485.recv(ch, 256, log=False)
        if chunk:
            buf.extend(chunk)
            last_rx = time.ticks_ms()
        else:
            if buf and last_rx is not None and time.ticks_diff(time.ticks_ms(), last_rx) > idle_ms:
                break
            time.sleep_ms(5)
    return bytes(buf)


def _read_ascii_response(ch: int, timeout_ms: int) -> bytes:
    """讀取直到遇到 '\\n' 或 timeout。"""
    buf = bytearray()
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        chunk = rs485.recv(ch, 256, log=False)
        if chunk:
            buf.extend(chunk)
            if b"\n" in chunk:
                break
        else:
            time.sleep_ms(5)
    return bytes(buf)


def _hex_to_int(val: str) -> int:
    """將 Hex 字串轉成 int，允許 0x 前綴。"""
    val = (val or "").strip()
    if val.lower().startswith("0x"):
        val = val[2:]
    return int(val, 16) if val else 0


def _format_hex_bytes(data: bytes) -> str:
    """輸出 HEX 字串，用於 UI/Log 顯示。"""
    return " ".join("%02X" % b for b in data)


def _parse_return(cmd: int, pdu: bytes):
    """依功能碼解析回覆資料，回傳顯示用字串。"""
    if not pdu:
        return "NO DATA"
    func = pdu[0]
    if func & 0x80:
        if len(pdu) >= 2:
            return "EXC %02X" % pdu[1]
        return "EXC"
    if func in (0x03, 0x04):
        if len(pdu) < 2:
            return "NO DATA"
        count = pdu[1]
        data = pdu[2 : 2 + count]
        return _format_hex_bytes(data)
    # Write responses: return last 2 bytes as value/qty
    if len(pdu) >= 5:
        return _format_hex_bytes(pdu[-2:])
    return _format_hex_bytes(pdu[1:])


def _update_registers(cmd: int, pdu: bytes, row_index: int, row_data: int):
    """將回覆資料寫入本地 0-255 registers（以表格列 index 為基準）。"""
    if not pdu:
        return
    func = pdu[0]
    # Exception 不更新
    if func & 0x80:
        return
    base = row_index
    if func in (0x03, 0x04):
        if len(pdu) < 2:
            return
        count = pdu[1]
        data = pdu[2 : 2 + count]
        regs = []
        for i in range(0, len(data), 2):
            if i + 1 >= len(data):
                break
            regs.append((data[i] << 8) | data[i + 1])
        set_regs(base, regs)
        return
    if func == 0x06 and len(pdu) >= 5:
        val = (pdu[-2] << 8) | pdu[-1]
        set_reg(base, val)
        return
    if func == 0x05 and len(pdu) >= 5:
        val = (pdu[-2] << 8) | pdu[-1]
        set_reg(base, val)
        return
    # 其他功能碼不更新


def _ensure_results_size(n: int):
    """確保 Return 列數與輪詢表格列數一致。"""
    global _results
    if len(_results) != n:
        _results = [""] * n


def tick():
    """每次主迴圈呼叫一次，依 interval 送出一列輪詢資料。"""
    global _last_enabled, _last_tick, _idx
    cfg = get_config()
    poller = cfg.get("poller") or {}
    enabled = bool(poller.get("enabled"))
    # Web start/stop 優先生效
    if _force_enabled is not None:
        enabled = _force_enabled
    if _last_enabled is None:
        _last_enabled = enabled
    if enabled != _last_enabled:
        _last_enabled = enabled
        _last_tick = 0
        _idx = 0

    if not enabled:
        return

    interval_ms = int(poller.get("interval_ms") or 1000)
    # 避免過短造成 UART 忙碌或瀏覽器擠爆
    if interval_ms < 50:
        interval_ms = 50

    rows = poller.get("rows") or []
    # 沒有表格列就直接不動作
    _ensure_results_size(len(rows))
    if not rows:
        return

    now = time.ticks_ms()
    if _last_tick and time.ticks_diff(now, _last_tick) < interval_ms:
        return
    _last_tick = now

    if _idx >= len(rows):
        _idx = 0

    row = rows[_idx]
    _idx += 1

    try:
        ch = int(row.get("ch"))
        station = _hex_to_int(row.get("station"))
        cmd = _hex_to_int(row.get("cmd"))
        reg = _hex_to_int(row.get("reg"))
        data = _hex_to_int(row.get("data"))
    except Exception:
        _results[_idx - 1] = "BAD ROW"
        return

    if not lock_acquire(ch):
        # RS485 半雙工，同通道不可同時收發
        _results[_idx - 1] = "BUSY"
        return

    try:
        modbus_cfg = cfg.get("modbus") or {}
        # 通道未啟用就直接回報
        ch0_enabled = bool(modbus_cfg.get("ch0_enabled", True))
        ch1_enabled = bool(modbus_cfg.get("ch1_enabled", True))
        if (ch == 0 and not ch0_enabled) or (ch == 1 and not ch1_enabled):
            _results[_idx - 1] = "DISABLED"
            return
        ch_cfg = (modbus_cfg.get("ch0") if ch == 0 else modbus_cfg.get("ch1")) or {}
        mode = (ch_cfg.get("mode") or "rtu").lower()
        baudrate = int(ch_cfg.get("baudrate") or 9600)
        rs485.init(
            ch,
            baudrate=baudrate,
            parity=ch_cfg.get("parity") or "N",
            stopbits=int(ch_cfg.get("stopbits") or 1),
            bits=int(ch_cfg.get("bits") or 8),
        )
        pdu = bytes(
            [
                cmd & 0xFF,
                (reg >> 8) & 0xFF,
                reg & 0xFF,
                (data >> 8) & 0xFF,
                data & 0xFF,
            ]
        )
        timeout_ms = int((modbus_cfg.get("response_timeout_ms") or 1200))
        rs485.flush_input(ch)
        # 依 RTU/ASCII 組包送出
        if mode == "ascii":
            frame = _build_ascii_frame(station, pdu)
            rs485.send(ch, frame)
            tx_time_ms = int((len(frame) * 11 * 1000) / max(1, baudrate)) + 2
            time.sleep_ms(tx_time_ms)
            raw = _read_ascii_response(ch, timeout_ms)
            resp_unit, resp_pdu = _parse_ascii_frame(raw)
        else:
            frame = _build_rtu_frame(station, pdu)
            rs485.send(ch, frame)
            tx_time_ms = int((len(frame) * 11 * 1000) / max(1, baudrate)) + 2
            time.sleep_ms(tx_time_ms)
            raw = _read_rtu_response(ch, timeout_ms, baudrate)
            resp_unit, resp_pdu = _parse_rtu_frame(raw)

        _last_comm["ch"] = ch
        _last_comm["tx"] = _format_hex_bytes(frame)
        _last_comm["rx"] = _format_hex_bytes(raw) if raw else ""
        _last_comm["err"] = ""
        _last_comm["rx_len"] = len(raw)
        _last_comm["ts"] = time.ticks_ms()
        if raw:
            print("RS485 CH%d RX:" % ch, _format_hex_bytes(raw))
        else:
            print("RS485 CH%d RX: <empty>" % ch)

        # 回覆解析失敗時，嘗試在 raw 內找可通過 CRC 的 frame
        if resp_unit is None or resp_pdu is None:
            resp_unit, resp_pdu = _find_rtu_frame(raw)
        if resp_unit is None or resp_pdu is None:
            _results[_idx - 1] = "TIMEOUT" if raw == b"" else "BAD CRC"
            return
        if resp_unit != station:
            _results[_idx - 1] = "STA MISMATCH"
            return
        _update_registers(cmd, resp_pdu, _idx - 1, data)
        _results[_idx - 1] = _parse_return(cmd, resp_pdu)
    except Exception as e:
        _last_comm["ch"] = ch if "ch" in locals() else 0
        _last_comm["err"] = str(e)[:40]
        _results[_idx - 1] = "ERR " + str(e)[:20]
    finally:
        lock_release(ch)


def status():
    """提供 Web UI 查詢：狀態、回覆、最近一次通訊。"""
    cfg = get_config()
    poller_cfg = cfg.get("poller") or {}
    return {
        "enabled": bool(_force_enabled if _force_enabled is not None else poller_cfg.get("enabled")),
        "interval_ms": int(poller_cfg.get("interval_ms") or 1000),
        "index": _idx,
        "results": _results,
        "last_comm": _last_comm,
        "row_count": len(poller_cfg.get("rows") or []),
    }


def set_enabled(enabled: bool) -> None:
    """立即啟停輪詢並重置索引與計時。"""
    global _force_enabled, _last_enabled, _last_tick, _idx
    _force_enabled = bool(enabled)
    _last_enabled = _force_enabled
    _last_tick = 0
    _idx = 0
