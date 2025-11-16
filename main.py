# main_xy.py — Pico 2 W + Pico-LCD-1.3
# English UI; 中文備註；X/Y 鍵取代 Left/Right；隱藏 SSID 不列出；
# 實心箭頭；Connect Setup 支援「多頁鍵盤」：0-9 / A-Z / _ - .（PG 切換頁），A=DEL，B=CLR。

import time, network, rp2
from machine import Pin
from array import array

try:
    import pico_lcd_1_3 as lcd_mod
except ImportError as e:
    raise ImportError("LCD driver not found. Please rename 'Pico-LCD-1.3.py' to 'pico_lcd_1_3.py' and place it with main.py.") from e

# ======================== 參數與常數 ========================
COUNTRY     = "TW"
PAGE_ROWS   = 10
DEBOUNCE_MS = 160
CONNECT_TIMEOUT_MS = 12000

# 顏色（RGB565）
BLACK=0x0000
WHITE=0xFFFF
RED=0xF800
BLUE=0x001F
GRAY=0x8410
PINK=0xFFFE
HL=0xCFF9

# ======================== LCD 初始化 ========================
lcd = lcd_mod.LCD_1inch3()
W, H = 240, 240
_HAS_POLY = hasattr(lcd, "poly")

# ======================== 按鍵腳位（X/Y 取代左右） ========================
keyA    = Pin(15, Pin.IN, Pin.PULL_UP)  # Home: Scan；Connect: DEL
keyB    = Pin(17, Pin.IN, Pin.PULL_UP)  # Home: Status；Connect: CLR
keyX    = Pin(19, Pin.IN, Pin.PULL_UP)  # Back
keyY    = Pin(21, Pin.IN, Pin.PULL_UP)  # Details / OK（在 OK 上）
keyUP   = Pin(2,  Pin.IN, Pin.PULL_UP)  # Move Up
keyDN   = Pin(18, Pin.IN, Pin.PULL_UP)  # Move Down
keyLEFT = Pin(16, Pin.IN, Pin.PULL_UP)  # Move Left
keyRIGHT= Pin(20, Pin.IN, Pin.PULL_UP)  # Move Right
keyCTRL = Pin(3,  Pin.IN, Pin.PULL_UP)  # Connect: 輸入目前選取鍵

def pressed(p): return p.value()==0
def wait_release(p, timeout_ms=800):
    t0=time.ticks_ms()
    while pressed(p) and time.ticks_diff(time.ticks_ms(),t0)<timeout_ms:
        time.sleep_ms(5)

# ======================== Wi-Fi ========================
try: rp2.country(COUNTRY)
except: pass
wlan=network.WLAN(network.STA_IF); wlan.active(True)

# ======================== 全域狀態 ========================
scan_list=[]        # 原始掃描結果
visible_list=[]     # 過濾 hidden 後
sel=0; first=0
mode="home"         # "home"|"list"|"detail"|"status"|"connect"
stack=[]

# Connect Setup 狀態
connect_ssid=""
psk_input=""
keypad_idx=0         # 當前選取 cell 索引
keypad_page=0        # 0=digits, 1=letters, 2=symbols

# 多頁鍵盤內容（每頁結尾附加 'PG' 切換頁、'OK' 確認）
KEYS_123=["1","2","3","4","5","6","7","8","9","0"]
KEYS_ABC=["A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z"]
KEYS_abc=["a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q","r","s","t","u","v","w","x","y","z"]
KEYS_SYM=["!","@","#","$","%","^","&","*","(",")","-","_","=","+","[","]","{","}",";",":","'",'"',",",".","<",">","/","?","\\","|","~","`"," "]
KEYPAD_PAGES = [KEYS_123, KEYS_ABC, KEYS_abc, KEYS_SYM]

# 網格設定（6 欄較能排下 A-Z）；計算位置以置中為主
KEYPAD_COLS = 6
CELL_W, CELL_H = 36, 22           # 6*36=216 ≤ 240
GRID_START_X, GRID_START_Y = 12, 80

_last=0
def debounce():
    global _last
    now=time.ticks_ms()
    if time.ticks_diff(now,_last)<DEBOUNCE_MS: return False
    _last=now; return True

# ======================== 小工具 ========================
# def now_string():
#     try:
#         y,m,d,hh,mm,ss,_,_=time.localtime()
#         return f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}"
#     except:
#         return f"t+{time.ticks_ms()//1000}s"

# ======================== Header / Footer / Scrollbar ========================
def fill_header(title):
    lcd.fill(WHITE)
    lcd.fill_rect(0, 0, W, 22, BLUE)
    lcd.text(title, 6, 6, WHITE)

# ======================== Footer Clear ========================
def footer_clear(): lcd.fill_rect(0,H-20,W,20,WHITE)

# ======================== Trim String ========================
def trim(s,n): return s if len(s)<=n else (s[:max(0,n-1)]+'…')

# ======================== Scrollbar ========================
def draw_scrollbar(total, first_idx, page_size):
    if total<=page_size: return
    x=W-8; y0=44; h=H-y0-28
    lcd.rect(x,y0,6,h,GRAY)
    thumb_h=max(10,int(h*page_size/total))
    max_first=total-page_size
    thumb_y=y0+int((h-thumb_h)*(first_idx/max_first))
    lcd.fill_rect(x+1,thumb_y,4,thumb_h,GRAY)

def auth_mode_to_str(m): return {0:'OPEN',1:'WEP',2:'WPA',3:'WPA2',4:'WPA/WPA2',5:'WPA2-ENT'}.get(m,str(m))
def fmt_bssid(b): return ":".join("{:02X}".format(x) for x in b)

# ======================== 實心箭頭圖示 ========================
def icon_arrow_left(x,y,c):
    if _HAS_POLY: lcd.poly(0,0, array('h',[x+4,y-4, x+4,y+4, x-3,y]), c, True)
    else:
        for i in range(4): lcd.line(x+3,y-3+i,x-3,y,c); lcd.line(x+3,y+3-i,x-3,y,c)

def icon_arrow_right(x,y,c):
    if _HAS_POLY: lcd.poly(0,0, array('h',[x-4,y-4, x-4,y+4, x+3,y]), c, True)
    else:
        for i in range(4): lcd.line(x-3,y-3+i,x+3,y,c); lcd.line(x-3,y+3-i,x+3,y,c)

def icon_cursor_right(x,y,c):
    if _HAS_POLY: lcd.poly(0,0, array('h',[x-5,y-5, x-5,y+5, x+6,y]), c, True)
    else:
        for i in range(6): lcd.line(x-4,y-4+i,x+5,y,c); lcd.line(x-4,y+4-i,x+5,y,c)

# ======================== 畫面：Home ========================
def show_home():
    global mode; mode='home'
    fill_header('Home')
    lcd.text('Welcome!', 88, 90, BLACK)
    lcd.text('Press A to Scan Wi-Fi', 40, 110, BLACK)
    lcd.text('Press B to Show Status', 36, 130, BLACK)
    footer_clear()
    lcd.fill_rect(0,H-20,W//2,20,PINK); icon_arrow_right(12,H-10,BLACK); lcd.text('(A) Scan',24,H-16,BLACK)
    lcd.fill_rect(W//2,H-20,W//2,20,PINK); icon_arrow_right(W//2+12,H-10,BLACK); lcd.text('(B) Status',W//2+24,H-16,BLACK)
    lcd.show()

# ======================== 掃描 / 列表 ========================
def do_scan():
    global scan_list, visible_list, sel, first
    fill_header('Scanning...')
    lcd.text('Please wait', 6, 40, GRAY)
    lcd.show()
    try:
        # 掃描並即時列印結果（只顯示有 SSID 的）
        print("Scan result:")
        raw = wlan.scan()
        filtered = []
        for ap in raw:
            ssid = (ap[0] or b"").decode("utf-8", "ignore").strip()
            if not ssid:   # 跳過空白 SSID
                continue
            rssi = ap[3]
            print(f"  SSID: {ssid}, RSSI: {rssi} dBm")
            filtered.append(ap)

        # 排序並更新顯示清單
        scan_list = sorted(filtered, key=lambda t: t[3], reverse=True)
        visible_list = scan_list[:]   # 不再過濾 hidden
        sel = 0
        first = 0

    except Exception as e:
        scan_list = []
        visible_list = []
        sel = 0
        first = 0
        fill_header('Scan failed')
        lcd.text(str(e)[:30], 6, 60, RED)
        lcd.show()
        time.sleep_ms(1200)

# ======================== 畫面：List ========================
def render_list():
    """列表只顯示 SSID；X=Back、Y=Details、KeyCtrl=Connect"""
    global mode; mode='list'
    fill_header('Scan Results')
    y=26; row_h=18
    page=visible_list[first:first+PAGE_ROWS]
    for i,n in enumerate(page):
        idx=first+i; ssid=(n[0] or b'').decode('utf-8','ignore')
        if idx==sel: lcd.fill_rect(2,y-2,W-12,row_h,HL); icon_cursor_right(10,y+6,BLACK)
        lcd.text(trim(ssid,26), 20, y, BLACK); y+=row_h
    draw_scrollbar(len(visible_list), first, PAGE_ROWS)
    footer_clear()
    lcd.fill_rect(0,H-20,W//2,20,PINK); icon_arrow_left(12,H-10,BLACK); lcd.text('(X) Back',24,H-16,BLACK)
    lcd.fill_rect(W//2,H-20,W//2,20,PINK); icon_arrow_right(W//2+12,H-10,BLACK); lcd.text('(Y) Details',W//2+24,H-16,BLACK)
    lcd.show()

# ======================== 列表選取移動 ========================
def move_selection(d):
    global sel, first
    total=len(visible_list)
    if total==0: return
    sel=max(0,min(total-1, sel+d))
    if sel<first: first=sel
    elif sel>=first+PAGE_ROWS: first=sel-(PAGE_ROWS-1)
    render_list()

# ======================== 畫面：Detail ========================
def show_detail():
    global mode; mode='detail'
    if not visible_list: show_home(); return
    n=visible_list[sel]
    ssid=(n[0] or b'').decode('utf-8','ignore'); bssid=fmt_bssid(n[1]); ch=n[2]; rssi=n[3]; enc=auth_mode_to_str(n[4]); hidden=n[5]
    fill_header('AP Details')
    y=26
    lcd.text(f'SSID    : {ssid or "<hidden>"}',6,y,BLACK); y+=18
    lcd.text(f'BSSID   : {bssid}',6,y,BLACK); y+=18
    lcd.text(f'Channel : {ch}',6,y,BLACK); y+=18
    lcd.text(f'RSSI    : {rssi} dBm',6,y,BLACK); y+=18
    lcd.text(f'Security: {enc}',6,y,BLACK); y+=18
    lcd.text(f'Hidden  : {bool(hidden)}',6,y,BLACK); y+=18
    footer_clear()
    lcd.fill_rect(0,H-20,W//2,20,PINK); icon_arrow_left(12,H-10,BLACK); lcd.text('(X) Back',24,H-16,BLACK)
    lcd.show()

# ======================== 連線設定（多頁鍵盤） ========================
def show_connect_setup():
    """對目前選取 SSID 進入 Connect，支援數字/字母/符號頁（PG 切換）。"""
    global mode, connect_ssid, psk_input, keypad_idx, keypad_page
    if not visible_list: render_list(); return
    n=visible_list[sel]
    connect_ssid=(n[0] or b'').decode('utf-8','ignore')
    psk_input=''; keypad_idx=0; keypad_page=0
    mode='connect'; render_connect()

# ======================== Connect Setup 畫面繪製 ========================
def current_page_keys():
    """回傳目前頁面的鍵列表（附加 'PG' 與 'OK'）。"""
    base = KEYPAD_PAGES[keypad_page][:]
    base += ['PG','OK']
    return base

# ======================== Connect Setup 重繪 ========================
def render_connect():
    """重繪 Connect Setup（含多頁鍵盤）"""
    fill_header('Connect Setup')
    # 標題列：SSID 與遮蔽 PSK
    y=26
    lcd.text(f'SSID: {trim(connect_ssid,20)}',6,y,BLACK)
    masked='*'*len(psk_input) if psk_input else '(empty)'
    lcd.text(f'PSK : {masked}',6,y+20,BLACK)
    # 右上角：頁面提示與硬鍵提示
    page_name = ['0-9','A-Z', 'a-z','@*!'][keypad_page]
    lcd.text(f'Page: {page_name}', 140, y+20, GRAY)
    lcd.text('A: DEL   B: CLR', 6, y+40, GRAY)

    # 繪製鍵盤（6 欄網格）
    keys = current_page_keys()
    for idx,label in enumerate(keys):
        col = idx % KEYPAD_COLS
        row = idx // KEYPAD_COLS
        x = GRID_START_X + col*CELL_W
        y = GRID_START_Y + row*CELL_H
        if idx==keypad_idx:
            lcd.fill_rect(x+1,y+1,CELL_W-2,CELL_H-2,HL)
        lcd.rect(x+1,y+1,CELL_W-2,CELL_H-2,PINK)
        # 文字置中
        tx = x + (CELL_W//2 - 4 if len(label)==1 else CELL_W//2 - 8)
        lcd.text(label, tx, y + 8, BLACK)

    footer_clear()
    lcd.fill_rect(0,H-20,W//2,20,PINK); icon_arrow_left(12,H-10,BLACK); lcd.text('(X) Back',24,H-16,BLACK)
    lcd.show()

# ======================== Connect Setup 鍵盤操作 ========================
def keypad_move(dx,dy):
    """在目前頁面的鍵盤上移動游標（越界忽略）。"""
    global keypad_idx
    keys = current_page_keys()
    cols = KEYPAD_COLS; rows = (len(keys)+cols-1)//cols
    col = keypad_idx % cols; row = keypad_idx // cols
    col = max(0, min(cols-1, col+dx))
    row = max(0, min(rows-1, row+dy))
    idx = row*cols + col
    if idx >= len(keys): return
    keypad_idx = idx; render_connect()

# ======================== Connect Setup 鍵盤按下 ========================
def keypad_press():
    """KeyCtrl：輸入字元；PG=切換頁；OK=嘗試連線。"""
    global psk_input, keypad_page, keypad_idx
    keys = current_page_keys()
    label = keys[keypad_idx]
    print(f"Input char: {label}")
    if label == 'OK':
        attempt_connect(); return
    if label == 'PG':
        keypad_page = (keypad_page + 1) % len(KEYPAD_PAGES)
        # 切頁後確保索引在範圍內
        keys2 = current_page_keys()
        if keypad_idx >= len(keys2): keypad_idx = max(0, len(keys2)-1)
        render_connect(); return
    # 輸入一般字元
    if len(psk_input) < 63:
        psk_input += label
        render_connect()

# ======================== 嘗試連線 ========================
def attempt_connect():
    """嘗試連線，成功或失敗都顯示 Status。"""
    fill_header('Connecting...')
    lcd.text(f'SSID: {trim(connect_ssid,20)}',6,46,BLACK)
    lcd.text('Please wait',6,66,GRAY); lcd.show()
    try:
        try: wlan.disconnect()
        except: pass
        wlan.active(True); wlan.connect(connect_ssid, psk_input)
        t0=time.ticks_ms()
        while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(),t0)<CONNECT_TIMEOUT_MS:
            time.sleep_ms(150)
    except Exception as e:
        pass
    stack.append('connect'); show_status()

# ======================== 狀態畫面 ========================
def show_status():
    global mode; mode='status'
    fill_header('Connection Status')
    y=26
    try:
        info=wlan.ifconfig()
        lcd.text(f'Active   : {wlan.active()}',6,y,BLACK); y+=18
        lcd.text(f'Connected: {wlan.isconnected()}',6,y,BLACK); y+=18
        if wlan.isconnected():
            ip,nm,gw,dns=info
            lcd.text(f'IP  : {ip}',6,y,BLACK); y+=18
            lcd.text(f'GW  : {gw}',6,y,BLACK); y+=18
            lcd.text(f'DNS : {dns}',6,y,BLACK); y+=18
            try: rssi=wlan.status('rssi'); lcd.text(f'RSSI: {rssi} dBm',6,y,BLACK); y+=18
            except: pass
        else:
            lcd.text('Not connected to any AP',6,y,BLACK); y+=18
    except Exception as e:
        lcd.text('Failed to read status',6,y,RED); y+=18; lcd.text(str(e)[:30],6,y,RED)
    footer_clear()
    lcd.fill_rect(0,H-20,W//2,20,PINK); icon_arrow_left(12,H-10,BLACK); lcd.text('(X) Back',24,H-16,BLACK)
    lcd.show()

# ======================== 主狀態機 ========================
def main():
    global psk_input
    show_home()
    while True:
        # Home
        if mode=='home':
            if pressed(keyA) and debounce(): wait_release(keyA); do_scan(); render_list() if visible_list else show_home()
            if pressed(keyB) and debounce(): wait_release(keyB); stack.append('home'); show_status()

        # List
        if mode=='list':
            if pressed(keyUP) and debounce(): wait_release(keyUP); move_selection(-1)
            if pressed(keyDN) and debounce(): wait_release(keyDN); move_selection(+1)
            if pressed(keyX) and debounce(): wait_release(keyX); show_home()
            if pressed(keyY) and debounce(): wait_release(keyY); show_detail()
            if pressed(keyCTRL) and debounce(): wait_release(keyCTRL); show_connect_setup()

        # Detail
        if mode=='detail':
            if pressed(keyX) and debounce(): wait_release(keyX); render_list()

        # Connect (multi-page keypad)
        if mode=='connect':
            # X: Back to list
            if pressed(keyX) and debounce(): wait_release(keyX); render_list()
            # Arrow keys: move selection
            if pressed(keyUP) and debounce(): wait_release(keyUP); keypad_move(0,-1)
            if pressed(keyDN) and debounce(): wait_release(keyDN); keypad_move(0,+1)
            if pressed(keyLEFT) and debounce(): wait_release(keyLEFT); keypad_move(-1,0)
            if pressed(keyRIGHT) and debounce(): wait_release(keyRIGHT); keypad_move(+1,0)
            # KeyCtrl: type / PG / OK
            if pressed(keyCTRL) and debounce(): wait_release(keyCTRL); keypad_press()
            # Y: acts as OK if current is OK
            if pressed(keyY) and debounce():
                wait_release(keyY)
                keys = current_page_keys()
                if keypad_idx < len(keys) and keys[keypad_idx]=='OK':
                    attempt_connect()
            # A: DEL；B: CLR
            if pressed(keyA) and debounce():
                wait_release(keyA)
                if psk_input:
                    psk_input = psk_input[:-1]; render_connect()
            if pressed(keyB) and debounce():
                wait_release(keyB)
                if psk_input:
                    psk_input = ""; render_connect()

        # Status
        if mode=='status':
            if pressed(keyX) and debounce():
                wait_release(keyX)
                prev=stack.pop() if stack else 'home'
                if prev=='list': render_list()
                elif prev=='detail': show_detail()
                elif prev=='connect': render_connect()
                else: show_home()

        time.sleep_ms(15)

# 進入點
main()
