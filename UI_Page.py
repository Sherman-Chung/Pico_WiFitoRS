# UI_Page.py - LCD UI 畫面/狀態機
# 專責畫面繪製與 UI 狀態，主程式只需呼叫這些方法即可。

import time

from LCD_Control import (
    lcd,
    W,
    H,
    BLACK,
    WHITE,
    RED,
    GRAY,
    PINK,
    GREEN,
    ORANGE,
    YELLOW,
    BLUE,
    HL,
    fill_header,
    footer_clear,
    trim,
    draw_scrollbar,
    icon_arrow_left,
    icon_arrow_right,
    icon_cursor_right,
)
from wifi_Scan_Connect import (
    wlan,
    scan_visible,
    connect_to_ap,
    read_status,
    CONNECT_TIMEOUT_MS,
)
from Pico_UPS import read_battery, battery_gauge_text, tick_battery, last_battery_error

PAGE_ROWS = 10

# Connect Setup 網格設定（6 欄較能排下 A-Z）
KEYPAD_COLS = 6
CELL_W, CELL_H = 36, 22
GRID_START_X, GRID_START_Y = 12, 80

# 全域 UI 狀態
# scan_list: 原始掃描結果；visible_list: 依關鍵字或排序後的可見列表
# sel/first 控制目前選取與頁面偏移；mode 表示目前畫面狀態
scan_list = []
visible_list = []
sel = 0
first = 0
mode = "home"  # home | list | detail | connect | status
stack = []

# Connect Setup 狀態
connect_ssid = ""
psk_input = ""
keypad_idx = 0
keypad_page = 0

# 多頁鍵盤內容
KEYS_123 = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
KEYS_ABC = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
KEYS_abc = [chr(c) for c in range(ord("a"), ord("z") + 1)]
KEYS_SYM = [
    "!",
    "@",
    "#",
    "$",
    "%",
    "^",
    "&",
    "*",
    "(",
    ")",
    "-",
    "_",
    "=",
    "+",
    "[",
    "]",
    "{",
    "}",
    ";",
    ":",
    "'",
    '"',
    ",",
    ".",
    "<",
    ">",
    "/",
    "?",
    "\\",
    "|",
    "~",
    "`",
    " ",
]
KEYPAD_PAGES = [KEYS_123, KEYS_ABC, KEYS_abc, KEYS_SYM]

_last_gauge = ""


def refresh_battery_gauge(force: bool = False, commit: bool = False):
    """在標頭更新電量百分比；commit=True 立即 lcd.show()，避免頻繁閃爍。"""
    global _last_gauge
    gauge = battery_gauge_text()
    if not gauge:
        if _last_gauge:
            x = W - 60
            lcd.fill_rect(x, 0, 60, 22, BLUE)
            if commit:
                lcd.show()
            _last_gauge = ""
        return
    if not force and gauge == _last_gauge:
        return
    _last_gauge = gauge
    x = W - 60
    lcd.fill_rect(x, 0, 60, 22, BLUE)
    lcd.text(gauge, x + 4, 6, YELLOW)
    if commit:
        lcd.show()


def auth_mode_to_str(m):
    """轉換加密模式為易讀字串。"""
    return {0: "OPEN", 1: "WEP", 2: "WPA", 3: "WPA2", 4: "WPA/WPA2", 5: "WPA2-ENT"}.get(
        m, str(m)
    )


def fmt_bssid(b):
    """BSSID 轉字串。"""
    return ":".join("{:02X}".format(x) for x in b)


def show_home():
    """首頁提示按鍵用途。"""
    global mode
    mode = "home"
    fill_header("Home")
    refresh_battery_gauge(force=True, commit=False)
    lcd.text("Welcome!", 88, 90, BLACK)
    lcd.text("Press A to Scan Wi-Fi", 40, 110, BLACK)
    lcd.text("Press B to Show Status", 36, 130, BLACK)
    footer_clear()
    lcd.fill_rect(0, H - 20, W // 2, 20, PINK)
    icon_arrow_right(12, H - 10, BLACK)
    lcd.text("(A) Scan", 24, H - 16, BLACK)
    lcd.fill_rect(W // 2, H - 20, W // 2, 20, PINK)
    icon_arrow_right(W // 2 + 12, H - 10, BLACK)
    lcd.text("(B) Status", W // 2 + 24, H - 16, BLACK)
    lcd.show()


def do_scan():
    """掃描 Wi-Fi，僅保留有 SSID 的 AP。"""
    global scan_list, visible_list, sel, first, mode
    fill_header("Scanning...")
    refresh_battery_gauge(force=True, commit=False)
    lcd.text("Please wait", 6, 40, GRAY)
    lcd.show()
    try:
        print("Scan result:")
        filtered = scan_visible()
        for ap in filtered:
            ssid = (ap[0] or b"").decode("utf-8", "ignore").strip()
            rssi = ap[3]
            print(f"  SSID: {ssid}, RSSI: {rssi} dBm")
        # 將掃描結果存起來，visible_list 後續可被關鍵字或排序調整
        scan_list = filtered
        visible_list = scan_list[:]
        sel = 0
        first = 0
        mode = "list"
        render_list()  # 即時顯示結果（即使沒有 AP 也停留在列表頁）
    except Exception as e:
        scan_list = []
        visible_list = []
        sel = 0
        first = 0
        fill_header("Scan failed")
        lcd.text(str(e)[:30], 6, 60, RED)
        lcd.show()
        time.sleep_ms(1200)


def render_list():
    """顯示掃描結果列表；同時畫出游標與右側捲軸。"""
    global mode
    mode = "list"
    fill_header("Scan Results")
    refresh_battery_gauge(force=True, commit=False)
    y = 26
    row_h = 18
    page = visible_list[first : first + PAGE_ROWS]
    if not page:
        lcd.text("未找到 AP，按 A 重新掃描", 12, y, GRAY)
    else:
        for i, n in enumerate(page):
            idx = first + i
            ssid = (n[0] or b"").decode("utf-8", "ignore")
            if idx == sel:
                lcd.fill_rect(2, y - 2, W - 12, row_h, HL)
                icon_cursor_right(10, y + 6, BLACK)
            lcd.text(trim(ssid, 26), 20, y, BLACK)
            y += row_h
    draw_scrollbar(len(visible_list), first, PAGE_ROWS)
    footer_clear()
    lcd.fill_rect(0, H - 20, W // 2, 20, PINK)
    icon_arrow_left(12, H - 10, BLACK)
    lcd.text("(X) Back", 24, H - 16, BLACK)
    lcd.fill_rect(W // 2, H - 20, W // 2, 20, PINK)
    icon_arrow_right(W // 2 + 12, H - 10, BLACK)
    lcd.text("(B) Details", W // 2 + 24, H - 16, BLACK)
    lcd.show()


def move_selection(delta: int):
    """列表游標移動並重繪。"""
    global sel, first
    total = len(visible_list)
    if total == 0:
        return
    sel = max(0, min(total - 1, sel + delta))
    if sel < first:
        first = sel
    elif sel >= first + PAGE_ROWS:
        first = sel - (PAGE_ROWS - 1)
    render_list()


def show_detail():
    """顯示當前 AP 詳細資訊。"""
    global mode
    mode = "detail"
    print("show_detail: enter")
    if not visible_list:
        show_home()
        return
    n = visible_list[sel]
    ssid = (n[0] or b"").decode("utf-8", "ignore")
    bssid = fmt_bssid(n[1])
    ch = n[2]
    rssi = n[3]
    enc = auth_mode_to_str(n[4])
    hidden = n[5]
    fill_header("AP Details")
    refresh_battery_gauge(force=True, commit=False)
    y = 26
    lcd.text(f"SSID    : {ssid or '<hidden>'}", 6, y, BLACK)
    y += 18
    lcd.text(f"BSSID   : {bssid}", 6, y, BLACK)
    y += 18
    lcd.text(f"Channel : {ch}", 6, y, BLACK)
    y += 18
    lcd.text(f"RSSI    : {rssi} dBm", 6, y, BLACK)
    y += 18
    lcd.text(f"Security: {enc}", 6, y, BLACK)
    y += 18
    lcd.text(f"Hidden  : {bool(hidden)}", 6, y, BLACK)
    y += 18
    footer_clear()
    lcd.fill_rect(0, H - 20, W // 2, 20, PINK)
    icon_arrow_left(12, H - 10, BLACK)
    lcd.text("(X) Back", 24, H - 16, BLACK)
    lcd.show()


def show_connect_setup():
    """進入 Connect 設定畫面並重置輸入狀態。"""
    global mode, connect_ssid, psk_input, keypad_idx, keypad_page
    if not visible_list:
        render_list()
        return
    n = visible_list[sel]
    connect_ssid = (n[0] or b"").decode("utf-8", "ignore")
    psk_input = ""
    keypad_idx = 0
    keypad_page = 0
    mode = "connect"
    render_connect()


def current_page_keys():
    """取得目前頁面的鍵列表（含 PG/OK）。"""
    base = KEYPAD_PAGES[keypad_page][:]
    base += ["PG", "OK"]
    return base


def render_connect():
    """重繪 Connect Setup 畫面。"""
    fill_header("Connect Setup")
    refresh_battery_gauge(force=True, commit=False)
    y = 26
    lcd.text(f"SSID: {trim(connect_ssid, 20)}", 6, y, BLACK)
    masked = "*" * len(psk_input) if psk_input else "(empty)"
    lcd.text(f"PSK : {masked}", 6, y + 20, BLACK)
    page_name = ["0-9", "A-Z", "a-z", "@*!"][keypad_page]
    lcd.text(f"Page: {page_name}", 140, y + 20, GRAY)
    lcd.text("A: DEL   B: CLR", 6, y + 40, GRAY)

    keys = current_page_keys()
    for idx, label in enumerate(keys):
        col = idx % KEYPAD_COLS
        row = idx // KEYPAD_COLS
        x = GRID_START_X + col * CELL_W
        y = GRID_START_Y + row * CELL_H
        if idx == keypad_idx:
            # 目前游標所在位置加上高亮底色
            lcd.fill_rect(x + 1, y + 1, CELL_W - 2, CELL_H - 2, HL)
        lcd.rect(x + 1, y + 1, CELL_W - 2, CELL_H - 2, PINK)
        tx = x + (CELL_W // 2 - 4 if len(label) == 1 else CELL_W // 2 - 8)
        lcd.text(label, tx, y + 8, BLACK)

    footer_clear()
    lcd.fill_rect(0, H - 20, W // 2, 20, PINK)
    icon_arrow_left(12, H - 10, BLACK)
    lcd.text("(X) Back", 24, H - 16, BLACK)
    lcd.show()


def keypad_move(dx: int, dy: int):
    """在鍵盤網格中移動游標。"""
    global keypad_idx
    keys = current_page_keys()
    cols = KEYPAD_COLS
    rows = (len(keys) + cols - 1) // cols
    col = keypad_idx % cols
    row = keypad_idx // cols
    col = max(0, min(cols - 1, col + dx))
    row = max(0, min(rows - 1, row + dy))
    idx = row * cols + col
    if idx >= len(keys):
        return
    keypad_idx = idx
    render_connect()


def keypad_press(on_connected=None):
    """KeyCtrl：輸入字元；PG=切換頁；OK=嘗試連線。"""
    keys = current_page_keys()
    label = keys[keypad_idx]
    print("Input char:", label)
    if label == "OK":
        attempt_connect(on_connected)
        return
    if label == "PG":
        switch_keypad_page()
        return
    append_char(label)


def switch_keypad_page():
    """切換鍵盤頁面並維持索引有效。"""
    global keypad_page, keypad_idx
    keypad_page = (keypad_page + 1) % len(KEYPAD_PAGES)
    keys2 = current_page_keys()
    if keypad_idx >= len(keys2):
        keypad_idx = max(0, len(keys2) - 1)
    render_connect()


def append_char(ch: str):
    """加入一個 PSK 字元並重繪。"""
    global psk_input
    if len(psk_input) < 63:
        psk_input += ch
        render_connect()


def delete_char():
    """刪除最後一個 PSK 字元。"""
    global psk_input
    if psk_input:
        psk_input = psk_input[:-1]
        render_connect()


def clear_psk():
    """清空 PSK。"""
    global psk_input
    if psk_input:
        psk_input = ""
        render_connect()


def attempt_connect(on_connected=None):
    """嘗試連線，成功時可執行 on_connected 回呼；失敗會提示狀態碼。"""
    fill_header("Connecting...")
    lcd.text(f"SSID: {trim(connect_ssid, 20)}", 6, 46, BLACK)
    lcd.text("Please wait", 6, 66, GRAY)
    lcd.show()

    status_code = None
    try:
        status_code = wlan.status()
    except Exception:
        status_code = None

    success = connect_to_ap(connect_ssid, psk_input, CONNECT_TIMEOUT_MS)
    # 以 isconnected + status 判斷，避免半連線狀態誤判成功
    try:
        status_code = wlan.status()
    except Exception:
        pass
    if not (wlan.isconnected() and (status_code in (3, None))):
        success = False

    if success:
        if on_connected:
            try:
                on_connected()
            except Exception as e:
                print("server start error:", e)
        # 連線成功後切到狀態畫面，並把上一頁資訊推入 stack 方便返回
        stack.append("connect")
        show_status()
        return True

    # 失敗時提示並回到輸入畫面，附帶 status code 方便診斷
    fill_header("Connect failed")
    msg = "Check password or signal"
    if status_code is not None:
        msg += f" (status {status_code})"
    lcd.text(msg[:28], 12, 60, RED)
    if len(msg) > 28:
        lcd.text(msg[28:56], 12, 80, RED)
    lcd.show()
    time.sleep_ms(1200)
    render_connect()
    return False


def show_status():
    """顯示目前 Wi-Fi 連線狀態。"""
    global mode
    mode = "status"
    fill_header("Connection Status")
    refresh_battery_gauge(force=True, commit=False)
    y = 26
    # 電池資訊
    batt = read_battery(force=True)
    print("batt status:", batt, "err:", last_battery_error())
    if batt is not None:
        # 若無 UPS 模組則 batt 會是 None，保留空白避免亂數顯示
        lcd.text(f"Batt V : {batt['v']:.2f}V", 6, y, BLACK); y += 18
        lcd.text(f"Batt I : {batt['i']:.3f}A", 6, y, BLACK); y += 18
        lcd.text(f"Batt % : {batt['p']:.0f}%", 6, y, BLACK); y += 18
    else:
        # 無 UPS 模組或讀取失敗：不顯示電池資訊
        pass

    info = read_status()
    try:
        lcd.text(f"Active   : {info['active']}", 6, y, BLACK)
        y += 18
        lcd.text(f"Connected: {info['connected']}", 6, y, BLACK)
        y += 18
        if info["connected"]:
            ip, nm, gw, dns = info["ifconfig"]
            lcd.text(f"IP  : {ip}", 6, y, BLACK)
            y += 18
            lcd.text(f"GW  : {gw}", 6, y, BLACK)
            y += 18
            lcd.text(f"DNS : {dns}", 6, y, BLACK)
            y += 18
            if info["rssi"] is not None:
                lcd.text(f"RSSI: {info['rssi']} dBm", 6, y, BLACK)
                y += 18
        else:
            lcd.text("Not connected to any AP", 6, y, BLACK)
            y += 18
    except Exception as e:
        lcd.text("Failed to read status", 6, y, WHITE)
        y += 18
        lcd.text(str(e)[:30], 6, y, WHITE)
    footer_clear()
    lcd.fill_rect(0, H - 20, W // 2, 20, PINK)
    icon_arrow_left(12, H - 10, BLACK)
    lcd.text("(X) Back", 24, H - 16, BLACK)
    lcd.show()
