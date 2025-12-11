# dns_captive.py - 極簡 DNS 假門牌伺服器：任何查詢都回指定 IP（可動態更新）
# 僅支援最基本的 A 記錄，輕量且適合 Pico。

import socket
import _thread


def _inet_aton(ip: str) -> bytes:
    """MicroPython 有時沒有 socket.inet_aton，自己轉換。"""
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


# 如果系統沒有 socket.inet_aton，補上一個
if not hasattr(socket, "inet_aton"):
    try:
        socket.inet_aton = _inet_aton  # type: ignore[attr-defined]
    except Exception:
        pass


class CaptiveDNS:
    def __init__(self, ip="192.168.4.1", port=53, ip_getter=None):
        # 若提供 ip_getter，每次回應都會取最新 IP（例如 STA IP）。
        self.ip = ip
        self.ip_getter = ip_getter
        self.port = port
        self._sock = None
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.port))
            self._running = True
            self._thread = _thread.start_new_thread(self._loop, ())
            print("Captive DNS started, all hosts ->", self.ip)
        except Exception as e:
            print("Captive DNS start failed:", e)
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
        # DNS header: ID(2) | flags(2) | QD(2) | AN(2) | NS(2) | AR(2)
        # 只回 A 記錄，且將查詢名稱指向指定 IP（或 ip_getter 回傳的 IP）。
        while self._running:
            if self._sock is None:
                break
            try:
                self._sock.settimeout(1.0)
                data, addr = self._sock.recvfrom(512)
            except OSError:
                continue
            except Exception:
                continue
            if not data or len(data) < 12:
                continue
            tid = data[0:2]
            flags = b"\x81\x80"  # standard query response, no error
            qdcount = data[4:6]
            # parse question to echo back
            idx = 12
            try:
                l = data[idx]
                while l and idx < len(data):
                    idx += 1
                    idx += l
                    l = data[idx]
                idx += 1  # skip zero
                qtype = data[idx : idx + 2]
                qclass = data[idx + 2 : idx + 4]
            except Exception:
                continue
            question = data[12: idx + 4]
            # only answer A
            if qtype != b"\x00\x01":
                continue

            target_ip = self.ip
            if self.ip_getter:
                try:
                    target_ip = self.ip_getter() or target_ip
                except Exception:
                    pass

            ans = b"\xc0\x0c"  # pointer to name at offset 12
            ans += b"\x00\x01"  # type A
            ans += b"\x00\x01"  # class IN
            ans += b"\x00\x00\x00\x1e"  # TTL 30s
            ans += b"\x00\x04"  # RDLENGTH
            ans += _inet_aton(target_ip)

            resp = b"".join(
                [
                    tid,
                    flags,
                    qdcount,
                    b"\x00\x01",
                    b"\x00\x00",
                    b"\x00\x00",
                    question,
                    ans,
                ]
            )
            try:
                if self._sock:
                    self._sock.sendto(resp, addr)
            except Exception:
                pass
