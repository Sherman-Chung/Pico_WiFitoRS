# Pico_UPS.py - 將 INA219 量測與電池百分比計算封裝在此，使用時僅需匯入本模組
# 內含簡化版 INA219 驅動（參考 pico-UPS-B 範例），預設嘗試位址 0x43/0x40。

import time
from machine import I2C

# ---------- INA219 Register 定義 ----------
_REG_CONFIG = 0x00
_REG_SHUNTVOLTAGE = 0x01
_REG_BUSVOLTAGE = 0x02
_REG_POWER = 0x03
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05


class BusVoltageRange:
    RANGE_16V = 0x00
    RANGE_32V = 0x01


class Gain:
    DIV_1_40MV = 0x00
    DIV_2_80MV = 0x01
    DIV_4_160MV = 0x02
    DIV_8_320MV = 0x03


class ADCResolution:
    ADCRES_9BIT_1S = 0x00
    ADCRES_10BIT_1S = 0x01
    ADCRES_11BIT_1S = 0x02
    ADCRES_12BIT_1S = 0x03
    ADCRES_12BIT_2S = 0x09
    ADCRES_12BIT_4S = 0x0A
    ADCRES_12BIT_8S = 0x0B
    ADCRES_12BIT_16S = 0x0C
    ADCRES_12BIT_32S = 0x0D
    ADCRES_12BIT_64S = 0x0E
    ADCRES_12BIT_128S = 0x0F


class Mode:
    POWERDOW = 0x00
    SVOLT_TRIGGERED = 0x01
    BVOLT_TRIGGERED = 0x02
    SANDBVOLT_TRIGGERED = 0x03
    ADCOFF = 0x04
    SVOLT_CONTINUOUS = 0x05
    BVOLT_CONTINUOUS = 0x06
    SANDBVOLT_CONTINUOUS = 0x07


class INA219:
    """簡化版 INA219 驅動，預設配置 32V/2A。"""

    def __init__(self, i2c_bus=1, addr=0x40):
        self.i2c = I2C(i2c_bus)
        self.addr = addr
        self._cal_value = 0
        self._current_lsb = 0
        self._power_lsb = 0
        self.set_calibration_32V_2A()

    def read(self, address):
        data = self.i2c.readfrom_mem(self.addr, address, 2)
        return (data[0] * 256) + data[1]

    def write(self, address, data):
        temp = bytearray(2)
        temp[1] = data & 0xFF
        temp[0] = (data & 0xFF00) >> 8
        self.i2c.writeto_mem(self.addr, address, temp)

    def set_calibration_32V_2A(self):
        """配置 32V / 2A 量測；取樣次數調低以縮短轉換時間。"""
        self._current_lsb = 1  # 100uA/bit
        self._cal_value = 4096
        self._power_lsb = 0.002  # 2mW/bit

        self.write(_REG_CALIBRATION, self._cal_value)
        self.bus_voltage_range = BusVoltageRange.RANGE_32V
        self.gain = Gain.DIV_8_320MV
        # 取樣 12bit/4S，兼顧速度與穩定度
        self.bus_adc_resolution = ADCResolution.ADCRES_12BIT_4S
        self.shunt_adc_resolution = ADCResolution.ADCRES_12BIT_4S
        self.mode = Mode.SANDBVOLT_CONTINUOUS
        self.config = (
            self.bus_voltage_range << 13
            | self.gain << 11
            | self.bus_adc_resolution << 7
            | self.shunt_adc_resolution << 3
            | self.mode
        )
        self.write(_REG_CONFIG, self.config)

    def getShuntVoltage_mV(self):
        value = self.read(_REG_SHUNTVOLTAGE)
        if value > 32767:
            value -= 65535
        return value * 0.01

    def getBusVoltage_V(self):
        self.read(_REG_BUSVOLTAGE)
        return (self.read(_REG_BUSVOLTAGE) >> 3) * 0.004

    def getCurrent_mA(self):
        value = self.read(_REG_CURRENT)
        if value > 32767:
            value -= 65535
        return value * self._current_lsb


# ---------- 電量封裝 ----------
_ina219 = None
_batt_cache = None
_batt_last_ms = 0
_batt_err = None
_batt_printed = False
_available = True  # 檢測模組是否存在；初始化失敗則關閉電量顯示


def _init_ina219():
    """嘗試初始化 INA219（地址 0x43 -> 0x40），成功回實例，失敗回 None。"""
    global _ina219, _available
    if _ina219 is not None:
        return _ina219
    if not _available:
        return None
    for addr in (0x43, 0x40):
        try:
            _ina219 = INA219(addr=addr)
            print("INA219 init ok, addr", hex(addr))
            return _ina219
        except Exception as e:
            print("INA219 init failed at", hex(addr), ":", e)
            _ina219 = None
    _available = False
    return None


def read_battery(force: bool = False):
    """讀取電池電壓/電流與粗估百分比；失敗時回最後成功值或 None。"""
    global _batt_cache, _batt_last_ms, _batt_err, _batt_printed, _available
    now = time.ticks_ms()
    if not force and _batt_cache is not None and time.ticks_diff(now, _batt_last_ms) < 300:
        return _batt_cache

    ina = _init_ina219()
    if ina is None:
        return _batt_cache if _available else None
    try:
        bus_voltage = ina.getBusVoltage_V()
        current = ina.getCurrent_mA() / 1000.0  # 轉 A
        percent = (bus_voltage - 3.0) / 1.2 * 100  # 3.0~4.2V 線性估計
        if percent < 0:
            percent = 0
        elif percent > 100:
            percent = 100
        _batt_cache = {"v": bus_voltage, "i": current, "p": percent}
        _batt_last_ms = now
        _batt_err = None
        if not _batt_printed:
            print("INA219 read ok:", _batt_cache)
            _batt_printed = True
        return _batt_cache
    except Exception as e:
        print("INA219 read failed:", e)
        _batt_err = str(e)
        return _batt_cache


def battery_gauge_text():
    """回傳電量百分比文字，供抬頭列顯示。"""
    batt = _batt_cache or read_battery()
    if batt is None:
        return ""
    return f"{batt['p']:.0f}%"


def tick_battery(force: bool = False):
    """在主迴圈週期呼叫，預設 2 秒更新一次以降低卡頓。"""
    now = time.ticks_ms()
    if not force and time.ticks_diff(now, _batt_last_ms) < 2000:
        return
    read_battery()


def last_battery_error():
    """取得最近的讀取錯誤字串（若有）。"""
    return _batt_err
