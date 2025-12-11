# main.py — Pico 2 W + Pico-LCD-1.3 重構版
# 將功能拆分成 LCD_Control / wifi_Scan_Connect / Server_CMD / Button_Control / Web_Page / UI_Page
# 方便後續維護與擴充：每個模組皆附中文註解，主程式專注於狀態機與事件分派。

import time
import machine

try:
    from config import FORCE_HEADLESS, AUTO_CONFIG_AP_ON_BOOT
except ImportError:
    FORCE_HEADLESS = False
    AUTO_CONFIG_AP_ON_BOOT = True

from Button_Control import (
    keyA,
    keyB,
    keyX,
    keyY,
    keyUP,
    keyDN,
    keyLEFT,
    keyRIGHT,
    keyCTRL,
    pressed,
    wait_release,
    debounce,
    key_hold,
)

# 若舊版 LCD_Control 無 LCD_AVAILABLE，改為 fallback 防止 ImportError
try:
    from LCD_Control import lcd, BLACK, WHITE, LCD_AVAILABLE
except ImportError:
    from LCD_Control import lcd, BLACK, WHITE
    LCD_AVAILABLE = False
import UI_Page as ui
from Server_CMD import start_cmd_server, poll_cmd_server
from Web_Page import start_http_server, poll_http_server
from wifi_Scan_Connect import start_config_ap, wait_for_station, ap_station_count, wlan
from mdns_service import MDNSResponder
from Pico_UPS import read_battery, last_battery_error


# =============== 網路服務啟動 ===============
def start_network_services():
    """Wi-Fi 連上後開啟 TCP 與 HTTP 服務。"""
    try:
        start_cmd_server()
        start_http_server()
    except Exception as e:
        print("server start error:", e)


# =============== 重啟功能 ===============
def reboot_when_ab_held(show_ui: bool = True):
    """A+B 同時按住 2 秒觸發重啟。"""
    if pressed(keyA) and pressed(keyB):
        t0 = time.ticks_ms()
        while pressed(keyA) and pressed(keyB):
            if time.ticks_diff(time.ticks_ms(), t0) >= 2000:
                # 進入真正重啟前畫面提示，避免誤會程式當掉
                if show_ui and LCD_AVAILABLE:
                    lcd.fill(BLACK)
                    lcd.text("Rebooting...", 60, 110, WHITE)
                    lcd.show()
                time.sleep_ms(300)
                machine.reset()
            time.sleep_ms(20)


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
def run_system_checks(headless: bool):
    """在進入主迴圈前檢查模組狀態（LCD/UPS/RS485），失敗則停機閃燈。"""
    print("=== System checks ===")
    errors = []

    if LCD_AVAILABLE and not getattr(lcd, "_is_dummy", False):
        print("LCD detected: ready")
    else:
        msg = "LCD not detected (headless mode)"
        print(msg)
        if not headless:
            errors.append(msg)

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

        rs485.init(0)
        print("RS485 CH0 init ok")
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
    # 2) 依是否有 LCD 進入 UI 或 headless 迴圈
    # 3) 持續輪詢 TCP/HTTP 伺服器與按鍵事件

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

    if AUTO_CONFIG_AP_ON_BOOT:
        # 預設開啟設定用 AP 以便手機立即連線；timeout 交由 wait_for_station 控制
        ap_started = start_config_ap("PicoSetup", "pico1234")
        if ap_started:
            print("Config AP active: PicoSetup (pwd: pico1234)")
            print("Open http://192.168.4.1 to configure Wi-Fi")
            print("Waiting for phone to connect to AP...")
            connected = wait_for_station(min_count=1, timeout_ms=None, poll_ms=500)
            if connected:
                print("Device connected to AP. Starting servers...")
                print("Current STA count:", ap_station_count())
            else:
                print("No device connected; continuing without servers.")
        else:
            print("Config AP failed to start")
        start_network_services()
        services_started = True
        maybe_start_mdns()

    headless = FORCE_HEADLESS or (not LCD_AVAILABLE) or getattr(lcd, "_is_dummy", False)
    # 進入主迴圈前先做一次模組檢查（失敗會停機閃燈）
    run_system_checks(headless)

    if headless:
        print("LCD module not detected; UI disabled.")
        if not ap_started:
            # 若未開 AP，進入 headless 模式時再補開一組，方便用 Web UI 配置
            ap_started = start_config_ap("PicoSetup", "pico1234")
            if ap_started:
                print("Connect to AP PicoSetup (pwd: pico1234) then open http://192.168.4.1")
                if not services_started:
                    print("Waiting for phone to connect to AP...")
                    wait_for_station(min_count=1, timeout_ms=None, poll_ms=500)
                    print("Device connected to AP. Starting servers...")
            else:
                print("Config AP failed to start")
        if not services_started:
            start_network_services()
            services_started = True
            maybe_start_mdns()
        while True:
            reboot_when_ab_held(show_ui=False)
            poll_cmd_server()
            poll_http_server()
            time.sleep_ms(200)

    # 開機先嘗試檢查 UPS/電量模組狀態並更新一次抬頭電量
    ui.tick_battery(force=True)
    ui.show_home()
    ui.refresh_battery_gauge(force=True, commit=True)
    maybe_start_mdns()
    while True:
        # UI 模式：每輪更新電量 → 處理按鍵（依 mode 切換頁面）→ 輪詢網路服務
        ui.tick_battery()
        # 電量有變化時才 commit，減少閃爍
        ui.refresh_battery_gauge(commit=True)
        reboot_when_ab_held()

        # 處理網路服務
        poll_cmd_server()
        poll_http_server()

        if ui.mode == "home":
            if pressed(keyA) and debounce():
                wait_release(keyA)
                ui.do_scan()  # do_scan 內部會設定 mode 並切到列表頁
            if pressed(keyB) and debounce():
                wait_release(keyB)
                ui.stack.append("home")
                ui.show_status()

        if ui.mode == "list":
            if pressed(keyUP) and debounce():
                wait_release(keyUP)
                ui.move_selection(-1)
            if pressed(keyDN) and debounce():
                wait_release(keyDN)
                ui.move_selection(+1)
            if pressed(keyX) and debounce():
                wait_release(keyX)
                ui.show_home()
            if pressed(keyB) and debounce():
                wait_release(keyB)
                ui.show_detail()
            if pressed(keyY) and debounce():
                wait_release(keyY)
                ui.show_connect_setup()

        if ui.mode == "detail":
            if pressed(keyX) and debounce():
                wait_release(keyX)
                ui.render_list()

        if ui.mode == "connect":
            if pressed(keyX) and debounce():
                wait_release(keyX)
                ui.render_list()
            if pressed(keyUP) and debounce():
                wait_release(keyUP)
                ui.keypad_move(0, -1)
            if pressed(keyDN) and debounce():
                wait_release(keyDN)
                ui.keypad_move(0, +1)
            if pressed(keyLEFT) and debounce():
                wait_release(keyLEFT)
                ui.keypad_move(-1, 0)
            if pressed(keyRIGHT) and debounce():
                wait_release(keyRIGHT)
                ui.keypad_move(+1, 0)
            if pressed(keyY) and debounce():
                wait_release(keyY)
                ui.keypad_press(start_network_services)
            if pressed(keyCTRL) and key_hold():
                wait_release(keyCTRL)
                ui.attempt_connect(start_network_services)
            if pressed(keyA) and debounce():
                wait_release(keyA)
                ui.delete_char()
            if pressed(keyB) and debounce():
                wait_release(keyB)
                ui.clear_psk()

        if ui.mode == "status":
            if pressed(keyX) and debounce():
                wait_release(keyX)
                prev = ui.stack.pop() if ui.stack else "home"
                if prev == "list":
                    ui.render_list()
                elif prev == "detail":
                    ui.show_detail()
                elif prev == "connect":
                    ui.render_connect()
                else:
                    ui.show_home()

        time.sleep_ms(15)


# 進入點
if __name__ == "__main__":
    main()
