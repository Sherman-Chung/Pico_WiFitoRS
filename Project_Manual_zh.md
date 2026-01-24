# Pico 2 W WiFi Modbus Gateway 專案手冊

---

## 1. 專案定位與特色
- **目標**：以 Raspberry Pi Pico 2 W 為核心，提供 2‑Port Modbus RTU/ASCII ↔ 1‑Port Modbus TCP 閘道器。  
- **連線模式**：AP 點對點 + STA 連線既有網路，可保存設定。  
- **純 Web 版本**：移除 LCD/按鍵，所有操作透過 Web UI 完成。  

---

## 2. 硬體架構
### 2.1 硬體清單
- Raspberry Pi Pico 2 W  
- Pico-2CH-RS485  
- Pico-UPS-B  
- RS485 Slave 裝置  
- USB 供電或電池  

### 2.2 介面對應
- UART0 (GP0/GP1) → RS485 CH0  
- UART1 (GP4/GP5) → RS485 CH1  
- I2C → UPS/INA219  
- Wi‑Fi AP/STA 共用  

---

## 3. 軟體模組與職責
- `main.py`：純 Web 主程式；啟動 AP/伺服器/mDNS，輪詢 TCP/HTTP/Modbus/輪詢表格  
- `modbus_gateway.py`：Modbus TCP ↔ RTU/ASCII 轉換  
- `poller.py`：輪詢表格循環送出與回覆解析  
- `Web_Page.py`：Web UI + HTTP API  
- `Server_CMD.py`：RS485 HEX 測試指令  
- `config_store.py`：設定永久保存（AP/STA/Modbus/輪詢）  
- `Pico_RS485.py`：UART/RS485 收發封裝  
- `wifi_Scan_Connect.py`：Wi‑Fi 管理（AP/STA）  
- `Pico_UPS.py`：電源監測  

---

## 4. 開機流程（純 Web 版）
1. 開機啟動 AP（預設 `PicoSetup / pico1234`）與 Captive DNS。  
2. 同步啟動 HTTP/TCP/Modbus 服務。  
3. 若已保存 STA，會自動嘗試連線。  
4. Web UI 透過 `http://192.168.4.1` 或 STA IP 存取。  

---

## 5. Web UI 功能
### 5.1 Gateway 設定
- CH0/CH1 模式：RTU / ASCII  
- UART 參數：baud/parity/stopbits/bits  
- 啟用/停用 CH0/CH1  
- 設定永久保存（狀態顯示「已儲存／請儲存設定」）  

### 5.2 AP / STA 設定
- AP SSID/密碼可更新  
- STA 連線可保存，斷電不遺失  

### 5.3 RS485 HEX 測試
- 選擇 CH0/CH1  
- 輸入 6 bytes → 自動補 CRC  
- 輸入 8 bytes → 直接送出  

### 5.4 輪詢表格
欄位：`CH / Station / Modbus CMD / REG Address / Data / Return`  
- Data 在 03/04 代表「讀取數量」  
- 支援新增/刪除/儲存/啟動  
- Return 顯示解析後回覆或錯誤狀態  

---

## 6. Modbus TCP ↔ RTU/ASCII
### 6.1 Modbus TCP 封包格式（MBAP + PDU）
- Transaction ID：2 bytes  
- Protocol ID：2 bytes（固定 0x0000）  
- Length：2 bytes（Unit ID + PDU 長度）  
- Unit ID：1 byte  
- PDU：功能碼 + 資料  

### 6.2 RTU/ASCII 校驗
- RTU：CRC‑16 (poly 0xA001, init 0xFFFF)  
- ASCII：LRC  

### 6.3 轉換流程
TCP 收到 MBAP+PDU → 擷取 Unit ID/PDU → 組成 RTU/ASCII → 送 RS485 → 收回覆 → 包回 MBAP 回覆 TCP。  

---

## 7. 輪詢表格範例
### 7.1 寫入線圈 (Function 05)
CH: CH1 / Station: 06 / CMD: 05 / REG: 0001 / Data: FF00  
RTU 送出：`06 05 00 01 FF 00 ?? ??`（CRC 自動補）  

### 7.2 讀取暫存器 (Function 03)
CH: CH0 / Station: 01 / CMD: 03 / REG: 0000 / Data: 0002  
RTU 送出：`01 03 00 00 00 02 ?? ??`  
回覆範例：`01 03 04 00 0A 00 14 ?? ??` → Return `00 0A 00 14`  

---

## 8. Modbus TCP 封包範例
### 8.1 Read Holding Registers (03)
TCP Hex：`00 01 00 00 00 06 01 03 00 00 00 02`  

### 8.2 Write Single Register (06)
TCP Hex：`00 02 00 00 00 06 01 06 00 01 00 55`  

### 8.3 Write Multiple Registers (16 / 0x10)
TCP Hex：`00 03 00 00 00 0B 01 10 00 01 00 02 04 00 0A 00 14`  

---

## 9. 錯誤回覆（Exception Code）
RTU：`01 83 02 ?? ??`  
TCP：`00 01 00 00 00 03 01 83 02`  

常見 Exception Code：  
- 01: Illegal Function  
- 02: Illegal Data Address  
- 03: Illegal Data Value  
- 06: Slave Device Busy  

---

## 10. 已知限制
- Pico 2 W 僅支援 2.4GHz 802.11 b/g/n  
- RS485 同通道半雙工，需序列化處理  

---

## 11. 常見問題
### 11.1 只有 TX 無 RX
- 確認 A/B 接線與終端電阻  
- 確認 baud/8N1 與功能碼  
- Function 05 僅允許 FF00/0000  

### 11.2 Web UI 連線異常
- 手機關閉行動數據  
- 忘記 Wi‑Fi 後重連  
- 使用 `SYS WIFI RESET` 或 `SYS AP RESET`  

---

## 12. 擴充方向
- 每列自訂輪詢間隔  
- 回覆解析為數值/JSON  
- 上傳雲端或資料庫  
- 增加告警與事件記錄  

---

## 13. 參考資料
- Raspberry Pi Pico 2：<https://www.raspberrypi.com/products/raspberry-pi-pico-2/>  
- Waveshare Pico-2CH-RS485：<https://www.waveshare.net/wiki/Pico-2CH-RS485>  
- Waveshare Pico-UPS-B：<https://www.waveshare.net/wiki/Pico-UPS-B>  
