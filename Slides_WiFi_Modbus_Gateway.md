# WiFi Modbus Gateway（專案新版）

## 1. 專案概覽
- Raspberry Pi Pico 2 W + Pico-2CH-RS485 + Pico-UPS-B
- 2-Port Modbus RTU/ASCII → 1-Port Modbus TCP 閘道器
- AP 模式點對點連線 + 可選 STA 模式連家用網路

## 2. 目標與價值
- Modbus TCP 與 Modbus RTU/ASCII 互通
- Wi‑Fi AP 模式可點對點接入
- 設定永久保存、停電不遺失

## 3. 系統架構
- Pico 2 W：主控 + Wi‑Fi AP/STA + Modbus TCP
- Pico-2CH-RS485：UART0/1 ↔ RS485 CH0/CH1
- Pico-UPS-B：電源/電量監測（INA219）

## 4. 硬體連接
- UART0 (GP0/GP1) → RS485 CH0
- UART1 (GP4/GP5) → RS485 CH1
- I2C → UPS/INA219
- Wi‑Fi AP/STA 共用

## 5. 韌體模組
- `main.py`：狀態機 + 伺服器輪詢
- `modbus_gateway.py`：Modbus TCP ↔ RTU/ASCII
- `poller.py`：輪詢表格（循環）
- `Web_Page.py`：Web UI（設定/輪詢/日誌）
- `Server_CMD.py`：RS485 HEX 測試指令
- `config_store.py`：設定永久保存
- `Pico_RS485.py`：UART/RS485 封裝
- `wifi_Scan_Connect.py`：Wi‑Fi 管理
- `Pico_UPS.py`：電源監測

## 6. Web UI 功能
- Gateway 設定：CH0/CH1 模式/baud/parity/stopbits/bits
- AP 設定與 STA 連線（可保存）
- RS485 HEX 測試（可選 CH0/CH1）
- 輪詢表格：新增/刪除/儲存/啟動
- Log 顯示 + System Reset（清空設定）

## 7. Modbus RTU/ASCII 支援
- RTU：CRC-16 (0xA001, init 0xFFFF)
- ASCII：LRC 檢查
- RS485 TX/RX 狀態顯示

## 8. 通訊流程（TCP↔RTU/ASCII）
1) Client 連線 PicoSetup AP
2) Modbus TCP 送出請求（port 502）
3) 轉換 RTU/ASCII 並送至 CH0/CH1
4) 收到回覆後回傳 TCP

## 9. 測試結果
- RS485 HEX 測試可正常 TX/RX
- 輪詢表格可循環送出並顯示回覆
- AP 模式可穩定連線與操作

## 10. 已知限制
- Pico 2 W 只支援 2.4GHz 802.11 b/g/n
- RS485 同通道需序列化處理（半雙工）

## 11. 可擴充方向
- 依欄位自訂輪詢間隔
- 解析回覆為數值/JSON
- 連接雲端與告警機制

## 12. 結論
- 完成 2-Port RTU/ASCII ↔ Modbus TCP Wi‑Fi 閘道器
- Web 設定與輪詢控制一體化
- 保留既有工業設備，提升布線與維運彈性
