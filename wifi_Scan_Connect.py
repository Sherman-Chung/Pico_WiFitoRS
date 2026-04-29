# wifi_Scan_Connect.py - Wi-Fi 掃描與連線管理
# 集中處理 WLAN 初始化、掃描結果整理與連線流程，方便 UI 直接呼叫。
#
# 維護導讀：
# - 若要改 AP/STA 共存策略，優先看 _dns_target_ip() 與 start_config_ap()。
# - 若要改連線等待行為，優先看 CONNECT_TIMEOUT_MS 與 connect_to_ap()。

import time
import network
import rp2
try:
    from dns_captive import CaptiveDNS
except Exception:
    CaptiveDNS = None

COUNTRY = "TW"
CONNECT_TIMEOUT_MS = 12000
AP_IP = "192.168.4.1"
AP_NETMASK = "255.255.255.0"
AP_GW = "192.168.4.1"
AP_DNS = "192.168.4.1"
RSSI_POLL_INTERVAL_MS = 3000

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
_last_rssi = None
_last_rssi_ms = 0


# =============== DNS 目標 IP 判定 ===============
# 說明：
# 決定 Captive DNS 應回覆的 IP（優先 AP，其次 STA）。
def _dns_target_ip():
    """DNS 回應用 IP：AP 啟用時優先回 AP IP，否則回 STA IP。"""
    # 避免在高頻 DNS 查詢下反覆讀 ap.status("stations")，導致 CYW43 ioctl timeout。
    try:
        if _ap_enabled and ap.active():
            return AP_IP
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
    return AP_IP


# =============== Captive DNS 啟動保證 ===============
# 說明：
# 確保 Captive DNS 實例存在且已啟動。
def _ensure_captive_dns():
    """啟動 DNS 假門牌（AP/STA 共用），讓 www.pico.pi.com 之類的名稱指向目前 IP。"""
    global _captive_dns
    if CaptiveDNS is None:
        return
    if _captive_dns is None:
        _captive_dns = CaptiveDNS(ip=AP_IP, ip_getter=_dns_target_ip)
    try:
        _captive_dns.start()
    except Exception as e:
        print("Captive DNS start failed:", e)


def poll_captive_dns():
    """主迴圈輪詢 Captive DNS，避免 DNS 背景 thread 佔用 core1。"""
    if _captive_dns is None:
        return
    try:
        _captive_dns.poll()
    except Exception:
        pass


# =============== 可見 AP 掃描 ===============
# 說明：
# 掃描並回傳可見 AP，依 RSSI 由強到弱排序。
def scan_visible():
    """掃描 AP 並回傳已排序的可見清單（忽略空白 SSID）。"""
    try:
        if not wlan.active():
            wlan.active(True)
            time.sleep_ms(120)
    except Exception:
        pass
    raw = wlan.scan()
    filtered = []
    for ap in raw:
        ssid = (ap[0] or b"").decode("utf-8", "ignore").strip()
        if not ssid:
            continue
        filtered.append(ap)
    filtered.sort(key=lambda t: t[3], reverse=True)
    return filtered


# =============== STA 連線 ===============
# 說明：
# 以指定 SSID/密碼嘗試連線，於 timeout 內回報成功或失敗。
def connect_to_ap(ssid: str, psk: str, timeout_ms: int = CONNECT_TIMEOUT_MS) -> bool:
    """嘗試連線指定 AP，成功回 True，失敗回 False。"""
    try:
        # 避免無條件 disconnect 觸發 CYW43 ioctl timeout。
        wlan.active(True)
        try:
            if wlan.isconnected():
                wlan.disconnect()
                time.sleep_ms(80)
        except Exception:
            pass
        wlan.connect(ssid, psk)
        t0 = time.ticks_ms()
        while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
            time.sleep_ms(150)
    except Exception:
        try:
            wlan.disconnect()
        except Exception:
            pass
        return False
    ok = wlan.isconnected()
    if not ok:
        # 失敗後明確清理，避免殘留在連線中狀態影響 AP。
        try:
            wlan.disconnect()
        except Exception:
            pass
    return ok


def set_sta_enabled(enabled: bool) -> bool:
    """顯式開關 STA 介面，供開機流程穩定 AP 使用。"""
    try:
        if enabled:
            wlan.active(True)
        else:
            try:
                wlan.disconnect()
            except Exception:
                pass
        return True
    except Exception:
        return False


# =============== Wi-Fi 狀態讀取 ===============
# 說明：
# 回傳 STA/AP 狀態資訊供 Web UI 顯示。
def read_status():
    """取得連線狀態資訊，方便 UI 顯示。"""
    global _last_rssi, _last_rssi_ms
    ap_active = _ap_enabled
    try:
        ap_active = bool(ap.active())
    except Exception:
        ap_active = _ap_enabled

    info = {
        "active": False,
        "connected": False,
        "ifconfig": (),
        "rssi": None,
        "ap_active": ap_active,
        "ap_essid": _ap_config.get("essid", ""),
    }
    try:
        info["active"] = wlan.active()
        info["connected"] = wlan.isconnected()
        info["ifconfig"] = wlan.ifconfig()
        # CYW43 在高頻 ioctl 下容易 timeout，RSSI 改低頻快取查詢。
        if info["connected"]:
            now_ms = time.ticks_ms()
            if _last_rssi is None or time.ticks_diff(now_ms, _last_rssi_ms) >= RSSI_POLL_INTERVAL_MS:
                try:
                    _last_rssi = wlan.status("rssi")
                except Exception:
                    pass
                _last_rssi_ms = now_ms
            info["rssi"] = _last_rssi
        else:
            info["rssi"] = None
            _last_rssi = None
    except Exception:
        pass
    return info


# =============== 設定 AP 啟動 ===============
# 說明：
# 啟動內建 AP 作為設定入口，並同步確保 Captive DNS 啟動。
def start_config_ap(essid: str = "PicoSetup", password: str = "") -> bool:
    """啟動內建 AP 方便手機連線設定，失敗回 False。"""
    global _ap_enabled, _ap_config, _captive_dns
    # 可依需求在這裡加 channel=6 等參數讓 AP 頻道與家用 Wi-Fi 一致，減少切頻掉線
    for attempt in (1, 2):
        try:
            ap.active(False)
        except Exception:
            pass
        time.sleep_ms(120)

        try:
            ap.active(True)
            # 固定 AP 網段，避免不同韌體/歷史設定導致 IP 漂移。
            try:
                ap.ifconfig((AP_IP, AP_NETMASK, AP_GW, AP_DNS))
            except Exception:
                pass
            cfg = {"essid": essid}
            if password:
                # WPA2 密碼需 8 碼以上；若給空字串則開啟開放 AP。
                cfg["password"] = password
            ap.config(**cfg)
            # 實際驗證 AP 介面是否啟用。
            if not ap.active():
                raise RuntimeError("AP active() returned False")
            _ap_enabled = True
            _ap_config = {"essid": essid, "password": password}
            ap_ip = AP_IP
            try:
                ap_ip = ap.ifconfig()[0]
            except Exception:
                pass
            print("Config AP started:", essid, "IP=", ap_ip)
            _ensure_captive_dns()
            return True
        except Exception as e:
            print("start_config_ap attempt %d failed:" % attempt, e)
            _ap_enabled = False
            time.sleep_ms(200)
            continue
    return False


# =============== 設定 AP 停止 ===============
# 說明：
# 關閉內建 AP，更新本地狀態旗標。
def stop_config_ap() -> None:
    """關閉內建 AP。"""
    global _ap_enabled
    try:
        ap.active(False)
    except Exception:
        pass
    _ap_enabled = False


# =============== 設定 AP 重啟 ===============
# 說明：
# 使用目前保存的 AP 參數，先停後啟。
def restart_config_ap() -> bool:
    """重新啟動內建 AP（使用上次設定）。"""
    cfg = _ap_config or {"essid": "PicoSetup", "password": ""}
    stop_config_ap()
    time.sleep_ms(200)
    return start_config_ap(cfg.get("essid", "PicoSetup"), cfg.get("password", ""))


# =============== AP 設定套用 ===============
# 說明：
# 更新 AP 參數，並可選擇是否立即重啟 AP 生效。
def apply_ap_config(essid: str, password: str, restart: bool = True) -> bool:
    """更新 AP 設定並選擇是否立即重啟。"""
    global _ap_config
    _ap_config = {"essid": essid, "password": password}
    if restart:
        stop_config_ap()
        time.sleep_ms(200)
        return start_config_ap(essid, password)
    return True


# =============== Wi-Fi 子系統重置 ===============
# 說明：
# 重建 STA 狀態並視情況重啟 AP，常用於網路異常恢復。
def reset_wifi() -> bool:
    """重新初始化 Wi-Fi（STA/AP），避免驅動狀態異常。"""
    global _ap_enabled
    try:
        try:
            wlan.disconnect()
        except Exception:
            pass
        wlan.active(False)
        time.sleep_ms(200)
        wlan.active(True)
    except Exception:
        return False
    if _ap_enabled:
        restart_config_ap()
    _ensure_captive_dns()
    return True


# =============== AP 連線數查詢 ===============
# 說明：
# 回傳目前連到 AP 的 station 數量。
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


# =============== 等待 AP 有裝置連線 ===============
# 說明：
# 迴圈等待 station 數量達門檻，可指定 timeout。
def wait_for_station(min_count: int = 1, timeout_ms=None, poll_ms: int = 500) -> bool:
    """等待有裝置連上 AP；預設不超時。"""
    t0 = time.ticks_ms()
    while True:
        if ap_station_count() >= min_count:
            return True
        if timeout_ms is not None and time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
            return False
        time.sleep_ms(poll_ms)
