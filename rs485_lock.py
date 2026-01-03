# rs485_lock.py - shared RS485 channel lock

_busy = {0: False, 1: False}


def acquire(ch: int) -> bool:
    if ch not in _busy:
        return False
    if _busy[ch]:
        return False
    _busy[ch] = True
    return True


def release(ch: int) -> None:
    if ch in _busy:
        _busy[ch] = False


def is_busy(ch: int) -> bool:
    return bool(_busy.get(ch))
