# Pico Gateway Hold Register 記憶體設計指南（現行程式版）

本文件對應目前程式：`hold_register_map.py`、`register_store.py`、`main.py`、`modbus_gateway.py`。

---

## 1. 設計目標
- 提供 512 個 Hold Register 作為系統統一控制面。
- 支援「RAM 套用」與「Flash 保存」分離：
  - RAM 套用：`REG64`
  - Flash 保存：`REG60`
- 配置區讀寫自動 encode/decode，減少上位機換算負擔。

---

## 2. 記憶體分佈

| 地址範圍 | 大小 | 用途 | 權限 |
|---|---:|---|---|
| 0-49 | 50 | 配置區 | RW |
| 50-59 | 10 | 狀態區 | RO |
| 60-69 | 10 | 控制區 | WO/RO |
| 100-355 | 256 | 輪詢回填區 | RO（對 TCP） |
| 356-511 | 156 | 保留區 | -- |

---

## 3. 配置區定義（0-22）

### 3.1 TCP / Gateway
| Reg | 名稱 | 值域 | 說明 |
|---:|---|---|---|
| 0 | TCP_PORT | uint16 | Modbus TCP 監聽埠（預設 502） |
| 1 | TCP_SLAVE_ID | 1-247 | 本地寄存器 Unit ID |
| 2 | TCP_TIMEOUT | ms/10 | Gateway timeout（120 代表 1200ms） |
| 3 | TCP_RS485_MODE | enum | 0=disabled, 1=ch0, 2=ch1 |
| 4 | RESERVED | - | 保留 |

### 3.2 CH0
| Reg | 名稱 | 編碼 |
|---:|---|---|
| 5 | CH0_ENABLED | 0=disabled, 1=enabled |
| 6 | CH0_MODE | 0=rtu, 1=ascii |
| 7 | CH0_BAUDRATE | 0=2400,1=4800,2=9600,3=38400,4=115200 |
| 8 | CH0_PARITY | 0=N,1=E,2=O |
| 9 | CH0_STOPBITS | 0=1,1=2 |
| 10 | CH0_BITS | 0=8,1=7 |

### 3.3 CH1
| Reg | 名稱 | 編碼 |
|---:|---|---|
| 11 | CH1_ENABLED | 0=disabled, 1=enabled |
| 12 | CH1_MODE | 0=rtu, 1=ascii |
| 13 | CH1_BAUDRATE | 0=2400,1=4800,2=9600,3=38400,4=115200 |
| 14 | CH1_PARITY | 0=N,1=E,2=O |
| 15 | CH1_STOPBITS | 0=1,1=2 |
| 16 | CH1_BITS | 0=8,1=7 |

### 3.4 AP / Poller
| Reg | 名稱 | 值域 | 說明 |
|---:|---|---|---|
| 20 | AP_ENABLED | 0/1 | AP 啟用控制 |
| 21 | POLLER_ENABLED | 0/1 | Poller 啟用 |
| 22 | POLLER_INTERVAL | ms/10 | 輪詢週期 |

---

## 4. 狀態區（50-56）

| Reg | 名稱 | 單位/編碼 |
|---:|---|---|
| 50 | SYS_RUN_TIME | 秒 |
| 51 | SYS_CPU_TEMP | °C |
| 52 | SYS_BATT_V | 0.01V |
| 53 | SYS_BATT_I | 0.1mA |
| 54 | SYS_BATT_P | % |
| 55 | SYS_STA_CONNECTED | 0/1 |
| 56 | SYS_AP_ACTIVE | 0/1 |

更新由 `main.update_hold_register_status()` 負責，現行採節流快取（約 1 秒採樣）。

---

## 5. 控制區（60-64）

| Reg | 名稱 | 動作 |
|---:|---|---|
| 60 | CMD_SAVE_CONFIG | 寫 1：保存目前記憶體配置到 Flash |
| 61 | CMD_RESET_CONFIG | 寫 1：重置配置並重啟 |
| 62 | CMD_REBOOT | 寫 1：重啟 |
| 63 | CMD_STATUS | 讀狀態：0 idle / 1 busy / 2 success / 3 error |
| 64 | CMD_APPLY_CONFIG | 寫 1：套用配置到 RAM + UART |

---

## 6. 編碼與解碼規則

- `register_store.set_reg(..., encode=True)`：應用值 -> 寄存器值
- `register_store.get_regs(..., decode=True)`：寄存器值 -> 應用值

Modbus TCP 本地寄存器路徑：
- 讀（FC03/FC04）地址在配置區時，會 decode。
- 寫（FC06/FC16）地址在配置區時，會 encode。

---

## 7. Flash <-> Memory 同步

### 7.1 開機（Flash -> Memory）
`register_store.initialize_from_config()`
- 呼叫 `hold_register_map.update_memory_from_config()`
- 將 `config_store` 的 `modbus/ap/poller` 對應到寄存器。

### 7.2 保存（Memory -> Flash）
`main` 偵測 `REG60` 後：
1. `register_store.export_config_from_memory(current_cfg)`
2. `update_config(..., persist=True)`
3. `REG63` 回報成功或失敗

### 7.3 套用（Memory -> RAM，不落盤）
`main` 偵測 `REG64` 後：
1. 匯出記憶體配置
2. `update_config(..., persist=False)`
3. 立即 `rs485.init(...)` 套用 UART

---

## 8. 與 Gateway 路由關係（重點）

`modbus_gateway.py` 對 `Unit ID != tcp_slave_id` 的請求：
- 讀 `tcp_rs485_mode`（Reg3）
  - `disabled`：拒絕轉送，回 `0x03`
  - `ch0`：固定 CH0
  - `ch1`：固定 CH1
- 指定通道未啟用：`0x0B`
- 鎖失敗：`0x06`
- 回覆異常/逾時：`0x0B`

> `config_store` 仍保留 `tcp_indirect_control` 作相容欄位，但由 `tcp_rs485_mode` 推導。

---

## 9. Web / API 實際語意

- `POST /gateway/configure`：
  - 先寫 `REG0-16`
  - 再觸發 `REG64`
- `POST /cfg`：
  - 更新 RAM 配置
  - `modbus.ch0/ch1` 會被後端移除（UART 需走 `REG64`）
- `POST /system/save`：觸發 `REG60`
- `POST /system/reset`：觸發 `REG61`

---

## 10. 測試建議

### 10.1 REG3 路由
1. 寫 `REG3=disabled`，確認非本地 Unit ID 回 `0x03`。
2. 寫 `REG3=ch0`，確認非本地 Unit ID 只走 CH0。
3. 寫 `REG3=ch1`，確認非本地 Unit ID 只走 CH1。

### 10.2 保存語意
1. Web 修改 Gateway/Poller 設定。
2. 不送 `REG60` 直接重開機 -> 設定不保證保留。
3. 送 `REG60=1` 後重開機 -> 設定保留。

### 10.3 命令狀態
- 觸發 `REG60/61/62/64` 後讀 `REG63`，檢查狀態轉換是否符合預期。

---

## 11. 常見誤解
- 「按下設定就會寫 Flash」：錯。大多數僅套用 RAM；需 `REG60` 才落盤。
- 「REG3=enabled/disabled」：錯。REG3 是三態（`disabled/ch0/ch1`）。
- 「AP 是否開啟只看 config 檔」：錯。開機流程會受 STA 連線結果與 REG20 共同影響。
