# Gateway 記憶體與流程實作檢查清單（現行程式）

本清單同步目前程式碼，供回歸檢查與交付確認。

---

## 1. 核心模組狀態

- [x] `hold_register_map.py`
  - [x] 512 暫存器區域定義（config/status/control/poll/reserved）
  - [x] enum encode/decode（Reg3/6-16/20-22/63）
  - [x] `update_memory_from_config()`
  - [x] `prune_config_from_memory()`

- [x] `register_store.py`
  - [x] `set_reg/set_regs/get_regs`
  - [x] `initialize_from_config/export_config_from_memory`
  - [x] `update_status`
  - [x] `check_and_clear_command`（含 `apply`）
  - [x] `set_command_status`

- [x] `main.py`
  - [x] 開機初始化 Hold Register
  - [x] STA + REG20 AP 決策
  - [x] 服務啟動（HTTP/Modbus/CMD/mDNS）
  - [x] 系統檢查（UPS + RS485）
  - [x] Core1 poller worker（REG21）
  - [x] 命令寄存器執行（REG60/61/62/64）

- [x] `modbus_gateway.py`
  - [x] 事件式非阻塞 TCP 處理
  - [x] 本地寄存器 FC03/04/06/16
  - [x] REG3 三態路由（disabled/ch0/ch1）
  - [x] 通道鎖與 exception code

- [x] `Web_Page.py`
  - [x] Gateway 設定走 `POST /gateway/configure` + `REG64`
  - [x] System 區塊 `REG60/REG61`
  - [x] AP Enable 對應 `REG20`，顯示讀 `REG56`
  - [x] Poller 套用為 RAM，落盤需 `REG60`

---

## 2. 寄存器範圍與命令位

- [x] 配置區：`0-49`
- [x] 狀態區：`50-59`
- [x] 控制區：`60-69`
- [x] 輪詢區：`100-355`
- [x] 保留區：`356-511`

控制命令：
- [x] `REG60` Save
- [x] `REG61` Reset
- [x] `REG62` Reboot
- [x] `REG63` Status
- [x] `REG64` Apply

---

## 3. 配置套用語意

- [x] Web/HTTP 多數設定為 RAM 套用（`persist=False`）
- [x] `REG64` 會把寄存器配置套用到執行中設定 + UART
- [x] `REG60` 才寫入 Flash（重開機保留）
- [x] `POST /cfg` 不允許直接改 `modbus.ch0/ch1`（需走 `REG64`）

---

## 4. 重要流程驗證項目

### 4.1 開機流程
- [ ] 開機時顯示 `Memory initialized from config`
- [ ] STA 有保存時會嘗試連線
- [ ] STA 失敗 + REG20=0 會強制啟 AP 並修正 REG20=1
- [ ] 服務啟動後進入系統檢查

### 4.2 Modbus 路由
- [ ] `Unit ID=tcp_slave_id` 可 FC03/04 讀本地
- [ ] `Unit ID=tcp_slave_id` 可 FC06/16 寫本地
- [ ] `REG3=disabled` 時，非本地 Unit ID 回 `0x03`
- [ ] `REG3=ch0/ch1` 時固定轉送指定通道

### 4.3 命令寄存器
- [ ] 寫 `REG64=1` -> `REG63` 經過 busy 到 success/error
- [ ] 寫 `REG60=1` -> 配置落盤成功
- [ ] 寫 `REG61=1` -> 重置並重啟
- [ ] 寫 `REG62=1` -> 重啟

### 4.4 Poller
- [ ] `POST /poller/start` 後 `poller.status.enabled=true`
- [ ] 輪詢回填可在 map 區讀取
- [ ] 通道忙碌時 Return 顯示 `BUSY`

---

## 5. 文件與圖表同步

- [x] `README_zh.md` 與程式一致
- [x] `Project_Manual_zh.md` 與程式一致
- [x] `MEMORY_DESIGN_GUIDE.md` 與程式一致
- [x] `flowcharts.md` 與程式一致
- [x] `main_flow.drawio` 更新
- [x] `http_flow.drawio` 更新
- [x] `modbus_flow.drawio` 更新
- [x] `poller_flow.drawio` 更新
- [x] `hardware_arch.drawio` 檢查/更新

---

## 6. 回歸命令（建議）

```bash
python3 -m py_compile main.py Web_Page.py modbus_gateway.py poller.py register_store.py hold_register_map.py config_store.py wifi_Scan_Connect.py
```

```bash
# 關鍵字檢查（避免舊語意）
rg -n "AUTO_CONFIG_AP_ON_BOOT|tcp_indirect_control.*unit_map|60-63 控制區" README_zh.md Project_Manual_zh.md flowcharts.md MEMORY_DESIGN_GUIDE.md
```
