# Server_CMD.py - 遠端指令解析與 TCP 伺服器
# handle_cmd 專責解析指令；start/poll 管理非阻塞 TCP 伺服器。
#
# 維護導讀：
# - 此模組提供「文字命令入口」，主要用於維運與測試。
# - 真正的 Modbus Gateway 資料平面在 modbus_gateway.py，不走 MB 子命令。
# - poll_cmd_server() 目前是「一連線一命令」模型，不維持長連線。

import socket
import time
from machine import Pin

from wifi_Scan_Connect import wlan, reset_wifi, restart_config_ap
import Pico_RS485 as rs485
from rs485_lock import acquire as lock_acquire, release as lock_release

SERVER_PORT = 12345  # 可依需求調整
server_sock = None


def _is_sock_timeout(err: Exception) -> bool:
    """判斷是否為 socket timeout/暫時無資料。"""
    code = None
    try:
        if getattr(err, "args", None):
            code = err.args[0]
    except Exception:
        code = None
    return code in (11, 35, 110, 116, 119)


# =============== Hex Token 解析 ===============
# 說明：
# 將命令列中的 hex token 轉成 bytes（支援 0x 前綴）。
def _parse_hex_bytes(tokens) -> bytes:
    """將多個 hex token 轉為 bytes（例如 06 / 0x06）。"""
    out = bytearray()
    for t in tokens:
        if not t:
            continue
        if t.lower().startswith("0x"):
            t = t[2:]
        if not t:
            continue
        out.append(int(t, 16) & 0xFF)
    return bytes(out)


# =============== 指令主解析器 ===============
# 說明：
# 解析並執行 SYS/LED/MB/RS 指令，回傳文字結果。
def handle_cmd(cmd: str) -> str:
    """
    核心命令解析入口。

    命令大類：
    - SYS: 系統與 Wi-Fi/AP 維運
    - LED: 板載 LED 控制
    - MB : 示範用回覆（非主資料路徑）
    - RS : RS485 原始收發測試
    """
    cmd = cmd.strip()
    if not cmd:
        return "ERR EMPTY"

    parts = cmd.split()
    name = parts[0].upper()
    args = parts[1:]

    # ---------- SYS 類 ----------
    if name == "SYS":
        if not args:
            return "ERR SYS ARG"
        sub = args[0].upper()

        if sub == "STATUS":
            try:
                ip, nm, gw, dns = wlan.ifconfig()
                return f"OK SYS STATUS \nIP={ip} \nNETMASK={nm} \nGW={gw} \nDNS={dns}"
            except Exception as e:
                return "ERR SYS STATUS " + str(e)[:60]

        elif sub == "WIFI":
            if len(args) >= 2 and args[1].upper() == "RESET":
                ok = reset_wifi()
                return "OK SYS WIFI RESET" if ok else "ERR SYS WIFI RESET"
            try:
                active = wlan.active()
                conn = wlan.isconnected()
                ip, nm, gw, dns = wlan.ifconfig()
                try:
                    rssi = wlan.status("rssi")
                except Exception:
                    rssi = None
                return f"OK SYS WIFI \nACTIVE={active} \nCONNECTED={conn} \nIP={ip} \nRSSI={rssi}"
            except Exception as e:
                return "ERR SYS WIFI " + str(e)[:60]

        elif sub == "PING":
            return "OK SYS PING"

        elif sub == "HELP":
            return "OK SYS CMDS: \nSYS STATUS \nSYS WIFI [RESET] \nSYS AP RESET \nSYS PING \nSYS HELP \nSYS MB R/W HR \nSYS COIL \nSYS LED ON/OFF \nRS SEND/RECV/HEX"
        elif sub == "AP" and len(args) >= 2 and args[1].upper() == "RESET":
            ok = restart_config_ap()
            return "OK SYS AP RESET" if ok else "ERR SYS AP RESET"

        else:
            return "ERR SYS UNKNOWN " + args[0]

    # ---------- LED 控制 ----------
    elif name == "LED":
        if not args:
            return "ERR LED ARG"
        sub = args[0].upper()
        if sub == "ON":
            Pin("LED", Pin.OUT).value(1)
            return "OK LED=ON"
        elif sub == "OFF":
            Pin("LED", Pin.OUT).value(0)
            return "OK LED=OFF"
        else:
            return "ERR LED " + args[0]

    # ---------- Modbus：MB 類 ----------
    elif name == "MB":
        if len(args) < 4:
            return "ERR MB ARG"

        rw = args[0].upper()  # R or W
        area = args[1].upper()  # HR / COIL / ...
        try:
            slave = int(args[2])
            addr = int(args[3])
        except ValueError:
            return "ERR MB NUM"

        if rw == "R" and area == "HR":
            if len(args) < 5:
                return "ERR MB RHR ARG"
            try:
                count = int(args[4])
            except ValueError:
                return "ERR MB RHR NUM"

            # 預留 Modbus 讀取接口，目前回傳假資料做示範
            values = [1234 + i for i in range(count)]
            vals_str = " ".join(str(v) for v in values)
            return f"OK MB R HR {slave} {addr} {vals_str}"

        elif rw == "W" and area == "HR":
            if len(args) < 5:
                return "ERR MB WHR ARG"
            try:
                value = int(args[4])
            except ValueError:
                return "ERR MB WHR NUM"

            # 預留 Modbus 寫入接口
            return f"OK MB W HR {slave} {addr} {value}"

        else:
            return f"ERR MB UNSUPPORTED {rw} {area}"

    # ---------- RS485 ----------
    elif name == "RS":
        if not args:
            return "ERR RS ARG"
        sub = args[0].upper()

        # RS SEND <ch> <text...>
        if sub == "SEND":
            if len(args) < 3:
                return "ERR RS SEND ARG"
            try:
                ch = int(args[1])
            except ValueError:
                return "ERR RS CH"
            payload = " ".join(args[2:])
            try:
                rs485.init(ch)
                n = rs485.send(ch, payload + "\r\n")
                return f"OK RS SEND {ch} {n}B"
            except Exception as e:
                return "ERR RS SEND " + str(e)[:60]

        # RS HEX <ch> <hex bytes...>（限定 8 bytes，直接發送）
        elif sub == "HEX":
            if len(args) < 3:
                return "ERR RS HEX ARG"
            try:
                ch = int(args[1])
            except ValueError:
                return "ERR RS CH"
            try:
                raw = _parse_hex_bytes(args[2:])
            except ValueError:
                return "ERR RS HEX PARSE"
            if len(raw) != 8:
                return "ERR RS HEX LEN"
            if not lock_acquire(ch):
                return "ERR RS BUSY"
            try:
                from config_store import get_config, get_response_timeout_ms

                cfg = get_config()
                modbus_cfg = cfg.get("modbus") or {}
                ch_cfg = (modbus_cfg.get("ch0") if ch == 0 else modbus_cfg.get("ch1")) or {}
                baudrate = int(ch_cfg.get("baudrate") or 9600)
                timeout_ms = get_response_timeout_ms(cfg)
                rs485.init(
                    ch,
                    baudrate=baudrate,
                    parity=ch_cfg.get("parity") or "N",
                    stopbits=int(ch_cfg.get("stopbits") or 1),
                    bits=int(ch_cfg.get("bits") or 8),
                )
                rs485.flush_input(ch)
                n = rs485.send(ch, raw)
                # 等待 UART 送完再切入接收（估算傳輸時間）
                tx_time_ms = int((len(raw) * 11 * 1000) / baudrate) + 2
                time.sleep_ms(tx_time_ms)
                # 簡易等待回覆：有資料就累積，若一段時間沒新資料就結束
                buf = bytearray()
                start = time.ticks_ms()
                last_rx = start
                while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
                    chunk = rs485.recv(ch, 256, log=False)
                    if chunk:
                        buf.extend(chunk)
                        last_rx = time.ticks_ms()
                    else:
                        if buf and time.ticks_diff(time.ticks_ms(), last_rx) > 100:
                            break
                        time.sleep_ms(10)
                if buf:
                    hex_txt = " ".join("%02X" % b for b in buf)
                    print("RS485 CH%d RX:" % ch, hex_txt)
                    return f"OK RS HEX {ch} {n}B RX {len(buf)}B {hex_txt}"
                return f"OK RS HEX {ch} {n}B RX 0B"
            except Exception as e:
                return "ERR RS HEX " + str(e)[:60]
            finally:
                lock_release(ch)

        # RS RECV <ch> [max]
        elif sub == "RECV":
            if len(args) < 2:
                return "ERR RS RECV ARG"
            try:
                ch = int(args[1])
                maxb = int(args[2]) if len(args) >= 3 else 256
            except ValueError:
                return "ERR RS NUM"
            try:
                rs485.init(ch)
                data = rs485.recv(ch, maxb)
                txt = data.decode("utf-8", "ignore")
                return f"OK RS RECV {ch} {len(data)}B {txt}"
            except Exception as e:
                return "ERR RS RECV " + str(e)[:60]

        else:
            return "ERR RS UNKNOWN " + sub

    # ---------- 傳統指令兼容 ----------
    elif name == "STATUS":
        return handle_cmd("SYS STATUS")

    else:
        return "ERR UNKNOWN CMD: " + cmd


# =============== 指令 TCP 伺服器啟動 ===============
# 說明：
# 建立非阻塞監聽 socket（port 12345），供主迴圈輪詢。
def start_cmd_server():
    """啟動非阻塞 TCP 伺服器（12345），失敗時會丟出例外便於偵錯。"""
    global server_sock
    addr = socket.getaddrinfo("0.0.0.0", SERVER_PORT)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.0)
    server_sock = s
    print("start_cmd_server: listening on", addr)


# =============== 指令 TCP 輪詢處理 ===============
# 說明：
# 接受一個 client，處理一筆命令並回覆後關閉連線。
def poll_cmd_server():
    """非阻塞檢查連線；收到後執行一筆命令並關閉連線。"""
    global server_sock
    if server_sock is None:
        return

    try:
        cl, addr = server_sock.accept()
    except OSError:
        return

    print("client connected from", addr)

    try:
        # 避免 CMD client 連上但不送資料時卡住主迴圈，影響 HTTP 反應。
        cl.settimeout(0.2)
        try:
            data = cl.recv(1024)
        except OSError as e:
            if _is_sock_timeout(e):
                return
            raise
        if not data:
            cl.close()
            return
        cmd = data.decode("utf-8", "ignore")
        resp = handle_cmd(cmd) + "\n"
        # 直接回應後關閉 socket，不維持長連線以節省資源
        cl.send(resp.encode("utf-8"))
    except OSError as e:
        print("poll_cmd_server recv/send error:", e)
    finally:
        cl.close()
