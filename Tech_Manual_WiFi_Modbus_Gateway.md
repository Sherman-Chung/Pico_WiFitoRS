# WiFi to Modbus RS485 Gateway 技術手冊

## 1. 系統簡介
本系統以 Raspberry Pi Pico2 W 為核心，搭配 Pico-2CH-RS485 與 Pico-UPS-B，提供 Wi‑Fi 遠端控制與 Modbus RTU RS485 通訊能力。

## 2. 硬體清單
- Raspberry Pi Pico2 W
- Pico-2CH-RS485
- Pico-UPS-B
- RS485 Slave 裝置
- USB 供電或電池

## 3. 硬體連接
- UART0 TX/RX → RS485 CH0
- I2C → UPS/INA219
- RS485 A/B → Slave

## 4. 軟體檔案說明
- `main.py`：主迴圈與服務啟動
- `Web_Page.py`：Web UI 與 HTTP 服務
- `Server_CMD.py`：指令解析與 RS485 送收
- `Pico_RS485.py`：UART 收發封裝
- `wifi_Scan_Connect.py`：Wi‑Fi AP/STA
- `Pico_UPS.py`：電源監測

## 5. Wi‑Fi 設定流程
1. 開機後啟用 AP：`PicoSetup`
2. 手機連線 SSID：`PicoSetup`，密碼：`pico1234`
3. 開啟 `http://192.168.4.1`
4. 掃描 AP → 選 SSID → 輸入密碼 → 送出

## 6. Web UI 操作
### 6.1 RS485 HEX
- 輸入 6 bytes：自動補 CRC
- 輸入 8 bytes：直接送出

範例：
```
06 05 00 01 FF 00
```

### 6.2 日誌
- TX/RX 會印在 Log
- RX 會一次性顯示完整封包

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

## 9. 常見問題
### 9.1 只能送無回覆
- 確認 A/B 接線
- 確認 baud/8N1
- 確認 Slave 支援功能碼

### 9.2 Web UI 連線異常
- 手機關閉行動數據
- 忘記 Wi‑Fi 後重連
- 使用 `SYS WIFI RESET` 或 `SYS AP RESET`

## 10. 擴充建議
- 增加 CH1 支援
- 支援 Modbus 功能碼解析
- 上傳雲端或資料庫
