# modbus_gateway.py - Modbus TCP <-> Modbus RTU/ASCII gateway
#
# 維護導讀：
# - 此模組是 TCP 與 RS485 的橋接核心。
# - 「本地 registers 回覆」與「轉送到 CH0/CH1」都在 poll_modbus_tcp_server()。
# - 若要改路由規則，優先看 _unit_id_to_channel() 與 unit_map 設定解析。
# - 若要改 exception 行為，優先看 _make_exception_pdu() 與各分支 code。

import socket
import time

import Pico_RS485 as rs485
from config_store import get_config
from rs485_lock import acquire as lock_acquire, release as lock_release
from register_store import get_regs

server_sock = None
LOG_TCP = True
TCP_IDLE_TIMEOUT_MS = None


# =============== CRC16 計算 ===============
# 說明：
# 計算 Modbus RTU 封包所需的 CRC16 校驗值。
def _crc16(data: bytes) -> int:
    """Modbus RTU CRC16。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


# =============== LRC 計算 ===============
# 說明：
# 計算 Modbus ASCII 封包所需的 LRC 校驗值。
def _lrc(data: bytes) -> int:
    """Modbus ASCII LRC。"""
    lrc = 0
    for b in data:
        lrc = (lrc + b) & 0xFF
    lrc = ((-lrc) & 0xFF)
    return lrc


# =============== Unit Map 解析 ===============
# 說明：
# 將設定字串（如 1-10,20,30）轉成可快速查詢的 Unit ID 集合。
def _parse_unit_map(expr: str):
    """將 '1-10,20,30' 這種文字映射轉成 Unit ID 集合。"""
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


# =============== Unit ID 通道映射 ===============
# 說明：
# 依設定決定某個 Unit ID 應走 CH0、CH1，或不允許轉送（None）。
def _unit_id_to_channel(unit_id: int, cfg) -> int | None:
    """依 unit_map 與 ch_enabled 判斷該 Unit ID 應走哪個 RS485 通道。"""
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


# =============== RTU 封包組建 ===============
# 說明：
# 將 Unit ID + PDU 組成 RTU frame，並附加 CRC。
def _build_rtu_frame(unit_id: int, pdu: bytes) -> bytes:
    """組 RTU frame: Unit ID + PDU + CRC。"""
    base = bytes([unit_id]) + pdu
    crc = _crc16(base)
    return base + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


# =============== RTU 封包解析 ===============
# 說明：
# 驗證 CRC，成功則拆解為 (unit_id, pdu)。
def _parse_rtu_frame(frame: bytes):
    """解析 RTU frame，成功回 (unit_id, pdu)。"""
    if len(frame) < 5:
        return None, None
    body = frame[:-2]
    crc_rx = frame[-2] | (frame[-1] << 8)
    if _crc16(body) != crc_rx:
        return None, None
    return body[0], body[1:]


# =============== ASCII 封包組建 ===============
# 說明：
# 將 Unit ID + PDU 組成 ASCII frame（:HEX...CRLF）並附加 LRC。
def _build_ascii_frame(unit_id: int, pdu: bytes) -> bytes:
    """組 ASCII frame: :HEX + LRC + CRLF。"""
    base = bytes([unit_id]) + pdu
    lrc = _lrc(base)
    hex_txt = "".join("%02X" % b for b in (base + bytes([lrc])))
    return (":" + hex_txt + "\r\n").encode()


# =============== ASCII 封包解析 ===============
# 說明：
# 驗證 ASCII frame 格式與 LRC，成功則回 (unit_id, pdu)。
def _parse_ascii_frame(frame: bytes):
    """解析 ASCII frame，成功回 (unit_id, pdu)。"""
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


# =============== RTU 回覆讀取 ===============
# 說明：
# 依 baudrate 推算 idle 間隔，於 timeout 內收集完整 RTU 回覆。
def _read_rtu_response(ch: int, timeout_ms: int, baudrate: int) -> bytes:
    """以 timeout + idle gap 收集 RTU 回覆。"""
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


# =============== ASCII 回覆讀取 ===============
# 說明：
# 於 timeout 內持續讀取，直到遇到 LF（行結束）。
def _read_ascii_response(ch: int, timeout_ms: int) -> bytes:
    """讀到 LF 或 timeout 即返回 ASCII 回覆。"""
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


# =============== Exception PDU 產生 ===============
# 說明：
# 依請求功能碼產生標準 Modbus Exception PDU。
def _make_exception_pdu(req_pdu: bytes, code: int) -> bytes:
    """依原 function code 組 Modbus exception PDU。"""
    func = req_pdu[0] if req_pdu else 0
    return bytes([func | 0x80, code])


# =============== Modbus TCP 伺服器啟動 ===============
# 說明：
# 建立非阻塞監聽 socket，供主迴圈輪詢 accept 使用。
def start_modbus_tcp_server(port: int = 502):
    """啟動非阻塞 Modbus TCP server socket。"""
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


# =============== 定長資料讀取 ===============
# 說明：
# 儘量讀滿 size bytes；處理 timeout、斷線與 partial read。
def _read_exact(sock, size: int, timeout_ms: int = 1000):
    """嘗試讀滿指定大小；回傳 None/b''/partial/complete。"""
    data = b""
    t0 = time.ticks_ms()
    while len(data) < size and time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        try:
            chunk = sock.recv(size - len(data))
        except OSError:
            return None if not data else data
        if not chunk:
            return b"" if not data else data
        data += chunk
    if len(data) < size:
        return None if not data else data
    return data


# =============== Modbus TCP 輪詢處理 ===============
# 說明：
# 輪詢 accept 後在同一 client 連線上持續處理 MBAP+PDU，
# 依 Unit ID 決定回本地 registers 或轉送 RS485。
def poll_modbus_tcp_server():
    """輪詢一次 Modbus TCP：accept 一個 client，並在同連線上持續處理請求。"""
    global server_sock
    if server_sock is None:
        return
    try:
        cl, addr = server_sock.accept()
    except OSError:
        return

    try:
        if LOG_TCP:
            print("TCP CONNECT:", addr)
        cl.settimeout(0.2)
        last_rx = time.ticks_ms()
        # 長連線模型：同一 client 可連續送多筆請求
        while True:
            head = _read_exact(cl, 7, timeout_ms=800)
            if head is None:
                if TCP_IDLE_TIMEOUT_MS is not None and time.ticks_diff(time.ticks_ms(), last_rx) > TCP_IDLE_TIMEOUT_MS:
                    break
                continue
            if head == b"":
                break
            if len(head) < 7:
                break
            last_rx = time.ticks_ms()
            tid = head[0:2]
            pid = head[2:4]
            length = (head[4] << 8) | head[5]
            unit_id = head[6]
            if pid != b"\x00\x00":
                break
            body = _read_exact(cl, max(0, length - 1), timeout_ms=800)
            if body is None and length > 1:
                if TCP_IDLE_TIMEOUT_MS is not None and time.ticks_diff(time.ticks_ms(), last_rx) > TCP_IDLE_TIMEOUT_MS:
                    break
                continue
            if body == b"" and length > 1:
                break
            if body is None:
                break
            if len(body) < max(0, length - 1):
                break
            last_rx = time.ticks_ms()
            pdu = body
            if LOG_TCP:
                print("TCP RX:", _hex_line(head + body))

            # 每筆請求都讀一次設定，讓 Web 更新可立即生效
            cfg = get_config()
            modbus_cfg = cfg.get("modbus") or {}
            timeout_ms = int(modbus_cfg.get("response_timeout_ms") or 1200)
            tcp_slave_id = int(modbus_cfg.get("tcp_slave_id") or 1)

            # Unit ID 命中本地 slave id：走本地 registers，不轉送 RS485
            if unit_id == tcp_slave_id:
                if len(pdu) >= 5 and pdu[0] in (0x03, 0x04):
                    addr = (pdu[1] << 8) | pdu[2]
                    count = (pdu[3] << 8) | pdu[4]
                    if count <= 0 or addr < 0 or addr + count > 256:
                        resp_pdu = _make_exception_pdu(pdu, 0x02)
                        _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
                        continue
                    regs = get_regs(addr, count)
                    data = bytearray()
                    for v in regs:
                        data.append((v >> 8) & 0xFF)
                        data.append(v & 0xFF)
                    resp_pdu = bytes([pdu[0], len(data)]) + bytes(data)
                    _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
                    continue
                resp_pdu = _make_exception_pdu(pdu, 0x01)
                _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
                continue

            # 其他 Unit ID：依 unit map 決定要轉送的 RS485 通道
            ch = _unit_id_to_channel(unit_id, cfg)
            if ch is None:
                resp_pdu = _make_exception_pdu(pdu, 0x0B)
                _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
                continue
            # 同一通道同時間只能有一筆收發，避免半雙工碰撞
            if not lock_acquire(ch):
                resp_pdu = _make_exception_pdu(pdu, 0x06)
                _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
                continue

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
                    continue
                # 依通道 mode 選擇 RTU 或 ASCII 封包流程
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
                    continue
                _send_mb_tcp_response(cl, tid, resp_unit, resp_pdu)
            finally:
                lock_release(ch)
    except Exception as e:
        print("Modbus TCP error:", e)
    finally:
        try:
            if LOG_TCP:
                print("TCP DISCONNECT:", addr)
            cl.close()
        except Exception:
            pass


# =============== MBAP 回應封裝 ===============
# 說明：
# 將 TID、Unit ID 與 PDU 組成完整 Modbus TCP 回應後送出。
def _send_mb_tcp_response(sock, tid: bytes, unit_id: int, pdu: bytes):
    """封裝 MBAP header 後回送 TCP payload。"""
    length = len(pdu) + 1
    mbap = tid + b"\x00\x00" + bytes([(length >> 8) & 0xFF, length & 0xFF, unit_id & 0xFF])
    payload = mbap + pdu
    if LOG_TCP:
        print("TCP TX:", _hex_line(payload))
    sock.send(payload)


# =============== HEX 顯示格式化 ===============
# 說明：
# 將 bytes 轉成空白分隔 HEX 字串，供除錯輸出使用。
def _hex_line(buf: bytes) -> str:
    return " ".join("%02X" % b for b in buf)
