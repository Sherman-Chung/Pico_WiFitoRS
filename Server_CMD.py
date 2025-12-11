# Server_CMD.py - 遠端指令解析與 TCP 伺服器
# handle_cmd 專責解析指令；start/poll 管理非阻塞 TCP 伺服器。

import socket
from machine import Pin

from wifi_Scan_Connect import wlan
import Pico_RS485 as rs485

SERVER_PORT = 12345  # 可依需求調整
server_sock = None


def handle_cmd(cmd: str) -> str:
    """核心指令解析：SYS / MB 兩大類，保留原本行為並加上中文註解。"""
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
            return "OK SYS CMDS: \nSYS STATUS \nSYS WIFI \nSYS PING \nSYS HELP \nSYS MB R/W HR \nSYS COIL \nSYS LED ON/OFF"

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


def poll_cmd_server():
    """非阻塞檢查是否有遠端連線，有的話收一筆指令並回覆。"""
    global server_sock
    if server_sock is None:
        return

    try:
        cl, addr = server_sock.accept()
    except OSError:
        return

    print("client connected from", addr)

    try:
        cl.settimeout(30)
        data = cl.recv(1024)
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
