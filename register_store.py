# register_store.py - local Modbus register map (0-255)

REG_COUNT = 256
_regs = [0] * REG_COUNT


def set_reg(index: int, value: int) -> None:
    if 0 <= index < REG_COUNT:
        _regs[index] = int(value) & 0xFFFF


def set_regs(start: int, values) -> None:
    if values is None:
        return
    for i, v in enumerate(values):
        idx = start + i
        if 0 <= idx < REG_COUNT:
            _regs[idx] = int(v) & 0xFFFF


def get_regs(start: int, count: int):
    if count <= 0:
        return []
    out = []
    for i in range(count):
        idx = start + i
        if 0 <= idx < REG_COUNT:
            out.append(_regs[idx])
        else:
            out.append(0)
    return out
