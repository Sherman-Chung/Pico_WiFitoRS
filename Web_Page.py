# Web_Page.py - 提供內建 Web UI 與簡易 HTTP 伺服器
# HTTP 伺服器會回傳內嵌的控制頁面，並透過 POST /cmd 呼叫指令處理器。

import socket
import json
import time
from Server_CMD import handle_cmd as default_handler
from wifi_Scan_Connect import (
    scan_visible,
    connect_to_ap,
    read_status,
    start_config_ap,
)

HTTP_PORT = 80
http_sock = None
_cmd_handler = default_handler

# 網頁內容與原本 main.py 相同，便於手機/瀏覽器遠端操控
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
    <h2>Wi-Fi 設定（無 LCD 時使用）</h2>
    <div class="small">1) 手機連上 Pico 的 AP（預設：PicoSetup / 密碼 pico1234）</div>
    <div class="small">2) 點「掃描可用 AP」選擇 SSID，輸入密碼並送出</div>
    <div class="btn-row" style="margin-top:6px;">
      <button onclick="refreshStatus()">更新狀態</button>
      <button onclick="refreshScan()">掃描可用 AP</button>
    </div>
    <div id="wifi-status" class="small"></div>
    <label style="margin-top:8px;">選擇可用 SSID</label>
    <select id="wifi-ssid" style="width:100%;padding:8px;border-radius:8px;border:1px solid #ccc;">
      <option value="">(尚未掃描)</option>
    </select>
    <label>密碼（若為開放網路可留空）</label>
    <input type="text" id="wifi-psk" placeholder="Wi-Fi Password" />
    <div class="btn-row">
      <button onclick="connectWifi()">送出連線</button>
    </div>
    <div id="wifi-msg" class="small"></div>
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
    refreshStatus();
    refreshScan();
  };

  function refreshStatus() {
    fetch('/wifi/status')
      .then(r => r.json())
      .then(d => {
        var txt = [];
        txt.push('STA connected: ' + d.connected + (d.ip ? ' / IP ' + d.ip : ''));
        if (d.rssi !== null && d.rssi !== undefined) txt.push('RSSI ' + d.rssi + ' dBm');
        txt.push('AP active: ' + d.ap_active + (d.ap_essid ? ' (' + d.ap_essid + ')' : ''));
        document.getElementById('wifi-status').textContent = txt.join(' | ');
      })
      .catch(() => {
        document.getElementById('wifi-status').textContent = '無法取得狀態';
      });
  }

  function refreshScan() {
    var sel = document.getElementById('wifi-ssid');
    sel.innerHTML = '<option>掃描中...</option>';
    fetch('/wifi/scan')
      .then(r => r.json())
      .then(d => {
        sel.innerHTML = '';
        var list = d.aps || [];
        if (!list.length) {
          sel.innerHTML = '<option value=\"\">找不到 AP</option>';
          return;
        }
        list.forEach(ap => {
          var opt = document.createElement('option');
          opt.value = ap.ssid;
          opt.textContent = ap.ssid + ' (' + ap.rssi + 'dBm, ' + ap.auth + ')';
          sel.appendChild(opt);
        });
      })
      .catch(() => {
        sel.innerHTML = '<option value=\"\">掃描失敗</option>';
      });
  }

  function connectWifi() {
    var ssid = document.getElementById('wifi-ssid').value;
    var psk = document.getElementById('wifi-psk').value;
    var msg = document.getElementById('wifi-msg');
    if (!ssid) { msg.textContent = '請先選擇 SSID'; return; }
    msg.textContent = '連線中...';
    fetch('/wifi/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid: ssid, psk: psk })
    })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          msg.textContent = '連線成功，IP: ' + (d.ip || '(取得中)');
        } else {
          msg.textContent = '連線失敗：' + (d.error || 'unknown');
        }
        refreshStatus();
      })
      .catch(() => {
        msg.textContent = '連線請求失敗';
      });
  }
</script>
</body>
</html>
"""


def start_http_server(cmd_handler=default_handler):
    """啟動非阻塞 HTTP 伺服器，預設使用 Server_CMD.handle_cmd。"""
    global http_sock, _cmd_handler
    _cmd_handler = cmd_handler
    addr = socket.getaddrinfo("0.0.0.0", HTTP_PORT)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(4)
    s.settimeout(0.0)
    http_sock = s
    print("HTTP server listening on", addr)


def poll_http_server():
    """非阻塞 HTTP：GET / 回頁面；POST /cmd 交由指令處理器。"""
    global http_sock
    if http_sock is None:
        return

    try:
        cl, addr = http_sock.accept()
    except OSError:
        return
    if not cl:
        return

    print("HTTP client from", addr)

    def send_all(buf: bytes):
        """確保資料送出完畢，避免部分瀏覽器顯示空白；分段重送直到全部送出或出錯。"""
        mv = memoryview(buf)
        total = len(mv)
        sent = 0
        while sent < total:
            try:
                n = cl.send(mv[sent:])
            except OSError as e:
                print("HTTP send error:", e)
                break
            if not n:
                break
            sent += n

    try:
        cl.settimeout(5)
        req = b""
        start_ts = time.time()
        while (b"\r\n\r\n" not in req and b"\n\n" not in req) and (time.time() - start_ts) < 5:
            try:
                chunk = cl.recv(512)
            except OSError as e:
                print("HTTP recv header error:", e)
                return
            if not chunk:
                break
            req += chunk

        if not req:
            return

        head, sep, body = req.partition(b"\r\n\r\n")

        try:
            first_line = head.split(b"\r\n", 1)[0].decode()
            method, path, _ = first_line.split(" ", 2)
            print("HTTP request:", method, path)
        except Exception as e:
            print("HTTP parse error:", e)
            resp = (
                "HTTP/1.1 400 Bad Request\r\n"
                "Content-Type: text/plain\r\n"
                "Content-Length: 11\r\n"
                "Connection: close\r\n"
                "\r\nBad Request"
            )
            send_all(resp.encode())
            return

        content_length = 0
        for line in head.split(b"\r\n")[1:]:
            line_low = line.lower()
            if line_low.startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip() or b"0")
                except Exception:
                    content_length = 0
                break

        if method == "POST" and content_length > len(body):
            need = content_length - len(body)
            start_body_ts = time.time()
            while need > 0 and (time.time() - start_body_ts) < 5:
                try:
                    chunk = cl.recv(512)
                except OSError as e:
                    print("HTTP recv body error:", e)
                    break
                if not chunk:
                    break
                body += chunk
                need -= len(chunk)

        def send_json(obj, status="200 OK"):
            body_bytes = json.dumps(obj).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status}\r\n"
                "Content-Type: application/json; charset=UTF-8\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            send_all(resp.encode())
            send_all(body_bytes)

        # ======= Web UI: GET / =======
        if method == "GET" and (path == "/" or path.startswith("/index")):
            body_bytes = WEB_PAGE.encode("utf-8")
            hdr = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=UTF-8\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            send_all(hdr.encode())
            send_all(body_bytes)
            return

        # ======= Wi-Fi API =======
        if method == "GET" and path == "/wifi/scan":
            aps = []
            try:
                for ap in scan_visible():
                    ssid = (ap[0] or b"").decode("utf-8", "ignore").strip()
                    aps.append({"ssid": ssid, "rssi": ap[3], "auth": ap[4]})
            except Exception as e:
                send_json({"aps": [], "error": str(e)[:80]}, status="500 Internal Server Error")
                return
            send_json({"aps": aps})
            return

        if method == "GET" and path == "/wifi/status":
            st = read_status()
            ip = ""
            try:
                ip = st.get("ifconfig", ("", ""))[0]
            except Exception:
                ip = ""
            send_json(
                {
                    "connected": st.get("connected", False),
                    "ip": ip,
                    "rssi": st.get("rssi"),
                    "ap_active": st.get("ap_active", False),
                    "ap_essid": st.get("ap_essid", ""),
                }
            )
            return

        if method == "POST" and path == "/wifi/connect":
            payload = {}
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                try:
                    txt = body.decode("utf-8", "ignore")
                    for part in txt.split("&"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            payload[k] = v
                except Exception:
                    payload = {}
            ssid = payload.get("ssid") or ""
            psk = payload.get("psk") or payload.get("password") or ""
            if not ssid:
                send_json({"ok": False, "error": "missing ssid"}, status="400 Bad Request")
                return
            ok = connect_to_ap(ssid, psk)
            st = read_status()
            ip = ""
            try:
                ip = st.get("ifconfig", ("", ""))[0]
            except Exception:
                ip = ""
            if ok:
                send_json({"ok": True, "ip": ip})
            else:
                send_json({"ok": False, "error": "connect failed"})
            return

        # ======= 指令 API: POST /cmd =======
        if method == "POST" and path == "/cmd":
            cmd_str = body.decode("utf-8", "ignore").strip()
            print("HTTP cmd:", repr(cmd_str))
            handler = _cmd_handler or default_handler
            result = handler(cmd_str)
            body_bytes = (result + "\n").encode("utf-8")
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; charset=UTF-8\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            send_all(resp.encode())
            send_all(body_bytes)
            return

        # ======= 瀏覽器自動請求的圖示，回空白避免噪音 =======
        if method == "GET" and (
            path.startswith("/favicon.ico")
            or path.startswith("/apple-touch-icon.png")
            or path.startswith("/apple-touch-icon-precomposed.png")
        ):
            resp = (
                "HTTP/1.1 204 No Content\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            send_all(resp.encode())
            return

        # ======= 未知路徑：回主頁 =======
        body_bytes = WEB_PAGE.encode("utf-8")
        hdr = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=UTF-8\r\n"
            f"Content-Length: {len(body_bytes)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        send_all(hdr.encode())
        send_all(body_bytes)
    except OSError as e:
        print("poll_http_server error:", e)
    finally:
        try:
            cl.close()
        except Exception:
            pass
