# LCD_Control.py - LCD 驅動 + 控制輔助整合版
# 已將 pico_lcd_1_3 原始驅動合併進來，對外僅需匯入 lcd、顏色與繪圖小工具即可。

from array import array
from machine import Pin, SPI, PWM
import framebuf

try:
    from config import FORCE_HEADLESS
except ImportError:
    FORCE_HEADLESS = False

# =============== LCD 硬體腳位 ===============
BL = 13
DC = 8
RST = 12
MOSI = 11
SCK = 10
CS = 9
LCD_AVAILABLE = False
LCD_INIT_ERROR = None


class _DummyLCD:
    """簡易空實作：當偵測不到 LCD 時避免 UI 呼叫崩潰。"""

    _is_dummy = True
    width = 240
    height = 240

    def fill(self, *args, **kwargs):
        pass

    def fill_rect(self, *args, **kwargs):
        pass

    def text(self, *args, **kwargs):
        pass

    def rect(self, *args, **kwargs):
        pass

    def line(self, *args, **kwargs):
        pass

    def poly(self, *args, **kwargs):
        pass

    def show(self, *args, **kwargs):
        pass


class LCD_1inch3(framebuf.FrameBuffer):
    """Pico-LCD-1.3 的驅動實作，直接繼承 FrameBuffer。"""

    def __init__(self):
        self.width = 240
        self.height = 240

        self.cs = Pin(CS, Pin.OUT)
        self.rst = Pin(RST, Pin.OUT)

        self.cs(1)
        # SPI 預設 100MHz；若不穩定可下調（介面共用 MOSI/SCK/CS）
        self.spi = SPI(1, 100000000, polarity=0, phase=0, sck=Pin(SCK), mosi=Pin(MOSI), miso=None)
        self.dc = Pin(DC, Pin.OUT)
        self.dc(1)
        self.buffer = bytearray(self.height * self.width * 2)
        super().__init__(self.buffer, self.width, self.height, framebuf.RGB565)
        self.init_display()

    def write_cmd(self, cmd):
        self.cs(1)
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([cmd]))
        self.cs(1)

    def write_data(self, buf):
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(bytearray([buf]))
        self.cs(1)

    def init_display(self):
        """初始化 LCD 控制器。"""
        self.rst(1)
        self.rst(0)
        self.rst(1)

        # 以下為 ST7789 初始化序列：主要設定色彩格式、伽瑪曲線等
        self.write_cmd(0x36)
        self.write_data(0x70)

        self.write_cmd(0x3A)
        self.write_data(0x05)

        self.write_cmd(0xB2)
        self.write_data(0x0C)
        self.write_data(0x0C)
        self.write_data(0x00)
        self.write_data(0x33)
        self.write_data(0x33)

        self.write_cmd(0xB7)
        self.write_data(0x35)

        self.write_cmd(0xBB)
        self.write_data(0x19)

        self.write_cmd(0xC0)
        self.write_data(0x2C)

        self.write_cmd(0xC2)
        self.write_data(0x01)

        self.write_cmd(0xC3)
        self.write_data(0x12)

        self.write_cmd(0xC4)
        self.write_data(0x20)

        self.write_cmd(0xC6)
        self.write_data(0x0F)

        self.write_cmd(0xD0)
        self.write_data(0xA4)
        self.write_data(0xA1)

        self.write_cmd(0xE0)
        self.write_data(0xD0)
        self.write_data(0x04)
        self.write_data(0x0D)
        self.write_data(0x11)
        self.write_data(0x13)
        self.write_data(0x2B)
        self.write_data(0x3F)
        self.write_data(0x54)
        self.write_data(0x4C)
        self.write_data(0x18)
        self.write_data(0x0D)
        self.write_data(0x0B)
        self.write_data(0x1F)
        self.write_data(0x23)

        self.write_cmd(0xE1)
        self.write_data(0xD0)
        self.write_data(0x04)
        self.write_data(0x0C)
        self.write_data(0x11)
        self.write_data(0x13)
        self.write_data(0x2C)
        self.write_data(0x3F)
        self.write_data(0x44)
        self.write_data(0x51)
        self.write_data(0x2F)
        self.write_data(0x1F)
        self.write_data(0x1F)
        self.write_data(0x20)
        self.write_data(0x23)

        self.write_cmd(0x21)
        self.write_cmd(0x11)
        self.write_cmd(0x29)

    def show(self):
        """將 frame buffer 寫入螢幕。"""
        self.write_cmd(0x2A)
        self.write_data(0x00)
        self.write_data(0x00)
        self.write_data(0x00)
        self.write_data(0xEF)

        self.write_cmd(0x2B)
        self.write_data(0x00)
        self.write_data(0x00)
        self.write_data(0x00)
        self.write_data(0xEF)

        self.write_cmd(0x2C)

        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(self.buffer)
        self.cs(1)


# =============== 螢幕參數與色彩常數 ===============
W, H = 240, 240
# 面板色彩順序為 BGR：程式送出的 R/G/B 會在面板上成為 B/R/G。
BLACK = 0x0000
WHITE = 0xFFFF
RED = 0x07E0    # 想顯示紅色 → 送出標準綠色
GREEN = 0x001F  # 想顯示綠色 → 送出標準藍色
BLUE = 0xF800   # 想顯示藍色 → 送出標準紅色
# 混合色：紅+綠 => G+B 通道
YELLOW = 0x07FF  # 紅+綠（G max + B max）
ORANGE = 0x07E8  # 紅為主、略帶綠（G max + B 約 1/4）
GRAY = 0x8410
PINK = 0xFFFE
HL = 0xCFF9

# LCD 實例，所有畫面繪製共用
lcd = _DummyLCD()
if FORCE_HEADLESS:
    LCD_AVAILABLE = False
else:
    try:
        lcd = LCD_1inch3()
        LCD_AVAILABLE = True
    except Exception as e:
        LCD_INIT_ERROR = e
        LCD_AVAILABLE = False
        print("LCD init failed, UI disabled:", e)

_HAS_POLY = hasattr(lcd, "poly")

# 可選背光控制：預設不動作，若需要可自行呼叫 set_backlight()
_backlight_pwm = None


def set_backlight(duty_u16: int = 32768):
    """設定背光亮度 (0~65535)；若硬體無背光則安全忽略。"""
    global _backlight_pwm
    try:
        if _backlight_pwm is None:
            _backlight_pwm = PWM(Pin(BL))
            _backlight_pwm.freq(1000)
        _backlight_pwm.duty_u16(max(0, min(65535, duty_u16)))
    except Exception as e:
        print("set_backlight ignored:", e)


def fill_header(title: str) -> None:
    """標頭繪製：統一抬頭區域樣式。"""
    lcd.fill(WHITE)
    lcd.fill_rect(0, 0, W, 22, BLUE)
    lcd.text(title, 6, 6, WHITE)


def footer_clear() -> None:
    """清除底部提示列，避免殘影。"""
    lcd.fill_rect(0, H - 20, W, 20, WHITE)


def trim(s: str, n: int) -> str:
    """過長字串以省略號縮短。"""
    return s if len(s) <= n else (s[: max(0, n - 1)] + "…")


def draw_scrollbar(total: int, first_idx: int, page_size: int) -> None:
    """列表捲軸顯示，依資料量計算滑塊比例。"""
    if total <= page_size:
        return
    x = W - 8
    y0 = 44
    h = H - y0 - 28
    lcd.rect(x, y0, 6, h, GRAY)
    thumb_h = max(10, int(h * page_size / total))
    max_first = total - page_size
    thumb_y = y0 + int((h - thumb_h) * (first_idx / max_first))
    lcd.fill_rect(x + 1, thumb_y, 4, thumb_h, GRAY)


def icon_arrow_left(x: int, y: int, c: int) -> None:
    """左箭頭小圖示。"""
    if _HAS_POLY:
        lcd.poly(0, 0, array("h", [x + 4, y - 4, x + 4, y + 4, x - 3, y]), c, True)
    else:
        for i in range(4):
            lcd.line(x + 3, y - 3 + i, x - 3, y, c)
            lcd.line(x + 3, y + 3 - i, x - 3, y, c)


def icon_arrow_right(x: int, y: int, c: int) -> None:
    """右箭頭小圖示。"""
    if _HAS_POLY:
        lcd.poly(0, 0, array("h", [x - 4, y - 4, x - 4, y + 4, x + 3, y]), c, True)
    else:
        for i in range(4):
            lcd.line(x - 3, y - 3 + i, x + 3, y, c)
            lcd.line(x - 3, y + 3 - i, x + 3, y, c)


def icon_cursor_right(x: int, y: int, c: int) -> None:
    """列表游標箭頭。"""
    if _HAS_POLY:
        lcd.poly(0, 0, array("h", [x - 5, y - 5, x - 5, y + 5, x + 6, y]), c, True)
    else:
        for i in range(6):
            lcd.line(x - 4, y - 4 + i, x + 5, y, c)
            lcd.line(x - 4, y + 4 - i, x + 5, y, c)
