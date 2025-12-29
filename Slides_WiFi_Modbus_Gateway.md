# WiFi to Modbus RS485 Gateway

## 1. 專案概覽
- 以 Raspberry Pi Pico2 W 為核心
- 結合 Pico-2CH-RS485 與 Pico-UPS-B
- 提供 Wi‑Fi 控制與 RS485 Modbus RTU 通訊

## 2. 目標與價值
- RS485 裝置無線化
- 保留既有 Modbus RTU 架構
- 行動裝置即可操作與維護

## 3. 系統架構
- Pico2 W：主控制 + Wi‑Fi
- Pico-2CH-RS485：UART↔RS485
- Pico-UPS-B：電壓/電流監測

## 4. 硬體連接
- UART0 (GP0/GP1) → RS485 CH0
- I2C → UPS/INA219
- AP/STA 模式共用 Wi‑Fi

## 5. 韌體模組
- `main.py`：狀態機與輪詢
- `Web_Page.py`：Web UI
- `Server_CMD.py`：指令解析
- `Pico_RS485.py`：UART 封裝
- `wifi_Scan_Connect.py`：Wi‑Fi 管理
- `Pico_UPS.py`：電源監測

## 6. Web UI 功能
- 快速指令（SYS/LED）
- Wi‑Fi 掃描與連線
- RS485 HEX 送出
- Log 回應顯示

## 7. Modbus RTU 支援
- CRC-16 (0xA001, init 0xFFFF)
- 6 bytes 自動補 CRC → 8 bytes
- 回覆封包顯示於 Log

## 8. 通訊流程
1) 連線 PicoSetup AP
2) 開啟 Web UI
3) 輸入 HEX
4) 自動補 CRC 並送出
5) 接收回覆

## 9. 測試結果
- Slave 可正確動作
- 回覆封包成功接收
- Web 操作可用

## 10. 已知限制
- DE/RE 自動切換，需等待窗口
- 瀏覽器背景請求造成 log 雜訊

## 11. 可擴充方向
- 批次輪詢
- 自動資料上雲
- 多通道 RS485 支援

## 12. 結論
- 成功建構 WiFi→Modbus RS485 Gateway
- 保留既有工業裝置，提升遠端管理能力
