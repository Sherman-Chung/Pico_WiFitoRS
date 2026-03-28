# Pico 2 W Wi-Fi Modbus Gateway 說明（中文）

本專案以 Raspberry Pi Pico 2 W 提供以下功能：
- 內建 AP + Captive DNS 設定入口（預設 `PicoSetup` / `pico1234`）。
- STA 連線既有 Wi-Fi（可保存設定，重開機自動嘗試重連）。
- 2-Port Modbus RTU/ASCII <-> 1-Port Modbus TCP Gateway。
- Web UI 管理 Gateway 參數、輪詢表格、Wi-Fi 與 RS485 HEX 測試。
- Poller 結果回填本地 0-255 registers，可由 Modbus TCP 讀取。
- 電源資訊（外部/電池/待機）與電量百分比顯示。

## 啟動流程（以目前程式為準）
1. `main.py` 讀取 `config_store`（AP/STA/Modbus/Poller）與 `AUTO_CONFIG_AP_ON_BOOT`。
2. 若 `AUTO_CONFIG_AP_ON_BOOT=True`，會先啟動 AP、DNS、HTTP/TCP/Modbus 服務與 mDNS。
3. 若有保存 STA，會嘗試連線。
4. 執行開機檢查（UPS 與已啟用的 RS485 通道）；失敗會 `fail_halt()` 並 LED 閃爍停機。
5. 若前面未啟 AP 或未啟服務，會在檢查後補啟。
6. 進入主迴圈，每 20ms 輪詢：`CMD TCP -> HTTP -> Modbus TCP -> Poller`。

說明：
- `AUTO_CONFIG_AP_ON_BOOT` 影響的是「啟動時機」，不是最終有無 AP。  
  在檢查通過的正常流程下，系統最後會確保 AP 與服務可用。

## 網路與名稱解析
- AP 與 STA 可並行。
- Captive DNS 會將所有網域查詢回覆為目標 IP：
  - 若 AP 目前有裝置連線，固定回 `192.168.4.1`。
  - 否則若 STA 已連線，回 STA IP。
  - 否則回 `192.168.4.1`。
- mDNS 會回覆 `pico.local` 的 A 記錄（若 5353/Multicast 可正常使用）。

## Modbus Gateway 行為
- Modbus TCP 預設 Port `502`（可配置）。
- 採長連線模式：同一 TCP Client 可連續送多筆請求，直到對方斷線或發生錯誤。
- Unit ID 路由規則：
  - `Unit ID == tcp_slave_id`：讀本地 registers（僅支援 Function 03/04）。
  - 其他 Unit ID：依 `unit_map_ch0/ch1` 對應 CH0 或 CH1，轉送到 RS485。
- 例外回覆：
  - `0x01`：本地 registers 不支援的功能碼。
  - `0x02`：本地 registers 位址/數量不合法。
  - `0x06`：RS485 通道忙碌（鎖定失敗）。
  - `0x0B`：無可用通道、Unit ID 無對應、或 RS485 回覆無效/逾時。

## Poller 行為
- Poller 設定來源：`config_store.poller`，最多 256 列。
- `interval_ms` 最小為 50ms。
- 每次 `tick()` 最多執行 1 列輪詢，依序循環。
- RS485 與 Modbus TCP 共用通道鎖，避免同通道同時收發。
- 回覆寫入本地 registers 規則（以列索引為起點）：
  - 03/04：依 Byte Count 解析多筆 16-bit 寫入。
  - 05/06：寫入單筆 16-bit。

## HTTP API（Port 80）
- `GET /`：Web UI。
- `GET /wifi/scan`：掃描 AP。
- `GET /wifi/status`：STA/AP 狀態、RSSI、IP、電源與電量。
- `POST /wifi/connect`：連線 STA，可選擇保存。
- `GET /cfg`：讀取完整設定。
- `POST /cfg`：更新設定（ap/sta/modbus/poller）。
- `POST /cfg/reset`：清空設定後重啟裝置。
- `GET /poller/config`：讀取 poller 設定。
- `POST /poller/start`：啟用 poller（可同時提交 poller 設定）。
- `POST /poller/stop`：停用 poller。
- `GET /poller/status`：讀取輪詢執行狀態與最近通訊。
- `POST /cmd`：文字指令入口（轉給 `Server_CMD.handle_cmd`）。

## TCP 指令（Port 12345）
- 每次連線收一筆命令即回覆並關閉連線（非長連線）。
- 常用指令：
  - `SYS STATUS` / `SYS WIFI [RESET]` / `SYS AP RESET` / `SYS PING` / `SYS HELP`
  - `LED ON` / `LED OFF`
  - `RS HEX <ch> <8 bytes>`
  - `RS RECV <ch> [max]`
- `MB ...` 指令目前為示範回覆，非 Gateway 主資料路徑。

## 預設設定重點
- AP：`PicoSetup` / `pico1234`
- Modbus：
  - `tcp_port=502`
  - `tcp_slave_id=1`
  - `unit_map_ch0="1-127"`
  - `unit_map_ch1=""`
  - `ch0_enabled=False`
  - `ch1_enabled=False`
- Poller：`enabled=False`、`interval_ms=1000`

## 快速測試
- 狀態：`curl http://192.168.4.1/wifi/status`
- 指令：`echo 'SYS STATUS' | nc 192.168.4.1 12345`
- RS485 HEX：`echo 'RS HEX 0 06 05 00 01 FF 00 A2 ED' | nc 192.168.4.1 12345`
- Modbus TCP：連 `502`，依 Unit ID 測試本地 registers 或 RS485 轉送

## 文件
- 詳細手冊：`Project_Manual_zh.md`
