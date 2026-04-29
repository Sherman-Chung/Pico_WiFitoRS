# mdns_service.py - 簡易 mDNS responder：回答 <hostname>.local 的 A 記錄
# 為節省資源僅支援基本 A 查詢，回應多播 224.0.0.251:5353。
#
# 維護導讀：
# - 回覆資料僅包含 A 記錄，若要擴充 PTR/SRV/TXT 需補完整 DNS record 組包。

import socket
try:
    import _thread
except Exception:
    _thread = None


# =============== IPv4 字串轉位元組 ===============
# 說明：
# 將 dotted IPv4 字串轉為 4-byte 表示，供 mDNS 封包使用。
def _inet_aton(ip: str) -> bytes:
    """MicroPython 有時沒有 socket.inet_aton，改用手動轉換。"""
    if hasattr(socket, "inet_aton"):
        try:
            return socket.inet_aton(ip)  # type: ignore[attr-defined]
        except Exception:
            pass
    parts = (ip or "0.0.0.0").split(".")
    if len(parts) != 4:
        return b"\x00\x00\x00\x00"
    try:
        return bytes(int(p) & 0xFF for p in parts)
    except Exception:
        return b"\x00\x00\x00\x00"


# 若缺少 socket.inet_aton，補上一個以避免 ImportError
if not hasattr(socket, "inet_aton"):
    try:
        socket.inet_aton = _inet_aton  # type: ignore[attr-defined]
    except Exception:
        pass

MDNS_MCAST_GRP = "224.0.0.251"
MDNS_PORT = 5353


class MDNSResponder:
    # =============== mDNS 建構子 ===============
    # 說明：
    # 初始化 hostname 與動態 IP 來源。
    def __init__(self, hostname="pico", ip_getter=None, threaded=False):
        self.hostname = hostname
        self.ip_getter = ip_getter or (lambda: "0.0.0.0")
        self._sock = None
        self._running = False
        self._thread = None
        self.threaded = threaded

    # =============== mDNS 啟動 ===============
    # 說明：
    # 建立 UDP multicast socket，並啟動背景回應迴圈。
    def start(self):
        if self._running:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 加入 mDNS multicast 群組，讓來自 224.0.0.251 的封包進入
            mreq = _inet_aton(MDNS_MCAST_GRP) + _inet_aton("0.0.0.0")
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self._sock.bind(("0.0.0.0", MDNS_PORT))
            self._running = True
            if self.threaded and _thread is not None:
                self._thread = _thread.start_new_thread(self._loop, ())
                print("mDNS responder started for %s.local" % self.hostname)
            else:
                self._sock.settimeout(0)
                print("mDNS responder started in poll mode for %s.local" % self.hostname)
        except OSError as e:
            # 5353 可能已被系統或其他服務占用；此情況下略過 mDNS 但不視為致命錯誤。
            code = e.args[0] if getattr(e, "args", None) else None
            if code == 98:
                print("mDNS unavailable: port 5353 already in use")
            else:
                print("mDNS start failed:", e)
            self.stop()
        except Exception as e:
            print("mDNS start failed:", e)
            self.stop()

    # =============== mDNS 停止 ===============
    # 說明：
    # 停止背景回應迴圈並釋放 socket。
    def stop(self):
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._thread = None

    # =============== mDNS 主迴圈 ===============
    # 說明：
    # 監聽 mDNS 查詢，命中 hostname.local 時回覆 A 記錄。
    def _loop(self):
        """簡易 responder：只處理 A 紀錄且僅回 hostname.local 的查詢。"""
        while self._running:
            self.poll(timeout=1.0)

    def poll(self, timeout=0):
        """非阻塞處理一筆 mDNS 查詢；由主迴圈呼叫可避免佔用 core1。"""
        if not self._running or self._sock is None:
            return
        target_name = (self.hostname + ".local").encode("utf-8")
        try:
            self._sock.settimeout(timeout)
        except Exception:
            pass
        try:
            data, addr = self._sock.recvfrom(512)
        except OSError:
            return
        except Exception:
            return
        if not data or len(data) < 12:
            return
        # 簡單解析問題
        try:
            idx = 12
            labels = []
            l = data[idx]
            while l and idx < len(data):
                idx += 1
                labels.append(data[idx : idx + l])
                idx += l
                l = data[idx]
            idx += 1  # zero
            qtype = data[idx : idx + 2]
        except Exception:
            return

        asked = b".".join(labels)
        if asked.lower() != target_name.lower():
            return
        if qtype != b"\x00\x01":  # A
            return

        try:
            ip = self.ip_getter()
            ip_bytes = _inet_aton(ip)
        except Exception:
            return

        tid = data[0:2]
        flags = b"\x84\x00"  # response, authoritative
        qdcount = b"\x00\x01"
        ancount = b"\x00\x01"
        nscount = b"\x00\x00"
        arcount = b"\x00\x00"

        ans = b"\xc0\x0c"  # pointer to name
        ans += b"\x00\x01"  # type A
        ans += b"\x00\x01"  # class IN
        ans += b"\x00\x00\x00\x1e"  # TTL 30s
        ans += b"\x00\x04"  # RDLENGTH
        ans += ip_bytes

        resp = b"".join(
            [
                tid,
                flags,
                qdcount,
                ancount,
                nscount,
                arcount,
                data[12: idx + 4],
                ans,
            ]
        )
        try:
            self._sock.sendto(resp, (MDNS_MCAST_GRP, MDNS_PORT))
        except Exception:
            pass
