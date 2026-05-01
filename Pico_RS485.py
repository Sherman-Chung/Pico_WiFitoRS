# Pico_RS485.py - 簡易封裝 Pico-2CH-RS485 的 UART 介面
# 兩組通道：CH0 使用 UART0 (GP0/GP1)，CH1 使用 UART1 (GP4/GP5)
# 預設 9600-N-8-1；如需調整可呼叫 init(baudrate=...)
#
# 維護導讀：
# - 此模組只做 UART 收發封裝，不做 Modbus 協議解析。
# - 若改硬體腳位，修改 UART_PINS 即可。

try:
    from typing import Union
except ImportError:  # MicroPython may not include typing
    Union = object

import time
from machine import UART, Pin

try:
    import log_buffer
except ImportError:
    log_buffer = None

UART_PINS = {
    0: {"tx": Pin(0), "rx": Pin(1)},
    1: {"tx": Pin(4), "rx": Pin(5)},
}

_uart_cache = {}
_rx_log_state = {
    0: {"buf": bytearray(), "last": None},
    1: {"buf": bytearray(), "last": None},
}
_RX_LOG_FLUSH_MS = 8

# =============== RS485 LOG 控制 ===============
# 說明：
# 全局變數控制 RS485 TX/RX 是否印出 LOG。設為 False 可關閉所有 RS485 LOG。
LOG_RS485 = False


# =============== RS485 通道初始化 ===============
# 說明：
# 依通道與通訊參數建立 UART 實例，並更新快取。
def init(
    ch: int = 0,
    baudrate: int = 9600,
    parity: str = "N",
    stopbits: int = 1,
    bits: int = 8,
):
    """初始化指定通道，重複呼叫會覆寫 UART 參數。"""
    if ch not in UART_PINS:
        raise ValueError("channel must be 0 or 1")
    cfg = UART_PINS[ch]
    parity = (parity or "N").upper()
    if parity == "E":
        par = 0
    elif parity == "O":
        par = 1
    else:
        par = None
    uart = UART(
        ch,
        baudrate=baudrate,
        tx=cfg["tx"],
        rx=cfg["rx"],
        parity=par,
        stop=stopbits,
        bits=bits,
    )
    # 快取 UART 實例，避免每次收發都重新初始化硬體
    _uart_cache[ch] = uart
    return uart


# =============== 取得 UART 實例 ===============
# 說明：
# 優先回傳快取中的 UART；若不存在則自動初始化。
def _get_uart(ch: int):
    if ch in _uart_cache:
        return _uart_cache[ch]
    return init(ch)


# =============== RS485 資料送出 ===============
# 說明：
# 送出 bytes/str 資料到指定通道，並輸出 TX HEX 供除錯。
def send(ch: int, data, log: bool = None):
    """送出資料（bytes 或 str）。回傳送出位元組數。"""
    uart = _get_uart(ch)
    if isinstance(data, str):
        data = data.encode()
    if log is None:
        log = LOG_RS485
    if data and log:
        hex_txt = " ".join("%02X" % b for b in data)
        msg = "RS485 CH%d TX: %s" % (ch, hex_txt)
        print(msg)
        if log_buffer:
            log_buffer.append_log(msg)
    return uart.write(data)


# =============== RS485 資料接收 ===============
# 說明：
# 非阻塞讀取指定通道可用資料；若無資料回空 bytes。
def _flush_rx_log(ch: int):
    state = _rx_log_state.get(ch)
    if not state:
        return
    if not state["buf"]:
        return
    hex_txt = " ".join("%02X" % b for b in state["buf"])
    msg = "RS485 CH%d RX: %s" % (ch, hex_txt)
    print(msg)
    if log_buffer:
        log_buffer.append_log(msg)
    state["buf"] = bytearray()
    state["last"] = None


def recv(ch: int, max_bytes: int = 256, log: bool = None):
    """非阻塞讀取通道資料，回傳 bytes（可能為空）。"""
    uart = _get_uart(ch)
    n = uart.any()
    if not n:
        state = _rx_log_state.get(ch)
        if state and state["buf"] and state["last"] is not None:
            if time.ticks_diff(time.ticks_ms(), state["last"]) > _RX_LOG_FLUSH_MS:
                _flush_rx_log(ch)
        return b""
    n = min(n, max_bytes)
    data = uart.read(n) or b""
    if log is None:
        log = LOG_RS485
    state = _rx_log_state.get(ch)
    if data and log and state is not None:
        now = time.ticks_ms()
        if state["buf"] and state["last"] is not None and time.ticks_diff(now, state["last"]) > _RX_LOG_FLUSH_MS:
            _flush_rx_log(ch)
        state["buf"].extend(data)
        state["last"] = now
    return data


# =============== RS485 輸入清空 ===============
# 說明：
# 讀掉 UART 輸入緩衝區殘留資料，避免舊封包干擾。
def flush_input(ch: int):
    """讀掉目前輸入緩衝。"""
    uart = _get_uart(ch)
    while uart.any():
        uart.read()
