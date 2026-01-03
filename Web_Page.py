# Web_Page.py - 提供內建 Web UI 與簡易 HTTP 伺服器
# HTTP 伺服器會回傳內嵌的控制頁面，並透過 POST /cmd 呼叫指令處理器。

import socket
import json
import time
import machine
from Server_CMD import handle_cmd as default_handler
from wifi_Scan_Connect import (
    scan_visible,
    connect_to_ap,
    read_status,
    apply_ap_config,
)
from config_store import get_config, update_config, reset_config

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
</style>
</head>
<body>
<div class="wrap">
  <h1>Pico Modbus Gateway</h1>
  <div class="sub">2-Ports Modbus RTU/ASCII → 1-Port Modbus TCP | AP 點對點連線</div>

  <div class="grid">
    <div class="card">
      <h2>Gateway 設定</h2>
      <div class="row">
        <div>
          <label>Modbus TCP Port (固定)</label>
          <input id="mb-port" type="number" value="502" disabled />
        </div>
        <div>
          <label>Response Timeout (ms)</label>
          <input id="mb-timeout" type="number" value="1200" min="100" />
        </div>
      </div>
      <div class="row" style="margin-top:10px;">
        <div>
          <label>
            <input type="checkbox" id="ch0-enabled" checked />
            啟用 CH0
          </label>
        </div>
        <div>
          <label>
            <input type="checkbox" id="ch1-enabled" checked />
            啟用 CH1
          </label>
        </div>
      </div>
      <div class="note">通道啟用後才參與通訊；未列出 Unit ID 的請求不會轉送。</div>

      <div class="row" style="margin-top:10px;">
        <div>
          <div class="pill">CH0</div>
          <label>模式</label>
          <select id="ch0-mode">
            <option value="rtu">RTU</option>
            <option value="ascii">ASCII</option>
          </select>
          <label>Baudrate</label>
          <input id="ch0-baud" type="number" value="9600" />
          <label>Parity</label>
          <select id="ch0-parity">
            <option value="N">None</option>
            <option value="E">Even</option>
            <option value="O">Odd</option>
          </select>
          <label>Stop Bits</label>
          <select id="ch0-stop">
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
          <label>Data Bits</label>
          <select id="ch0-bits">
            <option value="8">8</option>
            <option value="7">7</option>
          </select>
        </div>
        <div>
          <div class="pill">CH1</div>
          <label>模式</label>
          <select id="ch1-mode">
            <option value="rtu">RTU</option>
            <option value="ascii">ASCII</option>
          </select>
          <label>Baudrate</label>
          <input id="ch1-baud" type="number" value="9600" />
          <label>Parity</label>
          <select id="ch1-parity">
            <option value="N">None</option>
            <option value="E">Even</option>
            <option value="O">Odd</option>
          </select>
          <label>Stop Bits</label>
          <select id="ch1-stop">
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
          <label>Data Bits</label>
          <select id="ch1-bits">
            <option value="8">8</option>
            <option value="7">7</option>
          </select>
        </div>
      </div>

      <div class="btn-row">
        <button onclick="saveConfig()">儲存設定</button>
        <button class="ghost" onclick="loadConfig()">重新載入</button>
      </div>
      <div id="cfg-msg" class="note"></div>
    </div>

    <div class="card">
      <h2>AP / Wi-Fi</h2>
      <div class="row">
        <div>
          <label>AP SSID</label>
          <input id="ap-ssid" type="text" />
        </div>
        <div>
          <label>AP 密碼（>=8字）</label>
          <input id="ap-pwd" type="text" />
        </div>
      </div>
      <div class="btn-row">
        <button onclick="saveAp()">更新 AP 設定</button>
        <button class="secondary" onclick="refreshStatus()">更新狀態</button>
      </div>
      <div id="wifi-status" class="note"></div>

      <div style="margin-top:12px;">
        <div class="note">Infrastructure 連線（選擇要連的路由器）</div>
        <div class="btn-row">
          <button class="secondary" onclick="refreshScan()">掃描可用 AP</button>
        </div>
        <label>選擇可用 SSID</label>
        <select id="wifi-ssid">
          <option value="">(尚未掃描)</option>
        </select>
        <label>密碼（若為開放網路可留空）</label>
        <input type="text" id="wifi-psk" placeholder="Wi-Fi Password" />
        <label style="margin-top:6px;">
          <input type="checkbox" id="wifi-save" checked />
          連線成功後保存 STA 設定
        </label>
        <div class="btn-row">
          <button onclick="connectWifi()">連線</button>
        </div>
        <div id="wifi-msg" class="note"></div>
      </div>
    </div>

    <div class="card">
      <h2>快速操作</h2>
      <div class="btn-row">
        <button onclick="sendCmd('SYS STATUS')">SYS STATUS</button>
        <button onclick="sendCmd('SYS WIFI')">SYS WIFI</button>
        <button onclick="sendCmd('LED ON')">LED ON</button>
        <button onclick="sendCmd('LED OFF')">LED OFF</button>
        <button class="secondary" onclick="sendCmd('SYS HELP')">SYS HELP</button>
      </div>
      <label>自訂指令</label>
      <input id="cmd-input" type="text" placeholder="例如：SYS STATUS 或 MB R HR 1 0 3" />
      <div class="btn-row">
        <button onclick="sendCmdFromInput()">送出</button>
        <button class="ghost" onclick="clearLog()">清除 Log</button>
      </div>
    </div>

    <div class="card">
      <h2>RS485 HEX</h2>
      <div class="note">使用 Gateway 設定的通訊參數</div>
      <label>通道</label>
      <select id="hex-ch">
        <option value="0">CH0</option>
        <option value="1">CH1</option>
      </select>
      <label style="margin-top:6px;">HEX bytes</label>
      <input id="hex-input" type="text" placeholder="例如：06 05 00 01 55 00" />
      <div class="btn-row">
        <button id="hex-send" onclick="sendHex()">送出 HEX</button>
      </div>
      <div id="hex-info" class="note"></div>
    </div>

    <div class="card span-2">
      <h2>輪詢表格</h2>
      <div class="row">
        <div>
          <label>輪詢間隔 (ms)</label>
          <input id="poll-interval" type="number" value="1000" min="50" />
        </div>
        <div class="btn-row" style="align-items:flex-end;">
          <button class="secondary" onclick="pollStart()">啟動</button>
          <button class="ghost" onclick="pollStop()">停止</button>
          <button onclick="savePoller()">儲存表格</button>
        </div>
      </div>
      <div class="note" id="poll-status"></div>
      <div class="note" id="poll-ch-status"></div>
      <div class="note mono" id="poll-comm"></div>
      <div style="overflow-x:auto;margin-top:8px;">
        <table>
          <thead>
            <tr>
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
        <button class="ghost" onclick="addRow()">+ 新增</button>
      </div>
    </div>

    <div class="card span-2 log-card">
      <h2>回應 Log</h2>
      <div id="log" class="log"></div>
    </div>

    <div class="card span-2">
      <h2>System Reset</h2>
      <div class="note">清空所有設定並重啟裝置。</div>
      <div class="btn-row">
        <button class="danger" onclick="resetSystem()">System Reset</button>
      </div>
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
    document.getElementById('log').textContent = '';
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
  };

  function loadConfig() {
    fetch('/cfg')
      .then(r => r.json())
      .then(d => {
        var mb = d.modbus || {};
        document.getElementById('mb-timeout').value = mb.response_timeout_ms || 1200;
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
        var sta = d.sta || {};
        if (sta.ssid) {
          document.getElementById('wifi-msg').textContent = '已保存 STA：' + sta.ssid;
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
    document.getElementById('poll-interval').value = p.interval_ms || 1000;
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
        document.getElementById('poll-status').textContent = d.ok ? '已儲存' : ('儲存失敗：' + (d.error || 'unknown'));
      })
      .catch(() => {
        document.getElementById('poll-status').textContent = '儲存失敗';
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
          if (lc.err) lines.push(prefix + ' ERR ' + lc.err);
          document.getElementById('poll-comm').textContent = lines.join('\\n');
        }
        document.getElementById('poll-status').textContent =
          (d.enabled ? '輪詢中' : '已停止') + ' | interval ' + (d.interval_ms || 1000) + 'ms';
      });
  }

  function saveConfig() {
    var payload = {
      modbus: {
        tcp_port: 502,
        response_timeout_ms: Number(document.getElementById('mb-timeout').value || 1200),
        unit_map_ch0: document.getElementById('unit-map').value,
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
        document.getElementById('cfg-msg').textContent = d.ok ? '已儲存' : ('儲存失敗：' + (d.error || 'unknown'));
        updateChannelUi();
      })
      .catch(() => {
        document.getElementById('cfg-msg').textContent = '儲存失敗';
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

  function saveAp() {
    var payload = {
      ap: {
        ssid: document.getElementById('ap-ssid').value,
        password: document.getElementById('ap-pwd').value
      }
    };
    fetch('/cfg', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(r => r.json())
      .then(d => {
        document.getElementById('cfg-msg').textContent = d.ok ? 'AP 設定已更新' : ('更新失敗：' + (d.error || 'unknown'));
        refreshStatus();
      })
      .catch(() => {
        document.getElementById('cfg-msg').textContent = '更新失敗';
      });
  }

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
      body: JSON.stringify({ ssid: ssid, psk: psk, save: document.getElementById('wifi-save').checked })
    })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          msg.textContent = '連線成功，IP: ' + (d.ip || '(取得中)') + (d.saved ? '（已保存）' : '');
        } else {
          msg.textContent = '連線失敗：' + (d.error || 'unknown');
        }
        refreshStatus();
      })
      .catch(() => {
        msg.textContent = '連線請求失敗';
      });
  }

  function resetSystem() {
    if (!confirm('確認清空所有設定並重啟？')) return;
    fetch('/cfg/reset', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        document.getElementById('cfg-msg').textContent = d.ok ? '重啟中...' : ('Reset 失敗：' + (d.error || 'unknown'));
      })
      .catch(() => {
        document.getElementById('cfg-msg').textContent = 'Reset 失敗';
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
            if not (
                (method == "GET" and path == "/poller/status")
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
            if ok:
                saved = False
                if save_sta:
                    ok2, err2, _cfg = update_config({"sta": {"ssid": ssid, "password": psk}})
                    saved = ok2 and not err2
                send_json({"ok": True, "ip": ip, "saved": saved})
            else:
                send_json({"ok": False, "error": "connect failed"})
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
            ok, err, _ = update_config({"poller": poller_cfg})
            if ok:
                send_json({"ok": True})
            else:
                send_json({"ok": False, "error": err or "update failed"}, status="400 Bad Request")
            return

        if method == "POST" and path == "/poller/stop":
            ok, err, _ = update_config({"poller": {"enabled": False}})
            if ok:
                send_json({"ok": True})
            else:
                send_json({"ok": False, "error": err or "update failed"}, status="400 Bad Request")
            return

        if method == "GET" and path == "/poller/status":
            from poller import status as poller_status

            send_json(poller_status())
            return

        # ======= Config API =======
        if method == "GET" and path == "/cfg":
            send_json(get_config())
            return

        if method == "POST" and path == "/cfg":
            payload = {}
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            ok, err, cfg = update_config(payload)
            if ok and "ap" in (payload or {}):
                ap = (cfg.get("ap") or {})
                apply_ap_config(ap.get("ssid") or "PicoSetup", ap.get("password") or "")
            if ok:
                send_json({"ok": True})
            else:
                send_json({"ok": False, "error": err or "update failed"}, status="400 Bad Request")
            return

        if method == "POST" and path == "/cfg/reset":
            reset_config()
            send_json({"ok": True})
            time.sleep_ms(200)
            machine.reset()
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
