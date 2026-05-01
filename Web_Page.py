# Web_Page.py - 提供內建 Web UI 與簡易 HTTP 伺服器
# HTTP 伺服器會回傳內嵌的控制頁面，並透過 POST /cmd 呼叫指令處理器。
#
# 維護導讀：
# - 前半段 WEB_PAGE 為前端頁面字串，修改 UI 主要在此區。
# - 後半段 poll_http_server() 為 API 路由核心，修改後端行為看這裡。
# - 新增 API 時：在前端 fetch 與後端 route 分支同步加上。

import socket
import json
import time
from Server_CMD import handle_cmd as default_handler
from wifi_Scan_Connect import (
    scan_visible,
    connect_to_ap,
    read_status,
    apply_ap_config,
    stop_config_ap,
)
from config_store import get_config, update_config
from Pico_UPS import read_battery, power_source_text
import register_store
import log_buffer

HTTP_PORT = 80
HTTP_IO_TIMEOUT_S = 1.5
http_sock = None
_cmd_handler = default_handler


def _is_sock_timeout(err: Exception) -> bool:
    """判斷是否為 socket timeout/暫時無資料。"""
    code = None
    try:
        if getattr(err, "args", None):
            code = err.args[0]
    except Exception:
        code = None
    return code in (11, 35, 110, 116, 119)


def _stage_gateway_modbus_to_registers(modbus: dict):
    """
    將 Gateway 頁面的 Modbus/UART 參數寫入 Hold Register 配置區。
    只寫入暫存記憶體，不直接更新 config_store。
    """
    mb = modbus or {}
    ch0 = mb.get("ch0") or {}
    ch1 = mb.get("ch1") or {}
    tcp_rs485_mode = (mb.get("tcp_rs485_mode") or "").strip().lower()
    if tcp_rs485_mode not in ("disabled", "ch0", "ch1"):
        tcp_rs485_mode = "ch0" if bool(mb.get("tcp_indirect_control", False)) else "disabled"

    writes = [
        (0, 502),  # UI 固定 502
        (1, int(mb.get("tcp_slave_id") or 1)),
        (2, int(mb.get("response_timeout_ms") or 1200) // 10),
        (3, tcp_rs485_mode),
        (5, 1 if bool(mb.get("ch0_enabled", True)) else 0),
        (6, ch0.get("mode") or "rtu"),
        (7, int(ch0.get("baudrate") or 9600)),
        (8, ch0.get("parity") or "N"),
        (9, int(ch0.get("stopbits") or 1)),
        (10, int(ch0.get("bits") or 8)),
        (11, 1 if bool(mb.get("ch1_enabled", True)) else 0),
        (12, ch1.get("mode") or "rtu"),
        (13, int(ch1.get("baudrate") or 9600)),
        (14, ch1.get("parity") or "N"),
        (15, int(ch1.get("stopbits") or 1)),
        (16, int(ch1.get("bits") or 8)),
    ]

    for reg_idx, value in writes:
        ok, err = register_store.set_reg(reg_idx, value, encode=True)
        if not ok:
            return False, "REG%d: %s" % (reg_idx, err or "write failed")

    return True, None


def _sync_poller_config_to_registers(poller: dict, source: str = "web_cfg"):
    """
    將 Web 更新後的 Poller 設定同步到 REG21/REG22。
    REG64 會從 Hold Register 重建 config；若 REG22 沒同步，Gateway 設定會覆蓋回舊間隔。
    """
    poller = poller or {}
    if "enabled" in poller:
        reg21_val = 1 if bool(poller.get("enabled")) else 0
        ok, err = register_store.set_reg(21, reg21_val, encode=False, source=source)
        if not ok:
            return False, err or "write REG21 failed"
    if "interval_ms" in poller:
        try:
            interval_reg = int(poller.get("interval_ms") or 1000) // 10
        except Exception:
            return False, "poller interval invalid"
        if interval_reg < 5:
            interval_reg = 5
        ok, err = register_store.set_reg(22, interval_reg, encode=False, source=source)
        if not ok:
            return False, err or "write REG22 failed"
    return True, None

# 網頁內容與原本 main.py 相同，便於手機/瀏覽器遠端操控
WEB_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8" />
<title>Pico Modbus Gateway</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
  :root {
    color: #1b1c1f;
    --ink: #1b1c1f;
    --muted: #5a5f69;
    --accent: #0b6e4f;
    --accent-2: #0f8f6a;
    --paper: #f5f2ea;
    --card: #ffffff;
    --edge: rgba(0,0,0,0.08);
    font-family: "Trebuchet MS", "Gill Sans", "Lucida Grande", sans-serif;
    background: radial-gradient(120% 120% at 10% 0%, #f6f0de 0%, #f2f7f5 40%, #eef1f7 100%);
  }
  body {
    margin: 0;
    padding: 0;
  }
  .wrap {
    max-width: 860px;
    margin: 0 auto;
    padding: 18px;
  }
  h1 {
    margin: 0 0 6px 0;
    font-size: 26px;
    letter-spacing: 0.3px;
  }
  h2 {
    margin: 0 0 10px 0;
    font-size: 16px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
  }
  .sub {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 14px;
  }
  .grid {
    display: grid;
    gap: 12px;
    grid-template-columns: 1fr;
  }
  @media (min-width: 980px) {
    .grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .span-2 {
    grid-column: 1 / -1;
  }
  .log-card {
    min-height: 300px;
  }
  .card {
    background: var(--card);
    border-radius: 14px;
    padding: 14px;
    box-shadow: 0 12px 28px rgba(0,0,0,0.08);
    border: 1px solid var(--edge);
  }
  .row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  @media (max-width: 520px) {
    .row {
      grid-template-columns: 1fr;
    }
  }
  label {
    display: block;
    font-size: 12px;
    margin-bottom: 4px;
    color: var(--muted);
  }
  input, select, textarea {
    width: 100%;
    padding: 8px 10px;
    border-radius: 10px;
    border: 1px solid #d2d6dc;
    font-size: 13px;
    box-sizing: border-box;
    background: #fbfbfd;
    color: var(--ink);
  }
  input[type="checkbox"] {
    width: auto;
    padding: 0;
    border: none;
    background: transparent;
    box-shadow: none;
  }
  .checkline {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 0;
    line-height: 1.35;
    cursor: pointer;
  }
  .checkline input[type="checkbox"] {
    width: 18px;
    height: 18px;
    margin: 0;
    flex: 0 0 18px;
  }
  select {
    background: linear-gradient(180deg, #f7f8fb 0%, #eceff4 100%);
    border-color: #cfd4db;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.8);
  }
  input:disabled, select:disabled, textarea:disabled {
    color: #9aa1ad;
    background: #f1f3f6;
  }
  textarea {
    min-height: 120px;
  }
  .btn-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
  button {
    padding: 8px 12px;
    border-radius: 999px;
    border: none;
    background: var(--accent);
    color: #fff;
    font-size: 13px;
    cursor: pointer;
  }
  button.secondary {
    background: #505a66;
  }
  button.ghost {
    background: transparent;
    border: 1px solid var(--accent);
    color: var(--accent);
  }
  button.danger {
    background: #b7372f;
  }
  .pill {
    display: inline-block;
    padding: 4px 8px;
    border-radius: 999px;
    background: #ecf4f0;
    color: var(--accent);
    font-size: 11px;
    margin-right: 6px;
  }
  .log {
    width: 100%;
    min-height: 150px;
    max-height: 260px;
    padding: 8px;
    border-radius: 10px;
    border: 1px solid #222;
    background: #0c1216;
    color: #58f5b3;
    font-family: "Courier New", monospace;
    font-size: 12px;
    box-sizing: border-box;
    overflow-y: auto;
    white-space: pre-wrap;
  }
  .note {
    font-size: 11px;
    color: var(--muted);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    table-layout: fixed;
  }
  th, td {
    border-bottom: 1px solid #e1e4ea;
    padding: 6px 4px;
    text-align: left;
  }
  th:nth-child(6),
  td:nth-child(6) {
    width: 110px;
  }
  td:nth-child(6) {
    font-size: 13px;
  }
  th {
    color: var(--muted);
    font-weight: 600;
  }
  .mini {
    padding: 4px 6px;
    font-size: 11px;
  }
  .mono {
    font-family: "Courier New", monospace;
  }
  #poll-comm {
    white-space: pre-wrap;
    line-height: 1.35;
  }
  #wifi-status {
    white-space: pre-wrap;
    line-height: 1.35;
  }
</style>
</head>
<body>
  <div class="wrap">
  <!-- 頁首與專案簡介 -->
  <h1>Pico Modbus Gateway</h1>
  <div class="sub">2-Ports Modbus RTU/ASCII → 1-Port Modbus TCP | AP 點對點連線</div>

  <div class="grid">
    <!-- Gateway 參數設定 -->
    <div class="card">
      <h2>Gateway 設定</h2>
      <div class="row">
        <div>
          <!-- Modbus TCP 監聽 Port，固定 502 -->
          <label>Modbus TCP Port (固定)</label>
          <input id="mb-port" type="number" value="502" disabled />
        </div>
        <div>
          <!-- Gateway 回覆等待時間 -->
          <label>Response Timeout (ms)</label>
          <input id="mb-timeout" type="number" value="1200" min="100" />
        </div>
      </div>
      <div class="row" style="margin-top:10px;">
        <div>
          <!-- 本地 TCP Slave ID -->
          <label>TCP Slave ID（讀取本地 Registers）</label>
          <input id="tcp-slave-id" class="gw-field" type="number" value="1" min="1" max="247" />
        </div>
        <div></div>
      </div>
      <div class="row" style="margin-top:10px;">
        <div>
          <label>TCP 轉送模式（REG3）</label>
          <select id="tcp-rs485-mode" class="gw-field">
            <option value="disabled">disabled（TCP→Register Map）</option>
            <option value="ch0">ch0（TCP→RS485 CH0）</option>
            <option value="ch1">ch1（TCP→RS485 CH1）</option>
          </select>
        </div>
        <div></div>
      </div>
      <div class="row" style="margin-top:10px;">
        <div>
          <label class="checkline">
            <!-- 啟用/停用 CH0 -->
            <input type="checkbox" id="ch0-enabled" class="gw-field" checked />
            啟用 CH0
          </label>
        </div>
        <div>
          <label class="checkline">
            <!-- 啟用/停用 CH1 -->
            <input type="checkbox" id="ch1-enabled" class="gw-field" checked />
            啟用 CH1
          </label>
        </div>
      </div>
      <div class="note">通道啟用後才參與通訊；未列出 Unit ID 的請求不會轉送。</div>

      <div class="row" style="margin-top:10px;">
        <div>
          <div class="pill">CH0</div>
          <!-- CH0 通訊模式 RTU/ASCII -->
          <label>模式</label>
          <select id="ch0-mode" class="gw-field">
            <option value="rtu">RTU</option>
            <option value="ascii">ASCII</option>
          </select>
          <!-- CH0 通訊速率 -->
          <label>Baudrate</label>
          <select id="ch0-baud" class="gw-field">
            <option value="2400">2400</option>
            <option value="4800">4800</option>
            <option value="9600">9600</option>
            <option value="38400">38400</option>
            <option value="115200">115200</option>
          </select>
          <!-- CH0 Parity -->
          <label>Parity</label>
          <select id="ch0-parity" class="gw-field">
            <option value="N">None</option>
            <option value="E">Even</option>
            <option value="O">Odd</option>
          </select>
          <!-- CH0 Stop Bits -->
          <label>Stop Bits</label>
          <select id="ch0-stop" class="gw-field">
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
          <!-- CH0 Data Bits -->
          <label>Data Bits</label>
          <select id="ch0-bits" class="gw-field">
            <option value="8">8</option>
            <option value="7">7</option>
          </select>
        </div>
        <div>
          <div class="pill">CH1</div>
          <!-- CH1 通訊模式 RTU/ASCII -->
          <label>模式</label>
          <select id="ch1-mode" class="gw-field">
            <option value="rtu">RTU</option>
            <option value="ascii">ASCII</option>
          </select>
          <!-- CH1 通訊速率 -->
          <label>Baudrate</label>
          <select id="ch1-baud" class="gw-field">
            <option value="2400">2400</option>
            <option value="4800">4800</option>
            <option value="9600">9600</option>
            <option value="38400">38400</option>
            <option value="115200">115200</option>
          </select>
          <!-- CH1 Parity -->
          <label>Parity</label>
          <select id="ch1-parity" class="gw-field">
            <option value="N">None</option>
            <option value="E">Even</option>
            <option value="O">Odd</option>
          </select>
          <!-- CH1 Stop Bits -->
          <label>Stop Bits</label>
          <select id="ch1-stop" class="gw-field">
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
          <!-- CH1 Data Bits -->
          <label>Data Bits</label>
          <select id="ch1-bits" class="gw-field">
            <option value="8">8</option>
            <option value="7">7</option>
          </select>
        </div>
      </div>

      <div class="btn-row">
        <!-- 設定 Gateway（透過 REG64 套用） -->
        <button id="btn-save-config" type="button" onclick="saveConfig()">設定</button>
        <!-- 重新從裝置載入設定 -->
        <button type="button" class="ghost" onclick="loadConfig()">重新載入</button>
      </div>
      <!-- Gateway 設定提示 -->
      <div id="gw-status" class="note"></div>
    </div>

    <!-- AP / STA Wi-Fi 設定 -->
    <div class="card">
      <h2>AP / Wi-Fi</h2>
      <div class="row">
        <div>
          <!-- AP SSID -->
          <label>AP SSID</label>
          <input id="ap-ssid" type="text" />
        </div>
        <div>
          <!-- AP 密碼 -->
          <label>AP 密碼（>=8字）</label>
          <input id="ap-pwd" type="text" />
        </div>
      </div>
      <div class="row" style="margin-top:8px;">
        <div>
          <label class="checkline">
            <input type="checkbox" id="ap-enable" onchange="setApEnable()" />
            AP Enable
          </label>
        </div>
        <div></div>
      </div>
      <div class="btn-row">
        <!-- 更新 AP 設定 -->
        <button onclick="saveAp()">更新 AP 設定</button>
        <!-- 立即刷新 Wi-Fi 狀態 -->
        <button class="secondary" onclick="refreshStatus()">更新狀態</button>
      </div>
      <!-- STA/AP 狀態 -->
      <div id="wifi-status" class="note"></div>
      <div id="cfg-msg" class="note"></div>

      <div style="margin-top:12px;">
        <div class="note">Infrastructure 連線（選擇要連的路由器）</div>
        <div class="btn-row">
          <!-- 掃描可用 AP -->
          <button class="secondary" onclick="refreshScan()">掃描可用 AP</button>
        </div>
        <!-- 選擇可用 SSID -->
        <label>選擇可用 SSID</label>
        <select id="wifi-ssid">
          <option value="">(尚未掃描)</option>
        </select>
        <!-- STA 密碼 -->
        <label>密碼（若為開放網路可留空）</label>
        <input type="text" id="wifi-psk" placeholder="Wi-Fi Password" />
        <label class="checkline" style="margin-top:6px;">
          <!-- 是否套用 STA 設定 -->
          <input type="checkbox" id="wifi-save" checked />
          連線成功後套用 STA 設定
        </label>
        <div class="btn-row">
          <!-- 執行 STA 連線 -->
          <button onclick="connectWifi()">連線</button>
        </div>
        <!-- STA 連線結果 -->
        <div id="wifi-msg" class="note"></div>
      </div>
    </div>

    <!-- 快速操作與自訂指令 -->
    <div class="card">
      <h2>快速操作</h2>
      <div class="btn-row">
        <!-- 系統指令 -->
        <button onclick="sendCmd('SYS STATUS')">SYS STATUS</button>
        <button onclick="sendCmd('SYS WIFI')">SYS WIFI</button>
        <button onclick="sendCmd('LED ON')">LED ON</button>
        <button onclick="sendCmd('LED OFF')">LED OFF</button>
        <button class="secondary" onclick="sendCmd('SYS HELP')">SYS HELP</button>
      </div>
      <!-- 自訂指令輸入 -->
      <label>自訂指令</label>
      <input id="cmd-input" type="text" placeholder="例如：SYS STATUS 或 MB R HR 1 0 3" />
      <div class="btn-row">
        <!-- 送出指令 -->
        <button onclick="sendCmdFromInput()">送出</button>
        <!-- 清除 log -->
        <button class="ghost" onclick="clearLog()">清除 Log</button>
      </div>
    </div>

    <!-- RS485 HEX 測試工具 -->
    <div class="card">
      <h2>RS485 HEX</h2>
      <div class="note">使用 Gateway 設定的通訊參數</div>
      <!-- 選擇 CH0/CH1 -->
      <label>通道</label>
      <select id="hex-ch">
        <option value="0">CH0</option>
        <option value="1">CH1</option>
      </select>
      <!-- HEX bytes 輸入 -->
      <label style="margin-top:6px;">HEX bytes</label>
      <input id="hex-input" type="text" placeholder="例如：06 05 00 01 55 00" />
      <div class="btn-row">
        <!-- 送出 HEX -->
        <button id="hex-send" onclick="sendHex()">送出 HEX</button>
      </div>
      <!-- HEX 狀態訊息 -->
      <div id="hex-info" class="note"></div>
    </div>

    <!-- 系統回應 Log -->
    <div class="card span-2 log-card">
      <h2>回應 Log</h2>
      <div id="log" class="log"></div>
    </div>

    <!-- 輪詢表格（循環送出） -->
    <div class="card span-2">
      <h2>輪詢表格</h2>
      <div class="row">
        <div>
          <!-- 輪詢間隔 -->
          <label>輪詢間隔 (ms)</label>
          <input id="poll-interval" type="number" value="1000" min="50" />
        </div>
        <div class="btn-row" style="align-items:flex-end;">
          <!-- 啟動輪詢 -->
          <button class="secondary" onclick="pollStart()">啟動</button>
          <!-- 停止輪詢 -->
          <button class="ghost" onclick="pollStop()">停止</button>
          <!-- 套用輪詢表格（落盤需 REG60） -->
          <button onclick="savePoller()">套用表格</button>
        </div>
      </div>
      <!-- 輪詢狀態 -->
      <div class="note" id="poll-status"></div>
      <!-- CH0/CH1 啟用狀態 -->
      <div class="note" id="poll-ch-status"></div>
      <!-- RS485 TX/RX 狀態 -->
      <div class="note mono" id="poll-comm"></div>
      <div class="note">輪詢表格上限 256 筆，對應本地 100-355 registers。</div>
      <div style="overflow-x:auto;margin-top:8px;">
        <table>
          <thead>
            <tr>
              <!-- 表格欄位說明 -->
              <th>CH</th>
              <th>Station</th>
              <th>Modbus CMD</th>
              <th>REG Address</th>
              <th>Data</th>
              <th>Return</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="poll-rows"></tbody>
        </table>
      </div>
      <div class="btn-row">
        <!-- 新增輪詢列 -->
        <button class="ghost" onclick="addRow()">+ 新增</button>
      </div>
    </div>

    <!-- System -->
    <div class="card span-2">
      <h2>System</h2>
      <div class="note">所有設定先只保存在 RAM；按下「存入 Flash（REG60）」才會在重啟後保留。</div>
      <div class="btn-row">
        <!-- 寫入 Flash -->
        <button onclick="saveSystemConfig()">存入 Flash（REG60）</button>
        <!-- 系統重置 -->
        <button class="danger" onclick="resetSystemByReg()">System Reset（REG61）</button>
      </div>
      <div id="system-msg" class="note"></div>
    </div>
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
    fetch('/log/clear', { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          document.getElementById('log').textContent = '';
        } else {
          console.error('Failed to clear logs:', data.error);
        }
      })
      .catch(err => {
        console.error('Error clearing logs:', err);
      });
  }

  function pollLogs() {
    var log = document.getElementById('log');
    var atBottom = log.scrollHeight - log.clientHeight - log.scrollTop < 4;
    var prevScroll = log.scrollTop;
    fetch('/log')
      .then(r => r.text())
      .then(text => {
        if (log.textContent !== text) {
          log.textContent = text;
          if (atBottom) {
            log.scrollTop = log.scrollHeight;
          } else {
            log.scrollTop = prevScroll;
          }
        }
      })
      .catch(err => {
        // 忽略 fetch 錯誤
      });
  }

  function hexToBytes(raw) {
    var parts = raw.trim().split(/\s+/).filter(Boolean);
    var bytes = [];
    for (var i = 0; i < parts.length; i++) {
      var t = parts[i].toLowerCase();
      if (t.startsWith('0x')) t = t.slice(2);
      if (!t) continue;
      if (!/^[0-9a-f]{1,2}$/.test(t)) return null;
      bytes.push(parseInt(t, 16) & 0xFF);
    }
    return bytes;
  }

  function crc16Modbus(bytes) {
    var crc = 0xFFFF;
    for (var i = 0; i < bytes.length; i++) {
      crc ^= bytes[i];
      for (var j = 0; j < 8; j++) {
        if (crc & 1) {
          crc = (crc >> 1) ^ 0xA001;
        } else {
          crc = crc >> 1;
        }
      }
    }
    return crc & 0xFFFF;
  }

  function sendHex() {
    var ch0Enabled = document.getElementById('ch0-enabled').checked;
    var ch1Enabled = document.getElementById('ch1-enabled').checked;
    if (!ch0Enabled && !ch1Enabled) {
      document.getElementById('hex-info').textContent = 'CH0/CH1 皆停用，無法送出';
      return;
    }
    var inp = document.getElementById('hex-input');
    var raw = (inp.value || '').trim();
    if (!raw) return;
    var info = document.getElementById('hex-info');
    var bytes = hexToBytes(raw);
    if (!bytes) {
      info.textContent = '格式錯誤：請輸入 6 或 8 個 hex bytes（例如 06 05 00 01 FF 00）';
      return;
    }
    if (bytes.length !== 6 && bytes.length !== 8) {
      info.textContent = '長度錯誤：目前 ' + bytes.length + ' bytes，需 6 或 8 bytes';
      return;
    }
    var full = bytes;
    if (bytes.length === 6) {
      var crc = crc16Modbus(bytes);
      var crcLo = crc & 0xFF;
      var crcHi = (crc >> 8) & 0xFF;
      full = bytes.concat([crcLo, crcHi]);
      info.textContent = 'CRC: ' + [crcLo, crcHi].map(function(b) {
        return b.toString(16).padStart(2, '0').toUpperCase();
      }).join(' ') + '，送出 8 bytes';
    } else {
      info.textContent = '送出 8 bytes（已含 CRC）';
    }
    var hexStr = full.map(function(b) {
      return b.toString(16).padStart(2, '0').toUpperCase();
    }).join(' ');
    var ch = document.getElementById('hex-ch').value || '0';
    var cmd = 'RS HEX ' + ch + ' ' + hexStr;
    sendCmd(cmd);
  }

  window.onload = function() {
    appendLog('Web UI ready');
    loadConfig();
    refreshStatus();
    refreshScan();
    pollStatus();
    setInterval(pollStatus, 1000);
    setInterval(pollLogs, 1000);
    var btn = document.getElementById('btn-save-config');
    if (btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        saveConfig();
      });
    }
  };

  function loadConfig() {
    fetch('/cfg')
      .then(r => r.json())
      .then(d => {
        var mb = d.modbus || {};
        document.getElementById('mb-timeout').value = mb.response_timeout_ms || 1200;
        document.getElementById('tcp-slave-id').value = mb.tcp_slave_id || 1;
        var tcpRs485Mode = (mb.tcp_rs485_mode || '').toLowerCase();
        if (!tcpRs485Mode) {
          tcpRs485Mode = (mb.tcp_indirect_control === true) ? 'ch0' : 'disabled';
        }
        if (['disabled', 'ch0', 'ch1'].indexOf(tcpRs485Mode) < 0) {
          tcpRs485Mode = 'disabled';
        }
        document.getElementById('tcp-rs485-mode').value = tcpRs485Mode;
        document.getElementById('gw-status').textContent = '已套用';
        document.getElementById('ch0-enabled').checked = mb.ch0_enabled !== false;
        document.getElementById('ch1-enabled').checked = mb.ch1_enabled !== false;
        var ch0 = mb.ch0 || {};
        document.getElementById('ch0-mode').value = ch0.mode || 'rtu';
        document.getElementById('ch0-baud').value = ch0.baudrate || 9600;
        document.getElementById('ch0-parity').value = ch0.parity || 'N';
        document.getElementById('ch0-stop').value = ch0.stopbits || 1;
        document.getElementById('ch0-bits').value = ch0.bits || 8;
        var ch1 = mb.ch1 || {};
        document.getElementById('ch1-mode').value = ch1.mode || 'rtu';
        document.getElementById('ch1-baud').value = ch1.baudrate || 9600;
        document.getElementById('ch1-parity').value = ch1.parity || 'N';
        document.getElementById('ch1-stop').value = ch1.stopbits || 1;
        document.getElementById('ch1-bits').value = ch1.bits || 8;
        var ap = d.ap || {};
        document.getElementById('ap-ssid').value = ap.ssid || 'PicoSetup';
        document.getElementById('ap-pwd').value = ap.password || '';
        document.getElementById('ap-enable').checked = ap.enabled !== false;
        var sta = d.sta || {};
        if (sta.ssid) {
          document.getElementById('wifi-msg').textContent = '目前 STA：' + sta.ssid;
        }
        updateChannelUi();
        if (d.poller) {
          setPoller(d.poller);
        } else {
          fetch('/poller/config').then(r => r.json()).then(setPoller);
        }
      })
      .catch(() => {
        document.getElementById('cfg-msg').textContent = '讀取設定失敗';
      });
  }

  var pollRows = [];
  var pendingPollIntervalValue = null;

  function setPoller(p) {
    var rows = p.rows || [];
    pollRows = rows.map(r => ({
      ch: String(r.ch || 0),
      station: r.station || '01',
      cmd: r.cmd || '03',
      reg: r.reg || '0000',
      data: r.data || '0001',
      ret: ''
    }));
    document.getElementById('poll-interval').value = pendingPollIntervalValue || p.interval_ms || 1000;
    pendingPollIntervalValue = null;
    renderPollRows();
  }

  function renderPollRows() {
    var body = document.getElementById('poll-rows');
    body.innerHTML = '';
    pollRows.forEach((row, idx) => {
      var tr = document.createElement('tr');
      tr.innerHTML = `
        <td>
          <select class="mini" data-i="${idx}" data-k="ch">
            <option value="0">CH0</option>
            <option value="1">CH1</option>
          </select>
        </td>
        <td><input class="mini mono" data-i="${idx}" data-k="station" value="${row.station}" /></td>
        <td><input class="mini mono" data-i="${idx}" data-k="cmd" value="${row.cmd}" /></td>
        <td><input class="mini mono" data-i="${idx}" data-k="reg" value="${row.reg}" /></td>
        <td><input class="mini mono" data-i="${idx}" data-k="data" value="${row.data}" /></td>
        <td class="mono" data-ret="${idx}">${row.ret || ''}</td>
        <td><button class="mini danger" onclick="delRow(${idx})">-</button></td>
      `;
      body.appendChild(tr);
      var sel = tr.querySelector('select');
      sel.value = row.ch;
    });
  }

  document.addEventListener('input', function(ev) {
    var el = ev.target;
    if (!el || !el.dataset || el.dataset.i === undefined) return;
    var i = Number(el.dataset.i);
    var k = el.dataset.k;
    if (pollRows[i]) {
      pollRows[i][k] = el.value.trim();
    }
  });

  function addRow() {
    if (pollRows.length >= 256) {
      document.getElementById('poll-status').textContent = '已達 256 筆上限';
      return;
    }
    pollRows.push({ ch: '0', station: '01', cmd: '03', reg: '0000', data: '0001', ret: '' });
    renderPollRows();
  }

  function delRow(i) {
    pollRows.splice(i, 1);
    renderPollRows();
  }

  function savePoller() {
    var payload = {
      poller: {
        enabled: false,
        interval_ms: Number(document.getElementById('poll-interval').value || 1000),
        rows: pollRows.map(r => ({
          ch: Number(r.ch || 0),
          station: r.station,
          cmd: r.cmd,
          reg: r.reg,
          data: r.data
        }))
      },
      modbus: {
        ch0_enabled: document.getElementById('ch0-enabled').checked,
        ch1_enabled: document.getElementById('ch1-enabled').checked
      }
    };
    fetch('/cfg', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(r => r.json())
      .then(d => {
        document.getElementById('poll-status').textContent = d.ok ? '已套用（未寫入 Flash）' : ('套用失敗：' + (d.error || 'unknown'));
      })
      .catch(() => {
        document.getElementById('poll-status').textContent = '套用失敗';
      });
  }

  function pollStart() {
    var payload = {
      poller: {
        enabled: true,
        interval_ms: Number(document.getElementById('poll-interval').value || 1000),
        rows: pollRows.map(r => ({
          ch: Number(r.ch || 0),
          station: r.station,
          cmd: r.cmd,
          reg: r.reg,
          data: r.data
        }))
      }
    };
    fetch('/poller/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(r => r.json())
      .then(d => {
        document.getElementById('poll-status').textContent = d.ok ? '輪詢啟動' : ('啟動失敗：' + (d.error || 'unknown'));
      });
  }

  function pollStop() {
    fetch('/poller/stop', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        document.getElementById('poll-status').textContent = d.ok ? '輪詢停止' : ('停止失敗：' + (d.error || 'unknown'));
      });
  }

  function pollStatus() {
    fetch('/poller/status')
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d.results)) {
          d.results.forEach((ret, i) => {
            var cell = document.querySelector('[data-ret="' + i + '"]');
            if (cell) cell.textContent = ret || '';
          });
        }
        var ch0Enabled = document.getElementById('ch0-enabled').checked;
        var ch1Enabled = document.getElementById('ch1-enabled').checked;
        document.getElementById('poll-ch-status').textContent =
          'CH0 ' + (ch0Enabled ? 'ON' : 'OFF') + ' | CH1 ' + (ch1Enabled ? 'ON' : 'OFF');
        if (d.row_count === 0) {
          document.getElementById('poll-comm').textContent = 'RS485 NO ROWS';
        } else if (d.last_comm) {
          var lc = d.last_comm;
          var prefix = 'RS485 ' + (lc.ch === 1 ? 'CH1' : 'CH0');
          var lines = [];
          if (lc.tx) lines.push(prefix + ' TX ' + lc.tx);
          if (lc.rx) lines.push(prefix + ' RX ' + lc.rx);
          if (!lc.rx && lc.rx_len === 0) lines.push(prefix + ' RX <empty>');
          if (lc.err) lines.push(prefix + ' ERR ' + lc.err);
          document.getElementById('poll-comm').textContent = lines.join('\\n');
        }
        document.getElementById('poll-status').textContent =
          (d.enabled ? '輪詢中' : '已停止') + ' | interval ' + (d.interval_ms || 1000) + 'ms';
      });
  }

  function saveConfig() {
    pendingPollIntervalValue = document.getElementById('poll-interval').value;
    var payload = {
      modbus: {
        tcp_port: 502,
        response_timeout_ms: Number(document.getElementById('mb-timeout').value || 1200),
        tcp_slave_id: Number(document.getElementById('tcp-slave-id').value || 1),
        tcp_rs485_mode: document.getElementById('tcp-rs485-mode').value,
        ch0: {
          mode: document.getElementById('ch0-mode').value,
          baudrate: Number(document.getElementById('ch0-baud').value || 9600),
          parity: document.getElementById('ch0-parity').value,
          stopbits: Number(document.getElementById('ch0-stop').value || 1),
          bits: Number(document.getElementById('ch0-bits').value || 8)
        },
        ch1: {
          mode: document.getElementById('ch1-mode').value,
          baudrate: Number(document.getElementById('ch1-baud').value || 9600),
          parity: document.getElementById('ch1-parity').value,
          stopbits: Number(document.getElementById('ch1-stop').value || 1),
          bits: Number(document.getElementById('ch1-bits').value || 8)
        },
        tcp_indirect_control: document.getElementById('tcp-rs485-mode').value !== 'disabled',
        ch0_enabled: document.getElementById('ch0-enabled').checked,
        ch1_enabled: document.getElementById('ch1-enabled').checked
      }
    };
    document.getElementById('gw-status').textContent = '設定中...';
    fetch('/gateway/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          document.getElementById('gw-status').textContent = '已觸發設定（REG64）';
          setTimeout(loadConfig, 300);
        } else {
          document.getElementById('gw-status').textContent = '設定失敗：' + (d.error || 'unknown');
        }
        updateChannelUi();
      })
      .catch(() => {
        document.getElementById('gw-status').textContent = '設定失敗';
      });
  }

  function updateChannelUi() {
    var ch0Enabled = document.getElementById('ch0-enabled').checked;
    var ch1Enabled = document.getElementById('ch1-enabled').checked;
    setGroupDisabled('ch0', !ch0Enabled);
    setGroupDisabled('ch1', !ch1Enabled);
    var hexCh = document.getElementById('hex-ch');
    var hexSend = document.getElementById('hex-send');
    if (hexCh && hexCh.options && hexCh.options.length >= 2) {
      hexCh.options[0].disabled = !ch0Enabled;
      hexCh.options[1].disabled = !ch1Enabled;
    }
    if (!ch0Enabled && !ch1Enabled) {
      hexCh.disabled = true;
      hexSend.disabled = true;
      return;
    }
    hexCh.disabled = false;
    hexSend.disabled = false;
    if (!ch0Enabled && hexCh.value === '0') hexCh.value = '1';
    if (!ch1Enabled && hexCh.value === '1') hexCh.value = '0';
  }

  function setGroupDisabled(prefix, disabled) {
    var ids = [
      prefix + '-mode',
      prefix + '-baud',
      prefix + '-parity',
      prefix + '-stop',
      prefix + '-bits'
    ];
    ids.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.disabled = disabled;
    });
  }

  document.addEventListener('change', function(ev) {
    if (ev.target && (ev.target.id === 'ch0-enabled' || ev.target.id === 'ch1-enabled')) {
      updateChannelUi();
    }
  });

  document.addEventListener('input', function(ev) {
    if (ev.target && ev.target.classList && ev.target.classList.contains('gw-field')) {
      document.getElementById('gw-status').textContent = '請按設定';
    }
  });

  document.addEventListener('change', function(ev) {
    if (ev.target && ev.target.classList && ev.target.classList.contains('gw-field')) {
      document.getElementById('gw-status').textContent = '請按設定';
    }
  });

  function saveAp() {
    var payload = {
      ap: {
        ssid: document.getElementById('ap-ssid').value,
        password: document.getElementById('ap-pwd').value,
        enabled: document.getElementById('ap-enable').checked
      }
    };
    fetch('/cfg', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(r => r.json())
      .then(d => {
        showCfgMsg(d.ok ? '' : ('更新失敗：' + (d.error || 'unknown')));
        refreshStatus();
      })
      .catch(() => {
        document.getElementById('cfg-msg').textContent = '更新失敗';
      });
  }

  function setApEnable() {
    var enabled = document.getElementById('ap-enable').checked;
    fetch('/ap/enable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled })
    })
      .then(r => r.json())
      .then(d => {
        showCfgMsg(d.ok ? '' : ('AP Enable 更新失敗：' + (d.error || 'unknown')));
        refreshStatus();
      })
      .catch(() => {
        document.getElementById('cfg-msg').textContent = 'AP Enable 更新失敗';
      });
  }

  function showCfgMsg(text) {
    var el = document.getElementById('cfg-msg');
    if (!el) return;
    el.textContent = text || '';
  }

  function refreshStatus() {
    fetch('/wifi/status')
      .then(r => r.json())
      .then(d => {
        renderWifiStatus(d);
      })
      .catch(() => {
        document.getElementById('wifi-status').textContent = '無法取得狀態';
      });
  }

  function renderWifiStatus(d) {
    var txt = [];
    txt.push('STA connected: ' + d.connected);
    txt.push('IP ' + (d.ip || ''));
    if (d.rssi !== null && d.rssi !== undefined) txt.push('RSSI ' + d.rssi + ' dBm');
    var apEnable = (d.ap_enable_reg === 1 || d.ap_enable_reg === true);
    var apActive = (d.ap_active_reg === 1 || d.ap_active_reg === true);
    txt.push('AP enable: ' + apEnable);
    txt.push('AP active: ' + apActive + (d.ap_essid ? ' (' + d.ap_essid + ')' : ''));
    var apEnableEl = document.getElementById('ap-enable');
    if (apEnableEl) apEnableEl.checked = apEnable;
    if (d.power) {
      var pwr = d.power;
      if (pwr === 'external') pwr = '外部供電';
      if (pwr === 'battery') pwr = '電池供電';
      if (pwr === 'idle') pwr = '待機';
      if (pwr === 'unknown') pwr = '未知';
      txt.push('Power: ' + pwr + (d.batt_p !== null && d.batt_p !== undefined ? (' / ' + d.batt_p + '%') : ''));
    }
    document.getElementById('wifi-status').textContent = txt.join('\\n');
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
      body: JSON.stringify({ ssid: ssid, psk: psk, save: document.getElementById('wifi-save').checked })
    })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          msg.textContent = '連線成功，IP: ' + (d.ip || '(取得中)') + (d.saved ? '（已套用，未寫入 Flash）' : '');
        } else {
          msg.textContent = '連線失敗：' + (d.error || 'unknown');
        }
        if (d.connected !== undefined) {
          renderWifiStatus(d);
        } else {
          refreshStatus();
        }
        refreshStatus();
      })
      .catch(() => {
        msg.textContent = '連線請求失敗';
      });
  }

  function saveSystemConfig() {
    var msg = document.getElementById('system-msg');
    msg.textContent = '送出 REG60 中...';
    fetch('/system/save', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        msg.textContent = d.ok ? '已觸發 REG60，配置將寫入 Flash' : ('REG60 觸發失敗：' + (d.error || 'unknown'));
      })
      .catch(() => {
        msg.textContent = 'REG60 觸發失敗';
      });
  }

  function resetSystemByReg() {
    if (!confirm('確認執行 System Reset（REG61）？')) return;
    var msg = document.getElementById('system-msg');
    msg.textContent = '送出 REG61 中...';
    fetch('/system/reset', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        msg.textContent = d.ok ? '已觸發 REG61，系統即將重置' : ('REG61 觸發失敗：' + (d.error || 'unknown'));
      })
      .catch(() => {
        msg.textContent = 'REG61 觸發失敗';
      });
  }
</script>
</body>
</html>
"""


# =============== HTTP 伺服器啟動 ===============
# 說明：
# 建立非阻塞 HTTP 監聽 socket，並注入命令處理器供 /cmd 使用。
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


# =============== HTTP 輪詢處理 ===============
# 說明：
# 主迴圈呼叫的 HTTP 入口；負責接收連線、解析請求、路由分發與回應。
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

    # --------------- 內部函式：完整送出回應 ---------------
    # 說明：
    # 逐段送出資料直到完成，避免網頁在低速網路下出現部分內容缺漏。
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
        cl.settimeout(HTTP_IO_TIMEOUT_S)
        req = b""
        start_ts = time.time()
        while (b"\r\n\r\n" not in req and b"\n\n" not in req) and (time.time() - start_ts) < HTTP_IO_TIMEOUT_S:
            try:
                chunk = cl.recv(512)
            except OSError as e:
                if not _is_sock_timeout(e):
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
            if not (
                (method == "GET" and path == "/poller/status")
                or (method == "GET" and path == "/log")
                or (method == "POST" and path == "/poller/start")
                or (method == "POST" and path == "/poller/stop")
            ):
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
            while need > 0 and (time.time() - start_body_ts) < HTTP_IO_TIMEOUT_S:
                try:
                    chunk = cl.recv(512)
                except OSError as e:
                    if not _is_sock_timeout(e):
                        print("HTTP recv body error:", e)
                    break
                if not chunk:
                    break
                body += chunk
                need -= len(chunk)

        # --------------- 內部函式：JSON 回應封裝 ---------------
        # 說明：
        # 統一 JSON 回應格式與 HTTP 標頭，減少重複程式碼。
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
                "Cache-Control: no-store, no-cache, must-revalidate\r\n"
                "Pragma: no-cache\r\n"
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
            batt = read_battery()
            pwr = power_source_text()
            batt_p = None
            try:
                if batt:
                    batt_p = int(batt.get("p", 0))
            except Exception:
                batt_p = None
            send_json(
                {
                    "connected": st.get("connected", False),
                    "ip": ip,
                    "rssi": st.get("rssi"),
                    "ap_active": st.get("ap_active", False),
                    "ap_essid": st.get("ap_essid", ""),
                    "ap_enable_reg": register_store.get_regs(20, 1, decode=False)[0],
                    "ap_active_reg": register_store.get_regs(56, 1, decode=False)[0],
                    "power": pwr,
                    "batt_p": batt_p,
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
            save_sta = bool(payload.get("save"))
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
            status_payload = {
                "connected": st.get("connected", False),
                "ip": ip,
                "rssi": st.get("rssi"),
                "ap_active": st.get("ap_active", False),
                "ap_essid": st.get("ap_essid", ""),
                "ap_enable_reg": register_store.get_regs(20, 1, decode=False)[0],
                "ap_active_reg": register_store.get_regs(56, 1, decode=False)[0],
            }
            if ok:
                saved = False
                if save_sta:
                    ok2, err2, _cfg = update_config({"sta": {"ssid": ssid, "password": psk}})
                    saved = ok2 and not err2
                resp = {"ok": True, "ip": ip, "saved": saved}
                resp.update(status_payload)
                send_json(resp)
            else:
                resp = {"ok": False, "error": "connect failed"}
                resp.update(status_payload)
                send_json(resp)
            return

        if method == "POST" and path == "/ap/enable":
            payload = {}
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            enabled = bool(payload.get("enabled"))
            ok, err = register_store.set_reg(20, 1 if enabled else 0, encode=False)
            if not ok:
                send_json({"ok": False, "error": err or "set REG20 failed"}, status="500 Internal Server Error")
                return
            ap_cfg = (get_config().get("ap") or {})
            update_config(
                {
                    "ap": {
                        "ssid": ap_cfg.get("ssid") or "PicoSetup",
                        "password": ap_cfg.get("password") or "",
                        "enabled": enabled,
                    }
                }
            )
            if enabled:
                apply_ap_config(ap_cfg.get("ssid") or "PicoSetup", ap_cfg.get("password") or "")
            else:
                stop_config_ap()
            send_json({"ok": True, "enabled": enabled})
            return

        # ======= Poller API =======
        if method == "GET" and path == "/poller/config":
            cfg = get_config()
            send_json(cfg.get("poller") or {})
            return

        if method == "POST" and path == "/poller/start":
            payload = {}
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            poller_cfg = payload.get("poller") or {}
            poller_cfg["enabled"] = True
            ok, err, cfg = update_config({"poller": poller_cfg})
            if ok:
                ok_reg, err_reg = _sync_poller_config_to_registers((cfg.get("poller") or {}), source="web_poller")
                if not ok_reg:
                    send_json({"ok": False, "error": err_reg or "sync poller registers failed"}, status="500 Internal Server Error")
                    return
                send_json({"ok": True})
            else:
                send_json({"ok": False, "error": err or "update failed"}, status="400 Bad Request")
            return

        if method == "POST" and path == "/poller/stop":
            ok, err, cfg = update_config({"poller": {"enabled": False}})
            if ok:
                ok_reg, err_reg = _sync_poller_config_to_registers({"enabled": (cfg.get("poller") or {}).get("enabled")}, source="web_poller")
                if not ok_reg:
                    send_json({"ok": False, "error": err_reg or "write REG21 failed"}, status="500 Internal Server Error")
                    return
                send_json({"ok": True})
            else:
                send_json({"ok": False, "error": err or "update failed"}, status="400 Bad Request")
            return

        if method == "GET" and path == "/poller/status":
            from poller import status as poller_status

            send_json(poller_status())
            return

        # ======= Config API =======
        if method == "POST" and path == "/gateway/configure":
            payload = {}
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            modbus_payload = (payload or {}).get("modbus") or {}

            ok, err = _stage_gateway_modbus_to_registers(modbus_payload)
            if not ok:
                send_json({"ok": False, "error": err or "stage register failed"}, status="400 Bad Request")
                return

            poller_cfg = (get_config().get("poller") or {})
            ok, err = _sync_poller_config_to_registers(poller_cfg, source="web_gateway")
            if not ok:
                send_json({"ok": False, "error": err or "sync poller registers failed"}, status="500 Internal Server Error")
                return

            ok, err = register_store.set_reg(64, 1, encode=False)
            if not ok:
                send_json({"ok": False, "error": err or "trigger REG64 failed"}, status="500 Internal Server Error")
                return

            send_json({"ok": True, "queued": True})
            return

        if method == "POST" and path == "/system/save":
            ok, err = register_store.set_reg(60, 1, encode=False)
            if not ok:
                send_json({"ok": False, "error": err or "trigger REG60 failed"}, status="500 Internal Server Error")
                return
            send_json({"ok": True, "queued": True})
            return

        if method == "POST" and path == "/system/reset":
            ok, err = register_store.set_reg(61, 1, encode=False)
            if not ok:
                send_json({"ok": False, "error": err or "trigger REG61 failed"}, status="500 Internal Server Error")
                return
            send_json({"ok": True, "queued": True})
            return
        # ======= Config API: GET/POST /cfg =======
        if method == "GET" and path == "/cfg":
            send_json(get_config())
            return
        # ======= Config API: POST /cfg =======
        if method == "POST" and path == "/cfg":
            payload = {}
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            # UART 參數（ch0/ch1）只允許經由 REG64 套用流程更新。
            if isinstance(payload, dict) and isinstance(payload.get("modbus"), dict):
                payload["modbus"].pop("ch0", None)
                payload["modbus"].pop("ch1", None)
            ok, err, cfg = update_config(payload)
            if ok and "ap" in (payload or {}):
                ap = (cfg.get("ap") or {})
                ap_enabled = bool(ap.get("enabled", True))
                register_store.set_reg(20, 1 if ap_enabled else 0, encode=False)
                if ap_enabled:
                    apply_ap_config(ap.get("ssid") or "PicoSetup", ap.get("password") or "")
                else:
                    stop_config_ap()
            if ok and isinstance(payload, dict) and isinstance(payload.get("poller"), dict):
                ok_reg, err_reg = _sync_poller_config_to_registers((cfg.get("poller") or {}), source="web_cfg")
                if not ok_reg:
                    send_json({"ok": False, "error": err_reg or "sync poller registers failed"}, status="500 Internal Server Error")
                    return
            if ok:
                send_json({"ok": True})
            else:
                send_json({"ok": False, "error": err or "update failed"}, status="400 Bad Request")
            return

        # ======= 指令 API: POST /cmd =======
        if method == "POST" and path == "/cmd":
            cmd_str = body.decode("utf-8", "ignore").strip()
            print("HTTP cmd:", repr(cmd_str))
            handler = _cmd_handler or default_handler
            result = handler(cmd_str)
            log_buffer.append_log("CMD RESP: %s" % result.strip())
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

        # ======= 日誌 API: GET /log =======
        if method == "GET" and path == "/log":
            logs = log_buffer.get_logs_as_string()
            body_bytes = logs.encode("utf-8")
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

        # ======= 日誌清空 API: POST /log/clear =======
        if method == "POST" and path == "/log/clear":
            log_buffer.clear_logs()
            send_json({"ok": True})
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
