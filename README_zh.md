# Pico2W Wi-Fi/Modbus Gateway 說明（中文）

這份專案讓 Raspberry Pi Pico W 同時提供：
- 內建 AP（預設 SSID `PicoSetup` / 密碼 `pico1234`）+ Captive DNS，手機可直接連線設定。
- STA 模式連上家用 Wi‑Fi 後，透過 HTTP 網頁或 TCP 指令控制 LED / Modbus / RS485。
- 選配 LCD + 按鍵，提供本機 UI；若無 LCD 可在瀏覽器完成設定。

## 啟動流程
1. `main.py` 啟動後依 `config.py` 決定是否自動開 AP (`AUTO_CONFIG_AP_ON_BOOT`)。  
2. 開 AP 時同步啟動 Captive DNS（將任何網域導向 `192.168.4.1`），等待手機連上並啟動 TCP/HTTP 伺服器。  
3. 若 LCD 存在，進入 UI 狀態機：顯示首頁 → 可掃描/選網路/輸入密碼連線。  
4. 若無 LCD 或 `FORCE_HEADLESS=True`，維持 headless 迴圈，只跑網路服務。  
5. 連上家用 Wi‑Fi 後可透過 mDNS（`pico.local`，若未被占用）或取得的 IP 連線。

## 檔案導覽
- `main.py`：主程式狀態機；負責啟動 AP/伺服器/mDNS，以及輪詢 TCP/HTTP/按鍵與 UI。  
- `wifi_Scan_Connect.py`：Wi‑Fi 管理（STA/AP），掃描、連線、AP 啟停、Captive DNS。`_dns_target_ip` 會在 AP 有裝置時強制回 `192.168.4.1`，避免切到 STA IP 讓設定頁失聯。  
- `Web_Page.py`：HTTP 伺服器 + 內建 Web UI。路徑：`/` 主頁、`/wifi/scan`、`/wifi/status`、`/wifi/connect`、`/cmd`。  
- `Server_CMD.py`：TCP 伺服器（port 12345）與指令解析；支援 SYS/LED/MB/RS 指令。  
- `UI_Page.py`：LCD UI 畫面與狀態，包含掃描列表、細節、連線鍵盤、狀態頁。  
- `LCD_Control.py`：Pico-LCD-1.3 驅動與繪圖工具；若無 LCD 提供 `_DummyLCD` 防呆。  
- `Button_Control.py`：按鍵腳位定義、去抖動、長按偵測。  
- `Pico_RS485.py`：RS485 UART 初始化與收送封裝。  
- `Pico_UPS.py`：INA219 讀電流/電壓，計算電量狀態，提供 UI 顯示文字。  
- `dns_captive.py`：Captive DNS 伺服器，將所有 DNS 查詢導向指定 IP。  
- `mdns_service.py`：簡易 mDNS responder（只回 A 紀錄）。  
- `config.py`：開機行為設定：`FORCE_HEADLESS`、`AUTO_CONFIG_AP_ON_BOOT`。  
- `tempCodeRunnerFile.py`：暫存/無用檔，可忽略。

## HTTP 介面
- `GET /`：內建設定/控制頁。  
- `GET /wifi/scan`：回傳可見 AP 列表 JSON。  
- `GET /wifi/status`：回傳 STA/AP 狀態、RSSI、IP。  
- `POST /wifi/connect`：`{"ssid": "...", "psk": "..."}` 連線指定 AP。  
- `POST /cmd`：純文字指令，委派給 `Server_CMD.handle_cmd`。  
- 內建網頁會在載入後自動呼叫 `/wifi/status` 與 `/wifi/scan`。

## TCP 指令摘要（12345）
- `SYS STATUS` / `SYS WIFI` / `SYS PING` / `SYS HELP`：系統資訊。  
- `LED ON` / `LED OFF`：控制板載 LED。  
- `MB R HR <slave> <addr> <count>`：示範回傳假資料；可自行接 Modbus。  
- `MB W HR <slave> <addr> <value>`：示範寫入。  
- `RS SEND <ch> <text...>` / `RS RECV <ch> [max]`：透過 RS485 UART 送/收。

## Wi‑Fi 使用流程
1. 手機連到 `PicoSetup` → 瀏覽器開 `http://192.168.4.1`。  
2. 點「掃描可用 AP」，選擇家用 Wi‑Fi，輸入密碼送出。  
3. 連線成功後，訊息會顯示取得的 IP；之後可改用該 IP 或 `pico.local`（若 mDNS 正常）。  
4. 若要保持 AP + STA 並行，建議將 AP 頻道設成與家用 Wi‑Fi 相同：在 `start_config_ap` 中加入 `channel=<路由器頻道>`，避免切頻導致 AP 掉線。

## 常見注意事項
- 瀏覽器請用無痕/直接輸入 `192.168.4.1`，避免自動載入舊網址造成 timeout。  
- 若 mDNS 出現 `EADDRINUSE`，代表 5353 已被占用，可忽略或換其它設備再試。  
- 供電不足會讓 Wi‑Fi/RS485 不穩，請確保供電充足。  
- 完成設定後如不需 AP，可呼叫 `stop_config_ap()` 或設 `AUTO_CONFIG_AP_ON_BOOT=False` 減少干擾。

## 快速測試
- LED：`echo 'LED ON' | nc 192.168.4.1 12345`（或改成 STA IP）。  
- 狀態：`curl http://192.168.4.1/wifi/status`。  
- Modbus 範例：網頁按鈕或 `echo 'MB R HR 1 0 2' | nc ...`。
