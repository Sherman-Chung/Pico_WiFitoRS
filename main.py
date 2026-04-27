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

from Server_CMD import start_cmd_server, poll_cmd_server
from Web_Page import start_http_server, poll_http_server
from wifi_Scan_Connect import (
    start_config_ap,
    wlan,
    connect_to_ap,
    read_status,
)
from mdns_service import MDNSResponder
from Pico_UPS import read_battery, last_battery_error
from config_store import get_config, update_config, reset_config
from modbus_gateway import start_modbus_tcp_server, poll_modbus_tcp_server
from poller import tick as poller_tick, set_enabled as poller_set_enabled
import register_store

_poller_thread_started = False
_last_cmd_check_ms = 0
_cmd_check_interval_ms = 100
_last_status_update_ms = 0
_status_update_interval_ms = 500
try:
    _cpu_temp_adc = machine.ADC(4)
except Exception:
    _cpu_temp_adc = None


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


def _read_reg(reg_index: int, default_value: int = 0) -> int:
    """讀取單一 Hold Register，失敗時回預設值。"""
    try:
        values = register_store.get_regs(reg_index, 1, decode=False)
        if values:
            return int(values[0]) & 0xFFFF
    except Exception as e:
        print("Hold Register read failed:", reg_index, e)
    return int(default_value) & 0xFFFF


def _write_reg(reg_index: int, value: int):
    """寫入單一 Hold Register（供開機流程修正控制位用）。"""
    ok, err = register_store.set_reg(reg_index, int(value), encode=False)
    if not ok:
        print("Hold Register write failed:", reg_index, err)
    return ok


def wait_for_network_stable(timeout_ms: int = 1500):
    """等待網路狀態穩定（STA 連線或 AP 啟用）。"""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        try:
            st = read_status()
            if st.get("connected") or st.get("ap_active"):
                return
        except Exception:
            pass
        time.sleep_ms(100)


def _read_cpu_temp_c():
    """讀取 CPU 溫度（整數攝氏）。"""
    if _cpu_temp_adc is None:
        return None
    try:
        raw = _cpu_temp_adc.read_u16()
        voltage = raw * (3.3 / 65535)
        temp_c = 27 - (voltage - 0.706) / 0.001721
        return int(temp_c)
    except Exception:
        return None


def update_hold_register_status(boot_ticks_ms: int):
    """更新 50-56 系統狀態寄存器。"""
    now_ms = time.ticks_ms()
    state = {
        "run_time": time.ticks_diff(now_ms, boot_ticks_ms) // 1000,
        "sta_connected": 1 if wlan.isconnected() else 0,
    }
    try:
        st = read_status()
        state["ap_active"] = 1 if st.get("ap_active") else 0
    except Exception:
        state["ap_active"] = 0

    temp_c = _read_cpu_temp_c()
    if temp_c is not None:
        state["cpu_temp"] = temp_c

    batt = read_battery()
    if batt:
        state["batt_v"] = int(max(0.0, batt.get("v", 0.0)) * 100)     # 0.01V
        state["batt_i"] = int(batt.get("i", 0.0) * 10000)              # 0.1mA
        state["batt_p"] = int(max(0.0, min(100.0, batt.get("p", 0.0))))

    register_store.update_status(state)


def _apply_config_from_register_memory(persist_to_flash: bool = False):
    """
    將 Hold Register 配置套用到 config_store，並立即重建 UART 參數。
    回傳 (ok, error_msg)。
    """
    current_cfg = get_config()
    new_cfg = register_store.export_config_from_memory(current_cfg)
    ok, err, applied_cfg = update_config(new_cfg, persist=persist_to_flash)
    if not ok:
        return False, err or "update config failed"

    # 立即套用 UART 參數（即使未立刻有通訊請求，也先使設定生效）
    try:
        import Pico_RS485 as rs485
        modbus_cfg = (applied_cfg or {}).get("modbus") or {}
        for ch in (0, 1):
            enabled = bool(modbus_cfg.get("ch0_enabled" if ch == 0 else "ch1_enabled", False))
            if not enabled:
                continue
            ch_cfg = (modbus_cfg.get("ch0") if ch == 0 else modbus_cfg.get("ch1")) or {}
            rs485.init(
                ch,
                baudrate=int(ch_cfg.get("baudrate") or 9600),
                parity=ch_cfg.get("parity") or "N",
                stopbits=int(ch_cfg.get("stopbits") or 1),
                bits=int(ch_cfg.get("bits") or 8),
            )
    except Exception as e:
        return False, "uart apply failed: " + str(e)

    return True, None


# =============== 主狀態機 ===============
# 說明：
# 系統總控流程：
# 1) 讀取設定與初始化 Hold Register
# 2) 先判斷並嘗試 STA
# 3) 根據 REG20(AP_ENABLED) 決定/強制啟 AP
# 4) 啟服務並執行系統檢查
# 5) 根據 REG21(POLLER_ENABLED) 決定 Core1 poller
def main():
    ap_started = False
    mdns = None
    # main() 以 while 迴圈維持：1) 輪詢 CMD/HTTP/Modbus TCP
    # 2) 更新 Hold Register 狀態
    # 3) 依設定執行 poller

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
    
    # 【新增】初始化 Hold Register 記憶體：從 Flash 配置讀入
    try:
        register_store.initialize_from_config(cfg)
        print("Hold Register memory initialized from config")
    except Exception as e:
        print("Hold Register init failed:", e)
    
    ap_cfg = cfg.get("ap") or {}
    ap_ssid = ap_cfg.get("ssid") or "PicoSetup"
    ap_pwd = ap_cfg.get("password") or "pico1234"

    sta_cfg = cfg.get("sta") or {}
    sta_ssid = sta_cfg.get("ssid") or ""
    sta_pwd = sta_cfg.get("password") or ""
    sta_configured = bool(sta_ssid)
    sta_connected = False

    # 先判斷並嘗試 STA
    if sta_configured:
        print("Trying saved STA:", sta_ssid)
        try:
            sta_connected = bool(connect_to_ap(sta_ssid, sta_pwd, timeout_ms=6000))
        except Exception as e:
            print("STA connect failed:", e)
            sta_connected = False
    else:
        print("STA not configured, skip STA connect")

    # 開機 AP 決策：完全由 REG20 控制
    ap_enable_reg = 1 if _read_reg(20, 1) else 0
    if sta_connected:
        if ap_enable_reg == 1:
            ap_started = start_config_ap(ap_ssid, ap_pwd)
            if ap_started:
                print("AP started (STA connected):", ap_ssid)
            else:
                print("Config AP failed to start")
        else:
            print("STA connected, AP disabled by REG20")
    else:
        if ap_enable_reg == 1:
            ap_started = start_config_ap(ap_ssid, ap_pwd)
            if ap_started:
                print("AP started (STA unavailable):", ap_ssid)
            else:
                print("Config AP failed to start")
        else:
            # STA 不可用時不允許 AP 關閉，強制拉起 AP 並修正 REG20
            print("STA unavailable + REG20=0, force enable AP")
            _write_reg(20, 1)
            ap_started = start_config_ap(ap_ssid, ap_pwd)
            if ap_started:
                print("AP force-started:", ap_ssid)
            else:
                print("Config AP failed to start")

    # 統一啟動網路服務（HTTP + Modbus TCP + CMD TCP + mDNS）
    start_network_services()
    maybe_start_mdns()

    # 等待網路狀態穩定
    wait_for_network_stable()

    # 進入主迴圈前先做一次模組檢查（失敗會停機閃燈）
    run_system_checks()

    # REG21 觸發式控制：
    # - 開機先同步一次目前 REG21 狀態
    # - 後續只在 REG21 被寫入時觸發，不做週期性監看
    poller_enabled = False
    poller_on_core1 = False
    watched_reg21_sources = {"boot_sync", "modbus_tcp_local", "web_poller", "web_cfg"}

    def on_reg21_written(reg_index: int, raw_value: int, source: str):
        nonlocal poller_enabled, poller_on_core1
        if source not in watched_reg21_sources:
            return
        reg21_enabled = bool(int(raw_value) & 0xFFFF)
        if reg21_enabled == poller_enabled:
            return
        poller_enabled = reg21_enabled
        poller_set_enabled(reg21_enabled)
        if reg21_enabled:
            poller_on_core1 = start_poller_worker()
            if poller_on_core1:
                print("Poller enabled by REG21 (core1)")
            else:
                print("Poller enabled by REG21 (core0 fallback)")
        else:
            print("Poller disabled by REG21")

    register_store.set_write_hook(21, on_reg21_written)
    on_reg21_written(21, _read_reg(21, 0), "boot_sync")

    boot_ticks_ms = time.ticks_ms()
    update_hold_register_status(boot_ticks_ms)

    # =============== 主迴圈事件處理 ===============
    global _last_cmd_check_ms, _last_status_update_ms

    while True:
        # 主迴圈順序原則：
        # 1) 先處理人機介面命令（CMD/HTTP）與 Hold Register 命令
        # 2) 再處理 Modbus gateway
        # 3) 更新系統狀態暫存器
        # 4) Poller：REG21=1 時執行（優先 Core1，失敗則退回單核心）

        poll_cmd_server()
        poll_http_server()
        poll_modbus_tcp_server()

        # 週期更新系統狀態至 Hold Register (50-56)
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, _last_status_update_ms) >= _status_update_interval_ms:
            _last_status_update_ms = now_ms
            update_hold_register_status(boot_ticks_ms)

        # 【新增】定期檢查 Hold Register 控制命令 (60-64)
        if time.ticks_diff(now_ms, _last_cmd_check_ms) >= _cmd_check_interval_ms:
            _last_cmd_check_ms = now_ms
            try:
                cmd_info = register_store.check_and_clear_command()
                cmd = cmd_info.get('cmd')
                
                if cmd == 'save':
                    # 保存命令：將記憶體設定寫回 Flash
                    print("[main] Execute: Save config to Flash")
                    register_store.set_command_status(1)  # busy
                    try:
                        ok, err = _apply_config_from_register_memory(persist_to_flash=True)
                        if ok:
                            register_store.set_command_status(2)  # success
                            print("[main] Config saved successfully")
                        else:
                            register_store.set_command_status(3)  # error
                            print("[main] Config save failed:", err)
                    except Exception as e:
                        register_store.set_command_status(3)  # error
                        print("[main] Config save exception:", e)

                elif cmd == 'apply':
                    # 套用命令：將 Hold Register 設定套用到 config 與 UART
                    print("[main] Execute: Apply config from REG64")
                    register_store.set_command_status(1)  # busy
                    try:
                        ok, err = _apply_config_from_register_memory(persist_to_flash=False)
                        if ok:
                            register_store.set_command_status(2)  # success
                            print("[main] Config applied successfully")
                        else:
                            register_store.set_command_status(3)  # error
                            print("[main] Config apply failed:", err)
                    except Exception as e:
                        register_store.set_command_status(3)  # error
                        print("[main] Config apply exception:", e)
                
                elif cmd == 'reset':
                    # 重置命令：清空配置並重啟
                    print("[main] Execute: Reset config")
                    register_store.set_command_status(1)  # busy
                    try:
                        reset_config()
                        register_store.set_command_status(2)  # success
                        print("[main] Config reset, rebooting...")
                        time.sleep_ms(500)
                        machine.reset()
                    except Exception as e:
                        register_store.set_command_status(3)  # error
                        print("[main] Config reset exception:", e)
                
                elif cmd == 'reboot':
                    # 重啟命令
                    print("[main] Execute: Reboot")
                    register_store.set_command_status(1)  # busy
                    try:
                        register_store.set_command_status(2)  # success
                        time.sleep_ms(500)
                        machine.reset()
                    except Exception as e:
                        register_store.set_command_status(3)  # error
                        print("[main] Reboot exception:", e)
                        
            except Exception as e:
                print("[main] Hold Register command check failed:", e)

        if poller_enabled and not poller_on_core1:
            poller_tick()
        # 小睡避免 CPU 忙等，20ms 約等同 50Hz 排程節奏
        time.sleep_ms(20)


# 進入點
if __name__ == "__main__":
    main()
