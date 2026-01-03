# WiFi Modbus Gateway 技術手冊（專案新版）

## 1. 系統簡介
本系統以 Raspberry Pi Pico 2 W 為核心，搭配 Pico-2CH-RS485 與 Pico-UPS-B，提供 2-Port Modbus RTU/ASCII ↔ 1-Port Modbus TCP 閘道器能力，並支援 AP 模式點對點連線與 STA 模式連網。

## 2. 硬體清單
- Raspberry Pi Pico 2 W
- Pico-2CH-RS485
- Pico-UPS-B
- RS485 Slave 裝置
- USB 供電或電池

## 3. 硬體連接
- UART0 TX/RX → RS485 CH0
- UART1 TX/RX → RS485 CH1
- I2C → UPS/INA219
- RS485 A/B → Slave

## 4. 軟體檔案說明
- `main.py`：主迴圈與服務啟動
- `modbus_gateway.py`：Modbus TCP ↔ RTU/ASCII 轉換
- `poller.py`：輪詢表格與回覆解析
- `Web_Page.py`：Web UI 與 HTTP API
- `Server_CMD.py`：RS485 HEX 測試指令
- `config_store.py`：設定永久保存
- `Pico_RS485.py`：UART 收發封裝
- `wifi_Scan_Connect.py`：Wi‑Fi AP/STA
- `Pico_UPS.py`：電源監測

## 5. Wi‑Fi 設定流程
1. 開機後啟用 AP：`PicoSetup`
2. 手機連線 SSID：`PicoSetup`，密碼：`pico1234`
3. 開啟 `http://192.168.4.1`
4. 掃描 AP → 選 SSID → 輸入密碼 → 送出
5. 可保存 STA 設定，斷電不遺失

## 6. Web UI 操作
### 6.1 Gateway 設定
- CH0/CH1 模式：RTU / ASCII
- UART 參數：baud/parity/stopbits/bits
- 啟用/停用 CH0/CH1
- 設定永久保存

### 6.2 RS485 HEX
- 可選 CH0 或 CH1
- 輸入 6 bytes：自動補 CRC
- 輸入 8 bytes：直接送出

範例：
```
06 05 00 01 FF 00
```

### 6.3 輪詢表格
- 欄位：CH / Station / Modbus CMD / REG Address / Data / Return
- Data 在 03/04 時代表「讀取數量」
- 支援新增/刪除/儲存/啟動

### 6.4 日誌
- Log 顯示指令回應與狀態
- RS485 TX/RX 狀態顯示在輪詢區塊

## 7. 指令介面
### 7.1 系統指令
- `SYS STATUS`
- `SYS WIFI`
- `SYS WIFI RESET`
- `SYS AP RESET`

### 7.2 RS485 指令
- `RS HEX <ch> <8 bytes>`
- `RS RECV <ch> [max]`

## 8. CRC 規格
- CRC-16 (Modbus)
- Polynomial：0xA001
- Init：0xFFFF
- Output：低位元組先送

## 9. Modbus TCP ↔ RTU/ASCII
- TCP 監聽 Port：502
- MBAP + PDU 轉成 RTU/ASCII
- 轉送至 CH0/CH1 後回覆 TCP

## 10. 常見問題
### 10.1 只能送無回覆
- 確認 A/B 接線
- 確認 baud/8N1
- 確認 Slave 支援功能碼

### 10.2 Web UI 連線異常
- 手機關閉行動數據
- 忘記 Wi‑Fi 後重連
- 使用 `SYS WIFI RESET` 或 `SYS AP RESET`

## 11. 擴充建議
- 每列自訂輪詢間隔
- 回覆解析為數值/JSON
- 上傳雲端或資料庫
