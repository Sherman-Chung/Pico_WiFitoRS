# runtime_log.py - volatile in-RAM log bridge for Web UI

_MAX_LINES = 80
_MAX_LEN = 160
_DEFAULT_LIMIT = 12
_MAX_LIMIT = 40

_next_id = 1
_lines = []


def _last_id():
    return _next_id - 1


def log(*parts):
    """Print a line and keep a short RAM-only copy for the Web UI."""
    global _next_id
    try:
        text = " ".join(str(p) for p in parts)
    except Exception:
        text = "<log format error>"
    if len(text) > _MAX_LEN:
        text = text[: _MAX_LEN - 3] + "..."
    print(text)
    _lines.append((_next_id, text))
    _next_id += 1
    if len(_lines) > _MAX_LINES:
        del _lines[0 : len(_lines) - _MAX_LINES]


def get_since(since=None, limit=_DEFAULT_LIMIT):
    """Return log entries newer than the last seen id."""
    if since is None:
        return {"next": _last_id(), "count": len(_lines), "lines": []}
    try:
        since = int(since)
    except Exception:
        since = 0
    try:
        limit = int(limit)
    except Exception:
        limit = _DEFAULT_LIMIT
    if limit < 1:
        limit = _DEFAULT_LIMIT
    if limit > _MAX_LIMIT:
        limit = _MAX_LIMIT

    start = 0
    for idx, (ident, _text) in enumerate(_lines):
        if ident > since:
            start = idx
            break
    else:
        return {"next": _last_id(), "count": len(_lines), "lines": []}

    rows = _lines[start:]
    if len(rows) > limit:
        rows = rows[-limit:]
    out = []
    for ident, text in rows:
        out.append({"id": ident, "text": text})
    return {"next": _last_id(), "count": len(_lines), "lines": out}


def clear():
    """Clear the RAM copy only. Serial print output is unaffected."""
    global _lines
    _lines = []
    return {"next": _last_id(), "count": 0, "lines": []}
