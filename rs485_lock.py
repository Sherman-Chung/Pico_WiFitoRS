"""rs485_lock.py - shared RS485 channel lock

跨模組/跨核心共用 RS485 通道鎖。
"""

try:
    import _thread
except Exception:  # pragma: no cover - fallback for environments without _thread
    _thread = None


if _thread is not None:
    _locks = {0: _thread.allocate_lock(), 1: _thread.allocate_lock()}
else:
    _locks = None
    _busy = {0: False, 1: False}


# =============== 通道鎖定 ===============
# 說明：
# 嘗試鎖定指定通道；若通道不存在或已被占用則失敗。
def acquire(ch: int) -> bool:
    """嘗試鎖定通道；成功回 True。"""
    if _locks is None:
        if ch not in _busy:
            return False
        if _busy[ch]:
            return False
        _busy[ch] = True
        return True
    lock = _locks.get(ch)
    if lock is None:
        return False
    try:
        return bool(lock.acquire(False))
    except TypeError:
        # 某些 MicroPython 版本 waitflag 需用 0/1
        return bool(lock.acquire(0))
    except Exception:
        return False


# =============== 通道解鎖 ===============
# 說明：
# 釋放指定通道鎖，供下一筆收發使用。
def release(ch: int) -> None:
    """釋放通道鎖。"""
    if _locks is None:
        if ch in _busy:
            _busy[ch] = False
        return
    lock = _locks.get(ch)
    if lock is None:
        return
    try:
        lock.release()
    except Exception:
        pass


# =============== 通道忙碌查詢 ===============
# 說明：
# 查詢指定通道目前是否已被鎖定。
def is_busy(ch: int) -> bool:
    """查詢通道是否已被鎖定。"""
    if _locks is None:
        return bool(_busy.get(ch))
    lock = _locks.get(ch)
    if lock is None:
        return False
    got = False
    try:
        try:
            got = bool(lock.acquire(False))
        except TypeError:
            got = bool(lock.acquire(0))
        if got:
            lock.release()
    except Exception:
        return True
    return not got
