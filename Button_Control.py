# Button_Control.py - 按鍵定義與消抖工具集中管理
# 提供按鍵腳位、彈跳處理與長按偵測，主程式僅需匯入使用。

import time
from machine import Pin

DEBOUNCE_MS = 160
KEYHOLD_MS = 600

# 按鍵腳位（X/Y 取代左右）
keyA = Pin(15, Pin.IN, Pin.PULL_UP)  # Home: Scan；Connect: DEL
keyB = Pin(17, Pin.IN, Pin.PULL_UP)  # Home: Status；Connect: CLR
keyX = Pin(19, Pin.IN, Pin.PULL_UP)  # Back
keyY = Pin(21, Pin.IN, Pin.PULL_UP)  # List / Connect: Enter
keyUP = Pin(2, Pin.IN, Pin.PULL_UP)  # Move Up
keyDN = Pin(18, Pin.IN, Pin.PULL_UP)  # Move Down
keyLEFT = Pin(16, Pin.IN, Pin.PULL_UP)  # Move Left
keyRIGHT = Pin(20, Pin.IN, Pin.PULL_UP)  # Move Right
keyCTRL = Pin(3, Pin.IN, Pin.PULL_UP)  # Connect: Hold 600ms is OK

_last = 0


def pressed(p: Pin) -> bool:
    """判斷按鍵是否被按下（低電位觸發）。"""
    return p.value() == 0


def wait_release(p: Pin, timeout_ms: int = KEYHOLD_MS) -> None:
    """等待按鍵放開，避免誤觸。"""
    t0 = time.ticks_ms()
    while pressed(p) and time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        time.sleep_ms(5)


def debounce() -> bool:
    """簡易消抖：距離上次按鍵若未滿 DEBOUNCE_MS 則忽略。"""
    global _last
    now = time.ticks_ms()
    if time.ticks_diff(now, _last) < DEBOUNCE_MS:
        return False
    _last = now
    return True


def key_hold() -> bool:
    """長按檢查：距離上次紀錄滿 KEYHOLD_MS 才回 True。"""
    global _last
    now = time.ticks_ms()
    if time.ticks_diff(now, _last) < KEYHOLD_MS:
        return False
    _last = now
    return True


# 方便主程式集中管理的按鍵列表
KEYS = {
    "A": keyA,
    "B": keyB,
    "X": keyX,
    "Y": keyY,
    "UP": keyUP,
    "DN": keyDN,
    "LEFT": keyLEFT,
    "RIGHT": keyRIGHT,
    "CTRL": keyCTRL,
}

