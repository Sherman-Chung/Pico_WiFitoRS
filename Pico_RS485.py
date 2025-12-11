# Pico_RS485.py - 簡易封裝 Pico-2CH-RS485 的 UART 介面
# 兩組通道：CH0 使用 UART0 (GP0/GP1)，CH1 使用 UART1 (GP4/GP5)
# 預設 115200-N-8-1；如需調整可呼叫 init(baudrate=...)

from machine import UART, Pin

UART_PINS = {
    0: {"tx": Pin(0), "rx": Pin(1)},
    1: {"tx": Pin(4), "rx": Pin(5)},
}

_uart_cache = {}


def init(ch: int = 0, baudrate: int = 115200):
    """初始化指定通道，重複呼叫會覆寫 baudrate。"""
    if ch not in UART_PINS:
        raise ValueError("channel must be 0 or 1")
    cfg = UART_PINS[ch]
    uart = UART(ch, baudrate=baudrate, tx=cfg["tx"], rx=cfg["rx"])
    # 快取 UART 實例，避免每次收發都重新初始化硬體
    _uart_cache[ch] = uart
    return uart


def _get_uart(ch: int):
    if ch in _uart_cache:
        return _uart_cache[ch]
    return init(ch)


def send(ch: int, data: bytes | str):
    """送出資料（bytes 或 str）。回傳送出位元組數。"""
    uart = _get_uart(ch)
    if isinstance(data, str):
        data = data.encode()
    return uart.write(data)


def recv(ch: int, max_bytes: int = 256) -> bytes:
    """非阻塞讀取通道資料，回傳 bytes（可能為空）。"""
    uart = _get_uart(ch)
    n = uart.any()
    if not n:
        return b""
    n = min(n, max_bytes)
    return uart.read(n) or b""


def flush_input(ch: int):
    """讀掉目前輸入緩衝。"""
    uart = _get_uart(ch)
    while uart.any():
        uart.read()
