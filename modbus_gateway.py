# modbus_gateway.py - Modbus TCP <-> Modbus RTU/ASCII gateway

import socket
import time

import Pico_RS485 as rs485
from config_store import get_config
from rs485_lock import acquire as lock_acquire, release as lock_release

server_sock = None


def _crc16(data: bytes) -> int:
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
    lrc = 0
    for b in data:
        lrc = (lrc + b) & 0xFF
    lrc = ((-lrc) & 0xFF)
    return lrc


def _parse_unit_map(expr: str):
    ids = set()
    if not expr:
        return ids
    parts = expr.replace(";", ",").split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a = int(a.strip())
                b = int(b.strip())
            except Exception:
                continue
            if a > b:
                a, b = b, a
            for v in range(a, b + 1):
                if 0 <= v <= 247:
                    ids.add(v)
        else:
            try:
                v = int(part)
            except Exception:
                continue
            if 0 <= v <= 247:
                ids.add(v)
    return ids


def _unit_id_to_channel(unit_id: int, cfg) -> int:
    modbus_cfg = cfg.get("modbus") or {}
    ch0_expr = (modbus_cfg.get("unit_map_ch0") or "").strip()
    ch1_expr = (modbus_cfg.get("unit_map_ch1") or "").strip()
    ch0_set = _parse_unit_map(ch0_expr)
    ch1_set = _parse_unit_map(ch1_expr)
    ch0_enabled = bool(modbus_cfg.get("ch0_enabled", True))
    ch1_enabled = bool(modbus_cfg.get("ch1_enabled", True))
    if ch0_enabled and unit_id in ch0_set:
        return 0
    if ch1_enabled and unit_id in ch1_set:
        return 1
    return None


def _build_rtu_frame(unit_id: int, pdu: bytes) -> bytes:
    base = bytes([unit_id]) + pdu
    crc = _crc16(base)
    return base + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _parse_rtu_frame(frame: bytes):
    if len(frame) < 5:
        return None, None
    body = frame[:-2]
    crc_rx = frame[-2] | (frame[-1] << 8)
    if _crc16(body) != crc_rx:
        return None, None
    return body[0], body[1:]


def _build_ascii_frame(unit_id: int, pdu: bytes) -> bytes:
    base = bytes([unit_id]) + pdu
    lrc = _lrc(base)
    hex_txt = "".join("%02X" % b for b in (base + bytes([lrc])))
    return (":" + hex_txt + "\r\n").encode()


def _parse_ascii_frame(frame: bytes):
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


def _make_exception_pdu(req_pdu: bytes, code: int) -> bytes:
    func = req_pdu[0] if req_pdu else 0
    return bytes([func | 0x80, code])


def start_modbus_tcp_server(port: int = 502):
    global server_sock
    if server_sock is not None:
        return
    addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(2)
    s.settimeout(0.0)
    server_sock = s
    print("Modbus TCP server listening on", addr)


def _read_exact(sock, size: int, timeout_ms: int = 1000) -> bytes:
    data = b""
    t0 = time.ticks_ms()
    while len(data) < size and time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        try:
            chunk = sock.recv(size - len(data))
        except OSError:
            return data
        if not chunk:
            break
        data += chunk
    return data


def poll_modbus_tcp_server():
    global server_sock
    if server_sock is None:
        return
    try:
        cl, addr = server_sock.accept()
    except OSError:
        return

    try:
        cl.settimeout(3)
        head = _read_exact(cl, 7, timeout_ms=800)
        if len(head) < 7:
            return
        tid = head[0:2]
        pid = head[2:4]
        length = (head[4] << 8) | head[5]
        unit_id = head[6]
        if pid != b"\x00\x00":
            return
        body = _read_exact(cl, max(0, length - 1), timeout_ms=800)
        if len(body) < max(0, length - 1):
            return
        pdu = body

        cfg = get_config()
        modbus_cfg = cfg.get("modbus") or {}
        timeout_ms = int(modbus_cfg.get("response_timeout_ms") or 1200)
        ch = _unit_id_to_channel(unit_id, cfg)
        if ch is None:
            resp_pdu = _make_exception_pdu(pdu, 0x0B)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return
        if not lock_acquire(ch):
            resp_pdu = _make_exception_pdu(pdu, 0x06)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return

        try:
            ch_cfg = (modbus_cfg.get("ch0") if ch == 0 else modbus_cfg.get("ch1")) or {}
            mode = (ch_cfg.get("mode") or "rtu").lower()
            rs485.init(
                ch,
                baudrate=int(ch_cfg.get("baudrate") or 9600),
                parity=ch_cfg.get("parity") or "N",
                stopbits=int(ch_cfg.get("stopbits") or 1),
                bits=int(ch_cfg.get("bits") or 8),
            )
            if unit_id == 0:
                resp_pdu = _make_exception_pdu(pdu, 0x0B)
                _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
                return
            if mode == "ascii":
                frame = _build_ascii_frame(unit_id, pdu)
                rs485.flush_input(ch)
                rs485.send(ch, frame)
                raw = _read_ascii_response(ch, timeout_ms)
                resp_unit, resp_pdu = _parse_ascii_frame(raw)
            else:
                frame = _build_rtu_frame(unit_id, pdu)
                rs485.flush_input(ch)
                rs485.send(ch, frame)
                raw = _read_rtu_response(ch, timeout_ms, int(ch_cfg.get("baudrate") or 9600))
                resp_unit, resp_pdu = _parse_rtu_frame(raw)

            if resp_unit is None or resp_pdu is None:
                resp_pdu = _make_exception_pdu(pdu, 0x0B)
                _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
                return
            _send_mb_tcp_response(cl, tid, resp_unit, resp_pdu)
        finally:
            lock_release(ch)
    except Exception as e:
        print("Modbus TCP error:", e)
    finally:
        try:
            cl.close()
        except Exception:
            pass


def _send_mb_tcp_response(sock, tid: bytes, unit_id: int, pdu: bytes):
    length = len(pdu) + 1
    mbap = tid + b"\x00\x00" + bytes([(length >> 8) & 0xFF, length & 0xFF, unit_id & 0xFF])
    sock.send(mbap + pdu)
