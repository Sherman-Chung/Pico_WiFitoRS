# log_buffer.py - 全局日誌緩衝
# 供 modbus_gateway、Pico_RS485 等模組寫入 TCP/RS485 日誌

import time

_log_lines = []
_MAX_LOG_LINES = 200


def append_log(msg: str):
    """將訊息追加到日誌緩衝，並自動截斷超過上限。"""
    global _log_lines
    ts = time.localtime()
    ts_str = "%02d:%02d:%02d" % (ts[3], ts[4], ts[5])
    full_msg = "[%s] %s" % (ts_str, msg)
    _log_lines.append(full_msg)
    if len(_log_lines) > _MAX_LOG_LINES:
        _log_lines.pop(0)


def get_logs(count: int = None) -> list:
    """取得最近 N 筆日誌，或全部日誌。"""
    if count is None or count <= 0:
        return list(_log_lines)
    return list(_log_lines[-count:])


def clear_logs():
    """清空所有日誌。"""
    global _log_lines
    _log_lines = []


def get_logs_as_string(count: int = None) -> str:
    """將日誌格式化為字串，用於 Web UI 顯示。"""
    logs = get_logs(count)
    return "\n".join(logs)
