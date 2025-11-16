# main_xy.py — Pico 2 W + Pico-LCD-1.3
# English UI; 中文備註；X/Y 鍵取代 Left/Right；隱藏 SSID 不列出；
# 實心箭頭；Connect Setup 支援「多頁鍵盤」：0-9 / A-Z / _ - .（PG 切換頁），A=DEL，B=CLR。

import time, network, rp2, socket
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
KEYHOLD_MS = 600
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
keyY    = Pin(21, Pin.IN, Pin.PULL_UP)  # List / Connect: Enter
keyUP   = Pin(2,  Pin.IN, Pin.PULL_UP)  # Move Up
keyDN   = Pin(18, Pin.IN, Pin.PULL_UP)  # Move Down
keyLEFT = Pin(16, Pin.IN, Pin.PULL_UP)  # Move Left
keyRIGHT= Pin(20, Pin.IN, Pin.PULL_UP)  # Move Right
keyCTRL = Pin(3,  Pin.IN, Pin.PULL_UP)  # Connect: Hold 600ms is OK

# ======================== 按鍵按下檢查 ========================
def pressed(p): return p.value()==0

# ======================== 等待按鍵放開 ========================
def wait_release(p, timeout_ms=KEYHOLD_MS):
    t0=time.ticks_ms()
    while pressed(p) and time.ticks_diff(time.ticks_ms(),t0)<timeout_ms:
        time.sleep_ms(5)

# ======================== Wi-Fi ========================
try: rp2.country(COUNTRY)
except: pass
wlan=network.WLAN(network.STA_IF); wlan.active(True)

# 遠端指令伺服器設定
SERVER_PORT   = 12345      # 你可以改成自己喜歡的 port
server_sock   = None       # 之後會放 TCP 伺服器的 socket
# 
http_sock = None
HTTP_PORT = 8080   # 避免跟 80 衝突，你也可以改用 80


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
# ======================== 彈跳檢查 ========================
def debounce():
    global _last
    now=time.ticks_ms()
    if time.ticks_diff(now,_last)<DEBOUNCE_MS: return False# 未過去彈跳時間
    _last=now; return True

# ======================== 長按檢查 ========================
def KeyHold():
    global _last
    now=time.ticks_ms()
    if time.ticks_diff(now,_last)<KEYHOLD_MS: return False# 未過去彈跳時間
    _last=now; return True

# ======================== 小工具 ========================
# def now_string():
#     try:
#         y,m,d,hh,mm,ss,_,_=time.localtime()
#         return f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}"
#     except:
#         return f"t+{time.ticks_ms()//1000}s"

# ======================== Web UI (手機 / 瀏覽器控制台) ========================
WEB_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8" />
<title>Pico Modbus Gateway</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
  :root {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f5;
    color: #222;
  }
  body {
    margin: 0;
    padding: 0;
  }
  .wrap {
    max-width: 480px;
    margin: 0 auto;
    padding: 16px;
  }
  h1 {
    font-size: 20px;
    margin: 0 0 8px 0;
  }
  h2 {
    font-size: 16px;
    margin: 16px 0 8px 0;
  }
  .card {
    background: #ffffff;
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,.1);
  }
  .btn-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 4px;
  }
  button {
    flex: 1;
    min-width: 80px;
    padding: 8px 6px;
    border-radius: 999px;
    border: none;
    background: #007bff;
    color: #fff;
    font-size: 13px;
  }
  button.secondary {
    background: #6c757d;
  }
  button.danger {
    background: #dc3545;
  }
  button:active {
    opacity: 0.8;
  }
  label {
    display: block;
    font-size: 13px;
    margin-bottom: 4px;
  }
  input[type="text"], input[type="number"] {
    width: 100%;
    padding: 6px 8px;
    border-radius: 8px;
    border: 1px solid #ccc;
    font-size: 13px;
    box-sizing: border-box;
    margin-bottom: 6px;
  }
  #cmd-input {
    width: 100%;
    padding: 8px;
    border-radius: 8px;
    border: 1px solid #ccc;
    font-size: 13px;
    box-sizing: border-box;
  }
  #log {
    width: 100%;
    min-height: 150px;
    max-height: 260px;
    padding: 8px;
    border-radius: 8px;
    border: 1px solid #ccc;
    background: #111;
    color: #0f0;
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
    font-size: 12px;
    box-sizing: border-box;
    overflow-y: auto;
    white-space: pre-wrap;
  }
  .small {
    font-size: 11px;
    color: #666;
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>Pico Modbus Gateway</h1>
  <div class="small">透過 Wi-Fi 控制 Pico：SYS / LED / Modbus 指令。</div>

  <div class="card">
    <h2>快速操作</h2>
    <div class="btn-row">
      <button onclick="sendCmd('SYS STATUS')">SYS STATUS</button>
      <button onclick="sendCmd('SYS WIFI')">SYS WIFI</button>
    </div>
    <div class="btn-row">
      <button onclick="sendCmd('LED ON')">LED ON</button>
      <button onclick="sendCmd('LED OFF')">LED OFF</button>
    </div>
    <div class="btn-row">
      <button class="secondary" onclick="sendCmd('SYS HELP')">SYS HELP</button>
      <button class="secondary" onclick="sendCmd('SYS PING')">SYS PING</button>
    </div>
  </div>

  <div class="card">
    <h2>Modbus 指令（HR 範例）</h2>
    <label>Slave ID</label>
    <input type="number" id="mb-slave" value="1" min="1" max="247" />
    <label>Address (起始位址)</label>
    <input type="number" id="mb-addr" value="0" min="0" />
    <label>Count (讀取筆數)</label>
    <input type="number" id="mb-count" value="2" min="1" />
    <div class="btn-row">
      <button onclick="mbReadHR()">MB R HR</button>
    </div>
    <label>Write Value</label>
    <input type="number" id="mb-value" value="1234" />
    <div class="btn-row">
      <button class="danger" onclick="mbWriteHR()">MB W HR</button>
    </div>
    <div class="small">實際格式：MB R HR &lt;slave&gt; &lt;addr&gt; &lt;count&gt; / MB W HR &lt;slave&gt; &lt;addr&gt; &lt;value&gt;</div>
  </div>

  <div class="card">
    <h2>自訂指令</h2>
    <input id="cmd-input" type="text" placeholder="例如：SYS STATUS 或 MB R HR 1 0 3" />
    <div class="btn-row">
      <button onclick="sendCmdFromInput()">送出</button>
      <button class="secondary" onclick="clearLog()">清除 Log</button>
    </div>
  </div>

  <div class="card">
    <h2>回應 Log</h2>
    <div id="log"></div>
  </div>

</div>

<script>
  function appendLog(line) {
    var log = document.getElementById('log');
    var now = new Date();
    var ts = now.toLocaleTimeString();
    log.textContent += '[' + ts + '] ' + line + '\\n';
    log.scrollTop = log.scrollHeight;
  }

  function sendCmd(cmd) {
    appendLog('> ' + cmd);

    var xhr = new XMLHttpRequest();
    xhr.onreadystatechange = function() {
      if (xhr.readyState === 4) {
        var text = xhr.responseText || '';
        appendLog('< ' + text.trim());
      }
    };
    xhr.open('POST', '/cmd', true);
    xhr.setRequestHeader('Content-Type', 'text/plain');
    xhr.send(cmd);
  }

  function sendCmdFromInput() {
    var inp = document.getElementById('cmd-input');
    var cmd = inp.value.trim();
    if (!cmd) return;
    sendCmd(cmd);
  }

  function clearLog() {
    document.getElementById('log').textContent = '';
  }

  function mbReadHR() {
    var slave = document.getElementById('mb-slave').value || '1';
    var addr  = document.getElementById('mb-addr').value  || '0';
    var cnt   = document.getElementById('mb-count').value || '1';
    var cmd = 'MB R HR ' + slave + ' ' + addr + ' ' + cnt;
    sendCmd(cmd);
  }

  function mbWriteHR() {
    var slave = document.getElementById('mb-slave').value || '1';
    var addr  = document.getElementById('mb-addr').value  || '0';
    var val   = document.getElementById('mb-value').value || '0';
    var cmd = 'MB W HR ' + slave + ' ' + addr + ' ' + val;
    sendCmd(cmd);
  }

  window.onload = function() {
    appendLog('Web UI ready');
  };
</script>
</body>
</html>
"""


# ======================== HTTP 伺服器（未使用） ========================
def start_http_server():
    global http_sock
    addr = socket.getaddrinfo('0.0.0.0', HTTP_PORT)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.0)   # 非阻塞
    http_sock = s
    print("HTTP server listening on", addr)

# ======================== 非阻塞檢查 HTTP 伺服器 ========================
def poll_http_server():
    """非阻塞 HTTP 伺服器：支援
       - GET  / 或 /index.html → 回 Web UI
       - POST /cmd             → Body 是一條指令字串
    """
    global http_sock
    if http_sock is None:
        return

    try:
        cl, addr = http_sock.accept()
    except OSError:
        # 沒有 client，直接回主迴圈
        return

    print("HTTP client from", addr)

    try:
        # 設一個較寬鬆的 timeout
        cl.settimeout(5)

        # ---------- 第 1 階段：讀 header ----------
        req = b""
        while True:
            chunk = cl.recv(512)
            if not chunk:
                break
            req += chunk
            if b"\r\n\r\n" in req:
                break

        if not req:
            cl.close()
            return

        # 分開 head / body（目前 body 可能還不完整）
        head, sep, body = req.partition(b"\r\n\r\n")

        # 解析第一行
        try:
            first_line = head.split(b"\r\n", 1)[0].decode()
            print("HTTP first line:", first_line)
            method, path, _ = first_line.split(" ", 2)
        except Exception as e:
            print("HTTP parse error:", e)
            resp = "HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n\r\nBad Request"
            cl.send(resp.encode())
            cl.close()
            return

        # 解析 Content-Length
        content_length = 0
        for line in head.split(b"\r\n")[1:]:
            line_low = line.lower()
            if line_low.startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip() or b"0")
                except:
                    content_length = 0
                break

        # ---------- 第 2 階段：如果是 POST，補讀完整 body ----------
        if method == "POST" and content_length > len(body):
            need = content_length - len(body)
            while need > 0:
                chunk = cl.recv(512)
                if not chunk:
                    break
                body += chunk
                need -= len(chunk)

        # ======= 1) Web UI: GET / 或 GET /index.html =======
        if method == "GET" and (path == "/" or path.startswith("/index")):
            try:
                body_out = WEB_PAGE
            except Exception as e:
                body_out = "<html><body>Error loading page</body></html>"

            # 必須用 bytes 長度
            body_bytes = body_out.encode("utf-8")

            hdr = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=UTF-8\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            cl.send(hdr.encode())
            cl.send(body_bytes)
            return

        # ======= 2) 指令 API: POST /cmd =======
        if method == "POST" and path == "/cmd":
            cmd_str = body.decode("utf-8", "ignore").strip()
            print("HTTP cmd:", repr(cmd_str))  # 多印 repr，好 debug 空白 / 編碼
            result = handle_cmd(cmd_str)
            body_out = result + "\n"
            body_bytes = body_out.encode("utf-8")
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; charset=UTF-8\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            cl.send(resp.encode())
            cl.send(body_bytes)
        else:
            # 其他路徑：404
            resp = (
                "HTTP/1.1 404 Not Found\r\n"
                "Content-Type: text/plain\r\n"
                "Connection: close\r\n"
                "\r\nNot Found"
            )
            cl.send(resp.encode())

    except OSError as e:
        print("poll_http_server error:", e)
    finally:
        cl.close()

# ======================== 指令解析核心 ========================
def handle_cmd(cmd: str) -> str:
    """核心指令解析：SYS / MB 兩大類。"""
    cmd = cmd.strip()
    if not cmd:
        return "ERR EMPTY"

    parts = cmd.split()
    name  = parts[0].upper()
    args  = parts[1:]

    # ---------- SYS 類 ----------
    if name == "SYS":
        if not args:
            return "ERR SYS ARG"
        sub = args[0].upper()

        # SYS STATUS
        if sub == "STATUS":
            try:
                ip, nm, gw, dns = wlan.ifconfig()
                return f"OK SYS STATUS \nIP={ip} \nNETMASK={nm} \nGW={gw} \nDNS={dns}"
            except Exception as e:
                return "ERR SYS STATUS " + str(e)[:60]

        # SYS WIFI
        elif sub == "WIFI":
            try:
                active   = wlan.active()
                conn     = wlan.isconnected()
                ip, nm, gw, dns = wlan.ifconfig()
                try:
                    rssi = wlan.status('rssi')
                except:
                    rssi = None
                return f"OK SYS WIFI \nACTIVE={active} \nCONNECTED={conn} \nIP={ip} \nRSSI={rssi}"
            except Exception as e:
                return "ERR SYS WIFI " + str(e)[:60]

        # SYS PING
        elif sub == "PING":
            return "OK SYS PING"

        # SYS HELP
        elif sub == "HELP":
            return "OK SYS CMDS: \nSYS STATUS \nSYS WIFI \nSYS PING \nSYS HELP \nSYS MB R/W HR \nSYS COIL \nSYS LED ON/OFF"

        else:
            return "ERR SYS UNKNOWN " + args[0]

    # ---------- LED（保留你現有的） ----------
    elif name == "LED":
        if not args:
            return "ERR LED ARG"
        sub = args[0].upper()
        if sub == "ON":
            Pin("LED", Pin.OUT).value(1)
            return "OK LED=ON"
        elif sub == "OFF":
            Pin("LED", Pin.OUT).value(0)
            return "OK LED=OFF"
        else:
            return "ERR LED " + args[0]

    # ---------- Modbus：MB 類 ----------
    elif name == "MB":
        if len(args) < 4:
            return "ERR MB ARG"

        rw   = args[0].upper()   # R or W
        area = args[1].upper()   # HR / COIL / ...
        try:
            slave = int(args[2])
            addr  = int(args[3])
        except ValueError:
            return "ERR MB NUM"

        # MB R HR <slave> <addr> <count>
        if rw == "R" and area == "HR":
            if len(args) < 5:
                return "ERR MB RHR ARG"
            try:
                count = int(args[4])
            except ValueError:
                return "ERR MB RHR NUM"

            # ★ 未來這裡接你的 Modbus 讀取函式 ★
            # values = mb_read_holding(slave, addr, count)
            # DEMO: 先回假數據
            values = [1234 + i for i in range(count)]
            vals_str = " ".join(str(v) for v in values)
            return f"OK MB R HR {slave} {addr} {vals_str}"

        # MB W HR <slave> <addr> <value>
        elif rw == "W" and area == "HR":
            if len(args) < 5:
                return "ERR MB WHR ARG"
            try:
                value = int(args[4])
            except ValueError:
                return "ERR MB WHR NUM"

            # ★ 未來這裡接你的 Modbus 寫入函式 ★
            # mb_write_holding(slave, addr, value)
            return f"OK MB W HR {slave} {addr} {value}"

        # 其他 MB 功能（COIL 等）可以慢慢加：
        # elif rw == "R" and area == "COIL": ...
        else:
            return f"ERR MB UNSUPPORTED {rw} {area}"

    # ---------- 傳統指令兼容 ----------
    elif name == "STATUS":
        # 舊指令，轉到 SYS STATUS
        return handle_cmd("SYS STATUS")

    else:
        return "ERR UNKNOWN CMD: " + cmd

# ======================= 遠端指令伺服器 ========================
def start_cmd_server():
    global server_sock
    print("start_cmd_server: begin")
    addr = socket.getaddrinfo('0.0.0.0', SERVER_PORT)[0][-1]
    s = socket.socket()
    # 如果這行炸掉，就會印出錯誤
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.0)
    server_sock = s
    print("start_cmd_server: listening on", addr)


# ======================== 非阻塞檢查遠端指令 ========================
def poll_cmd_server():
    """非阻塞檢查是否有遠端連線，有的話收一筆指令並回覆。"""
    global server_sock
    if server_sock is None:
        return

    try:
        # server_sock 是非阻塞的：沒有 client 會直接丟 OSError，我們就 return
        cl, addr = server_sock.accept()
    except OSError:
        # 沒有連線進來
        return

    print("client connected from", addr)

    try:
        # 對這個「已經連進來的 client」改成「阻塞 + timeout」
        cl.settimeout(30)  # 最多等 3 秒（秒）
        data = cl.recv(1024)
        print("recv raw:", data)
        if not data:
            print("no data, closing client")
            cl.close()
            return
        cmd = data.decode("utf-8", "ignore")
        print("cmd:", cmd)
        resp = handle_cmd(cmd) + "\n"
        print("resp:", resp)
        cl.send(resp.encode("utf-8"))
    except OSError as e:
        print("poll_cmd_server recv/send error:", e)
    finally:
        cl.close()

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
    lcd.fill_rect(W//2,H-20,W//2,20,PINK); icon_arrow_right(W//2+12,H-10,BLACK); lcd.text('(B) Details',W//2+24,H-16,BLACK)
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
    #stack.append('connect'); show_status()
        # === 如果已連線，就啟動 TCP + HTTP 伺服器 ===
    if wlan.isconnected():
        try:
            start_cmd_server()    # TCP 指令伺服器（12345）
            start_http_server()   # HTTP 伺服器（8080）
        except Exception as e:
            print("server start error:", e)

    stack.append('connect')
    show_status()


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
        # === A + B 同時按住 2 秒 → 重啟 ===
        if pressed(keyA) and pressed(keyB):
            t0 = time.ticks_ms()
            while pressed(keyA) and pressed(keyB):
                if time.ticks_diff(time.ticks_ms(), t0) >= 2000:
                    lcd.fill(BLACK)
                    lcd.text("Rebooting...", 60, 110, WHITE)
                    lcd.show()
                    time.sleep_ms(300)
                    import machine
                    machine.reset()
                time.sleep_ms(20)
        
        # 先處理所有「網路服務」
        poll_cmd_server()   # TCP 字串
        poll_http_server()  # HTTP /cmd
        # 未來再加 MQTT check_msg()

        # Home
        if mode=='home':
            if pressed(keyA) and debounce(): wait_release(keyA); do_scan(); render_list() if visible_list else show_home()
            if pressed(keyB) and debounce(): wait_release(keyB); stack.append('home'); show_status()

        # List
        if mode=='list':
            if pressed(keyUP) and debounce(): wait_release(keyUP); move_selection(-1)
            if pressed(keyDN) and debounce(): wait_release(keyDN); move_selection(+1)
            if pressed(keyX) and debounce(): wait_release(keyX); show_home()
            if pressed(keyB) and debounce(): wait_release(keyB); show_detail()
            #if pressed(keyCTRL) and debounce(): wait_release(keyCTRL); show_connect_setup()
            if pressed(keyY) and debounce(): wait_release(keyY); show_connect_setup()

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
            # Y: type / PG / OK
            if pressed(keyY) and debounce(): wait_release(keyY); keypad_press()# 输入字元或操作
            # Ctrl: acts as OK if current is OK
            if pressed(keyCTRL) and KeyHold():
                wait_release(keyCTRL) # 按住直到放開
                # keys = current_page_keys()# 取得目前頁面的鍵列表
                # if keypad_idx < len(keys) and keys[keypad_idx]=='OK':# 如果目前選的是 OK 鍵
                attempt_connect()# 嘗試連線
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
