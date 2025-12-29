# 使用 Raspberry Pi Pico2 W、Pico-2CH-RS485、Pico-UPS-B 之 WiFi to Modbus RS485 Gateway 設計

## 摘要
本文設計並實作一套以 Raspberry Pi Pico2 W 為核心、搭配 Waveshare Pico-2CH-RS485 與 Pico-UPS-B 的 WiFi to Modbus RS485 Gateway。系統提供 Wi-Fi AP/STA 模式、Web UI 與命令通道，支援 Modbus RTU 格式封包的發送與回覆接收，並可透過 UPS 模組監測電壓/電流以提升可靠度。本文說明硬體架構、韌體模組化設計、網頁控制介面、CRC 計算與通訊驗證流程。

## 關鍵字
Raspberry Pi Pico2 W、Modbus RTU、RS485、Wi-Fi Gateway、嵌入式系統

## 1. 緒論
工業現場常以 RS485 匯流排連接感測器與致動器，Modbus RTU 為常見通訊協議。為了讓既有 RS485 裝置具備無線存取與遠端控制能力，本文以 Pico2 W 作為 Wi-Fi 與控制核心，搭配雙通道 RS485 擴展板與 UPS 電源模組，建構一套 WiFi to Modbus RS485 Gateway，提升設備部署彈性並保留既有通訊架構。

## 2. 系統需求與設計目標
1. **Wi-Fi 連線能力**：支援 AP 模式供行動裝置設定，並能切換至 STA 連線既有網路。
2. **Modbus RTU 通訊**：透過 RS485 傳輸 Modbus RTU 封包並接收回覆。
3. **Web 介面操作**：提供簡易網頁輸入指令或 HEX 封包。
4. **電源監測**：透過 UPS 模組監測電池電壓與電流狀態。
5. **易於擴充**：韌體模組化設計，方便後續擴增指令或協議。

## 3. 硬體架構
### 3.1 核心模組
- **Raspberry Pi Pico2 W**：具備 RP 系列 MCU 與 Wi-Fi 功能，作為控制與通訊核心。

### 3.2 RS485 介面
- **Pico-2CH-RS485**：提供雙通道 RS485 轉換，本文使用 CH0 (UART0, GP0/GP1)。

### 3.3 電源與監測
- **Pico-UPS-B**：提供電池供電與電壓/電流量測介面，提升系統穩定性。

### 3.4 介面對應
- UART0 TX/RX → RS485 CH0 TX/RX
- UPS 模組 I2C → Pico2 W I2C 腳位

## 4. 軟體與韌體設計
系統韌體以模組化方式撰寫，核心包含：
- `main.py`：主程式與狀態機
- `Pico_RS485.py`：RS485 UART 發送/接收
- `Server_CMD.py`：命令解析與 TCP/HTTP 介面
- `Web_Page.py`：嵌入式 Web UI
- `wifi_Scan_Connect.py`：Wi-Fi 掃描、連線與 AP 管理

### 4.1 Wi-Fi 模式與 Captive Portal
系統開機可啟動 PicoSetup AP，並提供 Captive DNS 將所有網域導向設定頁。使用者可在瀏覽器輸入 `http://192.168.4.1` 設定 STA 連線。

### 4.2 Modbus RTU 與 CRC
Modbus RTU 封包採用 CRC-16 (poly 0xA001, init 0xFFFF)，CRC 低位元組先送。Web UI 可輸入 6 bytes，系統自動補 CRC 形成 8 bytes 後送出。

### 4.3 Web UI 操作流程
1. 連線 PicoSetup AP
2. 開啟 Web UI
3. 輸入 RS485 HEX（6 bytes）
4. 系統自動計算 CRC、組成完整封包並送出
5. 回覆封包顯示於 Log

### 4.4 RS485 接收策略
UART 非阻塞接收，封包可能分段到達，因此在上層彙整後一次性輸出，以提高可讀性並避免誤判。

## 5. 測試與結果
- **封包送達性**：發送 Modbus RTU 封包，Slave 可正確動作。
- **回覆接收**：透過延長等待時間與彙整 RX 緩衝，成功取得完整回覆。
- **Web UI 可用性**：可從手機直接發送指令並觀察回覆。

## 6. 討論
1. **無 DE/RE GPIO 控制**：本硬體以 TX 觸發方向控制，需在韌體層透過等待時間補足接收窗口。
2. **瀏覽器背景請求**：手機瀏覽器可能發出額外連線，造成 log 噪音，但不影響 RS485 功能。
3. **擴充性**：可加入 Modbus 資料解析、批次輪詢與資料上雲等功能。

## 7. 結論
本文完成以 Pico2 W、Pico-2CH-RS485 與 Pico-UPS-B 組成的 WiFi to Modbus RS485 Gateway。系統具備 Wi-Fi 設定與 Web 操作介面，可在不更動既有 RS485 架構下提供無線存取能力。實測顯示可穩定發送與接收 Modbus RTU 封包，具備實務應用價值。

## 參考資料
- Raspberry Pi Pico 2 產品頁：https://www.raspberrypi.com/products/raspberry-pi-pico-2/
- Waveshare Pico-2CH-RS485：https://www.waveshare.net/wiki/Pico-2CH-RS485
