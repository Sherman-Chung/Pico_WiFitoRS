# mdns_service.py - 簡易 mDNS responder：回答 <hostname>.local 的 A 記錄
# 為節省資源僅支援基本 A 查詢，回應多播 224.0.0.251:5353。

import socket
import _thread


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
    def __init__(self, hostname="pico", ip_getter=None):
        self.hostname = hostname
        self.ip_getter = ip_getter or (lambda: "0.0.0.0")
        self._sock = None
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            mreq = _inet_aton(MDNS_MCAST_GRP) + _inet_aton("0.0.0.0")
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self._sock.bind(("0.0.0.0", MDNS_PORT))
            self._running = True
            self._thread = _thread.start_new_thread(self._loop, ())
            print("mDNS responder started for %s.local" % self.hostname)
        except Exception as e:
            print("mDNS start failed:", e)
            self.stop()

    def stop(self):
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._thread = None

    def _loop(self):
        """簡易 responder：只處理 A 紀錄且僅回 hostname.local 的查詢。"""
        target_name = (self.hostname + ".local").encode("utf-8")
        while self._running:
            try:
                self._sock.settimeout(1.0)
                data, addr = self._sock.recvfrom(512)
            except OSError:
                continue
            except Exception:
                continue
            if not data or len(data) < 12:
                continue
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
                continue

            asked = b".".join(labels)
            if asked.lower() != target_name.lower():
                continue
            if qtype != b"\x00\x01":  # A
                continue

            try:
                ip = self.ip_getter()
                ip_bytes = _inet_aton(ip)
            except Exception:
                continue

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
