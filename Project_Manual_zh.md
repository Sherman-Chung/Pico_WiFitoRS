# Pico 2 W Wi-Fi Modbus Gateway 專案手冊

本手冊內容已與 `README_zh.md`、`flowcharts.md` 同步，以下敘述以目前程式行為為準。

---

## 1. 專案定位
- 目標：提供 2-Port Modbus RTU/ASCII <-> 1-Port Modbus TCP Gateway。
- 平台：Raspberry Pi Pico 2 W（純 Web 介面版本，無 LCD/按鍵 UI）。
- 網路模式：AP + STA 並行。
- 設定保存：AP/STA/Modbus/Poller 皆可持久化。
- 本地暫存器：0-255（16-bit），供 Poller 回填與 Modbus TCP 讀取。

---

## 2. 系統總覽
### 2.1 主要功能
- AP 設定入口（預設 `PicoSetup` / `pico1234`）。
- Captive DNS（所有查詢導向目標 IP）。
- mDNS（`pico.local` A 記錄）。
- HTTP API + Web UI（Port 80）。
- 指令 TCP Server（Port 12345）。
- Modbus TCP Server（預設 Port 502，可調）。
- Poller 週期通訊與結果回填。

### 2.2 模組分工
- `main.py`：系統啟動、健康檢查、主迴圈排程。
- `wifi_Scan_Connect.py`：STA/AP、掃描、連線、Captive DNS。
- `Web_Page.py`：HTTP Server、Web UI、設定與輪詢 API。
- `modbus_gateway.py`：Modbus TCP 與 RTU/ASCII 轉換。
- `poller.py`：輪詢表格執行與 registers 回填。
- `Server_CMD.py`：指令 TCP Server 與命令解析。
- `config_store.py`：設定載入、驗證、保存。
- `register_store.py`：本地 0-255 registers。
- `rs485_lock.py`：CH0/CH1 通道互斥鎖。

---

## 3. 開機狀態機
1. 讀取 `AUTO_CONFIG_AP_ON_BOOT` 與 `config_store`。
2. 若 `AUTO_CONFIG_AP_ON_BOOT=True`：
   - 先啟動 AP、Captive DNS、HTTP/TCP/Modbus 服務、mDNS。
3. 若有保存 STA，嘗試 STA 連線。
4. 執行開機檢查：
   - UPS/INA219 檢查。
   - 已啟用 RS485 通道初始化檢查。
5. 若檢查失敗，進入 `fail_halt()`（LED 閃爍，停止服務循環）。
6. 若前面未啟 AP 或未啟服務，於此階段補啟。
7. 進入主迴圈：每 20ms 依序輪詢 `poll_cmd_server`、`poll_http_server`、`poll_modbus_tcp_server`、`poller.tick()`。

重點：
- `AUTO_CONFIG_AP_ON_BOOT` 只影響 AP/服務的啟動時機。
- 在「檢查成功」的正常流程下，系統最終會確保 AP 與服務可用。

---

## 4. Wi-Fi / DNS / mDNS 行為
### 4.1 AP/STA
- STA 用於連到既有網路。
- AP 提供手機/筆電直接設定入口。
- AP 與 STA 可同時開啟。

### 4.2 Captive DNS
- `dns_captive.py` 會回應 A 記錄，TTL 30s。
- `wifi_Scan_Connect._dns_target_ip()` 選擇回覆 IP：
  - AP 有 station -> `192.168.4.1`
  - 否則 STA 已連線 -> STA IP
  - 其餘 -> `192.168.4.1`

### 4.3 mDNS
- `mdns_service.py` 回覆 `pico.local` 的 A 記錄。
- 使用 multicast `224.0.0.251:5353`。

---

## 5. HTTP 介面（Port 80）
### 5.1 API 一覽
- `GET /`：回 Web UI。
- `GET /wifi/scan`：掃描 AP 清單。
- `GET /wifi/status`：STA/AP/IP/RSSI、供電與電量。
- `POST /wifi/connect`：連線 STA（可保存）。
- `GET /cfg`：讀完整設定。
- `POST /cfg`：更新設定。
- `POST /cfg/reset`：清空設定並 `machine.reset()`。
- `GET /poller/config`：讀 Poller 設定。
- `POST /poller/start`：啟用 Poller（可同時提交 Poller 設定）。
- `POST /poller/stop`：停用 Poller。
- `GET /poller/status`：讀 Poller 執行狀態。
- `POST /cmd`：文字命令代理到 `Server_CMD.handle_cmd`。

### 5.2 其他路由行為
- `favicon` / `apple-touch-icon` 路由回 `204 No Content`。
- 未知路徑回主頁 HTML。

---

## 6. Modbus Gateway（Port 502）
### 6.1 TCP 連線模型
- 採長連線模式。
- 每次 `accept()` 一個 client 後，持續在同連線讀取多筆 MBAP+PDU，直到 client 關閉或錯誤。

### 6.2 路由與回應
- `Unit ID == tcp_slave_id`：
  - 僅支援 Function 03/04 讀本地 registers。
  - 位址/數量非法回 Exception `0x02`。
  - 其他功能碼回 Exception `0x01`。
- 其他 Unit ID：
  - 依 `unit_map_ch0/ch1` + `ch0_enabled/ch1_enabled` 決定通道。
  - 無映射/未啟用/不可用回 Exception `0x0B`。
  - 通道鎖失敗回 Exception `0x06`。
  - 成功轉送 RS485 RTU/ASCII 並回封裝後 MBAP。

### 6.3 RS485 轉送流程
1. 依通道配置初始化 UART（baud/parity/stopbits/bits）。
2. `mode=rtu` 時組 RTU frame + CRC16；`mode=ascii` 時組 ASCII frame + LRC。
3. 發送後等待回覆並解析。
4. 回覆無效/校驗失敗/逾時 -> Exception `0x0B`。
5. 每筆轉送後釋放通道鎖。

---

## 7. Poller
### 7.1 基本規則
- 設定來源：`config_store.poller`。
- 表格最多 256 列（儲存時會正規化）。
- `interval_ms` 小於 50 時會被提升為 50。
- 每次 `tick()` 僅處理 1 列，依序循環。

### 7.2 每列欄位
- `ch`：`0` 或 `1`
- `station`：Hex（1 byte）
- `cmd`：Hex（1 byte）
- `reg`：Hex（2 bytes）
- `data`：Hex（2 bytes）
- `Return`：執行時回填，不存檔

### 7.3 回覆與本地 registers
- 03/04：依回覆 Byte Count 轉為多筆 16-bit，從「列索引」起寫入本地 registers。
- 05/06：寫入 1 筆 16-bit 到「列索引」。
- 通道忙碌或異常時，`Return` 可能顯示：
  - `BUSY` / `DISABLED` / `BAD ROW` / `TIMEOUT` / `BAD CRC` / `STA MISMATCH`

---

## 8. 指令 TCP（Port 12345）
- 每次連線只處理一筆命令後關閉。
- 常用命令：
  - `SYS STATUS`
  - `SYS WIFI [RESET]`
  - `SYS AP RESET`
  - `SYS PING`
  - `LED ON` / `LED OFF`
  - `RS HEX <ch> <8 bytes>`
  - `RS RECV <ch> [max]`
- `MB ...` 命令目前為示範回覆，不是 Gateway 主路徑。

---

## 9. 預設設定（`config_store`）
- `ap.ssid="PicoSetup"`、`ap.password="pico1234"`
- `sta.ssid=""`、`sta.password=""`
- `poller.enabled=False`、`poller.interval_ms=1000`
- `modbus.tcp_port=502`
- `modbus.response_timeout_ms=1200`
- `modbus.tcp_slave_id=1`
- `modbus.unit_map_ch0="1-127"`
- `modbus.unit_map_ch1=""`
- `modbus.ch0_enabled=False`
- `modbus.ch1_enabled=False`

---

## 10. 已知限制
- Pico 2 W 僅支援 2.4GHz 802.11 b/g/n。
- RS485 同通道為半雙工，需要鎖保護序列化。
- Modbus Gateway 為單執行緒輪詢架構，同時負載高時延遲會增加。

---

## 11. 快速驗證
- `curl http://192.168.4.1/wifi/status`
- `echo 'SYS STATUS' | nc 192.168.4.1 12345`
- `echo 'RS HEX 0 06 05 00 01 FF 00 A2 ED' | nc 192.168.4.1 12345`
- 以 Modbus TCP Client 連 502 測試：
  - Unit ID=`tcp_slave_id`：讀本地 registers
  - Unit ID=unit map 範圍：轉送 CH0/CH1

---

## 12. 參考資料
- Raspberry Pi Pico 2：<https://www.raspberrypi.com/products/raspberry-pi-pico-2/>
- Waveshare Pico-2CH-RS485：<https://www.waveshare.net/wiki/Pico-2CH-RS485>
- Waveshare Pico-UPS-B：<https://www.waveshare.net/wiki/Pico-UPS-B>
