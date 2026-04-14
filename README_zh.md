# Pico 2 W Wi-Fi Modbus Gateway 說明（中文）

本專案在 Raspberry Pi Pico 2 W 上提供：
- 內建 AP + Captive DNS 設定入口（預設 `PicoSetup` / `pico1234`）。
- STA 連線既有 Wi-Fi（可「套用到 RAM」，按 `REG60` 才寫入 Flash）。
- 2-Port Modbus RTU/ASCII <-> 1-Port Modbus TCP Gateway。
- Web UI 管理 Gateway、Poller、AP/STA、System 控制。
- Hold Register 512 格式化記憶體（配置/狀態/控制/輪詢）。

## 啟動流程（以現行程式為準）
1. `main.py` 讀取 `config_store`，並初始化 Hold Register 512（`register_store.initialize_from_config`）。
2. 若有保存 STA，先嘗試連線（目前 timeout 約 6 秒）。
3. 讀 `REG20 (AP enable)` 決策 AP：
   - STA 成功 + `REG20=1` -> 啟 AP。
   - STA 成功 + `REG20=0` -> 不啟 AP。
   - STA 失敗 + `REG20=1` -> 啟 AP。
   - STA 失敗 + `REG20=0` -> 強制啟 AP，並回寫 `REG20=1`。
4. 啟動網路服務：HTTP(80) + Modbus TCP(502) + CMD TCP(12345) + mDNS（若 5353 未被占用）。
5. 等待網路穩定後執行開機檢查：UPS/INA219 + 已啟用 RS485 通道。
6. 讀 `REG21` 決定是否啟動 Core1 Poller worker。
7. Core0 主迴圈每 20ms：`poll_cmd_server -> poll_http_server -> poll_modbus_tcp_server -> 更新狀態寄存器`。
8. 主迴圈每 100ms 檢查控制命令（`REG60/61/62/64`）。

## Hold Register 關鍵控制位
- `REG20`：AP Enable（0=off, 1=on）
- `REG21`：Poller Enable（0=off, 1=on）
- `REG60`：Save Config（寫 1 -> RAM 設定寫入 Flash）
- `REG61`：Reset Config（寫 1 -> 回預設並重啟）
- `REG62`：Reboot（寫 1 -> 重啟）
- `REG63`：Command Status（0 idle / 1 busy / 2 success / 3 error）
- `REG64`：Apply Config（寫 1 -> 將 Register 配置套用到 RAM + UART）

## Modbus Gateway 行為
- `Unit ID == tcp_slave_id`：走本地 Hold Register（FC03/FC04 讀；FC06/FC16 寫）。
- `Unit ID != tcp_slave_id`：依 `REG3 (TCP_RS485_MODE)`：
  - `disabled`：拒絕轉送，回 Exception `0x03`。
  - `ch0`：固定轉送 CH0。
  - `ch1`：固定轉送 CH1。
- 指定通道未啟用時回 `0x0B`；鎖失敗回 `0x06`；下游回覆異常/逾時回 `0x0B`。

## 設定套用與保存
- Gateway UART 參數由 `POST /gateway/configure` 寫入配置區後，觸發 `REG64` 套用。
- 大多數 Web 設定先更新 RAM（`update_config(..., persist=False)`）。
- 只有按 System 的「存入 Flash（REG60）」才會持久化。

## HTTP API（Port 80）
- `GET /`：Web UI
- `GET /wifi/scan`
- `GET /wifi/status`
- `POST /wifi/connect`
- `POST /ap/enable`（對應 `REG20`）
- `GET /cfg`
- `POST /cfg`（UART `ch0/ch1` 欄位不接受直接更新，需走 `REG64`）
- `POST /cfg/reset`（清空設定並重啟）
- `POST /gateway/configure`（寫配置區 + 觸發 `REG64`）
- `POST /system/save`（觸發 `REG60`）
- `POST /system/reset`（觸發 `REG61`）
- `GET /poller/config`
- `POST /poller/start`
- `POST /poller/stop`
- `GET /poller/status`
- `POST /cmd`

## Poller 行為
- 設定來源：`config.poller`，最多 256 rows。
- `interval_ms` 最小 50ms。
- timeout 統一使用 `modbus.response_timeout_ms`。
- 每次 `tick()` 只處理一列，完成後 `next_due = 完成時間 + interval`。
- 回填區域主要為 100-355（依 row index 對應）。

## 預設設定重點
- AP：`PicoSetup` / `pico1234` / `enabled=true`
- STA：空
- Modbus：
  - `tcp_port=502`
  - `tcp_slave_id=1`
  - `response_timeout_ms=1200`
  - `tcp_rs485_mode="disabled"`
  - `tcp_indirect_control=false`（相容欄位，由 `tcp_rs485_mode` 推導）
  - `ch0_enabled=false`, `ch1_enabled=false`
- Poller：`enabled=false`, `interval_ms=1000`

## 快速測試
- `curl http://192.168.4.1/wifi/status`
- `echo 'SYS STATUS' | nc 192.168.4.1 12345`
- Modbus TCP client 連 `502` 測試：
  - `Unit ID=tcp_slave_id`：讀寫本地 registers
  - `Unit ID!=tcp_slave_id` + `REG3=ch0/ch1`：轉送 RS485

## 文件
- 詳細手冊：`Project_Manual_zh.md`
- 記憶體設計：`MEMORY_DESIGN_GUIDE.md`
- 統一流程圖：`flowcharts.md`
