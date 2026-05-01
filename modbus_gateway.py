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
from register_store import get_regs, set_reg
import hold_register_map as hrm
import log_buffer

server_sock = None # TCP server socket，啟動後保持不變
TCP_LOG = True # 是否印出 TCP 收發資料的 LOG（包含完整 MBAP header 與 PDU）；設為 False 可關閉此 LOG
RS485_LOG = True # 是否印出 RS485 收發資料的 LOG（包含轉送的 RTU/ASCII frame）；設為 False 可關閉此 LOG
TCP_IDLE_TIMEOUT_MS = None # TCP 連線閒置逾時（毫秒），超過此時間未收到資料即自動斷線；設為 None 可關閉此機制
TCP_FAIR_YIELD_MS = 1 # 在高頻請求下主動讓出 CPU，避免其他工作（含 poller thread）飢餓；設為 0 可關閉此機制
TCP_RECV_CHUNK = 512 # 每次 recv 最多讀這麼多 bytes，避免一次讀太大導致記憶體壓力；也可調整以適應不同封包大小需求
TCP_POLL_BUDGET_MS = 1 # 每次 poll_modbus_tcp_server 最多花這麼多時間解析封包，避免 CPU 飢餓

_active_client = None
_active_addr = None
_active_buf = b""
_active_last_rx = 0


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
def _unit_id_to_channel(unit_id: int, cfg):
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
        chunk = rs485.recv(ch, 256, log=RS485_LOG)
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
        chunk = rs485.recv(ch, 256, log=RS485_LOG)
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
    log_buffer.append_log("Modbus TCP server listening on " + str(addr))


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


# =============== 活動 TCP client 管理 ===============
# 說明：
# 目前僅支援單一活動連線，並在斷線或錯誤時清理狀態。可選擇性加入閒置逾時機制。
def _close_active_client():
    """關閉目前活動 TCP client，並清空緩衝狀態。"""
    global _active_client, _active_addr, _active_buf, _active_last_rx
    try:
        if _active_client is not None:
            _active_client.close()
    except Exception:
        pass
    if TCP_LOG and _active_addr is not None:
        log_buffer.append_log("TCP DISCONNECT: " + str(_active_addr))
    _active_client = None
    _active_addr = None
    _active_buf = b""
    _active_last_rx = 0


# =============== Gateway 設定讀取 ===============
# 說明：
# 直接由 Hold Register 讀取 tcp_slave_id/timeout/reg3 路由資訊，避免每筆請求都讀整份 config 的開銷。
def _read_gateway_regs():
    """由 Hold Register 直接讀取 tcp_slave_id/timeout/reg3 路由資訊。"""
    regs = get_regs(1, 3, decode=False)  # REG1/2/3
    raw_slave_id = int(regs[0]) if len(regs) > 0 else 1
    raw_timeout_x10 = int(regs[1]) if len(regs) > 1 else 120
    raw_mode = int(regs[2]) if len(regs) > 2 else 0

    tcp_slave_id = raw_slave_id if 1 <= raw_slave_id <= 247 else 1
    timeout_ms = raw_timeout_x10 * 10
    if timeout_ms < 1:
        timeout_ms = 1

    tcp_rs485_mode = hrm.decode_value(3, raw_mode)
    if tcp_rs485_mode not in ("disabled", "ch0", "ch1"):
        tcp_rs485_mode = "disabled"

    return tcp_slave_id, timeout_ms, tcp_rs485_mode


# =============== 本地寄存器請求處理 ===============
# 說明：
# 直接處理針對 Hold Register 的讀寫請求，並回傳原始 u16 數值（不解碼）。其他功能碼或寄存器地址則回應 Illegal Function 或 Illegal Data Address。
def _handle_local_register_request(cl, tid: bytes, unit_id: int, pdu: bytes):
    """處理本地 Hold Register（FC03/04/06/16）。"""
    # Modbus FC 03 / FC 04 (Read Registers)
    if len(pdu) >= 5 and pdu[0] in (0x03, 0x04):
        reg_addr = (pdu[1] << 8) | pdu[2]
        count = (pdu[3] << 8) | pdu[4]
        if count <= 0 or count > 125 or reg_addr < 0 or reg_addr + count > 512:
            resp_pdu = _make_exception_pdu(pdu, 0x02)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return

        # Modbus TCP 本地寄存器統一回傳原始 u16（enum code），
        # 例如 mode/parity 這類欄位維持 0/1/2，不回傳字串。
        regs = get_regs(reg_addr, count, decode=False)

        data = bytearray()
        for v in regs:
            data.append((v >> 8) & 0xFF)
            data.append(v & 0xFF)
        resp_pdu = bytes([pdu[0], len(data)]) + bytes(data)
        _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
        return

    # Modbus FC 16 (Write Multiple Registers)
    if len(pdu) >= 9 and pdu[0] == 0x10:
        reg_addr = (pdu[1] << 8) | pdu[2]
        count = (pdu[3] << 8) | pdu[4]
        byte_count = pdu[5]

        if count <= 0 or count > 125 or byte_count != count * 2:
            resp_pdu = _make_exception_pdu(pdu, 0x02)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return

        if len(pdu) < 6 + byte_count:
            resp_pdu = _make_exception_pdu(pdu, 0x02)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return

        # 寫入配置區時自動編碼
        should_encode = 0 <= reg_addr < 23  # 配置區 0-22
        success_count = 0
        error = False

        for i in range(count):
            idx = reg_addr + i
            raw_val = (pdu[6 + i * 2] << 8) | pdu[6 + i * 2 + 1]

            # 自動驗證和編碼
            ok, err_msg = set_reg(idx, raw_val, encode=should_encode, source="modbus_tcp_local")
            if ok:
                success_count += 1
            else:
                if TCP_LOG:
                    print(f"Register write error at {idx}: {err_msg}")
                error = True

        if error or success_count != count:
            resp_pdu = _make_exception_pdu(pdu, 0x03)  # Illegal data value
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return

        # 回覆：FC 16 成功時回報地址與數量
        resp_pdu = bytes([0x10, pdu[1], pdu[2], pdu[3], pdu[4]])
        _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
        return

    # Modbus FC 06 (Write Single Register)
    if len(pdu) >= 5 and pdu[0] == 0x06:
        reg_addr = (pdu[1] << 8) | pdu[2]
        raw_val = (pdu[3] << 8) | pdu[4]

        if reg_addr < 0 or reg_addr >= 512:
            resp_pdu = _make_exception_pdu(pdu, 0x02)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return

        # 寫入配置區時自動編碼
        should_encode = 0 <= reg_addr < 23  # 配置區 0-22
        ok, err_msg = set_reg(reg_addr, raw_val, encode=should_encode, source="modbus_tcp_local")
        if not ok:
            if TCP_LOG:
                print(f"Register write error at {reg_addr}: {err_msg}")
            resp_pdu = _make_exception_pdu(pdu, 0x03)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return

        # 回覆：FC 06 成功時回覆原請求資料
        resp_pdu = bytes([0x06, pdu[1], pdu[2], pdu[3], pdu[4]])
        _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
        return

    # 不支援的功能碼
    resp_pdu = _make_exception_pdu(pdu, 0x01)
    _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)


# =============== Modbus TCP 請求處理 ===============
# 說明：
# 依 Unit ID 與設定決定是回覆本地寄存器，還是轉送到 CH0/CH1。轉送時處理通道啟用狀態、UART 參數設定，以及 RTU/ASCII 封包流程。
def _handle_mb_tcp_request(cl, tid: bytes, unit_id: int, pdu: bytes):
    """處理單筆已解析的 Modbus TCP 請求。"""
    # 快速路由：直接由 Hold Register 讀 REG1/2/3 判斷，不先讀整份 config。
    tcp_slave_id, timeout_ms, tcp_rs485_mode = _read_gateway_regs()

    # REG3=disabled：只比對 REG1（tcp_slave_id）決定是否走本地。
    if tcp_rs485_mode == "disabled":
        if unit_id == tcp_slave_id:
            _handle_local_register_request(cl, tid, unit_id, pdu)
        else:
            resp_pdu = _make_exception_pdu(pdu, 0x03)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
        return

    # 非 disabled 時，tcp_slave_id 仍走本地寄存器。
    if unit_id == tcp_slave_id:
        _handle_local_register_request(cl, tid, unit_id, pdu)
        return

    # 只有需要 RS485 轉送時，才讀設定（ch 啟用與 UART 參數）。
    cfg = get_config()
    modbus_cfg = cfg.get("modbus") or {}

    # 其他 Unit ID：REG3 指定固定轉送通道（ch0/ch1）
    if tcp_rs485_mode == "ch0":
        ch = 0
    elif tcp_rs485_mode == "ch1":
        ch = 1
    else:
        ch = _unit_id_to_channel(unit_id, cfg)

    # 指定通道若未啟用，回應 gateway target failed
    if ch == 0 and not bool(modbus_cfg.get("ch0_enabled", False)):
        resp_pdu = _make_exception_pdu(pdu, 0x0B)
        _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
        return
    if ch == 1 and not bool(modbus_cfg.get("ch1_enabled", False)):
        resp_pdu = _make_exception_pdu(pdu, 0x0B)
        _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
        return
    if ch is None:
        resp_pdu = _make_exception_pdu(pdu, 0x0B)
        _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
        return
    # 同一通道同時間只能有一筆收發，避免半雙工碰撞
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
        # 依通道 mode 選擇 RTU 或 ASCII 封包流程
        if mode == "ascii":
            frame = _build_ascii_frame(unit_id, pdu)
            rs485.flush_input(ch)
            rs485.send(ch, frame, log=RS485_LOG)
            raw = _read_ascii_response(ch, timeout_ms)
            resp_unit, resp_pdu = _parse_ascii_frame(raw)
        else:
            frame = _build_rtu_frame(unit_id, pdu)
            rs485.flush_input(ch)
            rs485.send(ch, frame, log=RS485_LOG)
            raw = _read_rtu_response(ch, timeout_ms, int(ch_cfg.get("baudrate") or 9600))
            resp_unit, resp_pdu = _parse_rtu_frame(raw)

        if resp_unit is None or resp_pdu is None:
            resp_pdu = _make_exception_pdu(pdu, 0x0B)
            _send_mb_tcp_response(cl, tid, unit_id, resp_pdu)
            return
        _send_mb_tcp_response(cl, tid, resp_unit, resp_pdu)
    finally:
        lock_release(ch)


# =============== Modbus TCP 輪詢處理 ===============
# 說明：
# 事件式非阻塞處理：只有收到 TCP 封包才解析/回覆，不在此函式內等待。
def poll_modbus_tcp_server():
    """輪詢一次 Modbus TCP：僅處理目前可得資料，不阻塞等待。"""
    global server_sock, _active_client, _active_addr, _active_buf, _active_last_rx
    if server_sock is None:
        return

    # 若沒有活動 client，嘗試非阻塞 accept 一個新連線
    if _active_client is None:
        try:
            cl, addr = server_sock.accept()
        except OSError:
            return
        try:
            cl.settimeout(0.0)
        except Exception:
            pass
        _active_client = cl
        _active_addr = addr
        _active_buf = b""
        _active_last_rx = time.ticks_ms()
        if TCP_LOG:
            log_buffer.append_log("TCP CONNECT: " + str(addr))

    # 連線閒置逾時（可選）
    if TCP_IDLE_TIMEOUT_MS is not None and _active_last_rx:
        if time.ticks_diff(time.ticks_ms(), _active_last_rx) > TCP_IDLE_TIMEOUT_MS:
            _close_active_client()
            return

    # 在高頻請求下主動讓出 CPU，避免其他工作（含 poller thread）飢餓。
    if TCP_FAIR_YIELD_MS > 0:
        time.sleep_ms(TCP_FAIR_YIELD_MS)

    # 非阻塞接收目前可得資料
    try:
        while True:
            try:
                chunk = _active_client.recv(TCP_RECV_CHUNK)
            except OSError as e:
                code = e.args[0] if e.args else None
                # 非阻塞下「目前沒資料」：直接進入解析流程
                if code in (11, 35, 110, 116, 119):
                    break
                _close_active_client()
                return
            if not chunk:
                _close_active_client()
                return
            _active_buf += chunk
            _active_last_rx = time.ticks_ms()
            if len(chunk) < TCP_RECV_CHUNK:
                break
    except Exception:
        _close_active_client()
        return

    # 解析目前緩衝內完整封包（時間預算內）
    parse_t0 = time.ticks_ms()
    try:
        while True:
            if time.ticks_diff(time.ticks_ms(), parse_t0) >= TCP_POLL_BUDGET_MS:
                return
            if len(_active_buf) < 7:
                return
            head = _active_buf[:7]
            tid = head[0:2]
            pid = head[2:4]
            length = (head[4] << 8) | head[5]
            unit_id = head[6]
            if pid != b"\x00\x00":
                _close_active_client()
                return
            body_len = max(0, length - 1)
            total_len = 7 + body_len
            if len(_active_buf) < total_len:
                return
            packet = _active_buf[:total_len]
            pdu = _active_buf[7:total_len]
            _active_buf = _active_buf[total_len:]
            if TCP_LOG:
                log_buffer.append_log("TCP RX: " + _hex_line(packet))
            _handle_mb_tcp_request(_active_client, tid, unit_id, pdu)
    except Exception as e:
        print("Modbus TCP error:", e)
        _close_active_client()


# =============== MBAP 回應封裝 ===============
# 說明：
# 將 TID、Unit ID 與 PDU 組成完整 Modbus TCP 回應後送出。
def _send_mb_tcp_response(sock, tid: bytes, unit_id: int, pdu: bytes):
    """封裝 MBAP header 後回送 TCP payload。"""
    length = len(pdu) + 1
    mbap = tid + b"\x00\x00" + bytes([(length >> 8) & 0xFF, length & 0xFF, unit_id & 0xFF])
    payload = mbap + pdu
    if TCP_LOG:
        log_buffer.append_log("TCP TX: " + _hex_line(payload))
    sock.send(payload)


# =============== HEX 顯示格式化 ===============
# 說明：
# 將 bytes 轉成空白分隔 HEX 字串，供除錯輸出使用。
def _hex_line(buf: bytes) -> str:
    return " ".join("%02X" % b for b in buf)
