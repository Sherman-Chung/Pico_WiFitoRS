# main.py — Pico 2 W Gateway（UPS + RS485）
# 保留 AP/STA + Web + Modbus TCP + 輪詢表格，移除 LCD/按鍵 UI。

import time
import machine

try:
    from config import AUTO_CONFIG_AP_ON_BOOT
except ImportError:
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


# =============== 網路服務啟動 ===============
def start_network_services():
    """Wi-Fi 連上後開啟 TCP 與 HTTP 服務。"""
    try:
        cfg = get_config()
        mb_port = int((cfg.get("modbus") or {}).get("tcp_port") or 502)
        start_modbus_tcp_server(mb_port)
        start_cmd_server()
        start_http_server()
    except Exception as e:
        print("server start error:", e)


# =============== 致命錯誤處理 ===============
def fail_halt(reason: str):
    """檢查失敗時停機並閃 LED 提示。"""
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
def main():
    services_started = False
    ap_started = False
    mdns = None
    # main() 以 while 迴圈維持：1) 開啟 AP + Captive Portal 便於設定
    # 2) 持續輪詢 TCP/HTTP 伺服器與輪詢表格

    def maybe_start_mdns():
        nonlocal mdns
        if mdns is not None:
            return
        try:
            def _get_ip():
                try:
                    return wlan.ifconfig()[0]
                except Exception:
                    return "0.0.0.0"

            mdns = MDNSResponder(hostname="pico", ip_getter=_get_ip)
            mdns.start()
        except Exception as e:
            print("mDNS start failed:", e)

    cfg = get_config()
    ap_cfg = cfg.get("ap") or {}
    ap_ssid = ap_cfg.get("ssid") or "PicoSetup"
    ap_pwd = ap_cfg.get("password") or "pico1234"

    if AUTO_CONFIG_AP_ON_BOOT:
        # 預設開啟設定用 AP 以便手機立即連線；timeout 交由 wait_for_station 控制
        ap_started = start_config_ap(ap_ssid, ap_pwd)
        if ap_started:
            print("Config AP active:", ap_ssid)
            print("Open http://192.168.4.1 to configure Wi-Fi")
        else:
            print("Config AP failed to start")
        start_network_services()
        services_started = True
        maybe_start_mdns()

    sta_cfg = cfg.get("sta") or {}
    sta_ssid = sta_cfg.get("ssid") or ""
    sta_pwd = sta_cfg.get("password") or ""
    if sta_ssid and not wlan.isconnected():
        print("Trying saved STA:", sta_ssid)
        try:
            connect_to_ap(sta_ssid, sta_pwd)
        except Exception as e:
            print("STA connect failed:", e)

    # 進入主迴圈前先做一次模組檢查（失敗會停機閃燈）
    run_system_checks()

    if not ap_started:
        # 若未開 AP，補開一組，方便用 Web UI 配置
        ap_started = start_config_ap(ap_ssid, ap_pwd)
        if ap_started:
            print("Connect to AP", ap_ssid, "then open http://192.168.4.1")
        else:
            print("Config AP failed to start")
    if not services_started:
        start_network_services()
        services_started = True
        maybe_start_mdns()

    while True:
        # Headless 模式：輪詢網路服務與輪詢表格
        poll_cmd_server()
        poll_http_server()
        poll_modbus_tcp_server()
        poller_tick()
        time.sleep_ms(20)


# 進入點
if __name__ == "__main__":
    main()
