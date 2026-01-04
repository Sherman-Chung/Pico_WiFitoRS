# config_store.py - persistent settings for AP/Modbus gateway

try:
    import ujson as json
except ImportError:
    import json

_CFG_PATH = "config_store.json"


def _default_config():
    return {
        "ap": {"ssid": "PicoSetup", "password": "pico1234"},
        "sta": {"ssid": "", "password": ""},
        "poller": {"enabled": False, "interval_ms": 1000, "rows": []},
        "modbus": {
            "tcp_port": 502,
            "response_timeout_ms": 1200,
            "unit_map_ch0": "1-127",
            "unit_map_ch1": "",
            "ch0_enabled": False,
            "ch1_enabled": False,
            "ch0": {"mode": "rtu", "baudrate": 9600, "parity": "N", "stopbits": 1, "bits": 8},
            "ch1": {"mode": "rtu", "baudrate": 9600, "parity": "N", "stopbits": 1, "bits": 8},
        },
    }


def _merge(dst, src):
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def load_config():
    cfg = _default_config()
    try:
        with open(_CFG_PATH, "r") as f:
            data = json.load(f)
        _merge(cfg, data)
    except Exception:
        pass
    return cfg


_cache = None


def get_config():
    global _cache
    if _cache is None:
        _cache = load_config()
    return _cache


def _sanitize_ap(ap):
    ssid = (ap.get("ssid") or "").strip()
    password = (ap.get("password") or "").strip()
    if not ssid:
        return None, "ssid required"
    if password and len(password) < 8:
        return None, "password must be >= 8 chars or empty"
    return {"ssid": ssid, "password": password}, None


def _sanitize_sta(sta):
    ssid = (sta.get("ssid") or "").strip()
    password = (sta.get("password") or "").strip()
    if ssid and password and len(password) < 8:
        return None, "password must be >= 8 chars or empty"
    return {"ssid": ssid, "password": password}, None


def _sanitize_ch(ch):
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


def _sanitize_modbus(modbus):
    out = {}
    try:
        out["tcp_port"] = int(modbus.get("tcp_port") or 502)
    except Exception:
        return None, "tcp_port invalid"
    try:
        out["response_timeout_ms"] = int(modbus.get("response_timeout_ms") or 1200)
    except Exception:
        return None, "response_timeout_ms invalid"
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


def _parse_bool(val):
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


def update_config(patch):
    global _cache
    cfg = get_config()
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


def reset_config():
    global _cache
    try:
        import os

        os.remove(_CFG_PATH)
    except Exception:
        pass
    _cache = _default_config()
    return _cache


def _sanitize_poller(poller):
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
    for row in rows:
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


def _norm_hex(val, width):
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
