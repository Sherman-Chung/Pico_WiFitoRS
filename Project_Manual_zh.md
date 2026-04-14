# Pico 2 W Wi-Fi Modbus Gateway 專案手冊

本手冊以目前程式碼為準（`main.py`、`Web_Page.py`、`modbus_gateway.py`、`poller.py`、`register_store.py`）。

---

## 1. 專案定位
- 目標：2-Port Modbus RTU/ASCII <-> 1-Port Modbus TCP Gateway。
- 平台：Raspberry Pi Pico 2 W（Web UI 版）。
- 網路：AP + STA（開機行為由 STA 狀態與 `REG20` 決策）。
- 配置模型：
  - RAM 配置（`config_store` cache + Hold Register）
  - Flash 配置（`config_store.json`，需 `REG60` 才寫入）

---

## 2. 系統架構
### 2.1 核心模組
- `main.py`：開機狀態機、服務啟動、主迴圈、命令寄存器執行。
- `wifi_Scan_Connect.py`：STA/AP 控制、掃描、連線、Captive DNS。
- `Web_Page.py`：HTTP server + UI + API 路由。
- `modbus_gateway.py`：Modbus TCP 封包處理與 RS485 轉送。
- `poller.py`：輪詢排程、通訊、結果回填。
- `config_store.py`：設定驗證/更新/持久化。
- `register_store.py`：Hold Register 512 存取、命令檢測、狀態更新。
- `hold_register_map.py`：寄存器定義、編碼/解碼、Flash<->Memory 同步。

### 2.2 執行模型
- Core0：`CMD TCP -> HTTP -> Modbus TCP` 輪詢、狀態更新、命令檢查。
- Core1（可用時）：`poller_tick()` + `sleep 5ms`。
- RS485 使用通道鎖，避免半雙工衝突。

---

## 3. 開機狀態機
1. `get_config()` 讀取配置。
2. `register_store.initialize_from_config()` 將配置寫入 Hold Register（0-22）。
3. 若有保存 STA，嘗試 `connect_to_ap(..., timeout_ms=6000)`。
4. 根據 `STA 連線結果 + REG20` 決定 AP：
   - STA 成功 + REG20=1：啟 AP。
   - STA 成功 + REG20=0：不啟 AP。
   - STA 失敗 + REG20=1：啟 AP。
   - STA 失敗 + REG20=0：強制啟 AP，並回寫 REG20=1。
5. 啟動 HTTP/Modbus/CMD 服務與 mDNS（5353 被占用時會記錄訊息並略過）。
6. `wait_for_network_stable()`。
7. `run_system_checks()`：UPS + 已啟用 RS485；失敗進入 `fail_halt()`。
8. 依 REG21 決定是否啟 Core1 poller。
9. 進入主迴圈。

---

## 4. Hold Register 設計（摘要）
- 配置區：`0-49`
- 狀態區：`50-59`（唯讀）
- 控制區：`60-69`
- 輪詢結果：`100-355`
- 保留：`356-511`

### 4.1 關鍵寄存器
- `REG3`：`TCP_RS485_MODE`（`disabled/ch0/ch1`）
- `REG20`：AP Enable
- `REG21`：Poller Enable
- `REG56`：AP Active（狀態）
- `REG60`：Save Config 到 Flash
- `REG61`：Reset Config
- `REG62`：Reboot
- `REG63`：Command Status
- `REG64`：Apply Config（尤其 UART 參數）

---

## 5. Modbus Gateway（Port 502）
### 5.1 本地寄存器模式
當 `Unit ID == tcp_slave_id`：
- FC03/FC04：讀本地 Hold Register
- FC06/FC16：寫本地 Hold Register
- 配置區 0-22 讀寫具自動 decode/encode

### 5.2 RS485 轉送模式
當 `Unit ID != tcp_slave_id`：
- 先看 `tcp_rs485_mode`（對應 REG3）
  - `disabled`：不轉送，回 `0x03`
  - `ch0`：固定轉送到 CH0
  - `ch1`：固定轉送到 CH1
- 目標通道未啟用：回 `0x0B`
- 鎖失敗：回 `0x06`
- 下游回覆無效/逾時：回 `0x0B`

### 5.3 UART 套用
- Web Gateway 設定會先寫入配置寄存器（0-16），再觸發 `REG64`。
- `main.py` 收到 `apply` 命令後即時 `rs485.init(...)` 套用 UART。

---

## 6. HTTP API（Port 80）
### 6.1 Wi-Fi / AP
- `GET /wifi/scan`
- `GET /wifi/status`
- `POST /wifi/connect`
- `POST /ap/enable`（同步 REG20）

### 6.2 配置
- `GET /cfg`
- `POST /cfg`（RAM 套用，`modbus.ch0/ch1` 會被忽略，需走 REG64）
- `POST /cfg/reset`
- `POST /gateway/configure`（寫 0-16 + 觸發 REG64）

### 6.3 System
- `POST /system/save`（觸發 REG60）
- `POST /system/reset`（觸發 REG61）

### 6.4 Poller
- `GET /poller/config`
- `POST /poller/start`
- `POST /poller/stop`
- `GET /poller/status`

### 6.5 Command Proxy
- `POST /cmd` -> `Server_CMD.handle_cmd`

---

## 7. Poller
- 啟用條件：`poller.enabled`，或 `set_enabled()` 設定 `_force_enabled`。
- `interval_ms >= 50`。
- 每次 `tick()` 只處理一列。
- timeout 統一來自 `modbus.response_timeout_ms`。
- 結果回填策略：
  - FC03/FC04：解析 byte count，連續寫入寄存器。
  - FC05/FC06：寫單一寄存器。
- 回填索引以 row index 為基準（實務上對應 100-355 由 map 規劃）。

---

## 8. 設定保存語意
- 大多數 Web 操作只改 RAM。
- `REG64`：套用寄存器配置到執行中設定與 UART（不落盤）。
- `REG60`：將目前寄存器配置匯出並寫入 Flash。
- 沒有 `REG60` 的操作，重開機後不保證保留。

---

## 9. 例外與已知訊息
- `mDNS unavailable: port 5353 already in use`：mDNS 未啟用，但不影響 AP/HTTP/Modbus。
- `HTTP recv header error: ETIMEDOUT`：常見於 client 半開連線超時，非致命。
- `CYW43 do_ioctl ... timeout`：Wi-Fi driver 壓力訊息，已在程式中採取狀態讀取節流降低機率。

---

## 10. 預設值（config_store）
- AP：`ssid=PicoSetup`、`password=pico1234`、`enabled=true`
- STA：空
- Modbus：
  - `tcp_port=502`
  - `response_timeout_ms=1200`
  - `tcp_slave_id=1`
  - `tcp_rs485_mode="disabled"`
  - `tcp_indirect_control=false`（相容欄位）
  - `ch0_enabled=false`
  - `ch1_enabled=false`
- Poller：`enabled=false`、`interval_ms=1000`

---

## 11. 快速驗證
- `curl http://192.168.4.1/wifi/status`
- `echo 'SYS STATUS' | nc 192.168.4.1 12345`
- Modbus TCP：
  - Unit ID=`tcp_slave_id` 讀寫本地 registers
  - Unit ID!=`tcp_slave_id` + REG3=`ch0/ch1` 測 RS485 轉送
