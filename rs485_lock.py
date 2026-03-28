# rs485_lock.py - shared RS485 channel lock
#
# 維護導讀：
# - 這是輕量「協作鎖」，用來避免同通道並發收發。
# - 本專案為單執行緒主迴圈，故以簡單 bool 狀態實作即可。

_busy = {0: False, 1: False}


# =============== 通道鎖定 ===============
# 說明：
# 嘗試鎖定指定通道；若通道不存在或已被占用則失敗。
def acquire(ch: int) -> bool:
    """嘗試鎖定通道；成功回 True。"""
    if ch not in _busy:
        return False
    if _busy[ch]:
        return False
    _busy[ch] = True
    return True


# =============== 通道解鎖 ===============
# 說明：
# 釋放指定通道鎖，供下一筆收發使用。
def release(ch: int) -> None:
    """釋放通道鎖。"""
    if ch in _busy:
        _busy[ch] = False


# =============== 通道忙碌查詢 ===============
# 說明：
# 查詢指定通道目前是否已被鎖定。
def is_busy(ch: int) -> bool:
    """查詢通道是否已被鎖定。"""
    return bool(_busy.get(ch))
