# wifi_Scan_Connect.py - Wi-Fi 掃描與連線管理
# 集中處理 WLAN 初始化、掃描結果整理與連線流程，方便 UI 直接呼叫。

import time
import network
import rp2
try:
    from dns_captive import CaptiveDNS
except Exception:
    CaptiveDNS = None

COUNTRY = "TW"
CONNECT_TIMEOUT_MS = 12000

# 初始化 Wi-Fi
try:
    rp2.country(COUNTRY)
except Exception:
    pass

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
ap = network.WLAN(network.AP_IF)
_ap_enabled = False
_ap_config = {"essid": "", "password": ""}
_last_stations = []
_captive_dns = None


def _dns_target_ip():
    """DNS 回應用 IP：若有裝置連在內建 AP，強制回 AP IP，否則回 STA IP。"""
    # 只要還有人連著 PicoSetup，就讓 www.pico.pi.com 保持在 192.168.4.1，
    # 避免 STA 連上家用 Wi-Fi 後 DNS 轉向新 IP，導致手機（仍在 AP）無法互動。
    try:
        if _ap_enabled and ap.active() and ap_station_count() > 0:
            return "192.168.4.1"
    except Exception:
        # 若無法判斷則繼續嘗試回 STA IP
        pass
    try:
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            if ip:
                return ip
    except Exception:
        pass
    return "192.168.4.1"


def _ensure_captive_dns():
    """啟動 DNS 假門牌（AP/STA 共用），讓 www.pico.pi.com 之類的名稱指向目前 IP。"""
    global _captive_dns
    if CaptiveDNS is None:
        return
    if _captive_dns is None:
        _captive_dns = CaptiveDNS(ip="192.168.4.1", ip_getter=_dns_target_ip)
    try:
        _captive_dns.start()
    except Exception as e:
        print("Captive DNS start failed:", e)


def scan_visible():
    """掃描 AP 並回傳已排序的可見清單（忽略空白 SSID）。"""
    raw = wlan.scan()
    filtered = []
    for ap in raw:
        ssid = (ap[0] or b"").decode("utf-8", "ignore").strip()
        if not ssid:
            continue
        filtered.append(ap)
    filtered.sort(key=lambda t: t[3], reverse=True)
    return filtered


def connect_to_ap(ssid: str, psk: str, timeout_ms: int = CONNECT_TIMEOUT_MS) -> bool:
    """嘗試連線指定 AP，成功回 True，失敗回 False。"""
    _ensure_captive_dns()
    try:
        try:
            wlan.disconnect()
        except Exception:
            pass
        wlan.active(True)
        wlan.connect(ssid, psk)
        t0 = time.ticks_ms()
        while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
            time.sleep_ms(150)
    except Exception:
        return False
    return wlan.isconnected()


def read_status():
    """取得連線狀態資訊，方便 UI 顯示。"""
    info = {
        "active": False,
        "connected": False,
        "ifconfig": (),
        "rssi": None,
        "ap_active": _ap_enabled,
        "ap_essid": _ap_config.get("essid", ""),
    }
    try:
        info["active"] = wlan.active()
        info["connected"] = wlan.isconnected()
        info["ifconfig"] = wlan.ifconfig()
        try:
            info["rssi"] = wlan.status("rssi")
        except Exception:
            info["rssi"] = None
    except Exception:
        pass
    return info


def start_config_ap(essid: str = "PicoSetup", password: str = "") -> bool:
    """啟動內建 AP 方便手機連線設定，失敗回 False。"""
    global _ap_enabled, _ap_config, _captive_dns
    # 可依需求在這裡加 channel=6 等參數讓 AP 頻道與家用 Wi-Fi 一致，減少切頻掉線
    try:
        ap.active(True)
        cfg = {"essid": essid}
        if password:
            # WPA2 密碼需 8 碼以上；若給空字串則開啟開放 AP。
            cfg["password"] = password
        ap.config(**cfg)
        _ap_enabled = True
        _ap_config = {"essid": essid, "password": password}
        print("Config AP started:", essid)
        _ensure_captive_dns()
        return True
    except Exception as e:
        print("start_config_ap failed:", e)
        _ap_enabled = False
        return False


def stop_config_ap() -> None:
    """關閉內建 AP。"""
    global _ap_enabled
    try:
        ap.active(False)
    except Exception:
        pass
    _ap_enabled = False


def ap_station_count() -> int:
    """回傳目前連上的 STA 數量（AP mode）。"""
    global _last_stations
    try:
        stas = ap.status("stations")
        # If the API returns an integer count, return it directly.
        if isinstance(stas, int):
            _last_stations = []
            return stas
        # Otherwise ensure we have a sequence we can take len() of.
        _last_stations = stas or []
        return len(_last_stations)
    except Exception:
        return 0


def wait_for_station(min_count: int = 1, timeout_ms=None, poll_ms: int = 500) -> bool:
    """等待有裝置連上 AP；預設不超時。"""
    t0 = time.ticks_ms()
    while True:
        if ap_station_count() >= min_count:
            return True
        if timeout_ms is not None and time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
            return False
        time.sleep_ms(poll_ms)
