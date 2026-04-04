# main.py — Pico 2 W Gateway（UPS + RS485）
# 保留 AP/STA + Web + Modbus TCP + 輪詢表格，移除 LCD/按鍵 UI。
#
# 維護導讀：
# 1) 若要調整「開機順序 / 服務啟動時機」，主要看 main() 前半段。
# 2) 若要調整「主迴圈排程順序」，主要看 while True 區塊。
# 3) 若要新增新的常駐服務，建議放在 start_network_services() 並在主迴圈輪詢。

import time
import machine
try:
    import _thread
except Exception:
    _thread = None

try:
    from config import AUTO_CONFIG_AP_ON_BOOT
except ImportError:
    # 若缺少 config.py，採安全預設：先開 AP，確保可進設定頁
    AUTO_CONFIG_AP_ON_BOOT = True

from Server_CMD import start_cmd_server, poll_cmd_server
from Web_Page import start_http_server, poll_http_server
from wifi_Scan_Connect import (
    start_config_ap,
    wlan,
    connect_to_ap,
)
from mdns_service import MDNSResponder
from Pico_UPS import read_battery, last_battery_error
from config_store import get_config
from modbus_gateway import start_modbus_tcp_server, poll_modbus_tcp_server
from poller import tick as poller_tick

_poller_thread_started = False


def _poller_worker_loop():
    """Core1 專用：持續執行 poller.tick()。"""
    while True:
        try:
            poller_tick()
        except Exception as e:
            print("poller worker error:", e)
        # 低延遲輪詢，同時避免空轉吃滿 CPU
        time.sleep_ms(5)


def start_poller_worker():
    """啟動 Core1 輪詢工作；若不可用回傳 False。"""
    global _poller_thread_started
    if _poller_thread_started:
        return True
    if _thread is None:
        print("Poller worker unavailable (_thread missing), fallback single-core")
        return False
    try:
        _thread.start_new_thread(_poller_worker_loop, ())
        _poller_thread_started = True
        print("Poller worker started on core1")
        return True
    except Exception as e:
        print("Poller worker start failed:", e)
        return False


# =============== 網路服務啟動 ===============
# 說明：
# 負責啟動三個對外服務：
# 1) Modbus TCP（資料平面）
# 2) CMD TCP（維運指令）
# 3) HTTP（Web UI + API）
def start_network_services():
    try:
        cfg = get_config()
        mb_port = int((cfg.get("modbus") or {}).get("tcp_port") or 502)
        start_modbus_tcp_server(mb_port)
        start_cmd_server()
        start_http_server()
    except Exception as e:
        print("server start error:", e)


# =============== 致命錯誤處理 ===============
# 說明：
# 當開機檢查發現不可忽略的錯誤時，系統不再進入主迴圈，
# 透過 LED 持續閃爍提示「裝置需要人工處理」。
def fail_halt(reason: str):
    print("FATAL:", reason)
    try:
        led = machine.Pin("LED", machine.Pin.OUT)
    except Exception:
        led = None
    while True:
        if led:
            led.value(1)
        time.sleep_ms(250)
        if led:
            led.value(0)
        time.sleep_ms(250)


# =============== 系統檢查（開機一次） ===============
# 說明：
# 在系統進入正式服務前，先驗證關鍵硬體是否可用：
# - UPS/INA219 讀值是否正常
# - 已啟用的 RS485 通道是否可初始化
# 任一檢查失敗都會進入 fail_halt()。
def run_system_checks():
    """在進入主迴圈前檢查模組狀態（UPS/RS485），失敗則停機閃燈。"""
    print("=== System checks ===")
    errors = []

    try:
        batt = read_battery(force=True)
        if batt:
            print(
                "UPS/INA219 ok: %.3f V, %.3f A, ~%d%%"
                % (batt.get("v", 0), batt.get("i", 0), int(batt.get("p", 0)))
            )
        else:
            err = last_battery_error()
            msg = "UPS/INA219 not available" + (": " + err if err else "")
            print(msg)
            errors.append(msg)
    except Exception as e:
        msg = "UPS/INA219 check failed: " + str(e)
        print(msg)
        errors.append(msg)

    try:
        import Pico_RS485 as rs485
        from config_store import get_config

        cfg = get_config()
        modbus_cfg = cfg.get("modbus") or {}
        ch0_enabled = bool(modbus_cfg.get("ch0_enabled", False))
        ch1_enabled = bool(modbus_cfg.get("ch1_enabled", False))
        if not ch0_enabled and not ch1_enabled:
            print("RS485 disabled (CH0/CH1 both off)")
        if ch0_enabled:
            rs485.init(0)
            print("RS485 CH0 init ok")
        if ch1_enabled:
            rs485.init(1)
            print("RS485 CH1 init ok")
    except Exception as e:
        msg = "RS485 init failed: " + str(e)
        print(msg)
        errors.append(msg)

    if errors:
        # 嚴重錯誤直接鎖死，避免主迴圈持續運作卻沒有顯示或網路功能
        fail_halt(" | ".join(errors))


# =============== 主狀態機 ===============
# 說明：
# 系統總控流程：
# 1) 依設定決定先啟 AP/服務
# 2) 嘗試 STA 連線
# 3) 執行一次硬體檢查
# 4) 進入主迴圈固定輪詢各服務
def main():
    services_started = False
    ap_started = False
    mdns = None
    # main() 以 while 迴圈維持：1) 開啟 AP + Captive Portal 便於設定
    # 2) 持續輪詢 TCP/HTTP 伺服器與輪詢表格

    # --------------- 內部函式：mDNS 啟動守門 ---------------
    # 說明：
    # 避免重複啟動 mDNS（重複綁定 5353 會失敗）。
    # 由於 AP/STA IP 可能改變，透過 ip_getter 動態回報目前 IP。
    def maybe_start_mdns():
        """mDNS 僅啟動一次，避免重複綁定 5353。"""
        nonlocal mdns
        if mdns is not None:
            return
        try:
            # --------------- 內部函式：取得目前對外 IP ---------------
            # 說明：
            # mDNS 回覆時即時抓 wlan.ifconfig()[0]，避免 IP 變更後回舊位址。
            def _get_ip():
                try:
                    return wlan.ifconfig()[0]
                except Exception:
                    return "0.0.0.0"
            # 這裡啟動 mDNS，hostname 固定 "pico"，IP 由 _get_ip 動態提供
            mdns = MDNSResponder(hostname="pico", ip_getter=_get_ip)
            mdns.start()
        except Exception as e:
            print("mDNS start failed:", e)

    # 讀取可保存設定（AP/STA/Modbus/Poller）
    cfg = get_config()
    ap_cfg = cfg.get("ap") or {}
    ap_ssid = ap_cfg.get("ssid") or "PicoSetup"
    ap_pwd = ap_cfg.get("password") or "pico1234"

    # 根據 AUTO_CONFIG_AP_ON_BOOT 決定開機是否先啟 AP
    if AUTO_CONFIG_AP_ON_BOOT:
        # 若設定要求開機先啟 AP，則立即啟動，讓使用者可直接連 AP 進行 Wi-Fi 設定
        ap_started = start_config_ap(ap_ssid, ap_pwd)
        if ap_started:
            print("Config AP active:", ap_ssid)
            print("Open http://192.168.4.1 to configure Wi-Fi")
        else:
            print("Config AP failed to start")
        # 先啟服務（包含 mDNS），確保設定頁可用
        start_network_services()
        services_started = True
        maybe_start_mdns()

    sta_cfg = cfg.get("sta") or {}
    sta_ssid = sta_cfg.get("ssid") or ""
    sta_pwd = sta_cfg.get("password") or ""
    # 若有保存 STA，嘗試背景接入既有 Wi-Fi
    if sta_ssid and not wlan.isconnected():
        print("Trying saved STA:", sta_ssid)
        try:
            connect_to_ap(sta_ssid, sta_pwd)
        except Exception as e:
            print("STA connect failed:", e)

    # 進入主迴圈前先做一次模組檢查（失敗會停機閃燈）
    run_system_checks()

    # 保底：若前面未啟 AP，這裡補啟，確保仍可透過 AP 進設定頁
    if not ap_started:
        # 若未開 AP，補開一組，方便用 Web UI 配置
        ap_started = start_config_ap(ap_ssid, ap_pwd)
        if ap_started:
            print("Connect to AP", ap_ssid, "then open http://192.168.4.1")
        else:
            print("Config AP failed to start")
    # 保底：若前面未啟服務，這裡補啟
    if not services_started:
        start_network_services()
        services_started = True
        maybe_start_mdns()

    poller_on_core1 = start_poller_worker()

    while True:
        # 主迴圈順序原則：
        # 1) 先處理人機介面命令（CMD/HTTP）
        # 2) 再處理 Modbus gateway
        # 3) Poller 由 Core1 執行；若 Core1 啟動失敗則退回單核心
        poll_cmd_server()
        poll_http_server()
        poll_modbus_tcp_server()
        if not poller_on_core1:
            poller_tick()
        # 小睡避免 CPU 忙等，20ms 約等同 50Hz 排程節奏
        time.sleep_ms(20)


# 進入點
if __name__ == "__main__":
    main()
