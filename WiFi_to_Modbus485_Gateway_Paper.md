# 使用 Raspberry Pi Pico 2 W、Pico-2CH-RS485、Pico-UPS-B 之 WiFi Modbus Gateway 設計（專案新版）

## 摘要
本文設計並實作一套以 Raspberry Pi Pico 2 W 為核心、搭配 Waveshare Pico-2CH-RS485 與 Pico-UPS-B 的 WiFi Modbus Gateway。系統提供 Wi-Fi AP/STA 模式、Web UI 與命令通道，支援 2-Port Modbus RTU/ASCII ↔ 1-Port Modbus TCP 轉換，並可透過 UPS 模組監測電壓/電流以提升可靠度。本文說明硬體架構、韌體模組化設計、網頁控制介面、Modbus 轉換與輪詢表格驗證流程。

## 關鍵字
Raspberry Pi Pico 2 W、Modbus TCP、Modbus RTU/ASCII、RS485、Wi-Fi Gateway、嵌入式系統

## 1. 緒論
工業現場常以 RS485 匯流排連接感測器與致動器，Modbus RTU/ASCII 為常見通訊協議。為了讓既有 RS485 裝置具備無線存取與遠端控制能力，本文以 Pico 2 W 作為 Wi-Fi 與控制核心，搭配雙通道 RS485 擴展板與 UPS 電源模組，建構一套 WiFi Modbus Gateway，實現 Modbus TCP 與 RTU/ASCII 互通，提升設備部署彈性並保留既有通訊架構。

## 2. 系統需求與設計目標
1. **Wi-Fi 連線能力**：支援 AP 模式供行動裝置設定，並可切換至 STA 連線既有網路且可保存設定。
2. **Modbus TCP ↔ RTU/ASCII**：可在 TCP 與雙通道 RS485 間互通。
3. **Web 介面操作**：提供 Gateway 設定、輪詢表格與 RS485 HEX 測試。
4. **電源監測**：透過 UPS 模組監測電池電壓與電流狀態。
5. **易於擴充**：韌體模組化設計，方便後續擴增輪詢與資料解析。

## 3. 硬體架構
### 3.1 核心模組
- **Raspberry Pi Pico2 W**：具備 RP 系列 MCU 與 Wi-Fi 功能，作為控制與通訊核心。

### 3.2 RS485 介面
- **Pico-2CH-RS485**：提供雙通道 RS485 轉換，本文使用 CH0 (UART0, GP0/GP1) 與 CH1 (UART1, GP4/GP5)。

### 3.3 電源與監測
- **Pico-UPS-B**：提供電池供電與電壓/電流量測介面，提升系統穩定性。

### 3.4 介面對應
- UART0 TX/RX → RS485 CH0 TX/RX
- UART1 TX/RX → RS485 CH1 TX/RX
- UPS 模組 I2C → Pico 2 W I2C 腳位

## 4. 軟體與韌體設計
系統韌體以模組化方式撰寫，核心包含：
- `main.py`：主程式與狀態機
- `modbus_gateway.py`：Modbus TCP ↔ RTU/ASCII 轉換
- `poller.py`：輪詢表格循環與回覆解析
- `config_store.py`：設定永久保存
- `Pico_RS485.py`：RS485 UART 發送/接收
- `Server_CMD.py`：命令解析與 RS485 HEX 測試
- `Web_Page.py`：嵌入式 Web UI + HTTP API
- `wifi_Scan_Connect.py`：Wi-Fi 掃描、連線與 AP 管理

### 4.1 Wi-Fi 模式與 Captive Portal
系統開機可啟動 PicoSetup AP，並提供 Captive DNS 將所有網域導向設定頁。使用者可在瀏覽器輸入 `http://192.168.4.1` 設定 STA 連線，並保存設定以便下次自動連線。

### 4.2 Modbus RTU/ASCII 與校驗
Modbus RTU 封包採用 CRC-16 (poly 0xA001, init 0xFFFF)，CRC 低位元組先送。Modbus ASCII 則使用 LRC 檢查。Web UI 可輸入 6 bytes，系統自動補 CRC 形成 8 bytes 後送出。

### 4.3 Web UI 操作流程
1. 連線 PicoSetup AP
2. 開啟 Web UI
3. 設定 CH0/CH1 通訊參數與啟用狀態
4. 輸入 RS485 HEX（6 bytes）或建立輪詢表格
5. 系統自動計算 CRC、送出並顯示回覆

### 4.4 RS485 接收策略
UART 非阻塞接收，封包可能分段到達，因此在上層彙整後一次性輸出。輪詢表格會依序送出並在介面顯示最後一次 TX/RX。

## 5. 測試與結果
- **Modbus TCP ↔ RTU/ASCII**：TCP 請求可轉送至 CH0/CH1 並回覆。
- **輪詢表格**：可循環送出並顯示回覆。
- **Web UI 可用性**：可從手機直接設定與測試。

## 6. 討論
1. **無 DE/RE GPIO 控制**：本硬體以 TX 觸發方向控制，需在韌體層透過等待時間補足接收窗口。
2. **RS485 半雙工**：同通道需序列化處理以避免碰撞。
3. **擴充性**：可加入回覆解析、每列間隔與資料上雲等功能。

## 6.1 Modbus TCP 封包格式
Modbus TCP 封包由 MBAP Header 與 PDU 組成：
- **Transaction ID**：2 bytes
- **Protocol ID**：2 bytes（固定 0x0000）
- **Length**：2 bytes（Unit ID + PDU 長度）
- **Unit ID**：1 byte
- **PDU**：功能碼 + 資料

本系統流程為：TCP 收到 MBAP+PDU → 擷取 Unit ID 與 PDU → 組成 RTU/ASCII → 送 RS485 → 收回覆 → 再包回 MBAP 回應給 TCP Client。

## 6.2 輪詢表格範例
輪詢表格每列欄位為：
`CH / Station / Modbus CMD / REG Address / Data / Return`

範例（單筆寫入線圈 0x0001 為 0xFF00）：
- CH: CH1
- Station: 06
- Modbus CMD: 05
- REG Address: 0001
- Data: FF00

轉換後 RTU 送出：
`06 05 00 01 FF 00 ?? ??`（CRC 由系統自動補上）
回覆成功時 Return 會顯示 `FF 00`。

範例（讀取暫存器，功能碼 03，讀取數量 0x0002）：
- CH: CH0
- Station: 01
- Modbus CMD: 03
- REG Address: 0000
- Data: 0002  （此欄在 03/04 表示讀取筆數）

轉換後 RTU 送出：
`01 03 00 00 00 02 ?? ??`

回覆封包範例：
`01 03 04 00 0A 00 14 ?? ??`
其中 `04` 為 Byte Count，資料為 `00 0A 00 14`，Return 會顯示 `00 0A 00 14`。

## 6.3 Modbus TCP 封包範例
以「讀取暫存器 0x0000 起始、數量 0x0002」為例：
- Transaction ID: 0x0001
- Protocol ID: 0x0000
- Unit ID: 0x01
- PDU: `03 00 00 00 02`

完整 Modbus TCP 封包（Hex）：
`00 01 00 00 00 06 01 03 00 00 00 02`
其中 Length=0x0006（Unit ID + PDU）。

## 6.4 寫入 06/16 對應 TCP 封包範例
### 6.4.1 寫入單一暫存器 (Function 06)
- Transaction ID: 0x0002
- Protocol ID: 0x0000
- Unit ID: 0x01
- PDU: `06 00 01 00 55`（寫入 0x0001 = 0x0055）

完整 Modbus TCP 封包（Hex）：
`00 02 00 00 00 06 01 06 00 01 00 55`

### 6.4.2 寫入多個暫存器 (Function 16 / 0x10)
- Transaction ID: 0x0003
- Protocol ID: 0x0000
- Unit ID: 0x01
- 起始位址: 0x0001
- 寫入數量: 0x0002
- Byte Count: 0x04
- 資料: 0x000A, 0x0014

PDU: `10 00 01 00 02 04 00 0A 00 14`

完整 Modbus TCP 封包（Hex）：
`00 03 00 00 00 0B 01 10 00 01 00 02 04 00 0A 00 14`
其中 Length=0x000B（Unit ID + PDU）。

## 6.5 Modbus ASCII 模式範例
以寫入單一線圈 (Function 05) 為例：
- Station: 06
- Function: 05
- Address: 0001
- Data: FF00

RTU PDU: `05 00 01 FF 00`  
ASCII Frame 範例（LRC 自動計算）：  
`:06050001FF00E5\r\n`

回覆成功時，ASCII 回傳會包含相同 Station/Function/Address/Data。

## 6.6 錯誤回覆（Exception Code）範例
若裝置不支援功能碼或位址非法，回覆會以 **Function | 0x80** 表示錯誤，並附上 Exception Code：

範例（Unit ID 0x01、功能碼 0x03，回覆 Illegal Data Address 0x02）：
- 原始 PDU: `03 00 00 00 02`
- 例外 PDU: `83 02`

RTU 回覆（含 CRC）格式：
`01 83 02 ?? ??`

TCP 回覆（含 MBAP）格式：
`00 01 00 00 00 03 01 83 02`

常見 Exception Code：
- 01: Illegal Function
- 02: Illegal Data Address
- 03: Illegal Data Value
- 06: Slave Device Busy

## 6.7 CRC/LRC 計算示範
### 6.7.1 CRC-16 (Modbus RTU)
以 `01 03 00 00 00 02` 為例，CRC 計算結果為 `0xC40B`（低位元組先送）：

RTU 封包：
`01 03 00 00 00 02 0B C4`

### 6.7.2 LRC (Modbus ASCII)
以 `06 05 00 01 FF 00` 為例，LRC 為 `E5`：

ASCII 封包：
`:06050001FF00E5\r\n`

## 7. 結論
本文完成以 Pico 2 W、Pico-2CH-RS485 與 Pico-UPS-B 組成的 WiFi Modbus Gateway。系統具備 Wi-Fi 設定與 Web 操作介面，可在不更動既有 RS485 架構下提供 Modbus TCP 與 RTU/ASCII 互通能力。實測顯示可穩定發送與接收封包，具備實務應用價值。

## 參考資料
- Raspberry Pi Pico 2 產品頁：https://www.raspberrypi.com/products/raspberry-pi-pico-2/
- Waveshare Pico-2CH-RS485：https://www.waveshare.net/wiki/Pico-2CH-RS485
