# register_store.py - local Modbus register map (0-255)
#
# 維護導讀：
# - 此暫存區用於「Poller 回填」與「Modbus TCP 本地讀取」。
# - 超出範圍的讀取會補 0；寫入則忽略。

REG_COUNT = 256
_regs = [0] * REG_COUNT


# =============== 單筆暫存器寫入 ===============
# 說明：
# 寫入單一 register；超出範圍時忽略。
def set_reg(index: int, value: int) -> None:
    """寫入單一 16-bit register。"""
    if 0 <= index < REG_COUNT:
        _regs[index] = int(value) & 0xFFFF


# =============== 多筆暫存器寫入 ===============
# 說明：
# 從指定起點連續寫入多筆資料；超出範圍時忽略該筆。
def set_regs(start: int, values) -> None:
    """從 start 起連續寫入多筆 16-bit register。"""
    if values is None:
        return
    for i, v in enumerate(values):
        idx = start + i
        if 0 <= idx < REG_COUNT:
            _regs[idx] = int(v) & 0xFFFF


# =============== 暫存器讀取 ===============
# 說明：
# 讀取連續 count 筆 register，越界位置補 0。
def get_regs(start: int, count: int):
    """讀取 count 筆 register；越界位置以 0 補齊。"""
    if count <= 0:
        return []
    # 讀取端採用無鎖 best-effort，避免高頻 TCP 讀取長時間阻塞 Poller 寫入。
    # 對 Gateway map 監看場景而言，偶發跨欄位非原子一致可接受。
    out = []
    for i in range(count):
        idx = start + i
        if 0 <= idx < REG_COUNT:
            out.append(_regs[idx])
        else:
            out.append(0)
    return out
